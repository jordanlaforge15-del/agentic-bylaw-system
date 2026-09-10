#!/usr/bin/env bash
# Behavioural test for the production Caddyfile (ABS-537).
#
# Caddy sits outside the Playwright stack — e2e talks to `next dev` and
# FastAPI directly — so nothing in `make e2e` ever loads this file. A
# typo, a misplaced directive, or a rate-limit rule that bites the wrong
# traffic only surfaced after a production reload. This script closes
# that gap by running the real Caddyfile in the real custom Caddy build
# and asserting on what comes out.
#
# How it stays honest: the harness is derived from the committed
# Caddyfile by changing only two things, both of which cannot run on a
# laptop —
#   * site addresses become http://<host>:8080, so Caddy serves plain
#     HTTP and never tries to obtain a certificate;
#   * `reverse_proxy <upstream>` becomes a fixed `respond`, since the
#     web / advisor containers do not exist here.
# Every log, header, rate_limit and redir directive is exercised
# byte-for-byte as it will run in production. The untouched Caddyfile is
# also run through `caddy adapt` first, so a syntax error in a line the
# harness rewrites still fails the test.
#
# Usage:  ./scripts/test-caddyfile.sh            (or: make caddy-test)
# Env:    CADDY_TEST_IMAGE  image to use (default bylaw-caddy-test:local;
#                           built from Dockerfile.caddy if absent)
#         CADDY_TEST_PORT   host port for the harness (default 18080)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${CADDY_TEST_IMAGE:-bylaw-caddy-test:local}"
PORT="${CADDY_TEST_PORT:-18080}"
NAME="abs-caddy-test-$$"
WORK="$(mktemp -d)"
FAILURES=0

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "==> Building $IMAGE from Dockerfile.caddy (first run only)"
  docker build -q -f "$ROOT/Dockerfile.caddy" -t "$IMAGE" "$ROOT" >/dev/null
fi
echo "==> Caddy: $(docker run --rm "$IMAGE" caddy version)"

# ---------------------------------------------------------------------------
# 1. The committed Caddyfile parses, unmodified.
# ---------------------------------------------------------------------------
echo "==> Adapting the committed Caddyfile"
if docker run --rm -v "$ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" "$IMAGE" \
     caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>"$WORK/adapt.err"; then
  pass "Caddyfile adapts cleanly"
else
  fail "Caddyfile does not adapt:"; sed 's/^/        /' "$WORK/adapt.err"
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Build and boot the harness.
# ---------------------------------------------------------------------------
python3 - "$ROOT/Caddyfile" "$WORK/Caddyfile" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
out = []
for line in open(src):
    # A site block opener: a bare hostname followed by "{".
    m = re.match(r'^([a-z0-9-]+(?:\.[a-z0-9-]+)+)\s*\{\s*$', line)
    if m:
        line = f"http://{m.group(1)}:8080 {{\n"
    # Upstreams do not exist here; answer in their place.
    line = re.sub(r'^(\s*)reverse_proxy\s+\S+\s*$', r'\1respond "upstream-ok" 200\n', line)
    out.append(line)
open(dst, "w").writelines(out)
PY

docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8080" \
  -v "$WORK/Caddyfile:/etc/caddy/Caddyfile:ro" "$IMAGE" >/dev/null

for _ in $(seq 1 40); do
  if curl -s -o /dev/null -H "Host: agenticbylawsystems.com" "http://127.0.0.1:${PORT}/"; then
    break
  fi
  sleep 0.5
done

req() {  # req <host> <path> [extra curl args...] -> prints status code
  local host="$1" path="$2"; shift 2
  curl -s -o /dev/null -w '%{http_code}' -H "Host: ${host}" "$@" "http://127.0.0.1:${PORT}${path}"
}

# ---------------------------------------------------------------------------
# 3. Access logging (ABS-537).
# ---------------------------------------------------------------------------
echo "==> Access logging (ABS-537)"

SECRET_Q="abs537secretquestion"
SECRET_COOKIE="abs537cookievalue"
SECRET_REF="abs537referrerquery"
SECRET_AUTH="abs537bearertoken"

s=$(req agenticbylawsystems.com "/pricing?first_message=${SECRET_Q}" \
      -A "abs537-probe/1.0" \
      -H "Cookie: session=${SECRET_COOKIE}" \
      -H "Authorization: Bearer ${SECRET_AUTH}" \
      -H "Referer: https://example.test/landing?q=${SECRET_REF}")
[ "$s" = "200" ] && pass "apex request served ($s)" || fail "apex request returned $s, expected 200"

s=$(req www.agenticbylawsystems.com "/about" -A "abs537-probe/1.0")
[ "$s" = "301" ] && pass "www request redirected ($s)" || fail "www request returned $s, expected 301"

s=$(req api.agenticbylawsystems.com "/healthz" -A "abs537-probe/1.0")
[ "$s" = "200" ] && pass "api request served ($s)" || fail "api request returned $s, expected 200"

sleep 1
docker logs "$NAME" > "$WORK/caddy.log" 2>&1

python3 - "$WORK/caddy.log" "$SECRET_Q" "$SECRET_COOKIE" "$SECRET_REF" "$SECRET_AUTH" <<'PY' || FAILURES=$((FAILURES + 1))
import json, sys
path, *secrets = sys.argv[1:]
raw = open(path).read()
access = []
for line in raw.splitlines():
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if rec.get("logger", "").startswith("http.log.access"):
        access.append(rec)

bad = 0
def check(ok, msg):
    global bad
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        bad += 1

probe = [r for r in access
         if (r.get("request", {}).get("headers", {}).get("User-Agent") or [""])[0] == "abs537-probe/1.0"]
hosts = {r["request"]["host"] for r in probe}
check(len(probe) >= 3, f"one access record per site block ({len(probe)} probe records)")
for h in ("agenticbylawsystems.com", "www.agenticbylawsystems.com", "api.agenticbylawsystems.com"):
    check(h in hosts, f"{h} is logged")

apex = [r for r in probe if r["request"]["host"] == "agenticbylawsystems.com"]
if apex:
    r = apex[0]
    check(r.get("status") == 200, f"status recorded ({r.get('status')})")
    check(bool(r["request"].get("remote_ip")), f"client IP recorded ({r['request'].get('remote_ip')})")
    check(r["request"].get("uri") == "/pricing?[redacted]",
          f"query string redacted, path kept ({r['request'].get('uri')})")
    ref = (r["request"]["headers"].get("Referer") or [""])[0]
    check(ref == "https://example.test/landing?[redacted]", f"Referer query redacted ({ref})")

for s in secrets:
    check(s not in raw, f"secret {s[:14]}... never reaches the log")

sys.exit(1 if bad else 0)
PY

# ---------------------------------------------------------------------------
# 4. Rate limiting (ABS-511).
#
# Every request below comes from one IP (the Docker bridge), so the zones
# are exercised exactly as a single crawler IP would hit them. Order
# matters: the browser burst deliberately exhausts `global` so that the
# robots.txt and POST checks after it run against an empty bucket.
# ---------------------------------------------------------------------------
echo "==> Rate limiting (ABS-511)"

# The crawler user-agent pattern appears in two zones (`crawlers`, and
# `global`'s `not` block). If they drift, a bot exempted from `global` but
# absent from `crawlers` matches no zone and is unlimited — and no burst
# test using Googlebot would notice. Checked statically, from the file.
ua_total=$(grep -cE '^[[:space:]]*header_regexp User-Agent ' "$ROOT/Caddyfile" || true)
ua_distinct=$(grep -E '^[[:space:]]*header_regexp User-Agent ' "$ROOT/Caddyfile" \
  | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | sort -u | wc -l | tr -d ' ')
if [ "$ua_total" -ge 2 ] && [ "$ua_distinct" -eq 1 ]; then
  pass "crawler user-agent pattern identical in both zones ($ua_total copies)"
else
  fail "crawler user-agent pattern differs between zones ($ua_total copies, $ua_distinct distinct)"
fi

GOOGLEBOT_UA="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BROWSER_UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"

count429() {  # count429 <n> <path> [extra curl args...] -> number of 429s out of n
  local n="$1" path="$2"; shift 2
  seq "$n" | xargs -P 25 -I{} curl -s -o /dev/null -w '%{http_code}\n' \
      -H "Host: agenticbylawsystems.com" "$@" "http://127.0.0.1:${PORT}${path}" \
    | grep -c '^429$' || true
}

n=$(count429 150 /pricing -A "$GOOGLEBOT_UA")
[ "$n" -eq 0 ] && pass "150 Googlebot GETs in a burst: none limited" \
               || fail "150 Googlebot GETs in a burst: $n got 429"

n=$(count429 150 /pricing -A "$BROWSER_UA")
[ "$n" -gt 0 ] && pass "150 browser GETs in a burst: $n limited (global zone still bites)" \
               || fail "150 browser GETs in a burst: none limited — global zone is not applied"

# The measured production defect: with this IP's page bucket empty,
# robots.txt must still answer. A 429 here reads to Google as "whole
# host disallowed".
s=$(req agenticbylawsystems.com /robots.txt -A "$BROWSER_UA")
[ "$s" = "200" ] && pass "robots.txt still 200 after the page bucket is exhausted" \
                 || fail "robots.txt returned $s after the page bucket was exhausted"
s=$(req agenticbylawsystems.com /sitemap.xml -A "$BROWSER_UA")
[ "$s" = "200" ] && pass "sitemap.xml still 200 after the page bucket is exhausted" \
                 || fail "sitemap.xml returned $s after the page bucket was exhausted"

# The crawler exemption is GET/HEAD only; a Googlebot user-agent on a
# POST lands in the exhausted global bucket.
s=$(req agenticbylawsystems.com /pricing -X POST -A "$GOOGLEBOT_UA")
[ "$s" = "429" ] && pass "Googlebot POST is not exempt (429)" \
                 || fail "Googlebot POST returned $s — the exemption covers more than GET/HEAD"

# Isolation, not immunity: a flood on robots.txt is still bounded.
n=$(count429 80 /robots.txt -A "$BROWSER_UA")
[ "$n" -gt 0 ] && pass "80 robots.txt requests in a burst: $n limited (own bucket, still bounded)" \
               || fail "80 robots.txt requests in a burst: none limited — crawl_meta zone missing"

# ---------------------------------------------------------------------------
echo
if [ "$FAILURES" -eq 0 ]; then
  echo "==> Caddyfile behaves as intended."
else
  echo "==> $FAILURES failing check group(s)." >&2
  exit 1
fi

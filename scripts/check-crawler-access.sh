#!/usr/bin/env bash
# What does a search crawler actually receive? (ABS-541)
#
# Fetches every public route the way Googlebot fetches HTML — smartphone
# user-agent, `Accept: text/html`, browser navigation headers — and fails
# if any of them is not a plain 200.
#
# Why this exists: for weeks every check of the live site used curl with
# no Accept header and got 200, while Googlebot's indexing fetch got a 307
# to Clerk's development-instance handshake on clerk.accounts.dev and never
# came back. Clerk only redirects browser-style requests, so the difference
# between "curl says fine" and "Google cannot index anything" was one
# request header. The e2e stack cannot catch this — it runs a Clerk mock,
# not a real instance — so this runs against a real deployment: production
# after a deploy, or a locally-run production image before one.
#
# Usage:  ./scripts/check-crawler-access.sh [BASE_URL]
#         (default https://agenticbylawsystems.com; or: make crawler-check)

set -euo pipefail

BASE="${1:-https://agenticbylawsystems.com}"
BASE="${BASE%/}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UA='Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'

# The public routes come from the same list the sitemap renders, so this
# check cannot drift from what we ask Google to crawl.
# (A while-read loop rather than mapfile: macOS ships bash 3.2.)
ROUTES=()
while IFS= read -r route; do ROUTES+=("$route"); done \
  < <(grep -oE 'path: "[^"]+"' "$ROOT/web/lib/public-routes.ts" | cut -d'"' -f2)
ROUTES+=("/robots.txt" "/sitemap.xml")

if [ "${#ROUTES[@]}" -lt 5 ]; then
  echo "could not read PUBLIC_ROUTES from web/lib/public-routes.ts" >&2
  exit 2
fi

echo "==> $BASE — ${#ROUTES[@]} routes, fetched as Googlebot requesting HTML"
failures=0
for path in "${ROUTES[@]}"; do
  out=$(curl -s -o /dev/null -m 20 -w '%{http_code} %{redirect_url}' \
    -A "$UA" \
    -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
    -H 'Sec-Fetch-Dest: document' \
    -H 'Sec-Fetch-Mode: navigate' \
    -H 'Sec-Fetch-Site: none' \
    "$BASE$path" || echo "000 curl-failed")
  code="${out%% *}"
  where="${out#* }"
  if [ "$code" = "200" ]; then
    printf '  PASS  %-12s 200\n' "$path"
  else
    printf '  FAIL  %-12s %s %s\n' "$path" "$code" "$where"
    failures=$((failures + 1))
  fi
done

echo
if [ "$failures" -eq 0 ]; then
  echo "==> Every public route serves a crawler a 200."
else
  echo "==> $failures route(s) do not serve a crawler a 200. Google cannot index those." >&2
  exit 1
fi

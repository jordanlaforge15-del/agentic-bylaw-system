# Deployment

This doc is the canonical context for deploying the Halifax Bylaw Advisor to its production host. It covers what's running, where, and how to ship a change. Pair it with `architecture.md` (data flows) and the per-service `web/AGENTS.md` (frontend conventions).

## Production server

- **Host**: Hetzner CX22 (Intel Xeon Skylake, 2 vCPU shared, 4 GB RAM, 40 GB SSD)
- **Region**: Nuremberg (lowest measured RTT from Halifax via the operator's ISP — see commit log for ping data)
- **SSH alias**: `bylaw-prod` in operator's `~/.ssh/config`. Login user: `deploy` (non-root, sudo-capable, key-only). Root SSH and password auth are disabled in `/etc/ssh/sshd_config.d/00-hardening.conf`.
- **Firewall**: `ufw` allowing only 22 / 80 / 443 inbound. `fail2ban` with the default sshd jail.
- **Public hostnames**:
  - `https://agenticbylawsystems.com` → web (Next.js)
  - `https://api.agenticbylawsystems.com` → advisor (FastAPI)
- **DNS**: A records at the registrar pointed at the server IPv4. Cloudflare is *not* in front (Caddy issues real Let's Encrypt certs via HTTP-01).

## Server file layout

Everything production lives under `/srv/bylaw/`:

```
/srv/bylaw/
├── docker-compose.yml      # production compose (NOT in git — see "follow-ups")
├── .env                    # all secrets (chmod 600, deploy:deploy)
├── Caddyfile               # reverse proxy + TLS + rate-limit config
├── Dockerfile.caddy        # custom Caddy build with caddy-ratelimit plugin
├── Dockerfile.postgres     # custom Postgres build with pgvector + PostGIS
├── backup.env              # backup config sourced by cron (chmod 600)
├── backup.pass             # gpg passphrase (chmod 600 — also in the password manager)
├── scripts/                # backup-prod-db.sh, verify-prod-backup.sh, installer
└── backups/                # rotation dir + backup.log + cron.log (ABS-131)
```

The repo's `docker-compose.yml` at root is the **local dev** compose (postgres + codex container); it's NOT used in production. The repo's `Caddyfile`, `Dockerfile.advisor`, `Dockerfile.caddy`, `Dockerfile.postgres`, `web/Dockerfile`, and root `.dockerignore` *are* the source-of-truth that the server-side copies mirror — sync them via `scp` when the repo versions change.

## Container architecture

Four containers, all in the default Docker network so they reach each other by service name:

| Service | Image | Ports | Notes |
|---|---|---|---|
| `caddy` | `bylaw-caddy:latest` (built from `Dockerfile.caddy`) | 80, 443 public | Terminates TLS, routes by host, enforces rate limits |
| `web` | `ghcr.io/jordanlaforge15-del/bylaw-web:X.Y.Z` | 3000 internal | Next.js standalone build. Reaches advisor at `http://advisor:8000` |
| `advisor` | `ghcr.io/jordanlaforge15-del/bylaw-advisor:X.Y.Z` | 8000 internal | FastAPI / uvicorn. Reads/writes Postgres at `postgres:5432`. Uploaded submission artefacts go to the `bylaw_submissions` volume at `/var/lib/bylaw/submissions` — the container's only writable non-tmpfs path (ABS-87) |
| `postgres` | `bylaw-postgres:latest` (built from `Dockerfile.postgres`) | 5432 internal | PG16 + pgvector + PostGIS 3.4. Data in Docker named volume `bylaw_bylaw_postgres_data` |

All containers run as non-root inside (UID 1000 advisor, UID 1001 nextjs), with `cap_drop: [ALL]`, `read_only: true` filesystems, `no-new-privileges:true`, and `mem_limit` / `pids_limit` caps. See `docker-compose.yml` on the server for the canonical config.

## Image build & publish workflow

We use **GitHub Container Registry** (`ghcr.io`) for the two app images. Custom Caddy/Postgres images are built directly on the server (no registry round-trip).

### Auth

Local Docker on the operator's laptop is logged in via:

```bash
echo <PAT> | docker login ghcr.io -u jordanlaforge15-del --password-stdin
```

The PAT needs `write:packages` scope. Credentials persist in `~/.docker/config.json` (base64-encoded — treat the file as a secret). The server has its own login with a `read:packages`-only deploy PAT.

### Web image

```bash
caffeinate -i -s docker buildx build \
  --platform linux/amd64 \
  -f web/Dockerfile \
  --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_... \
  -t ghcr.io/jordanlaforge15-del/bylaw-web:X.Y.Z \
  --push \
  web/
```

- Server is x86_64, build target must be `linux/amd64` (laptop is Apple Silicon — QEMU emulates).
- `caffeinate -i -s` prevents macOS sleep during the long upload (residential upstream is slow).
- **Always bump the version tag**. Never deploy `:latest` to production.
- Final image is ~120 MB.
- **`--build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...` is required.** Next.js inlines `NEXT_PUBLIC_*` env vars at build time. Client components (`top-nav.tsx`, `sidebar.tsx`) read the publishable key to decide whether to render Clerk's signed-in/out UI; if the build-arg is missing they bake in `undefined` and silently hide the sign-in CTA in production. The server-side `proxy.ts` no longer depends on this (it reads `CLERK_SECRET_KEY` at runtime instead) but the client components have no choice — they run in the browser. Pull the value from `/srv/bylaw/.env` on prod for parity.

### Advisor image

```bash
caffeinate -i -s docker buildx build \
  --platform linux/amd64 \
  -f Dockerfile.advisor \
  -t ghcr.io/jordanlaforge15-del/bylaw-advisor:X.Y.Z \
  --push \
  .
```

- Multi-stage build, runtime stage installs only `pip install ".[advisor]"` (the request-path subset of `pyproject.toml`'s optional extras).
- Final image is ~120 MB. If a build produces >500 MB, something's wrong with the `.[advisor]` split.
- Build context is the repo root — make sure `docs/agent/persona.md` is present (the advisor's chat persona loader still reads it from a path-from-package location; tracked as a packaging follow-up).

### Caddy and Postgres images

Built on the server, never pushed to a registry:

```bash
ssh bylaw-prod "cd /srv/bylaw && docker compose build caddy"
ssh bylaw-prod "cd /srv/bylaw && docker compose build postgres"
```

`docker compose build` reads the corresponding `Dockerfile.*` next to the compose file. Rebuild when:
- `Dockerfile.caddy` or `Dockerfile.postgres` changes (sync via `scp` first).
- You need a fresh base image for security patches (`docker compose build --no-cache <service>`).

## Deployment workflow (code change → production)

Standard recipe for a code change to web or advisor:

1. **Branch**: `git checkout -b fix/short-description` from main.
2. **Code & test locally**:
   - Web: edits + `npm run typecheck` in `web/`. Run `npm install` first if `web/node_modules/` is stale or missing — typecheck depends on the dev deps (e.g. `@playwright/test`) being on disk, and will otherwise fail with cascading "implicit any" errors in `e2e/`. Dev server (`npm run dev`) auto-reloads.
   - Advisor: edits + `pytest tests/advisor/` (must all pass).
3. **Commit** with a real message. Co-Authored-By line if Claude was a contributor.
4. **Build & push** with a bumped version tag (see "Image build & publish workflow" above).
5. **Update server compose** to reference the new tag:
   ```bash
   ssh bylaw-prod "sed -i 's|bylaw-web:OLD|bylaw-web:NEW|' /srv/bylaw/docker-compose.yml"
   # or for advisor:
   ssh bylaw-prod "sed -i 's|bylaw-advisor:OLD|bylaw-advisor:NEW|' /srv/bylaw/docker-compose.yml"
   ```
5a. **Advisor image preflight smoke (HARD GATE — advisor deploys only)**:
    Pull the new image, then run `scripts/preflight_advisor_image.sh` before swapping
    the container. If the smoke exits non-zero, **abort the deploy** — the old
    container is still running and no rollback is needed.
    ```bash
    # Pull the new image on the server (layers stay cached for the actual up -d)
    ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml pull advisor"

    # Run the import smoke under prod-mirroring runtime constraints
    ssh bylaw-prod "docker run --rm \
      --read-only \
      --tmpfs /tmp:size=64m,mode=1777 \
      --env-file /srv/bylaw/.env \
      --network bylaw_default \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      ghcr.io/jordanlaforge15-del/bylaw-advisor:NEW \
      python -c 'import advisor.api.main'"
    # Expect: exit 0. Any non-zero exit means a missing import or startup-time
    # filesystem violation — do NOT proceed to step 6.
    ```
    See `scripts/preflight_advisor_image.sh` for the canonical script form with
    help text and override env vars. **Prefer the script**: the one-liner above
    only proves the app imports. The script additionally loads every runtime
    data file the wheel must carry and the corpus-coherence audit's overlay
    declarations — the class of gap that let `/v1/monitoring/corpus-coherence`
    report a green while checking zero roles (ABS-412, ABS-420).
6. **Pull & restart just that service**:
   ```bash
   ssh bylaw-prod "cd /srv/bylaw && docker compose pull web && docker compose up -d web"
   # Advisor: pull was already done in step 5a; just recreate the container
   ssh bylaw-prod "cd /srv/bylaw && docker compose up -d advisor"
   ```
7. **Verify**: `curl` against the public endpoint, check `docker compose ps`, tail logs (`docker compose logs --tail 30 <svc>`). For chat changes, send a real query.
   - The release carrying migration `0024_document_retrieval_enabled` has its own
     post-deploy procedure — the retrieval scope switches from newest-per-by-law
     to an explicit per-document flag, and nothing looks different if the backfill
     ran against a corpus that moved. Run
     `scripts/apply-abs420-retrieval-rollout.sh verify` and follow
     [ABS-420-RETRIEVAL-ENABLED-ROLLOUT.md](ABS-420-RETRIEVAL-ENABLED-ROLLOUT.md).
8. **Merge to main** and push: `git checkout main && git merge --no-ff fix/... && git push origin main`.
9. **Delete the feature branch**: `git branch -d fix/...`.

Whole loop is typically ~10–20 minutes including build time, dominated by laptop upload speed on the GHCR push.

### Restarting / recreating

```bash
ssh bylaw-prod "cd /srv/bylaw && docker compose restart <svc>"   # process restart, same container
ssh bylaw-prod "cd /srv/bylaw && docker compose up -d <svc>"     # recreate (pulls new image if tag changed)
ssh bylaw-prod "cd /srv/bylaw && docker compose ps"              # status check
```

### Rollback

```bash
# Set compose tag back to the prior version, recreate
ssh bylaw-prod "sed -i 's|bylaw-X:NEW|bylaw-X:OLD|' /srv/bylaw/docker-compose.yml"
ssh bylaw-prod "cd /srv/bylaw && docker compose pull <svc> && docker compose up -d <svc>"
```

Old image layers stay on disk until you `docker image prune`, so rollback is fast.

## Configuration & secrets

`/srv/bylaw/.env` is the single source of truth. Loaded by `docker-compose.yml` via `env_file: .env` on the `postgres` and `advisor` services. (`web` uses an explicit `environment:` list because its Next.js build bakes `NEXT_PUBLIC_*` values at image-build time, so server-side env additions for the web service still need a compose edit. Postgres and advisor pick up new `.env` keys on the next `docker compose up -d`.)

Never check `.env` into git.

Currently populated keys:

```
# Postgres
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, DATABASE_URL

# Advisor LLM
ADVISOR_LLM_PROVIDER=anthropic, ADVISOR_LLM_MODEL=claude-opus-4-5, ANTHROPIC_API_KEY

# Layer 2 retrieval defaults
LAYER2_PROMPT_VERSION, LAYER2_RETRIEVAL_VERSION, LAYER2_TOKEN_BUDGET, LAYER2_TOP_K

# Google Maps geocoder (civic-address fallback)
GOOGLE_MAPS_API_KEY
GOOGLE_MAPS_COMPONENTS=country:CA|administrative_area:NS|locality:Halifax
# The components filter is a hard constraint, not a hint. Narrowing to
# NS+Halifax prevents Google from routing ambiguous queries like
# "5245 Smith St" to other Canadian cities (Winnipeg's Smith St used to
# win with just country:CA). See src/layer2/retrieval/google_geocoder.py.

# Advisor server bind
ADVISOR_HOST=0.0.0.0, ADVISOR_PORT=8000

# Billing posture (beta pivot, ABS-379). Go-live: billing ON, payments OFF.
ADVISOR_BILLING_ENABLED=true          # master switch; false → /v1/billing/* 503
ADVISOR_PAYMENTS_ENABLED=false        # false → paid top-ups 503; wallet funded by signup grant only
ADVISOR_CONVERSATION_ENTRY_ENABLED=true   # /cases/new leads with the turn-based chat
ADVISOR_OTHER_QUESTION_ENABLED=false  # off-menu free-form question kill switch
STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET # required only when ADVISOR_PAYMENTS_ENABLED=true
STRIPE_PRICE_TOPUP_SMALL, STRIPE_PRICE_TOPUP_MEDIUM, STRIPE_PRICE_TOPUP_LARGE  # token top-up Price IDs (ABS-381)
ADVISOR_BILLING_SUCCESS_URL, ADVISOR_BILLING_CANCEL_URL
# RETIRED: the legacy STRIPE_PRICE_<TIER>_<PACK> credit-pack Price IDs (quick/
# standard/complex × payg/starter/pro/enterprise). The token wallet replaced
# them; POST /v1/billing/checkout/pack now answers 410 packs_retired. Do NOT
# configure them — they sell nothing.

# Token wallet / turns parameters (ABS-380, recalibrated ABS-416). Read fresh
# per request — a re-calibration takes effect on `docker compose up -d advisor`,
# no rebuild. Defaults below are measured against prod burn: a real grounded
# question costs 103k-248k tokens (src/advisor/billing/turns.py has the sample).
ADVISOR_TOKENS_PER_TURN=175000          # display divisor (backend-owned "~N turns")
ADVISOR_SIGNUP_TOKEN_GRANT=525000       # one-time new-user wallet grant (3 turns)
ADVISOR_CHAT_MIN_BALANCE_TOKENS=0       # pre-flight floor: chat 402s at balance <= floor
ADVISOR_LOW_BALANCE_WARN_TOKENS=175000  # "low balance" at <= warn (1 turn)
# ABS-405 self-serve beta refill: the payments-off way out of an overdrawn
# wallet. Without it an exhausted tester needs a manual grant_tokens by an
# operator. Ignored once ADVISOR_PAYMENTS_ENABLED is true.
ADVISOR_BETA_REFILL_ENABLED=true         # off => back to the manual-grant dead-end
ADVISOR_BETA_REFILL_TOKENS=175000        # per claim (1 turn)
ADVISOR_BETA_REFILL_COOLDOWN_HOURS=6     # 0 = no cooldown; the cap still applies
ADVISOR_BETA_REFILL_MAX_GRANTS=3         # lifetime cap per account; 0 disables
ADVISOR_CHAT_MAX_ITERATIONS=20          # tool-loop cap per chat turn
ADVISOR_TURN_MAX_WALLET_TOKENS=350000   # per-turn ceiling on MEASURED burn (2 turns)

# Per-report gate (ABS-384): which of the five report SKUs are on sale
ADVISOR_ENABLED_QUESTIONS   # csv slugs; `*` = all; unset/empty = NONE (deny-by-default)

# Operator allowlist for /admin/* — the ONLY way in since ABS-530
# removed the shared-password gate. Needed by BOTH the advisor and the
# web container. Unset = nobody is an admin (fail closed).
ADVISOR_ADMIN_CLERK_USER_IDS=user_xxx,user_yyy
```

> **Signup-grant cost note (resized on ABS-404).** The `~$0.55 USD per 100k
> wallet-token` anchor this note used to quote was wrong by ~5x. The wallet
> counts `input + output` only, while cache writes and reads are 35% of the
> dollar cost — so the real figure is cost per *wallet-counted* token, measured
> at **~$28.9/MTok USD** (`docs/COST_MODEL.md`, ABS-303 real-API run). One 175k
> turn is therefore ~$5.05, not ~$0.96, and the old 10-turn grant was ~$50 of
> API spend per free, no-card signup rather than ~$9.60. The default is now 3
> turns (~$15). Both knobs are read fresh per request, so retuning either is
> `docker compose up -d advisor` — no code change or rebuild — and the "~N
> turns" the free-trial card advertises follows the env value automatically.

> **Do not set `ADVISOR_TURN_MAX_WALLET_TOKENS=0`.** Non-positive values fall
> back to the derived default by design: zero would otherwise disable the only
> breaker that bounds a turn in the unit the wallet charges, restoring the
> unbounded burn ABS-404 fixed. Raise it if grounded answers are being
> truncated; the trip is auditable as `trip_reason=wallet_cap_trip` on the
> `llm_call` UsageEvent's `metadata_json`.

### Compose variable substitution

Values referenced from the YAML as `${VAR}` are interpolated from `.env`. **Any literal `$` in a value must be escaped as `$$`** or compose will try to substitute it as a variable name and silently set it to an empty string. Diagnosed once during the original deploy when a strong password starting with `$` came through as `""`.

### Adding a new env var

1. Add to `/srv/bylaw/.env`.
2. **Advisor / postgres:** nothing else to do — both use `env_file: .env`. `docker compose up -d advisor` recreates the container with the new var. (Note: editing `.env` causes compose to also recreate `postgres` on the next `up -d` because it shares the same `env_file`. Postgres data lives in a named volume, so there's no data loss, but expect a brief DB restart.)
3. **Web:** add to the `environment:` block in `/srv/bylaw/docker-compose.yml` *and* rebuild the image if it's a `NEXT_PUBLIC_*` value (those are baked at build time). Server-only web env vars only need a `docker compose up -d web`.

### Submission artefact storage (ABS-87)

The submission upload endpoints (`POST /v1/submissions` for the web UI,
`POST /v1/integrations/submissions` for API-key/Speckle callers) stage the
uploaded `.ifc` / `.pdf` on disk before the extractors read it — the IFC and
APS parsers want a real path, not a stream. The advisor container is
`read_only: true`, so that write needs a mounted volume; without one, every
upload returns `503 {code:"submission_storage_unavailable"}`. (Before ABS-87
the router `mkdir`'d its storage root at app construction and the whole
container failed to boot — that's the ABS-70 / v0.8.4 incident.)

Two moving parts, both mirrored in the repo's `docker-compose.production.yml`:

* a `bylaw_submissions` named volume mounted at `/var/lib/bylaw/submissions`
  on the `advisor` service;
* `SUBMISSION_STORAGE_DIR=/var/lib/bylaw/submissions` in `/srv/bylaw/.env`.

**One-time rollout on a running deployment:**

```bash
# 1. Add the volume mount + env var to the server's compose file to match
#    docker-compose.production.yml (advisor service: `volumes:` entry;
#    file bottom: the `bylaw-submissions` volume with `name: bylaw_submissions`).
ssh bylaw-prod "vi /srv/bylaw/docker-compose.yml"

# 2. Point the app at the mount.
ssh bylaw-prod "echo 'SUBMISSION_STORAGE_DIR=/var/lib/bylaw/submissions' >> /srv/bylaw/.env"

# 3. Recreate the advisor. Docker creates the named volume on first use,
#    owned by root — the container runs as UID 1000 and cannot write to it
#    until it's chowned, so do that before declaring victory.
ssh bylaw-prod "cd /srv/bylaw && docker compose up -d advisor"
ssh bylaw-prod "docker run --rm -v bylaw_submissions:/mnt alpine chown -R 1000:1000 /mnt"

# 4. Verify — this is the check that would have caught the gap pre-deploy.
ssh bylaw-prod "curl -s localhost:8000/healthz" | jq .checks.submission_storage
# → "ok"   (a missing/unwritable mount reports "unwritable")
```

`checks.submission_storage` is deliberately **not** fatal to `/healthz` —
`status` stays `ok` and the endpoint stays 200, because the availability
monitor pages on a non-200 and a degraded upload feature shouldn't take the
chat product out of rotation. Read the field, don't rely on the status code.

**Backups: the volume is intentionally out of the backup story.** Uploaded
artefacts are reproducible inputs, not system of record — the extracted
attributes, overrides, approval decisions and audit trail are all in Postgres,
and nothing re-reads the file after ingest. The nightly `pg_dump` therefore
remains a complete backup; losing this volume costs an architect a re-upload
and nothing else. Revisit if a feature ever re-parses the original artefact
(e.g. re-running an improved extractor over historical submissions).

### Enabling / disabling a report SKU (ABS-384)

`ADVISOR_ENABLED_QUESTIONS` gates the five priced-report slugs
(`permitted_use`, `development_standards`, `due_diligence`,
`legal_nonconforming`, `variance_justification`) independently. It is read
at **request time**, so editing `/srv/bylaw/.env` and recreating the advisor
container (`docker compose up -d advisor`) is enough — no image rebuild.
Format: comma-separated slugs; `*` enables all; **unset/empty enables NONE**
(deny-by-default). A disabled slug vanishes from the `/v1/billing/questions`
menu and its purchase paths (`checkout/question`, `questions/intake`,
`questions/free-start`, and running an `authorized` purchase's answer) return
`503 {code:"question_disabled"}`.

**Drain before disabling.** Already-`captured` reports stay fully accessible
regardless of this flag (the answer-delivery routes are ungated). But a
purchase can sit in `authorized` — a Stripe hold placed, the answer not yet
run. Disabling that slug makes its `.../answer` run 503, stranding the hold.
So before turning a slug off, drain (run or void) any `authorized`
(uncaptured-hold) purchases for it; then flip the flag.

## Database operations

### Schema migrations

**Alembic migrations are run manually over SSH, never on container startup.** This is deliberate — startup migrations would block trial users during deploys. See `[Alembic version_num column width follow-up](#open-follow-ups)`.

```bash
# Always preview first
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic upgrade head --sql" | less

# Apply
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic upgrade head"

# Verify current revision
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic current"
```

### Data migrations (corpus repairs)

Some migrations repair *content*, not shape — `0027_permission_grid_backfill`
materializes the blank permission-matrix cells the PDF parser drops
([ABS-520](ABS-520-RAGGED-PERMISSION-GRID.md)). They ride in the same
`alembic upgrade head` for one reason: ABS-520's repair shipped as code plus a
hand-run script, the script was only ever run against dev, and production spent
a release cycle telling users a prohibition "could not be extracted" while every
test stayed green. A repair with no delivery mechanism is not deployed.

Two things behave differently for them:

* **`--sql` shows nothing.** The statements depend on what the corpus geometry
  says, so a data migration cannot be rendered offline. It logs a warning under
  `alembic upgrade head --sql` and does its work only against a live database.
* **They are corpus-sized, not row-sized.** Read the summary line the migration
  logs (`filled=… refused=…`) rather than assuming success — the refused count
  is real extraction debt that stays `unknown` on purpose.

Verify a corpus repair after the upgrade:

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor \
  python scripts/verify_permission_grid_integrity.py --zone ER-2"
```

### Expand/contract discipline

Because migrations run before the new code deploys (or before the old code is rolled back), every migration must be backwards-compatible across the deploy window:

1. SSH in → run the **additive** half of the migration (new nullable column, new table, new index `CONCURRENTLY`). Old advisor keeps working because it ignores the new shape.
2. Deploy the new advisor image. Both old and new code tolerate the transitional schema.
3. Once you're confident no rollback is needed, SSH in → run the **cleanup** half (drop old column, add `NOT NULL`, drop old index).

Avoid rename-or-drop-in-one-step migrations — they break the rollback story.

### Data restore from local dev

All ingest happens **locally** (the production Dockerfile.advisor deliberately omits docling and the rest of the PDF tooling — running an ingest CLI inside a prod container will fail and is policy-banned anyway). The runbook is: ingest locally → dump → ship → restore on prod.

#### Never overwrite user / billing / auth data

Prod is the source of truth for any table that records a real user, a real payment, a real conversation, or a real invite. Local dev never has the production rows for these and a bulk reload from local would silently destroy them. Hard rule: every dump command and every restore script for production **MUST exclude or skip every table in this set:**

| Table | Why |
|---|---|
| `advisor_user` | Real Clerk-keyed users; loss = signup work redone, billing orphaned |
| `advisor_case` | Open cases tied to billed credits |
| `advisor_case_credit` | One row per purchased credit — real money attached |
| `advisor_case_purchase` | Stripe checkout records — accounting source of truth |
| `advisor_case_event` | Append-only audit trail; needed for support diagnostics |
| `advisor_chat_session` | User conversation threads |
| `advisor_chat_message` | Individual turns within sessions |
| `advisor_usage_event` | Per-call billing/audit events |
| `invite_request` | Beta allowlist state, Clerk allowlist sync |
| `alembic_version` | Schema-state pointer — drives migration replay |

Replaceable (these are content / cache / derived data; restoring them from local is fine):

- `document`, `source_fragment`, `page_block`, `source_table`, `source_table_cell`, `cross_reference` — bylaw content
- `external_dataset`, `external_dataset_feature` — geo layers (zoning, heights, FAR, …, parcels)
- `geocode_cache` — pure cache; regenerable from queries

If you find yourself reaching for a destructive command (`TRUNCATE`, `DROP TABLE`, `pg_restore` without `--data-only --exclude-table` flags) against anything in the first table, **stop**. Take a `pg_dump` of that table before continuing and confirm with the operator. A surgical row-level `\COPY` of one specific feature's rows is fine; a wholesale data-only reload is not — it can drop hours of user-side activity that arrived since your local dump was taken.

#### Full reload (initial deploy / disaster recovery)

```bash
# 1. Local: pg_dump --data-only with EVERY user/billing/auth table excluded:
pg_dump --data-only \
  --exclude-table=alembic_version \
  --exclude-table=advisor_user --exclude-table=advisor_case \
  --exclude-table=advisor_case_credit --exclude-table=advisor_case_purchase \
  --exclude-table=advisor_case_event --exclude-table=advisor_chat_session \
  --exclude-table=advisor_chat_message --exclude-table=advisor_usage_event \
  --exclude-table=invite_request \
  layer1 | gzip > /tmp/layer1-content-$(date +%F).sql.gz
# 2. Strip pg_dump's \restrict and \unrestrict meta-commands (Postgres 16.13+ emits them;
#    server psql 16.4 doesn't recognise them). Use Python, NOT grep — grep is not
#    binary-safe against COPY data with embedded bytes.
# 3. scp the cleaned dump to /srv/bylaw/backups/
# 4. Restore wrapped in SET session_replication_role = replica; ... DEFAULT; (the source_fragment
#    table has a self-referential FK that pg_dump can't fully linearise).
```

See the commit `[advisor] Fix session-detail 404 caused by user_id format mismatch` and prior history for the exact restore script. Don't reinvent it.

#### Surgical reload (one new dataset)

Adding a single `external_dataset` (e.g. a new geo layer) doesn't need the full-reload sledgehammer. Use `psql \COPY (SELECT … WHERE external_dataset_id = N)` to scope the dump to just the new rows, then `\COPY … FROM` to insert on prod. After insert, re-derive the PostGIS `geometry` column with `UPDATE … SET geometry = ST_GeomFromGeoJSON(geometry_geojson::text)` — mirroring migration 0009's pattern. The two user/billing rules above still apply (a surgical insert never overwrites; a TRUNCATE-and-replace on `external_dataset_feature` would, so don't).

### Backups (automated — ABS-131)

Nightly at 02:30 the host dumps `layer1`, verifies the archive is readable
and carries the four system-of-record tables, encrypts it, rotates it
through 7 daily + 4 weekly slots, and mirrors that set to a Hetzner
Storage Box. At 04:00 on Sundays it restores the newest artifact into a
throwaway Postgres and counts rows, so a week of silently corrupt dumps
surfaces within seven days instead of during an outage.

Full runbook — Storage Box setup, the passphrase, the restore procedure —
in **[PROD_DB_BACKUP.md](PROD_DB_BACKUP.md)**. The short version:

```bash
# Check on it
tail -n 40 /srv/bylaw/backups/backup.log
/srv/bylaw/scripts/verify-prod-backup.sh            # fast archive check

# Take an extra dump by hand before a risky migration. Name it outside the
# rotation patterns so the prune never touches it.
set -a; . /srv/bylaw/backup.env; set +a
docker exec -i bylaw-postgres pg_dump -U layer1 -d layer1 -Fc \
  > /srv/bylaw/backups/layer1-prod-pre-migration-$(date +%F).dump.manual
```

The gpg passphrase lives in `/srv/bylaw/backup.pass` **and** in the
operator's password manager. If it only ever lived on the server, the
offsite copies are unreadable the day the server dies.

This dump is the *complete* backup. The only other stateful volume,
`bylaw_submissions` (uploaded IFC/PDF artefacts), is deliberately excluded —
see [Submission artefact storage](#submission-artefact-storage-abs-87) for why.

### Running ops scripts

Data processing scripts (e.g. `scripts/backfill_parcels.py`, `scripts/inspect_zoning_canonical.py`, `scripts/pilot_variance_report.py`) are built into the advisor image and can be run against the production database via:

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor python scripts/<name>.py"
```

Examples:

```bash
# Backfill parcels from the GIS layer
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor python scripts/backfill_parcels.py"

# Inspect the zoning bylaw canonical names
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor python scripts/inspect_zoning_canonical.py"

# Generate variance report
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor python scripts/pilot_variance_report.py"
```

Scripts inherit the same database connection and environment variables (`.env` keys) as the running advisor container, so any `DATABASE_URL`, `GOOGLE_MAPS_API_KEY`, or other credentials needed are already available.

## Auth modes

The advisor + web together support two auth modes, switched by env var presence:

### Clerk mode (the only production mode)

- `CLERK_JWKS_URL`, `CLERK_AUDIENCE`, `CLERK_ISSUER` set in advisor's env.
- `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` set in web's env.
- `ADVISOR_ADMIN_CLERK_USER_IDS` set in BOTH — it is what lets a signed-in user reach `/admin/*` at all.
- Web's `proxy.ts` middleware enforces Clerk on `/app/*` and `/admin/*`.
- Advisor verifies real JWTs from the Clerk JWKS.

### No-Clerk behaviour (ABS-530)

There used to be a second mode here: a shared-password cookie gate (`abs_demo` / `abs_admin`, the `/access` page, `DEMO_PASSWORD` / `ADMIN_PASSWORD`) that `proxy.ts` fell back to whenever the Clerk keys were absent. ABS-530 deleted it — one password for every user can't distinguish between people and leaves no audit trail, and by then Clerk had been live for months.

What the no-Clerk path does now:

- **Local dev** (`npm run dev`, no Clerk keys): `/app` and `/admin` are simply open. `advisor-auth.ts` forwards a synthetic `X-Test-User-Id` so the app is usable.
- **A production build with no Clerk secret**: protected routes return **503**. That is a misconfigured deploy, not a mode, and serving `/app` unauthenticated would be worse than being down.

So there is nothing to fall back to during a Clerk outage. That is deliberate.

### Enabling real Clerk auth (operator runbook)

This is the checklist for standing up Clerk from scratch. Kept for reference — production has been on Clerk since well before ABS-530 removed the alternative. Do it once, in this order:

#### 1. Create the Clerk instance

1. Sign up at <https://clerk.com> if you don't have an account.
2. Create a new application. When prompted, pick **Email + password** (and Google OAuth if you want it). You can change this later.
3. Clerk now provisions two "instances": a **Development** instance keyed off a `clerk.accounts.dev` hostname, and a **Production** instance for your real domain. Toggle to Production in the Clerk dashboard's top-left selector before grabbing the keys below — dev keys won't work for the public deployment.

#### 2. Configure restricted signups (private beta)

In Clerk dashboard → **User & Authentication → Restrictions**:

- Turn **"Restrict sign-ups to allowlist"** ON.
- Add the email addresses of your intended early users to the allowlist. Existing users (if any) are migrated as you add them.
- Anyone hitting `/sign-up` without an allowlisted email gets a Clerk-side error; they have no way around it. The marketing site already routes unauthenticated visitors to `/signup` (invite request) instead of `/sign-up`, so this is belt-and-suspenders.

You can flip this off later when you're ready for public signups — no code change required.

#### 3. Configure the JWT template

In Clerk dashboard → **JWT Templates**:

- Click **+ New template** and pick the "Blank" preset.
- Name: `advisor` (or anything you like — the backend doesn't check the name, only the JWKS public keys).
- Set the **Lifetime** to 60 seconds. Clerk's hosted sessions refresh continuously; a short JWT lifetime caps the blast radius if a token leaks.
- Leave the **Claims** at the defaults — `sub`, `iat`, `exp`, `sid` are required; `email` is convenient but optional (the backend has a fallback path).

Copy the **Issuer URL** and **JWKS Endpoint** from the template page — you'll paste these into env vars.

#### 4. Configure allowed redirect URLs

In Clerk dashboard → **Paths**:

- **Sign-in URL**: `/sign-in`
- **Sign-up URL**: `/sign-up`
- **After sign-in URL**: `/app`
- **After sign-up URL**: `/app`

Match what we set on `<ClerkProvider>` in [web/app/layout.tsx](../web/app/layout.tsx).

In Clerk dashboard → **Domains**, add `agenticbylawsystems.com` (and `localhost:3000` if you also want local dev to use Clerk).

#### 5. Configure the webhook

In Clerk dashboard → **Webhooks → + Add Endpoint**:

- **Endpoint URL**: `https://api.agenticbylawsystems.com/v1/webhooks/clerk`
- **Subscribe to events**:
  - `user.created`
  - `user.updated`
  - `user.deleted`
- After saving, copy the **Signing Secret** (starts with `whsec_…`). This goes into `CLERK_WEBHOOK_SECRET` below.

If you skip the webhook, the advisor still works — the backend creates / refreshes user rows lazily on first chat. But profile changes (email, name) made in Clerk's UserButton menu won't sync until the next chat, and `user.deleted` events won't remove the row at all. The webhook closes those gaps.

#### 6. Populate env vars

Edit `/srv/bylaw/.env` on the server. Add these (values from the Clerk dashboard):

```
# Frontend
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...

# Backend — from the JWT template page
CLERK_JWKS_URL=https://clerk.agenticbylawsystems.com/.well-known/jwks.json
CLERK_AUDIENCE=https://clerk.agenticbylawsystems.com
CLERK_ISSUER=https://clerk.agenticbylawsystems.com

# Backend — from the Webhooks page
CLERK_WEBHOOK_SECRET=whsec_...
```

Update both `web` and `advisor` `environment:` blocks in `/srv/bylaw/docker-compose.yml` so they pick the new vars up. The webhook secret is only needed on the advisor side (the webhook endpoint is mounted by FastAPI).

#### 7. Restart and verify

```bash
ssh bylaw-prod "cd /srv/bylaw && docker compose up -d web advisor"

# Verify advisor mounted the Clerk dependency (no fallback warning)
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml logs --tail 30 advisor" | grep -i clerk

# Verify the webhook route is mounted
ssh bylaw-prod "curl -s -o /dev/null -w '%{http_code}\n' \
  https://api.agenticbylawsystems.com/v1/webhooks/clerk -X POST"
# Expect: 400 (missing signature). 404 means the route didn't mount —
# usually because CLERK_WEBHOOK_SECRET was empty.

# Verify the frontend treats /app as gated by Clerk
curl -sI https://agenticbylawsystems.com/app | grep -i location
# Expect: location: https://clerk.agenticbylawsystems.com/sign-in?... or
# location: /sign-in. A 503 means CLERK_SECRET_KEY didn't reach the web
# container — proxy.ts fails closed rather than serving /app open.
```

Trigger a test delivery in Clerk dashboard → **Webhooks → your endpoint → Testing**: pick `user.created` and send. The advisor logs should show `clerk webhook: ignoring unhandled event type ...` or `created` depending on the payload.

#### 8. Populate the admin allowlist

Set `ADVISOR_ADMIN_CLERK_USER_IDS` (comma-separated Clerk user ids) on **both** the advisor and the web container, then recreate them. `proxy.ts` reads it once at module load, so a restart is required to add an admin. With the `abs_admin` cookie gone (ABS-530), an empty list means `/admin/*` 404s for everyone — including you.

## Rate limiting

Production Caddy is built with the [caddy-ratelimit plugin](https://github.com/mholt/caddy-ratelimit) compiled in. Two zones in production today:

| Zone | Match | Limit | Purpose |
|---|---|---|---|
| `global` | Any request to `agenticbylawsystems.com` | 120 req/min per IP | DoS shield on the public site |
| `chat` | POST to `/v1/chat*` on the api subdomain | 10 req/min per IP | Per-IP cap on the expensive endpoint |

(There used to be a third zone, `access_attempts`, rate-limiting `POST /api/access*`. ABS-530 removed the gate it protected; sign-in is Clerk-hosted, so credential brute-force never reaches this origin.)

Plus the advisor's per-user monthly quota (100 queries/mo on the free tier) as a server-side ceiling.

Adjust by editing `/srv/bylaw/Caddyfile` and `docker compose restart caddy`. Caddy validates config on start; the previous container stays up if the new config is invalid.

## Common ops

```bash
# Logs (single service, last 50 lines)
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml logs --tail 50 advisor"

# Tail logs live
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml logs -f advisor"

# All container status
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml ps"

# Disk usage on the boot disk
ssh bylaw-prod "df -h /"

# Image inventory
ssh bylaw-prod "docker image ls"

# Reclaim disk (after rolling out new image version)
ssh bylaw-prod "docker image prune -f"

# Restart a single service
ssh bylaw-prod "cd /srv/bylaw && docker compose restart <svc>"

# Take everything down (for nightly shutdown — preserves volumes)
ssh bylaw-prod "cd /srv/bylaw && docker compose stop"

# Bring back up
ssh bylaw-prod "cd /srv/bylaw && docker compose start"
```

### Production Postgres shell

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec postgres psql -U layer1 -d layer1"
```

### Send a test chat through the advisor

```bash
# Test-mode (no Clerk) — uses smoke-test-1 user
curl -N -X POST https://api.agenticbylawsystems.com/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Test-User-Id: smoke-test-1" \
  -d '{"message": "What zone is 6321 Quinpool Road in?"}'
```

## Known issues / workarounds

### 1. `docs/agent/persona.md` packaging

The advisor's chat persona loader reads the file from `Path(__file__).parents[3] / "docs/agent/persona.md"`, which only works under an editable install. The production Dockerfile.advisor has a workaround `COPY` that puts the file at `/opt/venv/lib/python3.11/docs/agent/persona.md` so the resolver finds it. Tracked: should be refactored to `importlib.resources`. Spawned as a separate session task.

### 2. Alembic `version_num` column width

Alembic's default `alembic_version.version_num` column is `VARCHAR(32)`. Several migration revision strings in the repo exceed 32 chars (e.g. `0008_advisor_billing_subscription` = 33). On a fresh database, `alembic upgrade head` fails partway, rolls back, and leaves the DB empty. Workaround during initial deploy: `alembic upgrade 0001` → `ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255);` → `alembic upgrade head`. Tracked as a separate session task — proper fix renames long revisions and enables `transaction_per_migration=True` in `alembic/env.py`.

### 3. Local dev `proxy.ts` gate — RESOLVED (ABS-530)

Local devs used to hit `localhost:3000/app`, get bounced to `/access`, and need a `DEMO_PASSWORD` in their env just to iterate. Removing the shared-password gate removed the friction with it: with no Clerk keys, `proxy.ts` now leaves protected routes open in a dev build. Configure local Clerk keys only when you actually want to exercise the auth path.

## Open follow-ups

1. **Move production `docker-compose.yml` into the repo** (perhaps as `compose.prod.yml`) so server config is also version-controlled.
2. **Automate backups** — DONE (ABS-131). Cron + verification + gpg + Hetzner Storage Box mirror; see [PROD_DB_BACKUP.md](PROD_DB_BACKUP.md).
3. **Switch to real Clerk auth** — DONE. Live at `pk_test_` dev instance (`stunning-goshawk-55.clerk.accounts.dev`). Flip to a Production instance before public launch (no code work — same env-var swap as the runbook).

### Invite-only access flow

How a new user goes from request → approved → signed in:

1. **Request.** Anyone hits `/signup`, fills the form, and the request lands in `invite_request` with `status='pending'`. The form is public — no auth required.
2. **Admin review.** Admin (Clerk userId in `ADVISOR_ADMIN_CLERK_USER_IDS`) opens `/admin/invites`. The page lists every request, pending first. Click Approve to open the inline cap-override form (queries/mo, input tokens/mo, output tokens/mo, RPM — defaults are 100 / 500k / 100k / 6). Click Reject to mark as rejected.
3. **Approval side-effects.** The approve handler calls Clerk's Backend API to add the email to the allowlist (which is what makes Clerk's sign-up flow actually accept the email — see [Restrictions setting](#enabling-real-clerk-auth-operator-runbook) for the underlying gate). It also stamps `expires_at = now + 14 days` on the row.
4. **User signs in.** Approved user goes to `/sign-in` and authenticates with Google or Apple. Clerk lets them through because their email is on the allowlist. They land at `/app`.
5. **First chat call.** The advisor's `resolve_or_create_user` looks up the user's email in `invite_request`. If `status='approved'`, it copies the `granted_*` caps onto the new `advisor_user` row and stamps `redeemed_at`. The invite is now "consumed."
6. **Expiry sweep.** Approved invites that never get redeemed (i.e. user didn't sign in within 14 days) are cleaned up: their email is removed from Clerk's allowlist and the row flips to `status='expired'`. Two trigger paths:
   - **Lazy:** the admin page POSTs to `/api/admin/invites/sweep-expired` on mount.
   - **Cron:** any process can call the same endpoint with header `X-Sweep-Token: $CLERK_SWEEP_TOKEN`. Set `CLERK_SWEEP_TOKEN` to a random string in `/srv/bylaw/.env` to enable this path.

Required env on the server (added to `/srv/bylaw/.env`):

```
ADVISOR_ADMIN_CLERK_USER_IDS=user_3DfTVYRZvyMIAKsVn43o8PnYO3F   # comma-separated for multiple admins
CLERK_SWEEP_TOKEN=<random-string-or-leave-empty>                # optional, cron-mode only
```

Per-user caps enforced by the advisor at chat time:
- `monthly_query_limit` — count of requests.
- `monthly_input_token_limit` / `monthly_output_token_limit` — separate caps because the price ratio between Anthropic's input and output tokens differs by ~4x.
- `requests_per_minute_limit` — sliding-window rate cap, counts both successful and rate-limited calls so a flood doesn't reset the window.

All four return a 429 with a `kind` field identifying which limit fired, so the frontend can show targeted messaging.
4. **Schedule security upgrades**: unattended-upgrades is enabled (security-only). Verify nightly. The advisor's per-user quota (100 / month) is the only cost ceiling once the gate is cracked — combine with rate limiting at Caddy.
5. **CI/CD**: no automation yet. Builds and deploys are operator-driven from the laptop. GitHub Actions to build + push images on main would be the obvious next step.
6. **Persona.md and alembic version_num fixes** (see "Known issues") were spawned as separate tasks at deploy time.

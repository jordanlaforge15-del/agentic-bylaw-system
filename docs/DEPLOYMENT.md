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
└── backups/                # nightly pg_dump targets (manual for now)
```

The repo's `docker-compose.yml` at root is the **local dev** compose (postgres + codex container); it's NOT used in production. The repo's `Caddyfile`, `Dockerfile.advisor`, `Dockerfile.caddy`, `Dockerfile.postgres`, `web/Dockerfile`, and root `.dockerignore` *are* the source-of-truth that the server-side copies mirror — sync them via `scp` when the repo versions change.

## Container architecture

Four containers, all in the default Docker network so they reach each other by service name:

| Service | Image | Ports | Notes |
|---|---|---|---|
| `caddy` | `bylaw-caddy:latest` (built from `Dockerfile.caddy`) | 80, 443 public | Terminates TLS, routes by host, enforces rate limits |
| `web` | `ghcr.io/jordanlaforge15-del/bylaw-web:X.Y.Z` | 3000 internal | Next.js standalone build. Reaches advisor at `http://advisor:8000` |
| `advisor` | `ghcr.io/jordanlaforge15-del/bylaw-advisor:X.Y.Z` | 8000 internal | FastAPI / uvicorn. Reads/writes Postgres at `postgres:5432` |
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
   - Web: edits + `npm run typecheck` in `web/`. Dev server (`npm run dev`) auto-reloads.
   - Advisor: edits + `pytest tests/advisor/` (167+ tests, must all pass).
3. **Commit** with a real message. Co-Authored-By line if Claude was a contributor.
4. **Build & push** with a bumped version tag (see "Image build & publish workflow" above).
5. **Update server compose** to reference the new tag:
   ```bash
   ssh bylaw-prod "sed -i 's|bylaw-web:OLD|bylaw-web:NEW|' /srv/bylaw/docker-compose.yml"
   # or for advisor:
   ssh bylaw-prod "sed -i 's|bylaw-advisor:OLD|bylaw-advisor:NEW|' /srv/bylaw/docker-compose.yml"
   ```
6. **Pull & restart just that service**:
   ```bash
   ssh bylaw-prod "cd /srv/bylaw && docker compose pull web && docker compose up -d web"
   # or advisor / both:
   ssh bylaw-prod "cd /srv/bylaw && docker compose pull && docker compose up -d advisor"
   ```
7. **Verify**: `curl` against the public endpoint, check `docker compose ps`, tail logs (`docker compose logs --tail 30 <svc>`). For chat changes, send a real query.
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

`/srv/bylaw/.env` is the single source of truth. Loaded by `docker-compose.yml` via `env_file:` (postgres) or explicit `environment:` entries (web, advisor). Never check this file into git.

Currently populated keys:

```
# Postgres
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, DATABASE_URL

# Advisor LLM
ADVISOR_LLM_PROVIDER=anthropic, ADVISOR_LLM_MODEL=claude-opus-4-5, ANTHROPIC_API_KEY

# Layer 2 retrieval defaults
LAYER2_PROMPT_VERSION, LAYER2_RETRIEVAL_VERSION, LAYER2_TOKEN_BUDGET, LAYER2_TOP_K

# Advisor server bind
ADVISOR_HOST=0.0.0.0, ADVISOR_PORT=8000

# Stripe (dormant; ADVISOR_BILLING_ENABLED=false)
ADVISOR_BILLING_ENABLED, STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_PRO, STRIPE_PRICE_TEAM,
ADVISOR_BILLING_SUCCESS_URL, ADVISOR_BILLING_CANCEL_URL

# Shared-password gate
DEMO_PASSWORD=$$<password>    # NB: literal $ in value must be escaped as $$ for compose
```

### Compose variable substitution

Values referenced from the YAML as `${VAR}` are interpolated from `.env`. **Any literal `$` in a value must be escaped as `$$`** or compose will try to substitute it as a variable name and silently set it to an empty string. Diagnosed once during the original deploy when a strong password starting with `$` came through as `""`.

### Adding a new env var to web or advisor

1. Add to `/srv/bylaw/.env`.
2. Update the service's `environment:` block in `/srv/bylaw/docker-compose.yml` (web/advisor use explicit lists, not `env_file:`, so this is needed).
3. Restart the service.

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

### Expand/contract discipline

Because migrations run before the new code deploys (or before the old code is rolled back), every migration must be backwards-compatible across the deploy window:

1. SSH in → run the **additive** half of the migration (new nullable column, new table, new index `CONCURRENTLY`). Old advisor keeps working because it ignores the new shape.
2. Deploy the new advisor image. Both old and new code tolerate the transitional schema.
3. Once you're confident no rollback is needed, SSH in → run the **cleanup** half (drop old column, add `NOT NULL`, drop old index).

Avoid rename-or-drop-in-one-step migrations — they break the rollback story.

### Data restore from local dev

Workflow we used on initial deploy (still works for full reloads):

```bash
# 1. Local: pg_dump --data-only --exclude-table=alembic_version --exclude-table=advisor_user
#    --exclude-table=advisor_chat_session ... (see deploy commit history for full list)
#    Output to a gzipped file.
# 2. Strip pg_dump's \restrict and \unrestrict meta-commands (Postgres 16.13+ emits them;
#    server psql 16.4 doesn't recognise them). Use Python, NOT grep — grep is not
#    binary-safe against COPY data with embedded bytes.
# 3. scp the cleaned dump to /srv/bylaw/backups/
# 4. Restore wrapped in SET session_replication_role = replica; ... DEFAULT; (the source_fragment
#    table has a self-referential FK that pg_dump can't fully linearise).
```

See the commit `[advisor] Fix session-detail 404 caused by user_id format mismatch` and prior history for the exact `pg_dump` / restore commands. Don't reinvent them.

### Backups (manual, ~no automation yet)

```bash
# Run on the server
docker compose -f /srv/bylaw/docker-compose.yml exec -T postgres \
  pg_dump -U layer1 layer1 | gzip > /srv/bylaw/backups/layer1-$(date +%F).sql.gz
```

Tracked as a deploy follow-up: schedule this in cron and ship to a Hetzner Storage Box. For now, run by hand before risky migrations.

## Auth modes

The advisor + web together support two auth modes, switched by env var presence:

### Clerk mode (production target, not active yet)

- `CLERK_JWKS_URL`, `CLERK_AUDIENCE`, `CLERK_ISSUER` set in advisor's env.
- `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` set in web's env.
- Web's `proxy.ts` middleware enforces Clerk on `/app/*` and `/admin/*`.
- Advisor verifies real JWTs from the Clerk JWKS.

### Shared-password fallback (current production state)

- Clerk env vars unset.
- Web's `proxy.ts` falls back to the legacy `abs_demo` / `abs_admin` cookie gate, redirecting unauth users to `/access`.
- `/api/access` validates against `DEMO_PASSWORD` (and optionally `ADMIN_PASSWORD`) env vars.
- Advisor accepts the `X-Test-User-Id` header in place of a Clerk JWT.
- Web proxy injects `X-Test-User-Id: <ADVISOR_DEMO_USER_ID>` on every advisor call (defaults to `demo-user-1`; we point it at `smoke-test-1` which has an `advisor_user` row).

Switching from fallback to Clerk is "set the Clerk env vars and restart." No code change.

### Enabling real Clerk auth (operator runbook)

This is the checklist for going from the shared-password fallback (current state) to real Clerk auth. Do it once, in this order:

#### 1. Create the Clerk instance

1. Sign up at <https://clerk.com> if you don't have an account.
2. Create a new application. When prompted, pick **Email + password** (and Google OAuth if you want it). You can change this later.
3. Clerk now provisions two "instances": a **Development** instance keyed off a `clerk.accounts.dev` hostname, and a **Production** instance for your real domain. Toggle to Production in the Clerk dashboard's top-left selector before grabbing the keys below — dev keys won't work for the public deployment.

#### 2. Configure restricted signups (private beta)

In Clerk dashboard → **User & Authentication → Restrictions**:

- Turn **"Restrict sign-ups to allowlist"** ON.
- Add the email addresses you've already shared `DEMO_PASSWORD` with to the allowlist. Existing users (if any) are migrated as you add them.
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

# Verify the frontend treats /app as gated by Clerk (not the cookie gate)
curl -sI https://agenticbylawsystems.com/app | grep -i location
# Expect: location: https://clerk.agenticbylawsystems.com/sign-in?... or
# location: /sign-in. NOT /access — that's the cookie-gate fallback.
```

Trigger a test delivery in Clerk dashboard → **Webhooks → your endpoint → Testing**: pick `user.created` and send. The advisor logs should show `clerk webhook: ignoring unhandled event type ...` or `created` depending on the payload.

#### 8. Remove the password gate (optional, after Clerk is healthy)

The shared-password gate stays compiled in as a fallback. Once Clerk is verified working end-to-end you can:

- Leave it in place (zero-cost insurance; the `isClerkConfigured()` check skips it when Clerk keys are real).
- OR remove the `DEMO_PASSWORD` / `ADMIN_PASSWORD` env vars and let `/api/access` return 503 to anyone who hits it. The `/access` page becomes unreachable through normal nav.

Either is fine. We've been recommending "leave it in" so a Clerk outage isn't a full site outage — flip `CLERK_JWKS_URL` back off and the fallback resumes.

## Rate limiting

Production Caddy is built with the [caddy-ratelimit plugin](https://github.com/mholt/caddy-ratelimit) compiled in. Three zones in production today:

| Zone | Match | Limit | Purpose |
|---|---|---|---|
| `global` | Any request to `agenticbylawsystems.com` | 120 req/min per IP | DoS shield on the public site |
| `access_attempts` | POST to `/api/access*` | 5 req/min per IP | Brute-force protection on the gate |
| `chat` | POST to `/v1/chat*` on the api subdomain | 10 req/min per IP | Per-IP cap on the expensive endpoint |

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

### 3. Local dev `proxy.ts` gate

The shared-password gate is enabled whenever Clerk isn't configured — including in local dev. After the gate landed in `web/proxy.ts`, local devs hitting `localhost:3000/app` get redirected to `/access` and need a `DEMO_PASSWORD` in their local env. To bypass for short iteration: either set a local `DEMO_PASSWORD` and enter it once (cookie persists 30 days), or configure local Clerk keys.

## Open follow-ups

1. **Move production `docker-compose.yml` into the repo** (perhaps as `compose.prod.yml`) so server config is also version-controlled.
2. **Automate backups** — cron + Hetzner Storage Box upload, encrypted with `age` or `gpg`.
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

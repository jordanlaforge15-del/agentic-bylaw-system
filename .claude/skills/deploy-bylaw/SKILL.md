---
name: deploy-bylaw
description: >
  Build the advisor and web Docker images for linux/amd64, push them to GHCR,
  update the production docker-compose.yml tag via SSH, run any pending Alembic
  migrations, recreate the affected containers, health-check the live endpoints,
  and roll back automatically on failure. Use this whenever the user says
  "deploy", "ship to prod", "release to production", "build and push", or any
  phrase implying building images and restarting the prod stack. Prefer
  test-and-deploy-bylaw when work is still sitting on dev and has not been
  promoted to main yet — that skill tests, promotes, then chains into this one.
  This skill's precondition is that main is already current and tagged with the
  target vX.Y.Z.
---

# deploy-bylaw — build, push, and ship to production

## Context

- **Repo root**: `~/dev/agentic-bylaw-system` (or the active worktree)
- **Prod branch**: `main` — must already be current and tagged before this skill runs
- **Production server SSH alias**: `bylaw-prod` (non-root `deploy` user, key-only auth)
- **Compose file on server**: `/srv/bylaw/docker-compose.yml`
- **GHCR org**: `ghcr.io/jordanlaforge15-del`
- **Image names**: `bylaw-web` and `bylaw-advisor`
- **Maintenance window**: container-touching changes should respect the 23:00 AST window documented in project memory; build and push are safe anytime.

Anchor docs (link, don't restate):

- [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) — canonical source for server layout, image build flags, rollback recipe, Alembic runbook, and known issues.
- [docs/BRANCHING_STRATEGY.md](../../../docs/BRANCHING_STRATEGY.md) — promotion gate; this skill runs *after* promotion is complete.
- [.claude/skills/test-and-deploy-bylaw/SKILL.md](../test-and-deploy-bylaw/SKILL.md) — the upstream skill that tests dev, promotes to main, tags, and then chains here.

---

## Preconditions (verify before any build)

This skill assumes the caller has already:

1. Merged all intended work into `main` via the promotion gate.
2. Tagged the promotion commit with `vX.Y.Z` and pushed the tag (`git push origin vX.Y.Z`).

If called from `test-and-deploy-bylaw`, both preconditions are guaranteed by Steps 7.3–7.4 of that skill. If called directly, verify:

```bash
git rev-parse --abbrev-ref HEAD   # must be main
git status --porcelain             # must be empty
git describe --tags --exact-match  # must return a vX.Y.Z tag
```

Halt if any check fails. Do not build from an untagged or dirty state.

---

## Step 1 — Determine scope and version

The caller (or `test-and-deploy-bylaw` Step 8) should carry forward:

- **`VERSION`** — the `vX.Y.Z` tag just pushed (e.g. `v1.4.3`).
- **`SCOPE`** — `web`, `advisor`, or `both`.
- **Migration flag** — whether any Alembic revision was touched in the promotion.

If not carried forward, derive them:

```bash
# Current version on prod (what is running right now)
ssh bylaw-prod "grep -E 'bylaw-(web|advisor):' /srv/bylaw/docker-compose.yml"

# Tag on HEAD of main
git describe --tags --exact-match HEAD

# Scope: files changed since the prior tag
git log $(git describe --tags --abbrev=0 HEAD^)..HEAD --name-only | sort -u | head -60
```

Compute `VERSION` from the tag on HEAD. The old version (from the `grep` above) becomes `OLD_VERSION` — needed for rollback.

Surface to the user:
- What is being deployed (`VERSION`, `SCOPE`)
- What `OLD_VERSION` was (in case rollback is needed)
- Whether a migration will run (yes/no, and which revision(s))

---

## Step 2 — Preflight checks

Before touching prod, verify the build environment is ready:

```bash
# Docker is logged in to GHCR
docker login ghcr.io -u jordanlaforge15-del 2>&1 | grep -i "login succeeded\|already"

# buildx builder is available
docker buildx ls
```

If Docker is not logged in to GHCR, halt and ask the user to authenticate:

```bash
echo <PAT> | docker login ghcr.io -u jordanlaforge15-del --password-stdin
```

The PAT requires `write:packages` scope. Credentials live in `~/.docker/config.json` — treat the file as a secret.

Also confirm the prod server is reachable:

```bash
ssh bylaw-prod "echo ok"
```

Halt on any failure — a build that cannot push or deploy is wasted time.

---

## Step 3 — Build images

Build only the services in `SCOPE`. Both builds target `linux/amd64` because the Hetzner server is x86_64 and the build laptop is Apple Silicon (QEMU emulates).

### Advisor image (if SCOPE includes advisor)

```bash
caffeinate -i -s docker buildx build \
  --platform linux/amd64 \
  -f Dockerfile.advisor \
  -t ghcr.io/jordanlaforge15-del/bylaw-advisor:VERSION \
  --push \
  .
```

Build context is the repo root. The multi-stage Dockerfile installs only `pip install ".[advisor]"` (the request-path extras). If the final image exceeds ~500 MB, something is wrong with the extras split — halt and investigate before pushing.

### Web image (if SCOPE includes web)

The `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` build-arg is required. Pull the value from prod for parity:

```bash
CLERK_KEY=$(ssh bylaw-prod "grep ^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY /srv/bylaw/.env | cut -d= -f2")
```

Then build:

```bash
caffeinate -i -s docker buildx build \
  --platform linux/amd64 \
  -f web/Dockerfile \
  --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="$CLERK_KEY" \
  -t ghcr.io/jordanlaforge15-del/bylaw-web:VERSION \
  --push \
  web/
```

`caffeinate -i -s` prevents macOS sleep during the upload (residential upstream is slow). Both images land at ~120 MB when the build is healthy.

**Never tag with `:latest`** in production builds — the version tag is the rollback anchor.

---

## Step 4 — Alembic migrations (if any)

If the promotion touched any file under `advisor/migrations/` (or wherever Alembic revisions live), run the migration **before** the container swap. Old advisor keeps working because every migration must follow the expand/contract rule documented in [docs/DEPLOYMENT.md §Expand/contract discipline](../../../docs/DEPLOYMENT.md).

### Preview first (always)

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic upgrade head --sql" | less
```

Review the SQL output. If the preview shows any destructive operation (DROP COLUMN, RENAME, DROP TABLE, DROP INDEX on a live column), halt and consult the user — the migration violates the expand/contract rule and is a deployment blocker.

### Apply

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic upgrade head"
```

### Verify revision

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml exec advisor alembic current"
```

Confirm the revision pointer matches what the new code expects. If alembic reports an error, halt — do not proceed with the container swap until the schema is in the expected state. Rollback of a partial migration may require a manual `alembic downgrade -1` over SSH.

---

## Step 5 — Update server compose tags

Update the image tag(s) in the server-side compose file. Use `sed -i` over SSH so there is no copy/paste risk:

```bash
# Web (if SCOPE includes web)
ssh bylaw-prod "sed -i 's|bylaw-web:OLD_VERSION|bylaw-web:VERSION|' /srv/bylaw/docker-compose.yml"

# Advisor (if SCOPE includes advisor)
ssh bylaw-prod "sed -i 's|bylaw-advisor:OLD_VERSION|bylaw-advisor:VERSION|' /srv/bylaw/docker-compose.yml"
```

After the sed, confirm the substitution landed:

```bash
ssh bylaw-prod "grep -E 'bylaw-(web|advisor):' /srv/bylaw/docker-compose.yml"
```

If the grep still shows `OLD_VERSION`, the sed pattern did not match — halt. Common causes: a space in the tag string, a different path separator in the compose file, or `OLD_VERSION` was already at `VERSION` (no-op deploy). Do not proceed to Step 6 until the compose file reflects `VERSION`.

---

## Step 6 — Pull new images and restart services

```bash
# Pull the new image(s) on the server
ssh bylaw-prod "cd /srv/bylaw && docker compose pull <web|advisor|both>"

# Recreate just the changed service(s) — leaves caddy and postgres untouched
ssh bylaw-prod "cd /srv/bylaw && docker compose up -d <web|advisor|both>"
```

Substitute `<web|advisor|both>` with the actual service names from `SCOPE`. Do not restart services that were not rebuilt.

After `up -d`, wait ~10 seconds for containers to initialize, then check status:

```bash
ssh bylaw-prod "cd /srv/bylaw && docker compose ps"
```

All restarted services must show `running` (or `healthy` if a healthcheck is configured). If any show `restarting` or `exited`, jump immediately to [Step 7a — Rollback](#step-7a--rollback).

---

## Step 7 — Health verification

Run end-to-end checks against the live public endpoints.

### Advisor

```bash
# Smoke: unauthenticated chat in test mode
curl -N --max-time 30 -X POST https://api.agenticbylawsystems.com/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Test-User-Id: smoke-test-1" \
  -d '{"message": "What zone is 6321 Quinpool Road in?"}' \
  | head -5
```

Expected: a streaming response starting with zone or district text, no `500`, no connection refused.

### Web

```bash
# HTTPS reachable
curl -sI https://agenticbylawsystems.com | head -3
# Expect: HTTP/2 200 or HTTP/2 3xx (redirect to /app or /access)

# /app is gated (not 200 without auth)
curl -sI https://agenticbylawsystems.com/app | grep -i location
# Expect: location pointing to /sign-in or /access — NOT a 200
```

### Container logs (on any anomaly)

```bash
ssh bylaw-prod "docker compose -f /srv/bylaw/docker-compose.yml logs --tail 30 <svc>"
```

If any health check fails or logs show startup errors, jump to [Step 7a — Rollback](#step-7a--rollback).

---

## Step 7a — Rollback

If Step 6 or Step 7 fails (container does not come up, health checks fail, unacceptable error rate in logs):

```bash
# Flip compose tag back to old version
ssh bylaw-prod "sed -i 's|bylaw-web:VERSION|bylaw-web:OLD_VERSION|' /srv/bylaw/docker-compose.yml"
ssh bylaw-prod "sed -i 's|bylaw-advisor:VERSION|bylaw-advisor:OLD_VERSION|' /srv/bylaw/docker-compose.yml"

# Pull old image (layers likely still cached on the server) and recreate
ssh bylaw-prod "cd /srv/bylaw && docker compose pull <web|advisor|both>"
ssh bylaw-prod "cd /srv/bylaw && docker compose up -d <web|advisor|both>"

# Confirm old version is running
ssh bylaw-prod "cd /srv/bylaw && docker compose ps"
ssh bylaw-prod "grep -E 'bylaw-(web|advisor):' /srv/bylaw/docker-compose.yml"
```

After rollback, halt and report to the user:

- What service was rolled back
- `VERSION` → `OLD_VERSION`
- The failure symptom (container exit code, log tail, or failed health check)
- Whether the Alembic migration needs a manual downgrade (if it ran before the container swap, it is now "ahead" of the rolled-back image — surface this explicitly and do NOT attempt an automatic downgrade, as that risks data loss)

Do **not** attempt a second deploy automatically. Let the user investigate the failure first.

---

## Step 8 — Post-deploy cleanup

Once health checks pass:

```bash
# Reclaim disk from old image layers
ssh bylaw-prod "docker image prune -f"
```

Surface a deploy summary to the user:

- `OLD_VERSION` → `VERSION` deployed
- `SCOPE` (which services)
- Whether a migration ran (and which revision)
- Health check results (curl responses, container status)
- Any nits or anomalies observed in logs that don't block the deploy but warrant a follow-up

---

## Common abort branches

| Symptom | Branch |
|---|---|
| HEAD is not on a `vX.Y.Z` tag | Halt at Preconditions. The caller must tag main before this skill runs. |
| `git status --porcelain` non-empty | Halt at Preconditions. Do not build from dirty state — the image would capture uncommitted changes. |
| Docker not authenticated to GHCR | Halt at Step 2. Ask user to re-authenticate with a valid `write:packages` PAT. |
| `ssh bylaw-prod "echo ok"` fails | Halt at Step 2. Prod unreachable — network, key rotation, or host issue. |
| Image build exits non-zero | Halt at Step 3. Surface the build log tail. Do not push a partial image. |
| Built image exceeds ~500 MB | Halt at Step 3. The extras split in Dockerfile is wrong. Investigate before pushing. |
| Migration SQL preview shows DROP / RENAME on live columns | Halt at Step 4. Expand/contract rule violated — migration is a deployment blocker. |
| `alembic upgrade head` fails | Halt at Step 4. Do not proceed to container swap. Surface the error and revision state. |
| `sed -i` did not replace the tag | Halt at Step 5. Do not proceed to `compose up -d` against the wrong tag. |
| Container shows `restarting` or `exited` after `up -d` | Trigger Step 7a immediately. |
| Health check fails (curl timeout, 5xx, wrong response) | Trigger Step 7a immediately. |
| Rollback `docker compose ps` still shows unhealthy | Halt. Escalate to user — do not loop. Old image may have a startup bug independent of the new code. |

---

## Alembic migration ordering reference

For migrations touching the schema, the expand/contract discipline from [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) applies:

1. Run the **additive** half (new nullable column, new table, new index `CONCURRENTLY`) **before** the container swap. Old advisor running against the new schema is fine because it ignores new columns.
2. Deploy the new image (`up -d`). Both old and new code tolerate the transitional schema.
3. Once rollback window has passed, run the **cleanup** half (drop old column, add `NOT NULL` constraint, drop old index) as a separate maintenance operation.

Never rename or drop a column in the same migration step as the feature code deploys — that breaks the rollback story. If you find a migration that does this, halt and surface it; it is a deployment blocker, not something to paper over.

---

## What this skill explicitly does NOT do

- **Test or promote dev.** That is owned by [test-and-deploy-bylaw](../test-and-deploy-bylaw/SKILL.md), which chains here after promotion.
- **Rebuild Caddy or Postgres images.** Those are built directly on the server (`docker compose build caddy`) when their Dockerfiles change; they have no GHCR round-trip. Trigger that separately if needed.
- **Seed or migrate data.** Data ingest happens locally and ships via pg_dump / restore per the runbook in [docs/DEPLOYMENT.md §Data restore from local dev](../../../docs/DEPLOYMENT.md). This skill only runs schema migrations.
- **Handle the hotfix path.** Hotfixes land directly on `main` per BRANCHING_STRATEGY; they use the same deploy recipe here, but the promotion gate step is skipped. See [docs/BRANCHING_STRATEGY.md §Hotfix path](../../../docs/BRANCHING_STRATEGY.md).
- **Automatically downgrade a migration after rollback.** A schema downgrade is a destructive manual operation that requires operator judgement — this skill flags the need but does not execute it.

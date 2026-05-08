# ABS° web frontend

Next.js 16 app that talks to the FastAPI advisor backend in
`../src/advisor/api/`. Marketing pages live in `app/(marketing)/`,
the chat product lives at `/app` under `app/(product)/`.

## Quick start (dev mode)

```bash
cp .env.local.example .env.local   # leave Clerk vars at the placeholder values
npm install
npm run dev
```

Then in a separate terminal, from the repo root:

```bash
uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:3000>. The chat at `/app` is keyed to
`ADVISOR_DEMO_USER_ID` (`demo-user-1`); sessions live in the
backend's in-memory store and disappear on backend restart.

## Auth modes

The app boots in one of two modes depending on whether Clerk env
vars are set:

- **Dev / `X-Test-User-Id`** — `CLERK_SECRET_KEY` unset. Clerk's
  middleware no-ops, the chat sidebar shows a static placeholder
  in place of `<UserButton />`, and the API proxy forwards
  `X-Test-User-Id` upstream.
- **Clerk** — `CLERK_SECRET_KEY` and `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
  set. `proxy.ts` (Next 16's middleware convention) gates `/app/*`
  and `/admin/*`; the API proxy mints a session JWT and forwards
  it as `Authorization: Bearer …`. FastAPI must be running its
  production entrypoint (`advisor.api.main`) with `CLERK_JWKS_URL`
  set so it knows how to verify.

Full operator documentation including env-var matrix and rollback
notes lives in [`../README.md` → "Web Demo"](../README.md#web-demo).

## Layout

- `app/(marketing)/` — public marketing surface (home, pricing,
  request-invite at `/signup`).
- `app/sign-in/[[...sign-in]]/`, `app/sign-up/[[...sign-up]]/` —
  Clerk-hosted auth widgets, themed to match the brand.
- `app/(product)/app/` — the chat product. Gated by `proxy.ts` in
  Clerk mode.
- `app/admin/invites/` — admin dashboard for the invite-request
  funnel. Gated by `proxy.ts` in Clerk mode.
- `app/api/chat/`, `app/api/chat/sessions/` — proxy routes that
  forward to the FastAPI backend with the auth header
  (`Authorization: Bearer …` or `X-Test-User-Id` in dev).
- `lib/advisor-auth.ts` — shared helper that picks the auth mode at
  request time.
- `proxy.ts` — Clerk middleware. (Next 16 renamed `middleware.ts`
  to `proxy.ts`; the Clerk SDK helper is still called
  `clerkMiddleware` because it predates that rename.)

## Scripts

```bash
npm run dev          # Next dev server with hot reload
npm run build        # production build (also runs type-aware checks)
npm run typecheck    # tsc --noEmit
```

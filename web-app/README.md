# Web App — Secure DB Access Gateway UI

React 19 + Vite + TypeScript SPA. Served through the nginx TLS edge (`https://localhost:8443`) in Docker; dev server runs on `http://localhost:5173` via Vite.

## Auth model
- Auth is httpOnly cookie JWT (BFF). Browser never stores raw JWT; `localStorage` holds only `app_jwt_exists` flag and non-sensitive `user` metadata (`src/services/auth-service.tsx:12`).
- CSRF double-submit (`csrf_token` + `X-CSRF-Token` + `X-Requested-With`) handled by `src/services/api-client.ts:35`.

## Prerequisites
- Node 20 (see CI `web-app` Node 20)
- Auth0 tenant + `.env` at repo root (see `../.env.example` and `../scripts/bootstrap-dev.sh`)

## Run

```bash
npm install
npm run dev        # Vite on 5173 (proxied via nginx :8443 when using docker compose)
```

Docker (repo root): `docker compose up --build` (nginx, auth0_api, sql_query_api, redis, opa, otel-lgtm, web_app)

## Scripts (per AGENTS.md)

- `npm run lint` — eslint
- `npm test` — typecheck only (`tsc -b`) — no unit tests
- `npm run build` — Vite production build
- `npm run test:e2e` — Playwright (auto-starts Vite dev server; only real browser suite)

## Env

- `VITE_API_BASE_URL` (default `https://localhost:8443` in `docker-compose.yml:22`, prod `https://app.secure-db-access-gateway.org` in `docker-compose.prod.yml:33`)
- `VITE_SQL_GRAPHQL_BASE_URL` (default `https://localhost:8443/api/graphql`)

## Security notes
- Do not store JWT in `localStorage`; use httpOnly cookie flow.
- Logout is `POST /api/logout` (CSRF-protected), not GET.

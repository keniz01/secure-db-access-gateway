# OAuth 2.0 / 2.1 Compliance Matrix

Generated: 2026-09-23 · last reviewed 2026-10-04

Scope: `auth0_api` (Auth0 confidential client, BFF) + `sql_query_api` (Resource Server)

## Summary

- **OAuth 2.0 RFC 6749/6750**: Compliant (Authorization Code + BFF, no deprecated grants)
- **OAuth 2.1 (draft)**: Compliant after PKCE S256, static `redirect_uri`, `nonce`, revocation, JWKS caching
- **Best practice**: OAuth 2.0 for Browser-Based Apps (BFF) + RFC 6819 + RFC 7636 + RFC 7009

## Implementation Table

| Requirement | RFC | Status | File:Line |
|-------------|-----|--------|-----------|
| Confidential client `client_secret` | 6749 §2.1 | ✅ | `auth0_api/app/auth/oauth.py:35` |
| Authorization Code flow | 6749 §4.1 | ✅ | `auth0_api/app/routes/auth_routes.py: login, auth_callback` |
| PKCE S256 mandatory | 7636, 2.1 §4.1 | ✅ | `auth_routes.py: _generate_pkce_pair` → `authorize_redirect(code_challenge, code_challenge_method=S256)` → `authorize_access_token(code_verifier)` |
| `state` CSRF | 6749 §4.1.1, 10.12 | ✅ | Authlib `SessionMiddleware` `app/middleware/setup.py:139` |
| `nonce` for `id_token` replay | OIDC 1.0 | ✅ | `auth_routes.py: oidc_nonce` store + validate against `id_token_claims.nonce` |
| Exact `redirect_uri` matching | 6749 §3.1.2, 2.1 | ✅ | `settings.OAUTH_REDIRECT_URI` static `app/config/settings.py`; `?redirect_origin` ignored `auth_routes.py: login` |
| No `implicit`, `password`, `client_credentials` | 2.1 deprecated | ✅ | Not present (grep) |
| No token in URL, httpOnly + SameSite=Lax | BCP 9700 | ✅ | `session_store.py: create_session` + `gateway_session` `setup.py:139` |
| Bearer `Authorization` header to RS | 6750 | ✅ | `app/routes/graphql_routes.py:58` + `sql_query_api/auth.py:59` (`extract_bearer_token`) |
| JWT RS256, `aud`/`iss`/`exp`/`sub` + leeway | 7519 | ✅ | `sql_query_api/auth.py: _get_jwks_client` singleton `cache_keys=True` + `jwt.decode(..., leeway=2, require=[exp,iss,aud,sub])` (`auth.py:132`) |
| Tenant claim mandatory | app zero-trust | ✅ | `TENANT_ID_CLAIM` `sql_query_api/auth.py:21` — RS rejects tokens without the claim (claim originates in Auth0 user metadata) |
| Token revocation on logout | 7009 | ✅ | `auth_routes.py: _revoke_token_at_auth0` best-effort before `revoke_session` |
| Shared session store for scale | — | ✅ | `session_store.py: REDIS_URL mandatory in prod` + `validate_session_store()` (`session_store.py:63`) fail-closed (`docker-compose.yml:120` default `REDIS_URL`) |
| Scope validation (optional) | 6749 §3.3 | ⚠️ noted | RS validates `scope` type if present; fine-grained auth via policy engine `services/policy_engine.py` |

## How to Verify

```bash
# PKCE: login redirect must contain code_challenge (via the TLS edge)
curl -kis https://localhost:8443/api/login | grep code_challenge
# Static redirect: ?redirect_origin is ignored
curl -kis "https://localhost:8443/api/login?redirect_origin=https://evil.com"
# RS validation: invalid aud/iss -> 401
# Logout revocation: check auth0_api logs "Access token revoked at Auth0" or warning
```

## Operator Checklist

- [ ] Register `OAUTH_REDIRECT_URI` verbatim in Auth0 > Applications > Allowed Callback URLs (e.g. `https://app.example.com/auth`, `https://localhost:8443/auth` for dev)
- [ ] Remove `redirect_origin` query usage from SPA/clients
- [ ] Set `REDIS_URL` in production (`REDIS_URL=redis://redis:6379/0` default in `docker-compose.yml:120`; `session_store.py:63` fails fast if missing)
- [ ] Ensure `AUTH0_AUDIENCE` / `AUTH0_DOMAIN` identical between `auth0_api` and `sql_query_api`

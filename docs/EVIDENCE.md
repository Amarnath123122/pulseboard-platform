# Evidence: the system works end to end

Everything here was captured from real runs. Nothing is mocked except AWS SQS locally (moto), which the brief asked for.

## 1. Live deployment (Azure, Central India)

| Component | URL |
|---|---|
| Website (Static Web Apps) | https://black-mud-02ef90500.6.azurestaticapps.net |
| API gateway (API Management, Consumption) | https://pb-gw-ktuth67ohzdbe.azure-api.net (`/identity`, `/core`) |
| Identity service | https://pb-id-ktuth67ohzdbe.azurewebsites.net/readyz |
| API service | https://pb-api-ktuth67ohzdbe.azurewebsites.net/readyz |

**Readiness at capture time:**
- identity: `{"status":"ok","database":"up"}`
- api: `{"status":"ok","database":"up","cache":"off"}` (Redis is off by default in Azure to keep cost low)

**Browser configuration served by the website (`/config.json`):**
```json
{ "apiUrl": "https://pb-gw-ktuth67ohzdbe.azure-api.net/core",
  "authUrl": "https://pb-gw-ktuth67ohzdbe.azure-api.net/identity",
  "wsUrl": "wss://pb-api-ktuth67ohzdbe.azurewebsites.net/ws/events" }
```

**CORS preflight from the website's origin, answered by the gateway:**
```
OPTIONS /identity/api/v1/auth/login -> HTTP/1.1 200 OK, Access-Control-Allow-Origin: https://black-mud-02ef90500.6.azurestaticapps.net
OPTIONS /core/api/v1/meta          -> HTTP/1.1 200 OK, Access-Control-Allow-Origin: https://black-mud-02ef90500.6.azurestaticapps.net
```

## 2. End-to-end checks

`scripts/e2e_check.py` makes real HTTP, SSE and WebSocket calls through the gateway:
- sign-in and token checks
- RBAC rules and board ownership
- metric CRUD, search and pagination, and the cache flag
- the setup assistant's streamed replies (SSE) and the live board update (WebSocket)
- batched tile data, the blocked `/internal` route, and refresh-token rotation

The same script ran against Docker and against live Azure.

### Azure (live, API Management gateway): 17/17 checks passed
_Run at 2026-10-17 06:20 UTC against `https://pb-gw-ktuth67ohzdbe.azure-api.net/core`_

| # | Check | Result | Evidence | ms |
|---|---|---|---|---|
| 1 | Identity publishes JWKS (RS256) | ✅ PASS | HTTP 200, kid=pulseboard-1 | 467 |
| 2 | Admin signs in through the gateway | ✅ PASS | HTTP 200, role=ADMIN | 785 |
| 3 | Wrong password is rejected | ✅ PASS | HTTP 401 | 432 |
| 4 | New user registers as VIEWER | ✅ PASS | HTTP 201, role=VIEWER | 349 |
| 5 | API rejects an invalid token | ✅ PASS | HTTP 401 | 160 |
| 6 | API verifies identity token (JWKS) and serves /meta | ✅ PASS | HTTP 200, areas=5, regions=['NA', 'EMEA', 'APAC', 'LATAM'] | 591 |
| 7 | RBAC: VIEWER cannot create metrics | ✅ PASS | HTTP 403, missing=['metrics:write'] | 82 |
| 8 | RBAC: ADMIN creates a metric | ✅ PASS | HTTP 201, key=e2e.metric_d7d668 | 141 |
| 9 | Search + pagination (+ cache flag) | ✅ PASS | total=1, pages=1, first cached=False, second cached=False | 376 |
| 10 | CRUD: update then delete | ✅ PASS | PUT 200, DELETE 204, GET after delete 404 | 600 |
| 11 | RBAC: only ADMIN lists users | ✅ PASS | viewer 403, admin 200 (2 users) | 336 |
| 12 | SSE: setup assistant streams replies | ✅ PASS | 55 streamed chunks, board=37c5c651-bcd1-4299-a802-877cc8da948d | 1917 |
| 13 | WebSocket: board.published pushed live | ✅ PASS | events=['connected', 'board.building', 'board.published'] | 2659 |
| 14 | Board is LIVE; all tile data in one batched call | ✅ PASS | version=1, tiles=5, cached on repeat=0/5 | 368 |
| 15 | Ownership: another user cannot read the board | ✅ PASS | HTTP 404 | 201 |
| 16 | Gateway does not expose /internal | ✅ PASS | HTTP 404 | 480 |
| 17 | Refresh rotates; replaying the old token is refused | ✅ PASS | first 200, replay 401 | 318 |

### Docker (local, nginx gateway): 17/17 checks passed
_Run at 2026-10-17 05:57 UTC against `http://localhost:8080`_

| # | Check | Result | Evidence | ms |
|---|---|---|---|---|
| 1 | Identity publishes JWKS (RS256) | ✅ PASS | HTTP 200, kid=pulseboard-1 | 170 |
| 2 | Admin signs in through the gateway | ✅ PASS | HTTP 200, role=ADMIN | 120 |
| 3 | Wrong password is rejected | ✅ PASS | HTTP 401 | 36 |
| 4 | New user registers as VIEWER | ✅ PASS | HTTP 201, role=VIEWER | 33 |
| 5 | API rejects an invalid token | ✅ PASS | HTTP 401 | 8 |
| 6 | API verifies identity token (JWKS) and serves /meta | ✅ PASS | HTTP 200, areas=5, regions=['NA', 'EMEA', 'APAC', 'LATAM'] | 13 |
| 7 | RBAC: VIEWER cannot create metrics | ✅ PASS | HTTP 403, missing=['metrics:write'] | 53 |
| 8 | RBAC: ADMIN creates a metric | ✅ PASS | HTTP 201, key=e2e.metric_a62600 | 86 |
| 9 | Search + pagination (+ cache flag) | ✅ PASS | total=1, pages=1, first cached=False, second cached=True | 22 |
| 10 | CRUD: update then delete | ✅ PASS | PUT 200, DELETE 204, GET after delete 404 | 78 |
| 11 | RBAC: only ADMIN lists users | ✅ PASS | viewer 403, admin 200 (3 users) | 22 |
| 12 | SSE: setup assistant streams replies | ✅ PASS | 55 streamed chunks, board=c506ac3e-cfbf-4b11-9dbd-220b8acb9c5d | 1390 |
| 13 | WebSocket: board.published pushed live | ✅ PASS | events=['connected', 'board.building', 'board.published'] | 2033 |
| 14 | Board is LIVE; all tile data in one batched call | ✅ PASS | version=1, tiles=5, cached on repeat=5/5 | 24 |
| 15 | Ownership: another user cannot read the board | ✅ PASS | HTTP 404 | 4 |
| 16 | Gateway does not expose /internal | ✅ PASS | HTTP 404 | 2 |
| 17 | Refresh rotates; replaying the old token is refused | ✅ PASS | first 200, replay 401 | 115 |

In Azure, "cached" is 0 because `PB_CACHE_BACKEND=none` is the low-cost default. Docker runs Redis and shows the cache hits. Deploying with `-DeployRedis` turns Redis on in Azure.

## 3. Continuous integration (GitHub Actions, latest runs)

| Repo | Result | Run | Commit |
|---|---|---|---|
| pulseboard-identity | success | [run](https://github.com/Amarnath123122/pulseboard-identity/actions/runs/39006817140) | `efd22d1` |
| pulseboard-api | success | [run](https://github.com/Amarnath123122/pulseboard-api/actions/runs/39007935876) | `7a3307d` |
| pulseboard-web | success | [run](https://github.com/Amarnath123122/pulseboard-web/actions/runs/39007938358) | `fbe7d83` |
| pulseboard-platform | success | [run](https://github.com/Amarnath123122/pulseboard-platform/actions/runs/39007940215) | `9a598f9` |

| Repo | What CI proves |
|---|---|
| identity | 7 tests (JWKS verification, rotation and replay, lockout, RBAC admin, last-admin guard). Docker image boots on Postgres and serves JWKS and admin login. |
| api | 20 tests (RBAC from tokens, bad-token rejection, CRUD, cache invalidation, N+1 query-count guard, SQS batching). The image runs against Postgres, Redis and moto, then `demo.py` end to end. |
| web | App and library build, production bundle, Docker image |
| platform | Bicep compiles, the compose file is valid, the gateway passes `nginx -t` |

## 4. How to reproduce
```bash
# Docker (whole system)
cd pulseboard-platform && docker compose up --build -d
python scripts/e2e_check.py --name docker --auth http://localhost:8080 --api http://localhost:8080 \
  --ws ws://localhost:8080/ws/events --admin-email admin@example.com --admin-password Admin-Pulseboard-2026

# Azure
G=https://<gateway>.azure-api.net
python scripts/e2e_check.py --name azure --auth $G/identity --api $G/core \
  --ws wss://<api-app>.azurewebsites.net/ws/events --admin-email <owner> --admin-password <adminPassword>
```

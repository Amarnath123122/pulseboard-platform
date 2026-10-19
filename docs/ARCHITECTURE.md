# Pulseboard architecture, A to Z

A granular reference for every layer of Pulseboard: the business view, the components, data, security, runtime, deployment and operations. All screenshots are real captures of the running system (`scripts/screenshots.py`); the last two come from the live Azure deployment.

> Short on time? The [Guide](00-GUIDE.md) is the summary. This file is the deep dive.

---

## Index
| | Section | | Section |
|---|---|---|---|
| A | [Purpose, scope, quality goals](#a-purpose-scope-quality-goals) | N | [Realtime: SSE, WebSocket, LISTEN/NOTIFY](#n-realtime) |
| B | [System context (C4 level 1)](#b-system-context-c4-level-1) | O | [Background work: scheduler, queue, worker](#o-background-work) |
| C | [Containers (C4 level 2)](#c-containers-c4-level-2) | P | [Reliability and failure modes](#p-reliability-and-failure-modes) |
| D | [Identity service components](#d-identity-service-components) | Q | [Observability](#q-observability) |
| E | [API service components](#e-api-service-components) | R | [Deployment: Azure topology](#r-deployment-azure-topology) |
| F | [Web app components](#f-web-app-components) | S | [Deployment: local and Docker topology](#s-deployment-local-and-docker) |
| G | [API gateway](#g-api-gateway) | T | [CI/CD pipeline](#t-cicd-pipeline) |
| H | [Request lifecycle, hop by hop](#h-request-lifecycle-hop-by-hop) | U | [Configuration matrix](#u-configuration-matrix) |
| I | [Authentication and token anatomy](#i-authentication-and-token-anatomy) | V | [UI walkthrough with screenshots](#v-ui-walkthrough-with-screenshots) |
| J | [Authorization (RBAC + ownership)](#j-authorization-rbac--ownership) | W | [Architecture decisions (ADR log)](#w-architecture-decisions) |
| K | [Data model, both databases](#k-data-model) | X | [Cost model](#x-cost-model) |
| L | [State machines](#l-state-machines) | Y | [Repository and file map](#y-repository-and-file-map) |
| M | [Caching and performance](#m-caching-and-performance) | Z | [Limits and roadmap](#z-limits-and-roadmap) |

---

## A. Purpose, scope, quality goals

**Purpose:** give every user a personal, self-updating board of business metrics, where alerts come first and each alert has its own detail and briefing boards.

| In scope | Out of scope (today) |
|---|---|
| Accounts, sign-in, RBAC | SSO / social login (roadmap: Entra ID) |
| Setup assistant (rules engine) | A real LLM (the interface is ready) |
| Boards, tiles, alerts, follow-ups | Real warehouse connectors (a mock source with the same contract) |
| Metric catalog CRUD | Multi-tenant organisations |
| Hourly reviews, live updates | Email or push notifications |

| Quality goal | How it is met |
|---|---|
| **Correctness** | Fenced publishing (claim + generation), one transaction per publish, idempotent reviews |
| **Security** | RS256 + JWKS, rotating refresh tokens, RBAC on every route, no secrets in code |
| **Performance** | Redis cache-aside, batched tile data, N+1 guards in tests |
| **Reliability** | Stateless services, retries, degraded modes (cache or queue down) |
| **Low cost** | One App Service plan, one Postgres server, Consumption APIM, Free SWA |
| **Operability** | Health probes, request ids, App Insights, one-command deploy and pause |

---

## B. System context (C4 level 1)

```mermaid
flowchart TB
    U[Person<br/>Viewer / Editor / Admin]
    PB[Pulseboard system]
    AZ[(Microsoft Azure<br/>hosting, Key Vault, monitoring)]
    GH[(GitHub<br/>source, CI/CD)]
    SRC[(Metric source<br/>mocked today)]
    U -- "uses via browser (HTTPS, WSS)" --> PB
    PB -- "reads metric values" --> SRC
    PB -- "runs on" --> AZ
    GH -- "builds, tests, deploys" --> PB
```

---

## C. Containers (C4 level 2)

```mermaid
flowchart LR
    subgraph Client
      SPA[Angular SPA<br/>pulseboard app + data-access lib]
    end
    subgraph Edge
      GW[API gateway<br/>nginx / API Management]
    end
    subgraph Identity[pulseboard-identity]
      IAPI[FastAPI app<br/>gunicorn x2 workers]
    end
    subgraph Domain[pulseboard-api]
      DAPI[FastAPI app<br/>gunicorn x2 workers]
      BG[Background tasks<br/>scheduler, worker, NOTIFY relay]
    end
    subgraph Data
      IDB[(Postgres DB: identity)]
      PDB[(Postgres DB: pulseboard)]
      RDS[(Redis)]
      QUE[[Queue: SQS / Azure Storage Queue]]
      KV[(Key Vault)]
    end
    SPA -->|HTTPS REST + SSE| GW
    SPA -.->|WSS /ws/events| DAPI
    GW -->|identity routes| IAPI
    GW -->|/api/v1/*| DAPI
    DAPI -->|JWKS, cached| IAPI
    IAPI --> IDB
    IAPI -.-> RDS
    DAPI --> PDB
    DAPI --> RDS
    BG --> QUE
    BG --> PDB
    IAPI -. secrets .-> KV
    DAPI -. secrets .-> KV
```

| Container | Tech | Port (local) | Scales by |
|---|---|---|---|
| SPA | Angular 17, nginx (Docker) / Static Web Apps | 8080 via gateway / 5173 dev | CDN |
| Gateway | nginx 1.27 / APIM Consumption | 8080 | Managed |
| Identity | Python 3.12, FastAPI, SQLAlchemy async, PyJWT | 8100 (dev) | More workers or instances (stateless) |
| API | Python 3.12, FastAPI, SQLAlchemy async, redis-py, boto3 / azure-storage-queue | 8000 | More workers or instances (stateless; NOTIFY relay) |
| Postgres | 16 (Docker) / Flexible Server B1ms | 5432 | Vertical, then read replicas |
| Redis | 7 (Docker) / Azure Managed Redis (optional) | internal | Vertical |
| Queue | moto SQS (Docker/dev) / Azure Storage Queue | 5000 (moto) | Managed |

---

## D. Identity service components

```mermaid
flowchart TB
    R[routes.py<br/>auth, users, roles, JWKS, OIDC discovery, health]
    S[service.py<br/>register, login, refresh, logout, users admin, bootstrap admin]
    SEC[security.py<br/>scrypt, RSA key, JWKS, issue/verify JWT, RBAC map, require]
    T[throttle.py<br/>failed-login counters: Redis or memory]
    DB[db.py<br/>User, RefreshToken, async engine]
    CFG[config.py<br/>ID_* settings + production guards]
    R --> S --> SEC
    S --> T
    S --> DB
    SEC --> CFG
```

| Module | Key responsibilities |
|---|---|
| `security.py` | `hash_password` / `verify_password` (scrypt, n=2¹⁴, r=8, p=1, 16-byte salt). `private_key()` reads the key from `ID_JWT_PRIVATE_KEY_B64`, or creates an **atomic** dev key file. `jwks()`. `issue_access_token()`. `PERMISSIONS`. `require()`. |
| `service.py` | Constant-time login (a dummy hash for unknown emails). Refresh **rotation** with **family revocation** on reuse. Last-admin guard. Role change revokes sessions. |
| `throttle.py` | `INCR` + `EXPIRE NX` per email and per IP (Redis), or an in-process dict with TTL |
| `routes.py` | Pydantic validation (EmailStr, password ≥ 10 characters). Typed responses. |

---

## E. API service components

```mermaid
flowchart TB
    subgraph HTTP
      PUB[api/public.py<br/>assistant SSE, boards, tiles, tile-data, follow-up]
      MET[api/metrics_admin.py<br/>catalog CRUD]
      BRW[api/browse.py<br/>meta, my boards, jobs]
      INT[api/internal.py<br/>service-key ops]
      RT[api/realtime.py<br/>WebSocket]
      HL[api/health.py]
    end
    subgraph Domain
      SET[assistants/setup.py<br/>conversation + background build]
      PLN[assistants/planner.py<br/>watchlist to LayoutPlan]
      REV[assistants/reviewer.py<br/>hourly review]
    end
    subgraph Services
      LAY[services/layout.py<br/>claim / publish / release]
      CAT[services/catalog.py<br/>metric CRUD, search, cached catalog]
      MTR[services/metrics.py<br/>mock source: query, projection, grade]
      CCH[services/cache.py + keys.py<br/>Redis/memory/none]
      QUE[services/queue.py<br/>SQS / Azure queue]
      WRK[services/workers.py<br/>scheduler + worker]
      EVT[services/events.py<br/>WebSocket hub + pg_notify relay]
      PRF[services/preferences.py]
    end
    SECU[core/security.py<br/>JWKS verify + require perms]
    PUB --> SET --> PLN --> MTR
    SET --> LAY
    REV --> PLN
    REV --> LAY
    WRK --> QUE
    WRK --> REV
    LAY --> EVT
    LAY --> CCH
    PUB --> CCH
    MET --> CAT --> CCH
    PLN --> CAT
    PUB --> SECU
    MET --> SECU
    BRW --> SECU
    RT --> SECU
```

---

## F. Web app components

```mermaid
flowchart TB
    subgraph App[projects/pulseboard]
      SH[AppComponent<br/>sidebar, toasts, role-aware nav]
      RTS[app.routes.ts<br/>signedIn / signedOut / can perm guards]
      PG[pages<br/>sign-in, setup, board, boards, metrics, activity, users]
      TC[tile-card, pager]
    end
    subgraph Lib[projects/data-access]
      API[ApiService<br/>REST + SSE via fetch]
      AUTH[AuthService<br/>login/register/refresh/users]
      INT[authInterceptor<br/>bearer + single-flight refresh on 401]
      SES[SessionService<br/>signals: user, tokens, can]
      LIVE[LiveEventsService<br/>reconnecting WebSocket]
      META[MetaService]
      TST[ToastService]
      CFG[PULSEBOARD_CONFIG<br/>from /config.json]
    end
    SH --> LIVE
    PG --> API
    PG --> AUTH
    API --> INT
    AUTH --> SES
    INT --> AUTH
    API --> CFG
    AUTH --> CFG
    LIVE --> CFG
```

**Boot sequence (`main.ts`):**
1. `fetch('/config.json')`.
2. `bootstrapApplication` with `PULSEBOARD_CONFIG`.
3. Route guards check the session signal.
4. `LiveEventsService` opens the WebSocket whenever a token exists.

---

## G. API gateway

| Concern | nginx (local/Docker) | API Management (Azure) |
|---|---|---|
| Identity routes | `location /api/v1/auth/`, `/api/v1/users`, `/.well-known/` | API `identity` (path `/identity`), operations GET/POST/PUT/PATCH/DELETE on `/api/v1/auth/*`, `/api/v1/users[/*]`, `/.well-known/*` |
| API routes | `location /api/` (buffering off), `/ws/` (upgrade) | API `core` (path `/core`), `/api/v1/*` |
| Blocked | `location ~ ^/(internal\|docs\|redoc\|openapi\.json) { return 404; }` | not defined as operations, so unreachable |
| CORS | same origin, not needed | `<cors>` policy (SWA origin + localhost:5173) |
| SSE | `proxy_buffering off; proxy_read_timeout 300s` | `<forward-request buffer-response="false" timeout="120"/>` |
| WebSocket | `Upgrade`/`Connection` headers, 1 h timeout | not supported on Consumption, so the SPA connects to the API directly |
| Rate limit | `limit_req` 10 r/s auth, 50 r/s API per IP | identity throttling (paid tiers: `rate-limit-by-key`) |
| Headers | `X-Request-ID $request_id`, `X-Forwarded-For`, nosniff, DENY, no-referrer | `X-Forwarded-For` from `context.Request.IpAddress` |

---

## H. Request lifecycle, hop by hop

Example: `GET /api/v1/boards/{id}/tile-data` from the browser.

```mermaid
sequenceDiagram
    autonumber
    participant SPA as SPA (ApiService)
    participant INT as authInterceptor
    participant GW as Gateway
    participant MW as API middleware
    participant SEC as require("boards:read")
    participant J as JWKS client
    participant R as Redis
    participant DB as Postgres
    participant M as Metrics source
    SPA->>INT: http.get(apiUrl + path)
    INT->>GW: + Authorization: Bearer access
    GW->>MW: + X-Request-ID, X-Forwarded-For
    MW->>SEC: dependency
    SEC->>J: signing key for kid (cached 5 min)
    SEC->>SEC: verify RS256, iss, aud, exp, typ, perms contains boards:read
    MW->>R: GET board:<id> (ownership check, cached)
    MW->>DB: SELECT tiles WHERE board_id ORDER BY position   (1 query)
    MW->>R: MGET tile:<id>:<hour> x N                    (1 round trip)
    alt cache misses
      MW->>R: GET catalog:v1
      MW->>M: run_query per missing tile (in a thread)
      MW->>R: pipelined SET for misses                   (1 round trip)
    end
    MW-->>GW: 200 JSON + X-Request-ID
    GW-->>SPA: response
    Note over INT: on 401, one shared refresh, then retry once
```

---

## I. Authentication and token anatomy

### I.1 Access token (JWT, RS256, 15 minutes)
```json
// header
{ "alg": "RS256", "kid": "pulseboard-1", "typ": "JWT" }
// payload
{ "sub": "6b0c…", "email": "priya@example.com", "role": "VIEWER",
  "perms": ["boards:follow_up","boards:read","jobs:read","metrics:read","setup:run"],
  "iss": "pulseboard-identity", "aud": "pulseboard", "typ": "access",
  "iat": 1792240000, "exp": 1792240900 }
```

### I.2 Refresh token (opaque, 7 days)
- **Format:** 48 random bytes, URL-safe. Only its **SHA-256** is stored, in `refresh_tokens.token_hash`.
- **Family:** belongs to a `family_id`, created at login.
- **On refresh:** the presented token is revoked and a new one is issued in the same family.
- **Reuse:** presenting a revoked token revokes **every** token in the family (theft detection).

```mermaid
sequenceDiagram
    participant B as Browser
    participant I as Identity
    B->>I: login
    I-->>B: A1 + R1 (family F)
    B->>I: refresh R1
    I->>I: revoke R1
    I-->>B: A2 + R2 (family F)
    Note over B: attacker replays stolen R1
    B->>I: refresh R1
    I->>I: R1 already revoked, so revoke ALL of family F (R2 too)
    I-->>B: 401 "already used, sign in again"
```

### I.3 Keys
| Environment | Private key | Public key consumers |
|---|---|---|
| Local | `.keys/jwt-private.pem`, created atomically on first start | API via `http://localhost:8100/.well-known/jwks.json` |
| Docker | named volume `idkeys` | API via `http://identity:8000/.well-known/jwks.json` |
| Azure | Key Vault secret `identity-jwt-key`, read through a Key Vault reference | API via `https://pb-id-…/.well-known/jwks.json`; also exposed through the gateway |

**Rotation:**
1. Publish a new key with a new `kid`.
2. The API refetches JWKS when it sees an unknown `kid`.
3. Old tokens expire within 15 minutes.

---

## J. Authorization (RBAC + ownership)

```mermaid
flowchart LR
    V[VIEWER] -->|+ metrics:write| E[EDITOR] -->|+ users:read, users:write| A[ADMIN]
```

| Route | Permission | Extra rule |
|---|---|---|
| `POST /api/v1/assistant/messages` | `setup:run` | |
| `GET /api/v1/boards*`, `/meta` | `boards:read` | the board must belong to the token's `sub`; otherwise **404** |
| `PATCH /api/v1/tiles/{id}/follow-up` | `boards:follow_up` | ownership via one JOIN |
| `GET /api/v1/metrics*` | `metrics:read` | |
| `POST/PUT/DELETE /api/v1/metrics*` | `metrics:write` | |
| `GET /api/v1/jobs` | `jobs:read` | only the user's own jobs |
| `GET/PATCH /api/v1/users*` | `users:read` / `users:write` | the last active admin is protected; you can't deactivate yourself |
| `/internal/v1/*` | `X-Service-Key` (constant-time compare) | never routed by a gateway |

**Denied response (403), as captured:**
```json
{"error":{"code":"FORBIDDEN","message":"You don't have permission to do this",
 "details":{"missing":["metrics:write"],"role":"VIEWER"},"request_id":"…"}}
```

---

## K. Data model

### K.1 Identity database
```mermaid
erDiagram
    users ||--o{ refresh_tokens : has
    users {
      string id PK
      string email UK "lower-cased"
      string display_name
      string password_hash "scrypt$n$r$p$salt$hash"
      string role_cd "ADMIN|EDITOR|VIEWER"
      bool is_active
      datetime last_login_at
      datetime created_at
      datetime updated_at
    }
    refresh_tokens {
      string id PK
      string user_id FK
      string family_id "rotation family"
      string token_hash UK "sha256"
      datetime expires_at
      datetime revoked_at
      datetime created_at
    }
```

### K.2 Pulseboard database
```mermaid
erDiagram
    boards ||--o{ boards : "parent (detail/briefing)"
    boards ||--o{ tiles : contains
    boards ||--o{ board_versions : history
    tiles }o--o| boards : "detail_board_id / briefing_board_id"
    conversations ||--o{ conversation_messages : has
    assistant_profiles ||--o{ assistant_jobs : runs
    boards {
      string id PK
      string user_id "identity sub"
      string parent_board_id FK
      string kind_cd "OVERVIEW|DETAIL|BRIEFING"
      string slot_key "UK with parent"
      string title
      string origin_cd "SETUP|DEFAULT_TEMPLATE|REVIEW"
      string build_status_cd "BUILDING|LIVE|FAILED"
      json settings
      int version
      string claim_holder
      datetime claim_expires_at
      int generation "fencing token"
      datetime next_review_at
    }
    tiles {
      string id PK
      string board_id FK
      int position "UK with board"
      string slot_key
      string visual_cd "STAT|TREND|BREAKDOWN|NOTE"
      string tier_cd "ALERT|HEADLINE|CONTEXT"
      string priority_cd "LOW|MEDIUM|HIGH"
      string follow_up_cd "NONE|PENDING|SEEN|DONE"
      json query_spec
      json presentation
    }
    board_versions {
      string id PK
      string board_id FK
      int version
      string job_id
      json snapshot
    }
    metrics {
      string key PK "area.name"
      string area
      string title
      string unit
      float base
      float volatility
      bool higher_is_better
      json owners
      json sources
      bool enabled
    }
    user_preferences {
      string id PK
      string user_id
      string scope_cd "LAYOUT|WATCHLIST"
      string key
      json value
    }
    assistant_jobs {
      string id PK
      string job_type_cd "SETUP|REVIEW"
      string user_id
      string board_id
      string status_cd
      datetime started_at
      datetime finished_at
    }
```

**Migrations:**
- `pulseboard-identity/migrations/versions/0001_*` creates the identity tables.
- `pulseboard-api/migrations/versions/0001_*` creates the core tables, and `0002_*` adds `metrics`.
- Both run `alembic upgrade head` on start.

---

## L. State machines

```mermaid
stateDiagram-v2
    [*] --> BUILDING: claim (setup or review)
    BUILDING --> LIVE: publish (same claim holder + generation)
    BUILDING --> LIVE: release after failure (previous version kept)
    BUILDING --> FAILED: release with no previous version
    LIVE --> BUILDING: next claim
```

```mermaid
stateDiagram-v2
    [*] --> RUNNING
    RUNNING --> SUCCEEDED
    RUNNING --> FAILED
    RUNNING --> SKIPPED: board already claimed by another job
```

```mermaid
stateDiagram-v2
    [*] --> NONE
    NONE --> SEEN: Mark seen
    NONE --> DONE: Done
    SEEN --> DONE: Done
    note right of SEEN: preserved across reviews (matched by slot_key)
```

---

## M. Caching and performance

| Key | TTL | Written by | Invalidated by |
|---|---|---|---|
| `catalog:v1` | 300 s | `load_catalog` | metric create, update or delete |
| `metrics:list:<q>\|<area>\|…` | 60 s | `catalog.search` | any metric write |
| `board:<id>` | 300 s, never while BUILDING | `_board_dict` | publish (family), release |
| `tiles:<board>` | 300 s | `GET …/tiles` | publish, follow-up |
| `tile:<tile>:<YYYYMMDDHH>` | 300 s | `_tile_results` | metric write; hourly rollover |
| `boards:<user>:*` | 60 s | `GET /boards` | publish for that user |
| `login:fail:{email\|ip}:*` | 900 s | identity throttle | successful login |

| N+1 fixed | Before | After |
|---|---|---|
| Tile data | N HTTP calls × 2 SQL | 1 call, 1 SQL, 1 MGET, 1 pipeline |
| Tile ownership | 2 queries | 1 JOIN |
| Board counts | N count queries | 1 `GROUP BY` |
| Queue send | N calls | `send_message_batch` (10 per call) |

**Guard test:** `test_board_tile_data_is_batched_cached_and_constant_queries` allows ≤3 SQL statements cold and ≤1 warm.

---

## N. Realtime

```mermaid
sequenceDiagram
    participant W1 as API instance 1 (publisher)
    participant PG as Postgres
    participant W2 as API instance 2 (holds the socket)
    participant B as Browser
    W1->>PG: SELECT pg_notify('pulseboard_events', {user, event})
    PG-->>W1: NOTIFY
    PG-->>W2: NOTIFY
    W2->>B: WebSocket send board.published
    B->>B: refetch board, toast "Board updated"
```

| Channel | Payloads | Client behaviour |
|---|---|---|
| SSE `POST /assistant/messages` | `conversation`, `delta`, `turn.end`, `board`, `error` | append text; progress bar; redirect on `board` |
| WS `/ws/events?token=` | `connected`, `board.building`, `board.published`, `pong` | refetch; toast; reconnect with backoff (0.5 s → 30 s); ping every 25 s |

---

## O. Background work

```mermaid
flowchart LR
    SCH[review_scheduler<br/>every 30 s<br/>SELECT … FOR UPDATE SKIP LOCKED] -->|send_many| Q[[Queue]]
    Q -->|receive 5| WK[review_worker]
    WK -->|attempts over 5| DROP[delete + log poison]
    WK --> RV[review_board]
    RV --> CL{claim?}
    CL -- conflict --> SK[job SKIPPED]
    CL -- ok --> PUB[plan + publish] --> NEXT[next_review_at = now + 60 min]
    WK -->|success| DEL[delete message]
```

---

## P. Reliability and failure modes

| Failure | Effect | Behaviour |
|---|---|---|
| Redis down | slower | every cache call falls back to the DB; `/readyz` shows `cache: down`; login throttling pauses |
| Queue down | reviews delayed | the worker backs off (1 s → 60 s); setup and reads unaffected |
| Identity down | no new sign-ins | issued tokens keep working until they expire (JWKS cached) |
| API down | no boards | sign-in still works; the UI shows retry |
| Postgres down | both services unready | `/readyz` 503; App Service health check recycles; the gateway returns 5xx |
| Two jobs publish the same board | prevented | generation fence; the loser gets 409 / SKIPPED |
| Worker crash mid-build | board stuck BUILDING? | the claim expires (120 s); `release` restores LIVE; the UI polls every 3 s |
| Duplicate queue delivery | harmless | claim + generation make publish idempotent |
| Poison message | isolated | dropped after 5 attempts |
| Kudu deploy 504 | none | async deploy plus `/readyz` polling; retry on 409 |

---

## Q. Observability

| Signal | Where |
|---|---|
| Logs | JSON to stdout with `request_id`, path, status, ms → App Service logs and Log Analytics |
| Traces and metrics | Application Insights via `azure-monitor-opentelemetry` (API) |
| Health | `/healthz`, `/readyz` on both services; `/gateway/health` (nginx); App Service health check |
| Alerts | 5xx > 10 in 5 min (API); monthly budget, 50% actual / 100% forecast emails |
| Correlation | the gateway sets `X-Request-ID`; services echo it in responses and error bodies |

---

## R. Deployment: Azure topology

```mermaid
flowchart TB
    subgraph RG[Resource group rg-pulseboard · Central India]
      SWA[Static Web App<br/>pb-web-* Free]
      APIM[API Management<br/>pb-gw-* Consumption]
      subgraph PLAN[App Service plan pb-plan-* B1 Linux]
        IDAPP[Web app pb-id-*<br/>Python 3.12, managed identity]
        APIAPP[Web app pb-api-*<br/>Python 3.12, managed identity, WebSockets on]
      end
      PG[(Postgres Flexible pb-pg-* B1ms<br/>DBs: pulseboard, identity)]
      KV[(Key Vault pb-kv-*<br/>RBAC)]
      ST[(Storage pbq*<br/>queue board-reviews, no shared keys)]
      AI[App Insights + Log Analytics]
      AL[Action group + alerts + budget]
    end
    SWA -.->|"config.json"| APIM
    APIM --> IDAPP
    APIM --> APIAPP
    IDAPP --> PG
    APIAPP --> PG
    IDAPP -- Key Vault Secrets User --> KV
    APIAPP -- Key Vault Secrets User --> KV
    APIAPP -- Storage Queue Data Contributor --> ST
    IDAPP --> AI
    APIAPP --> AI
```

**Key Vault secrets:**
- `database-url`
- `identity-database-url`
- `identity-jwt-key`
- `identity-admin-password`
- `service-key`
- `redis-url` (only when Redis is deployed)

The apps read them as `@Microsoft.KeyVault(SecretUri=…)` app settings.

---

## S. Deployment: local and Docker

```mermaid
flowchart LR
    subgraph Docker[docker compose · project pulseboard]
      GWN[gateway nginx :8080]
      WEBC[web nginx-unprivileged :8080 internal]
      IDC[identity :8000 internal]
      APIC[api :8000 → host 8000]
      PGC[(postgres:16 · init.sql creates identity DB)]
      RDC[(redis:7 · 128 MB LRU)]
      MOTO[[moto SQS]]
    end
    Browser --> GWN
    GWN --> WEBC
    GWN --> IDC
    GWN --> APIC
    IDC --> PGC
    APIC --> PGC
    IDC --> RDC
    APIC --> RDC
    APIC --> MOTO
```

**Start order (health-gated):** postgres and redis → identity → api → gateway.

---

## T. CI/CD pipeline

```mermaid
flowchart LR
    C[git push] --> CI1[identity: ruff, pytest x7, Docker boot on Postgres]
    C --> CI2[api: ruff, pytest x20, image vs Postgres+Redis+moto, demo.py]
    C --> CI3[web: app+lib build, Docker build]
    C --> CI4[platform: bicep build, compose config, nginx -t]
    CI2 -->|main + green| CD2[deploy.yml: zip deploy App Service]
    CI3 -->|main| CD3[deploy.yml: SWA upload with env config.json]
    DEV[developer] -->|azure-deploy.ps1| ALL[infra + identity + api + web + smoke test]
```

---

## U. Configuration matrix

| Setting | Local | Docker | Azure |
|---|---|---|---|
| `ID_DATABASE_URL` | SQLite | `postgres/identity` | Key Vault `identity-database-url` |
| `ID_JWT_PRIVATE_KEY_B64` | empty, so a dev key file is used | empty, volume key file | Key Vault `identity-jwt-key` |
| `PB_DATABASE_URL` | SQLite | `postgres/pulseboard` | Key Vault `database-url` |
| `PB_JWKS_URL` | `localhost:8100` | `identity:8000` | `https://pb-id-…` |
| `PB_CACHE_BACKEND` | `memory` | `redis` | `none` (or `redis` with `-DeployRedis`) |
| `PB_QUEUE_BACKEND` | `sqs` (moto) | `sqs` (moto) | `azure` |
| `PB_CORS_ORIGINS` / `ID_CORS_ORIGINS` | dev proxy, unused | `[]` | `[]` (APIM answers CORS) |
| web `config.json` | `""`, `""`, `ws://localhost:5173/ws/events` | `""`, `""`, `ws://localhost:8080/ws/events` | `…/core`, `…/identity`, `wss://pb-api-…/ws/events` |

---

## V. UI walkthrough with screenshots

Each screen lists the API calls behind it.

### V.1 Sign in and create an account
`POST /api/v1/auth/login` · `POST /api/v1/auth/register` (identity)

![Sign in](images/01-sign-in.png)
![Create account](images/02-create-account.png)

### V.2 Setup assistant (SSE)
`POST /api/v1/assistant/messages` streamed, plus `GET /api/v1/meta` for the answer chips

![Question 1](images/04-setup-question-1.png)
![Pick areas: chips built from the catalog](images/05-setup-pick-areas.png)
![Done: building the board](images/06-setup-done.png)
![Building state](images/07-building-state.png)

### V.3 Overview board
`GET /boards/overview` · `GET /boards/{id}/tiles` · **one** `GET /boards/{id}/tile-data` · WebSocket `board.published` (see the toast)

![Overview](images/08-overview-board.png)

### V.4 Detail and briefing boards
![Detail: trend, regions, projection](images/09-detail-board.png)
![Briefing: summary, next steps, owners, sources](images/10-briefing-board.png)

### V.5 My boards and Activity
`GET /api/v1/boards?q&kind&page&size` · `GET /api/v1/jobs?job_type&status&page&size`

![My boards](images/11-my-boards.png)
![Activity](images/12-activity.png)

### V.6 Metric catalog: Viewer (read-only) vs Admin (CRUD)
`GET /api/v1/metrics?…` · `POST/PUT/DELETE /api/v1/metrics/{key}` (needs `metrics:write`)

![Viewer: read-only](images/13-metric-catalog-viewer-read-only.png)
![Admin: edit and delete](images/14-metric-catalog-admin.png)
![Create form](images/15-metric-create-form.png)
![Search](images/16-metric-search.png)

### V.7 Users (Admin)
`GET /api/v1/users` · `PATCH /api/v1/users/{id}` · `GET /api/v1/auth/roles`

![Users and the role matrix](images/17-users-admin.png)

### V.8 Mobile layout and API docs
![Mobile](images/18-mobile-layout.png)
![OpenAPI (direct to API; blocked at the gateway)](images/19-api-openapi-docs.png)

### V.9 Live on Azure
![Azure sign in](images/20-azure-sign-in.png)
![Azure overview board](images/21-azure-overview-board.png)

---

## W. Architecture decisions

| # | Decision | Why | Trade-off |
|---|---|---|---|
| 1 | Separate **identity** microservice | One owner for credentials and RBAC; other services hold no secrets | One more deployable |
| 2 | **RS256 + JWKS** instead of a shared HS256 secret | Verification without sharing a signing secret; key rotation via `kid` | Key management |
| 3 | **Rotating refresh tokens with family revocation** | Detects token theft | A refresh endpoint call every 15 min |
| 4 | **Permissions in the token** | The API needs no identity call per request | Role changes apply at the next refresh (≤15 min) |
| 5 | **APIM Consumption** in Azure, **nginx** locally | Real gateway at ~zero cost; same routes in both | Consumption has no WebSocket support, so WS goes direct |
| 6 | **One App Service plan, one Postgres server, two databases** | Free-trial budget | Shared capacity |
| 7 | **Cache-aside** with central key names | Simple, correct, Redis-optional | First request after a write is a miss |
| 8 | **Claim + generation fencing** | No lost updates between setup and reviews | Extra columns and a claim step |
| 9 | **Runtime `config.json`** | One web build for every environment | One extra request at boot |
| 10 | **Mock metrics source** behind `run_query(catalog, spec)` | Deterministic demos and tests | Not real data (yet) |
| 11 | **Async zip deploy + readiness polling** | Kudu's synchronous call times out on pip installs | Script polls |
| 12 | **Rules-engine assistant** | Predictable, free, testable | Not conversational AI |

---

## X. Cost model

| Resource | Tier | ≈ cost |
|---|---|---|
| App Service plan (identity + api) | B1 | ₹35/day |
| Postgres Flexible | B1ms, 32 GB | free 12 months (750 h) |
| API Management | Consumption | first 1M calls/month free |
| Static Web Apps | Free | ₹0 |
| Key Vault, Storage Queue, App Insights (capped) | — | ≈₹0 |
| Azure Managed Redis (optional) | Balanced B0 | ₹40–50/day |

**Pause:** `scripts/azure-stop.ps1` moves the plan to F1 and stops the apps and Postgres.

---

## Y. Repository and file map

```
pulseboard-identity/
  app/config.py  db.py  security.py  service.py  throttle.py  routes.py  main.py  errors.py  logging.py
  migrations/  tests/test_identity.py  Dockerfile  startup.sh  .github/workflows/ci.yml
pulseboard-api/
  app/core/{config,security,errors,logging}.py   app/db/{models,session,seed,seed_metrics}.py
  app/api/{public,browse,metrics_admin,internal,realtime,health}.py
  app/assistants/{setup,planner,reviewer}.py
  app/services/{layout,catalog,metrics,cache,keys,queue,workers,events,preferences}.py
  migrations/  tests/{conftest,test_api}.py  scripts/{demo,mint_token,dev}.py|ps1  Dockerfile  startup.sh
pulseboard-web/
  projects/pulseboard/src/app/{app.component,app.routes,app.config}.ts
  projects/pulseboard/src/app/pages/{sign-in,setup,board,boards,metrics,activity,users}.page.ts, tile-card, pager
  projects/data-access/src/lib/{api,auth,session,meta,toast,live-events}.service.ts, auth.interceptor.ts, models.ts
  projects/pulseboard/{proxy.conf.json, public/config.json, staticwebapp.config.json}  docker/  Dockerfile
pulseboard-platform/
  README.md (the guide)  docs/{ARCHITECTURE,EVIDENCE}.md  docs/images/*.png
  gateway/nginx.conf  postgres/init.sql  docker-compose.yml
  infra/main.bicep  scripts/{azure-deploy,azure-stop}.ps1  scripts/{e2e_check,screenshots}.py
```

---

## Z. Limits and roadmap

| Today | Next |
|---|---|
| Email and password only | Entra ID / OIDC federation (SSO) |
| Rules-engine assistant | Real model producing the same `LayoutPlan` contract |
| Mock metric source | Warehouse or semantic-layer connector behind `run_query` |
| WebSocket bypasses the gateway in Azure | APIM v2 / Web PubSub, or Front Door |
| Postgres reachable from Azure services (firewall rule) | VNet integration + private endpoints |
| Single region | Zone redundancy / second region when budget allows |
| Tokens in `localStorage` | httpOnly refresh cookie behind a same-site gateway |

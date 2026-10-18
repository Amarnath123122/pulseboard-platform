# 01 · Features, A to Z

Every user-facing feature: who can use it, how it behaves step by step, which API calls it makes, which data it touches, and how to test it.

> Series: **01 Features** · [02 Frontend](02-FRONTEND.md) · [03 Backend](03-BACKEND.md) · [04 Database](04-DATABASE.md) · [05 Docker](05-DOCKER.md) · [06 Deployment](06-DEPLOYMENT.md) · [Architecture overview](ARCHITECTURE.md)

---

## Feature map

| # | Feature | Viewer | Editor | Admin | Service(s) |
|---|---|:-:|:-:|:-:|---|
| F1 | Create account | ✓ | ✓ | ✓ | identity |
| F2 | Sign in / sign out | ✓ | ✓ | ✓ | identity |
| F3 | Stay signed in (silent token refresh) | ✓ | ✓ | ✓ | identity + web |
| F4 | Setup assistant (streamed chat) | ✓ | ✓ | ✓ | api |
| F5 | Skip setup (default board) | ✓ | ✓ | ✓ | api |
| F6 | Building state | ✓ | ✓ | ✓ | api + web |
| F7 | Overview board (alerts first) | ✓ | ✓ | ✓ | api |
| F8 | Detail board (trend, regions, projection) | ✓ | ✓ | ✓ | api |
| F9 | Briefing board (summary, owners, sources, next steps) | ✓ | ✓ | ✓ | api |
| F10 | Follow-ups (Mark seen / Done) | ✓ | ✓ | ✓ | api |
| F11 | Live updates (WebSocket, toasts) | ✓ | ✓ | ✓ | api |
| F12 | Hourly automatic review | ✓ | ✓ | ✓ | api (worker) |
| F13 | My boards (search, filter, paginate) | ✓ | ✓ | ✓ | api |
| F14 | Activity (job history) | ✓ | ✓ | ✓ | api |
| F15 | Metric catalog: browse, search, filter, sort, paginate | ✓ | ✓ | ✓ | api |
| F16 | Metric catalog: create, edit, delete | | ✓ | ✓ | api |
| F17 | Users: list, search, filter by role | | | ✓ | identity |
| F18 | Users: change role, activate or deactivate | | | ✓ | identity |
| F19 | Role and permission matrix (shown in the UI) | | | ✓ | identity |
| F20 | Account protection (lockout, theft detection) | system | system | system | identity |
| F21 | Responsive layout (desktop and phone) | ✓ | ✓ | ✓ | web |
| F22 | Caching (fast repeat views) | system | system | system | api + Redis |

---

## F1 · Create account
| | |
|---|---|
| **Where** | `/sign-in` → **Create account** tab |
| **Fields** | Your name (1–120 chars) · Email (valid address) · Password (**≥ 10 chars**) |
| **API** | `POST {authUrl}/api/v1/auth/register` |
| **Result** | Signed in immediately as **VIEWER** (`ID_SIGNUP_ROLE`). New users go to `/setup`. |
| **Errors** | 409 "An account with this email already exists" · 422 password too short or bad email · 403 if sign-up is disabled (`ID_ALLOW_SIGNUP=false`) |
| **Data** | `identity.users` row. Email is stored lower-cased; the password is stored as a scrypt hash, never in plain text. |

Request and response:
```http
POST /api/v1/auth/register
{"email":"Priya@Example.com","password":"Correct-Horse-9","display_name":"Priya"}

201 Created
{"access_token":"eyJhbGciOiJSUzI1NiIs…","refresh_token":"x7Q…","token_type":"bearer","expires_in":900,
 "user":{"id":"6b0c…","email":"priya@example.com","display_name":"Priya","role":"VIEWER","is_active":true,
         "permissions":["boards:follow_up","boards:read","jobs:read","metrics:read","setup:run"]}}
```

**Test:** create an account, then try the same email again. Expect *409*.

## F2 · Sign in / sign out
| | |
|---|---|
| **API** | `POST /api/v1/auth/login` · `POST /api/v1/auth/logout` (body: `refresh_token`) |
| **Wrong password** | 401 "Email or password is incorrect". The **same message** for an unknown email, so nobody can probe which accounts exist. |
| **Lockout** | 5 failures for the same email **or** the same IP within 15 minutes → **429** |
| **Deactivated user** | 403 "This account is deactivated" |
| **Sign out** | Revokes the whole session (the refresh-token family), clears local storage, returns to `/sign-in` |

## F3 · Stay signed in (silent refresh)
1. The access token lasts **15 minutes**; the refresh token lasts **7 days**.
2. When any API call returns 401, the web app calls `POST /api/v1/auth/refresh` **once**. Concurrent failing calls share that one refresh ("single-flight").
3. It retries the original call with the new token.
4. Every refresh **rotates** the refresh token. Re-using an old one is treated as theft, and the whole family is revoked.
5. If the refresh fails, the user is signed out.

## F4 · Setup assistant
| Step | UI | API (SSE stream) |
|---|---|---|
| Open `/setup` | Greeting and question 1 stream in word by word | `POST /api/v1/assistant/messages {"action":"start"}` |
| Q1 "Which team are you on?" | Free text | `{"conversation_id":…, "text":"Engineering"}` |
| Q2 "Which areas should I watch?" | **Chips** built from the live metric catalog (multi-select) + free text | `{"text":"sales, customers"}` |
| Q3 "Which region?" | Chips: *All regions* + `PB_REGIONS` | `{"text":"EMEA"}` |
| Done | "Building your board now", then redirect to `/` | SSE event `board` |

**SSE events:**
- `conversation`: `{conversation_id}`
- `delta`: `{text}`, repeated
- `turn.end`: `{step, total_steps, finished}`
- `board`: `{board_id, build_status}`
- `error`: `{code, message}`

**Data written:**
- `conversations` and `conversation_messages`
- `user_preferences`: `team`, `areas`, `region`
- `assistant_jobs` (SETUP)
- `boards`, `tiles` and `board_versions`

## F5 · Skip setup
`{"action":"skip"}` builds a board that watches **every area in the catalog**, with `origin_cd = DEFAULT_TEMPLATE`.

## F6 · Building state
While `build_status_cd = BUILDING` the page shows a pulse and "Building your board…". It updates when the WebSocket sends `board.published`. If the socket is down, it polls every 3 seconds.

## F7 · Overview board
| Element | Meaning |
|---|---|
| **Needs attention (n)** | Alert tiles: HIGH (red border) first, then MEDIUM (amber), ordered by size of change |
| Tile header | Metric title · priority chip · **›** opens the detail board |
| Summary line | e.g. "Net promoter score is down 60.5% against its recent average." |
| Big number | Latest value, formatted by unit (`$172K`, `2.26×`, `89 tickets`, `96.13%`) |
| Delta | ▲/▼ % vs baseline, coloured **good** or **bad** by whether higher is better |
| Footer | Follow-up state · Mark seen · Done · **Open briefing** |
| **Watching** | Healthy headline stats and context trend lines |
| Header | version number · next review time · team · areas · region · priority legend |

**API calls:**
1. `GET /boards/overview`
2. `GET /boards/{id}/tiles`
3. **one** `GET /boards/{id}/tile-data` for every tile at once

**How priority is graded:**
- The change is first adjusted for direction, so a rise counts as bad when lower is better.
- **HIGH** if the adjusted change is ≤ −8%.
- **MEDIUM** if it's ≤ −3%.
- **LOW** otherwise.

## F8 · Detail board
Breadcrumb **Overview › Detail**. It has three tiles:
1. **Last 12 weeks:** weekly trend line.
2. **By region:** horizontal bars, one per `PB_REGIONS`.
3. **Next 4 weeks (projected):** least-squares projection, drawn dashed.

## F9 · Briefing board
Breadcrumb **Overview › Briefing**. It shows:
- a summary
- **Next steps** (ordered; includes "Raise it at the next <team> sync" for HIGH)
- **Owners** and **Sources** (from the catalog)
- tiles: *Now vs baseline* and *Outlook*

## F10 · Follow-ups
`PATCH /api/v1/tiles/{id}/follow-up {"follow_up_cd":"SEEN"|"DONE"}`
- Allowed values: `NONE`, `PENDING`, `SEEN`, `DONE`.
- **Survives reviews:** the next publish matches tiles by `slot_key` and keeps the user's choice.
- Clears the `tiles:<board>` cache key.

## F11 · Live updates
| Event | Effect in the UI |
|---|---|
| `connected` | Sidebar shows "● Live updates on" |
| `board.building` | Open board switches to the Building state |
| `board.published` | Open board and tiles refetch · Activity refreshes · toast "Board updated to version N" |

Reconnects automatically with backoff (0.5 s, doubling up to 30 s), and pings every 25 s.

## F12 · Hourly automatic review
1. Every 30 s the scheduler finds overview boards with `next_review_at <= now`.
2. It queues them in batches.
3. The worker re-reads preferences and the current catalog, re-grades every metric, and publishes a new **version**.
4. Follow-ups are kept, and users see `board.published`.
5. Job rows go to `assistant_jobs` (REVIEW).

## F13 · My boards
`GET /api/v1/boards?q=&kind=&page=&size=`

| Column | |
|---|---|
| Title | Link; overview opens `/`, others `/boards/{id}` |
| Kind | overview / detail / briefing |
| Tiles, Alerts | counts from one `GROUP BY` query |
| Version, Updated | |

Search is case-insensitive on the title. The page sizes in the picker come from `/api/v1/meta`.

## F14 · Activity
`GET /api/v1/jobs?job_type=&status=&page=&size=`
- Columns: Job, Status (coloured), Started, Took, Board, Note.
- It refreshes on every `board.published`.

## F15 · Metric catalog: browse
`GET /api/v1/metrics?q=&area=&enabled=&sort=key|title|area|updated_at&desc=&page=&size=`
- Search covers key, title and area, with a 300 ms debounce.
- Click a column header to sort.
- The footer shows "cached" when Redis served the page.
- Viewers see a **read-only** tag instead of edit buttons.

## F16 · Metric catalog: create, edit, delete (Editor, Admin)
| Field | Rule |
|---|---|
| Key | `area.name`, lowercase, e.g. `sales.win_rate`; unique (409 on duplicate); fixed after creation |
| Title | 1–256 chars |
| Area | lowercase slug; drives setup-assistant chips |
| Unit | one of `PB_UNITS` (422 otherwise) |
| Typical level (`base`) | > 0 |
| Volatility | 0 < v ≤ 1 |
| Higher is better | flips the alert direction |
| Owners, Sources | comma-separated lists, up to 20 each |
| Enabled | disabled metrics leave boards at their next build |

- **Delete** asks for confirmation inline (Confirm delete / Keep).
- Every write clears the catalog, list and tile caches, and refreshes `/meta`.
- A Viewer attempting a write gets **403** `{"missing":["metrics:write"]}`.

## F17–F19 · Users (Admin)
| Action | API | Guard |
|---|---|---|
| List, search, filter | `GET /api/v1/users?q=&role=&page=&size=` | `users:read` |
| Change role | `PATCH /api/v1/users/{id} {"role":"EDITOR"}` | `users:write`; ends that user's sessions so the change applies within 15 min |
| Deactivate / activate | `PATCH … {"is_active":false}` | can't deactivate yourself; the last active admin is protected (409) |
| Role matrix | `GET /api/v1/auth/roles` | public |

## F20 · Account protection
| Protection | Detail |
|---|---|
| Password hashing | scrypt (n = 16384, r = 8, p = 1), 16-byte random salt |
| Timing-safe login | A dummy hash is checked for unknown emails, so response times match |
| Lockout | Redis counters `login:fail:email:*` and `login:fail:ip:*`, 15 min window |
| Theft detection | Re-using a rotated refresh token revokes the whole family |
| Gateway rate limits (Docker) | 10 req/s per IP for `/api/v1/auth/*`, 50 req/s for `/api/*` |

## F21 · Responsive layout
- Above 760 px wide: a fixed 240 px sidebar.
- Below that, the sidebar becomes a top bar.
- Light or dark theme follows the operating system.
- Keyboard focus rings are visible, and animations respect `prefers-reduced-motion`.

## F22 · Caching
See [03-BACKEND.md §8](03-BACKEND.md#8-cache-layer). Visible to users as faster repeat loads, and as the **cached** markers on the catalog footer and in the tile-data responses.

---

## Feature test checklist
| # | Do | Expect |
|---|---|---|
| 1 | Create account | Lands on Setup as Viewer |
| 2 | Answer the 3 questions | Streaming text, then Building, then the Overview with alerts |
| 3 | Click › on an alert | Detail board with 3 tiles |
| 4 | Back, then **Open briefing** | Summary, next steps, owners, sources |
| 5 | **Mark seen** on an alert | Footer shows "seen"; still "seen" after a review |
| 6 | Metric catalog as Viewer | "read-only" tag, no edit buttons |
| 7 | Sign in as Admin → Users → set that user to **EDITOR** | Toast confirms |
| 8 | Sign in again as that user → Metric catalog | **+ New metric**, Edit, Delete visible |
| 9 | Wrong password 5 times | 6th attempt: "Too many failed sign-in attempts" |
| 10 | Resize to phone width | Sidebar becomes a top bar |

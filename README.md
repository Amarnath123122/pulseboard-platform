# pulseboard-platform

This repo runs, deploys and documents the whole Pulseboard system.

| Repo | What it is |
|---|---|
| [pulseboard-identity](https://github.com/Amarnath123122/pulseboard-identity) | Accounts, RS256 tokens and JWKS, refresh rotation, RBAC |
| [pulseboard-api](https://github.com/Amarnath123122/pulseboard-api) | Boards, metric catalog, setup assistant (SSE), reviews (queue), WebSocket, Redis cache |
| [pulseboard-web](https://github.com/Amarnath123122/pulseboard-web) | Angular 17 monorepo UI |
| **pulseboard-platform** | API gateway, Docker Compose, Azure Bicep, deploy scripts, architecture docs |

## Run everything locally
```bash
# with all four repos checked out side by side
cd pulseboard-platform
docker compose up --build -d
```
- **App:** http://localhost:8080, through the gateway.
- **Admin:** `admin@example.com` / `Admin-Pulseboard-2026`.
- **New accounts:** they start as VIEWER; promote them on the Users page.

## Deploy to Azure (low cost)
```powershell
az login
./scripts/azure-deploy.ps1 -OwnerEmail you@example.com   # infra + identity + api + web + smoke test
./scripts/azure-stop.ps1                                 # pause billing
```
The admin account is `OwnerEmail`. Its password is generated into `infra/secrets.local.json` (`adminPassword`), which git ignores.

## Docs
- [docs/EVIDENCE.md](docs/EVIDENCE.md): captured proof from live Azure, Docker and CI (17/17 end-to-end checks)
- [docs/MICROSERVICES.md](docs/MICROSERVICES.md): services, gateway, auth and RBAC, reliability, cost, and the end-to-end workflow
- [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md): deep architecture reference
- `pulseboard-api/docs/`: project guide, caching and performance, runbook

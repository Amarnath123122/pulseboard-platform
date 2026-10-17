"""End-to-end check of a running Pulseboard system, through its gateway, with a Markdown report.

    python scripts/e2e_check.py --name docker --auth http://localhost:8080 --api http://localhost:8080 \
        --ws ws://localhost:8080/ws/events --admin-email admin@example.com --admin-password Admin-Pulseboard-2026

Needs: httpx, websockets. Every check is a real HTTP/SSE/WebSocket call; the report records status codes,
timings and key values so it can be published as evidence.
"""

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime

import httpx
import websockets

results: list[tuple[str, bool, str, float]] = []


def check(name: str, ok: bool, detail: str, started: float) -> None:
    results.append((name, ok, detail, (time.perf_counter() - started) * 1000))
    print(("PASS " if ok else "FAIL ") + name + " :: " + detail)


async def main(a: argparse.Namespace) -> int:
    async with httpx.AsyncClient(timeout=60) as c:
        t = time.perf_counter()
        r = await c.get(f"{a.auth}/.well-known/jwks.json")
        keys = r.json().get("keys", []) if r.status_code == 200 else []
        check("Identity publishes JWKS (RS256)", bool(keys) and keys[0].get("alg") == "RS256",
              f"HTTP {r.status_code}, kid={keys[0].get('kid') if keys else '-'}", t)

        t = time.perf_counter()
        r = await c.post(f"{a.auth}/api/v1/auth/login", json={"email": a.admin_email, "password": a.admin_password})
        admin = r.json() if r.status_code == 200 else {}
        check("Admin signs in through the gateway", admin.get("user", {}).get("role") == "ADMIN",
              f"HTTP {r.status_code}, role={admin.get('user', {}).get('role')}", t)
        H_admin = {"Authorization": f"Bearer {admin.get('access_token', '')}"}

        t = time.perf_counter()
        r = await c.post(f"{a.auth}/api/v1/auth/login", json={"email": a.admin_email, "password": "wrong-password"})
        check("Wrong password is rejected", r.status_code == 401, f"HTTP {r.status_code}", t)

        email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
        t = time.perf_counter()
        r = await c.post(f"{a.auth}/api/v1/auth/register",
                         json={"email": email, "password": "E2e-Password-2026", "display_name": "E2E viewer"})
        viewer = r.json() if r.status_code == 201 else {}
        check("New user registers as VIEWER", viewer.get("user", {}).get("role") == "VIEWER",
              f"HTTP {r.status_code}, role={viewer.get('user', {}).get('role')}", t)
        H = {"Authorization": f"Bearer {viewer.get('access_token', '')}"}

        t = time.perf_counter()
        r = await c.get(f"{a.api}/api/v1/boards", headers={"Authorization": "Bearer not-a-token"})
        check("API rejects an invalid token", r.status_code == 401, f"HTTP {r.status_code}", t)

        t = time.perf_counter()
        r = await c.get(f"{a.api}/api/v1/meta", headers=H)
        meta = r.json() if r.status_code == 200 else {}
        check("API verifies identity token (JWKS) and serves /meta", r.status_code == 200,
              f"HTTP {r.status_code}, areas={len(meta.get('areas', []))}, regions={meta.get('regions')}", t)

        body = {"key": f"e2e.metric_{uuid.uuid4().hex[:6]}", "area": "e2e", "title": "E2E metric", "unit": "%",
                "base": 50, "volatility": 0.05}
        t = time.perf_counter()
        r = await c.post(f"{a.api}/api/v1/metrics", json=body, headers=H)
        missing = r.json().get("error", {}).get("details", {}).get("missing") if r.status_code == 403 else None
        check("RBAC: VIEWER cannot create metrics", r.status_code == 403, f"HTTP {r.status_code}, missing={missing}", t)

        t = time.perf_counter()
        r = await c.post(f"{a.api}/api/v1/metrics", json=body, headers=H_admin)
        check("RBAC: ADMIN creates a metric", r.status_code == 201, f"HTTP {r.status_code}, key={body['key']}", t)

        t = time.perf_counter()
        p1 = (await c.get(f"{a.api}/api/v1/metrics", params={"q": "e2e", "size": 5}, headers=H)).json()
        p2 = (await c.get(f"{a.api}/api/v1/metrics", params={"q": "e2e", "size": 5}, headers=H)).json()
        check("Search + pagination (+ cache flag)", p1.get("total", 0) >= 1,
              f"total={p1.get('total')}, pages={p1.get('pages')}, first cached={p1.get('cached')}, "
              f"second cached={p2.get('cached')}", t)

        t = time.perf_counter()
        r = await c.put(f"{a.api}/api/v1/metrics/{body['key']}", headers=H_admin,
                        json={k: v for k, v in body.items() if k != "key"} | {"title": "E2E metric (edited)"})
        r2 = await c.delete(f"{a.api}/api/v1/metrics/{body['key']}", headers=H_admin)
        r3 = await c.get(f"{a.api}/api/v1/metrics/{body['key']}", headers=H_admin)
        check("CRUD: update then delete", r.status_code == 200 and r2.status_code == 204 and r3.status_code == 404,
              f"PUT {r.status_code}, DELETE {r2.status_code}, GET after delete {r3.status_code}", t)

        t = time.perf_counter()
        r = await c.get(f"{a.auth}/api/v1/users", params={"size": 5}, headers=H)
        r2 = await c.get(f"{a.auth}/api/v1/users", params={"size": 5}, headers=H_admin)
        check("RBAC: only ADMIN lists users", r.status_code == 403 and r2.status_code == 200,
              f"viewer {r.status_code}, admin {r2.status_code} ({r2.json().get('total') if r2.status_code == 200 else '-'} users)", t)

        # WebSocket + SSE: open the socket, run the setup chat (SSE), expect board.published on the socket.
        events: list[str] = []

        async def listen() -> None:
            async with websockets.connect(f"{a.ws}?token={viewer.get('access_token', '')}", open_timeout=30) as ws:
                while True:
                    events.append(json.loads(await ws.recv()).get("type", ""))
                    if "board.published" in events:
                        return

        listener = asyncio.create_task(listen())
        await asyncio.sleep(2)
        t = time.perf_counter()
        convo, deltas, board_id = None, 0, None
        for action, text in [("start", ""), ("reply", "Engineering"), ("reply", "sales and finance"), ("reply", "EMEA")]:
            async with c.stream("POST", f"{a.api}/api/v1/assistant/messages", headers=H,
                                json={"conversation_id": convo, "action": action, "text": text}) as resp:
                event = None
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        deltas += event == "delta"
                        convo = data.get("conversation_id", convo)
                        board_id = data.get("board_id", board_id)
        check("SSE: setup assistant streams replies", deltas > 10 and board_id is not None,
              f"{deltas} streamed chunks, board={board_id}", t)

        t = time.perf_counter()
        try:
            await asyncio.wait_for(listener, timeout=60)
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        check("WebSocket: board.published pushed live", ok and "board.published" in events, f"events={events}", t)

        t = time.perf_counter()
        board = (await c.get(f"{a.api}/api/v1/boards/{board_id}", headers=H)).json()
        cold = (await c.get(f"{a.api}/api/v1/boards/{board_id}/tile-data", headers=H)).json()
        warm = (await c.get(f"{a.api}/api/v1/boards/{board_id}/tile-data", headers=H)).json()
        check("Board is LIVE; all tile data in one batched call",
              board.get("build_status_cd") == "LIVE" and len(cold) > 0 and all(d["status"] == "OK" for d in cold),
              f"version={board.get('version')}, tiles={len(cold)}, cached on repeat={sum(d.get('cached', False) for d in warm)}/{len(warm)}",
              t)

        t = time.perf_counter()
        r = await c.get(f"{a.api}/api/v1/boards/{board_id}", headers=H_admin)
        check("Ownership: another user cannot read the board", r.status_code == 404, f"HTTP {r.status_code}", t)

        t = time.perf_counter()
        r = await c.get(f"{a.api}/internal/v1/jobs")
        check("Gateway does not expose /internal", r.status_code in (403, 404), f"HTTP {r.status_code}", t)

        t = time.perf_counter()
        r1 = await c.post(f"{a.auth}/api/v1/auth/refresh", json={"refresh_token": viewer.get("refresh_token")})
        r2 = await c.post(f"{a.auth}/api/v1/auth/refresh", json={"refresh_token": viewer.get("refresh_token")})
        check("Refresh rotates; replaying the old token is refused", r1.status_code == 200 and r2.status_code == 401,
              f"first {r1.status_code}, replay {r2.status_code}", t)

    passed = sum(ok for _, ok, _, _ in results)
    lines = [f"### {a.name}: {passed}/{len(results)} checks passed",
             f"_Run at {datetime.now(UTC):%Y-%m-%d %H:%M UTC} against `{a.api}`_", "",
             "| # | Check | Result | Evidence | ms |", "|---|---|---|---|---|"]
    for i, (name, ok, detail, ms) in enumerate(results, 1):
        lines.append(f"| {i} | {name} | {'✅ PASS' if ok else '❌ FAIL'} | {detail.replace('|', '/')} | {ms:.0f} |")
    with open(a.report, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--auth", required=True, help="base URL that routes /api/v1/auth, /api/v1/users, /.well-known")
    p.add_argument("--api", required=True, help="base URL that routes /api/v1/* of the API")
    p.add_argument("--ws", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--report", default="e2e-report.md")
    raise SystemExit(asyncio.run(main(p.parse_args())))

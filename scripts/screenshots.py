"""Drives the real UI with a headless browser and saves screenshots to docs/images/.

    python scripts/screenshots.py http://localhost:8080 [azure-web-url]

Needs: playwright (pip install playwright && python -m playwright install chromium) and a running system.
"""

import sys
import uuid
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8080"
AZURE = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else None
OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
ADMIN = ("admin@example.com", "Admin-Pulseboard-2026")


def shot(page: Page, name: str, full: bool = True) -> None:
    page.wait_for_timeout(600)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print("saved", name)


def sign_in(page: Page, base: str, email: str, password: str) -> None:
    page.goto(f"{base}/sign-in")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("button.primary")
    page.wait_for_url(lambda u: "/sign-in" not in u, timeout=30000)


def register(page: Page, base: str, email: str, name: str) -> None:
    page.goto(f"{base}/sign-in")
    page.click("text=Create account")
    page.fill("input[name=name]", name)
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", "Screenshot-Pass-2026")
    page.click("button.primary")
    page.wait_for_url(lambda u: "/sign-in" not in u, timeout=30000)


def sign_out(page: Page) -> None:
    page.click("text=Sign out")
    page.wait_for_url("**/sign-in", timeout=15000)


def run_setup(page: Page, prefix: str) -> None:
    page.wait_for_url("**/setup", timeout=30000)
    page.wait_for_selector(".msg.assistant >> nth=0")
    page.wait_for_timeout(2500)
    shot(page, f"{prefix}04-setup-question-1")
    page.fill("input[name=text]", "Engineering")
    page.click("button:has-text('Send')")
    page.wait_for_selector(".chip-btn", timeout=30000)
    page.wait_for_timeout(2500)
    for chip in ("sales", "customers", "operations"):
        page.click(f".chip-btn:has-text('{chip}')")
    shot(page, f"{prefix}05-setup-pick-areas")
    page.click("button:has-text('Send')")
    page.wait_for_selector(".chip-btn:has-text('EMEA')", timeout=30000)
    page.wait_for_timeout(2500)
    page.click(".chip-btn:has-text('EMEA')")
    page.wait_for_selector(".callout", timeout=30000)
    shot(page, f"{prefix}06-setup-done")
    page.wait_for_url(lambda u: u.rstrip("/").endswith(("8080", ".net")) or u.endswith("/"), timeout=30000)
    try:
        page.wait_for_selector(".thinking", timeout=4000)
        shot(page, f"{prefix}07-building-state")
    except Exception:  # noqa: BLE001 - build may already be done
        pass
    page.wait_for_selector(".tile", timeout=60000)
    page.wait_for_timeout(2500)


def local() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(f"{BASE}/sign-in")
        shot(page, "01-sign-in")
        page.click("text=Create account")
        shot(page, "02-create-account")

        register(page, BASE, f"shots-{uuid.uuid4().hex[:6]}@example.com", "Priya Viewer")
        page.wait_for_timeout(500)
        run_setup(page, "")
        shot(page, "08-overview-board")

        page.click("a.open >> nth=0")
        page.wait_for_selector(".tile", timeout=30000)
        page.wait_for_timeout(2500)
        shot(page, "09-detail-board")
        page.go_back()
        page.wait_for_selector("text=Open briefing", timeout=30000)
        page.click("text=Open briefing >> nth=0")
        page.wait_for_selector(".briefing", timeout=30000)
        page.wait_for_timeout(2500)
        shot(page, "10-briefing-board")

        page.click("nav >> text=My boards")
        page.wait_for_selector("table tbody tr")
        shot(page, "11-my-boards")
        page.click("nav >> text=Activity")
        page.wait_for_selector("table tbody tr")
        shot(page, "12-activity")
        page.click("nav >> text=Metric catalog")
        page.wait_for_selector("table tbody tr")
        shot(page, "13-metric-catalog-viewer-read-only")
        sign_out(page)

        sign_in(page, BASE, *ADMIN)
        page.goto(f"{BASE}/metrics")
        page.wait_for_selector("table tbody tr")
        shot(page, "14-metric-catalog-admin")
        page.click("text=+ New metric")
        page.wait_for_selector("form.form-grid")
        shot(page, "15-metric-create-form")
        page.click("button:has-text('Cancel')")
        page.fill(".toolbar input", "churn")
        page.wait_for_timeout(1200)
        shot(page, "16-metric-search")
        page.goto(f"{BASE}/users")
        page.wait_for_selector("table tbody tr")
        page.click("summary")
        shot(page, "17-users-admin")

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        mobile.add_init_script("() => {}")
        sign_in(mobile, BASE, *ADMIN)
        mobile.goto(f"{BASE}/metrics")
        mobile.wait_for_selector("table tbody tr")
        shot(mobile, "18-mobile-layout", full=False)

        page.goto("http://localhost:8000/docs")
        page.wait_for_selector(".opblock", timeout=30000)
        shot(page, "19-api-openapi-docs", full=False)
        browser.close()


def azure() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{AZURE}/sign-in")
        page.wait_for_selector("input[name=email]", timeout=60000)
        shot(page, "20-azure-sign-in")
        register(page, AZURE, f"azure-shots-{uuid.uuid4().hex[:6]}@example.com", "Azure Demo")
        run_setup(page, "azure-")
        shot(page, "21-azure-overview-board")
        browser.close()


if __name__ == "__main__":
    local()
    if AZURE:
        azure()

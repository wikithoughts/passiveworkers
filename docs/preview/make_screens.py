"""Generate UI screenshots of the three Passive Workers surfaces (desktop + mobile) with Playwright.
Not part of the package. Requires local servers: pw serve (:8770) + coordinator on the demo DB (:8801).
Run: python docs/preview/make_screens.py
"""
import pathlib
import time

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent / "img"
OUT.mkdir(parents=True, exist_ok=True)

SERVE = "http://127.0.0.1:8770"
COORD = "http://127.0.0.1:8801"
REPORT = "2026-06-14-what-is-the-current-us-federal-funds-rate-target-range-and-a.md"
HANDLE, SECRET = "founder3", "IkFRRapS7rreNaRadVCOTe-OnAi1m0UJ"

DEVICES = {"desktop": {"width": 1280, "height": 900}, "mobile": {"width": 375, "height": 812}}


def shot(page, name, full=True):
    p = OUT / name
    page.screenshot(path=str(p), full_page=full)
    print("  ✓", name, p.stat().st_size, "bytes")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    for dev, vp in DEVICES.items():
        print(f"== {dev} {vp['width']}x{vp['height']} ==")
        # 1) Research desk — render the real fed-funds report
        ctx = browser.new_context(viewport=vp, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(SERVE, wait_until="networkidle")
        pg.evaluate("(n)=>openReport(n)", REPORT)
        pg.wait_for_timeout(1500)
        shot(pg, f"research-desk-{dev}.png")
        ctx.close()

        # 2) Marketplace app — signed in, showing a rendered council answer (illustrative job)
        ctx = browser.new_context(viewport=vp, device_scale_factor=2)
        ctx.add_init_script(f"localStorage.setItem('pw_handle','{HANDLE}');"
                            f"localStorage.setItem('pw_secret','{SECRET}');")
        pg = ctx.new_page()
        pg.goto(f"{COORD}/#job=demojob01", wait_until="networkidle")
        pg.wait_for_timeout(3500)   # openJob polls + renders; map tiles load
        shot(pg, f"marketplace-{dev}.png")
        ctx.close()

        # 3) Operator dashboard — nodes + leaderboard + map
        ctx = browser.new_context(viewport=vp, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(f"{COORD}/dashboard", wait_until="networkidle")
        pg.wait_for_timeout(4000)   # tick() populates + Leaflet tiles
        shot(pg, f"dashboard-{dev}.png")
        ctx.close()
    browser.close()
print("done ->", OUT)

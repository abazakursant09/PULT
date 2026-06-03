"""
Business-Pult QA: checks all routes for errors, takes screenshots.
"""
import asyncio
import os
from playwright.async_api import async_playwright

BASE = "http://localhost:3002"
SHOTS = "/c/business-pult/qa/screenshots"

ROUTES = [
    "/",
    "/login",
    "/dashboard",
    "/profit-calculator",
    "/auto-promotions",
    "/seo-cards",
    "/ai-agents",
    "/community",
    "/dashboard/security",
    "/suppliers",
    "/logistics",
]

os.makedirs(SHOTS, exist_ok=True)

async def check_page(page, path):
    url = BASE + path
    errors = []
    console_errors = []

    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    def on_page_error(err, _errors=errors):
        msg = str(err)
        # Ignore expected network errors when backend is offline
        if any(x in msg for x in ["Failed to fetch", "net::ERR_", "CORS"]):
            return
        _errors.append(f"PAGE ERROR: {msg}")

    page.on("pageerror", on_page_error)

    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=15000)
        status = resp.status if resp else 0
    except Exception as e:
        return {"path": path, "status": "TIMEOUT", "errors": [str(e)], "console": []}

    await page.wait_for_timeout(1000)

    slug = path.strip("/").replace("/", "_") or "home"
    await page.screenshot(path=f"{SHOTS}/{slug}.png", full_page=True)

    body_text = await page.inner_text("body")
    has_content = len(body_text.strip()) > 100

    has_error_page = any(x in body_text for x in [
        "Application error", "An error occurred",
        "500 Internal Server Error", "Error: Minified React error",
    ])

    nav_links = await page.locator("a[href]").count()

    real_errors = [e for e in console_errors if not any(x in e for x in [
        "Warning:", "DevTools", "hydrat", "favicon",
        "CORS policy", "net::ERR_FAILED", "net::ERR_",
        "Failed to load resource",
    ])]

    return {
        "path": path,
        "http_status": status,
        "has_content": has_content,
        "has_error_page": has_error_page,
        "nav_links": nav_links,
        "errors": errors,
        "console_errors": real_errors[:5],
        "screenshot": f"{SHOTS}/{slug}.png",
    }


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
        )

        await context.add_cookies([{
            "name": "token",
            "value": "fake-token-for-ui-check",
            "domain": "localhost",
            "path": "/",
        }])

        page = await context.new_page()
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.evaluate("""() => {
            localStorage.setItem('token', 'fake-token-for-ui-check');
            localStorage.setItem('user', JSON.stringify({
                id: '1', name: 'Test User', email: 'test@test.com', plan: 'master'
            }));
        }""")

        results = []
        for path in ROUTES:
            print(f"Checking {path}...")
            fresh_page = await context.new_page()
            result = await check_page(fresh_page, path)
            await fresh_page.close()
            results.append(result)

            ok = result.get("http_status") == 200 and result.get("has_content") and not result.get("has_error_page")
            status_icon = "[OK]" if ok else "[ERR]"
            print(f"  {status_icon} HTTP {result.get('http_status')} | content={result.get('has_content')} | nav_links={result.get('nav_links')}")
            if result.get("errors"):
                for e in result["errors"]:
                    print(f"  WARN: {e}")
            if result.get("console_errors"):
                for e in result["console_errors"]:
                    print(f"  CONSOLE: {e}")

        await browser.close()

        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        issues = [r for r in results if r.get("http_status") != 200 or r.get("has_error_page") or r.get("errors") or r.get("console_errors")]
        if not issues:
            print("[OK] All pages clean")
        else:
            for r in issues:
                print(f"\n[ERR] {r['path']}")
                if r.get("errors"):
                    for e in r["errors"]: print(f"   ERROR: {e}")
                if r.get("console_errors"):
                    for e in r["console_errors"]: print(f"   CONSOLE: {e}")
                if r.get("has_error_page"):
                    print(f"   Has error page content")

if __name__ == "__main__":
    asyncio.run(main())

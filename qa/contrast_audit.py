"""
WCAG 2.1 contrast audit for all pages.
Checks all visible text elements, reports violations < 4.5:1 (normal) or < 3:1 (large).
"""
import asyncio
import json

BASE = "http://localhost:3002"

ROUTES = [
    "/", "/login", "/dashboard", "/profit-calculator",
    "/auto-promotions", "/seo-cards", "/ai-agents",
    "/community", "/dashboard/security", "/suppliers", "/logistics",
]

# Injected into each page — returns list of contrast violations
AUDIT_JS = r"""
() => {
  function linearize(c) {
    c = c / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }
  function lum(r, g, b) {
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b);
  }
  function ratio(L1, L2) {
    var hi = Math.max(L1, L2), lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  }
  function parseRgb(s) {
    var m = s.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  function effectiveBg(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var bg = window.getComputedStyle(node).backgroundColor;
      if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
        var rgb = parseRgb(bg);
        if (rgb && !(rgb[0] === 0 && rgb[1] === 0 && rgb[2] === 0 && bg.indexOf('rgba') !== -1)) {
          return rgb;
        }
        if (rgb && bg.indexOf('rgba') === -1) return rgb;
      }
      node = node.parentElement;
    }
    var bodyBg = window.getComputedStyle(document.body).backgroundColor;
    return parseRgb(bodyBg) || [28, 28, 30];
  }

  var selectors = 'h1,h2,h3,h4,h5,h6,p,a,button,label,span,li,td,th,input,textarea,small';
  var els = document.querySelectorAll(selectors);
  var violations = [];
  var seen = new Set();

  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    var rect = el.getBoundingClientRect();
    // skip invisible elements
    if (rect.width === 0 || rect.height === 0) continue;
    var style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

    var text = (el.innerText || el.textContent || '').trim().slice(0, 80);
    if (!text || text.length < 2) continue;

    // dedupe by text+color combo
    var key = text.slice(0, 30) + style.color;
    if (seen.has(key)) continue;
    seen.add(key);

    var fg = parseRgb(style.color);
    if (!fg) continue;

    var bg = effectiveBg(el);
    var fgL = lum(fg[0], fg[1], fg[2]);
    var bgL = lum(bg[0], bg[1], bg[2]);
    var cr = ratio(fgL, bgL);

    var fontSize = parseFloat(style.fontSize);
    var fontWeight = parseInt(style.fontWeight) || 400;
    var isLarge = fontSize >= 18 || (fontSize >= 14 && fontWeight >= 700);
    var threshold = isLarge ? 3.0 : 4.5;

    if (cr < threshold) {
      violations.push({
        tag: el.tagName.toLowerCase(),
        text: text.slice(0, 60),
        fg: 'rgb(' + fg.join(',') + ')',
        bg: 'rgb(' + bg.join(',') + ')',
        ratio: Math.round(cr * 100) / 100,
        need: threshold,
        size: Math.round(fontSize),
        weight: fontWeight,
      });
    }
  }
  // sort by worst ratio first
  violations.sort(function(a, b) { return a.ratio - b.ratio; });
  return violations.slice(0, 50);
}
"""


async def audit_page(context, path):
    page = await context.new_page()
    try:
        resp = await page.goto(BASE + path, wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(600)
        violations = await page.evaluate(AUDIT_JS)
        return violations
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        await page.close()


async def main():
    async with __import__("playwright.async_api", fromlist=["async_playwright"]).async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})

        await context.add_cookies([{
            "name": "token", "value": "fake-token",
            "domain": "localhost", "path": "/",
        }])
        page = await context.new_page()
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.evaluate("""() => {
            localStorage.setItem('token', 'fake-token');
            localStorage.setItem('user', JSON.stringify({
                id:'1', name:'Test User', email:'t@t.com', plan:'master'
            }));
        }""")
        await page.close()

        all_results = {}
        for path in ROUTES:
            print(f"Auditing {path}...")
            viols = await audit_page(context, path)
            all_results[path] = viols
            ok = [v for v in viols if "error" not in v]
            err = [v for v in viols if "error" in v]
            if err:
                print(f"  ERROR: {err[0]['error'][:80]}")
            elif not ok:
                print(f"  [OK] No contrast violations")
            else:
                print(f"  [FAIL] {len(ok)} violations:")
                for v in ok[:8]:
                    tag = v['tag']; ratio = v['ratio']; need = v['need']
                    size = v['size']; weight = v['weight']
                    fg = v['fg']; bg = v['bg']
                    txt = v['text'][:40].encode('ascii', 'replace').decode()
                    print(f"    {ratio}:1 (need {need}) | {tag} {size}px w{weight} | fg={fg} bg={bg} | '{txt}'")

        await browser.close()

        # Save full results
        with open("C:/business-pult/qa/contrast_results.json", "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        total = sum(len([v for v in vs if "error" not in v]) for vs in all_results.values())
        if total == 0:
            print("[OK] All pages pass WCAG contrast requirements")
        else:
            print(f"[FAIL] {total} total violations across {sum(1 for vs in all_results.values() if any('error' not in v for v in vs))} pages")
        print("Full results: C:/business-pult/qa/contrast_results.json")


if __name__ == "__main__":
    asyncio.run(main())

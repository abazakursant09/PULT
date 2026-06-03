const { chromium } = require('playwright');

const BASE = 'http://localhost:3000';

const PAGES = [
  { path: '/', auth: false },
  { path: '/login', auth: false },
  { path: '/privacy', auth: false },
  { path: '/dashboard', auth: true },
  { path: '/profit-calculator', auth: true },
  { path: '/auto-promotions', auth: true },
  { path: '/ai-agents', auth: true },
  { path: '/community', auth: true },
  { path: '/settings/mfa', auth: true },
];

const MOCK_USER = { id: 1, name: 'Test', email: 'test@test.com', plan: 'profi', is_active: true };
const MOCK_TOKEN = 'mock_token_for_contrast_check';

function parseRgb(str) {
  if (!str || str === 'transparent') return null;
  const m = str.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
  if (!m) return null;
  return { r: +m[1], g: +m[2], b: +m[3], a: m[4] !== undefined ? +m[4] : 1 };
}

function toLinear(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function luminance({ r, g, b }) {
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

function contrast(l1, l2) {
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

function blendOnWhite(fg, alpha) {
  return {
    r: Math.round(fg.r * alpha + 255 * (1 - alpha)),
    g: Math.round(fg.g * alpha + 255 * (1 - alpha)),
    b: Math.round(fg.b * alpha + 255 * (1 - alpha)),
  };
}

async function checkPage(page, path) {
  const failures = [];

  const results = await page.evaluate(() => {
    function parseRgb(str) {
      if (!str || str === 'transparent') return null;
      const m = str.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if (!m) return null;
      return { r: +m[1], g: +m[2], b: +m[3], a: m[4] !== undefined ? +m[4] : 1 };
    }

    function toLinear(c) {
      const s = c / 255;
      return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    }

    function luminance({ r, g, b }) {
      return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
    }

    function contrast(l1, l2) {
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
      return (hi + 0.05) / (lo + 0.05);
    }

    function blendWithBackground(fg, bgStack) {
      let blended = { r: fg.r, g: fg.g, b: fg.b };
      if (fg.a < 1) {
        const bg = bgStack.length > 0 ? bgStack[bgStack.length - 1] : { r: 255, g: 255, b: 255 };
        blended = {
          r: Math.round(fg.r * fg.a + bg.r * (1 - fg.a)),
          g: Math.round(fg.g * fg.a + bg.g * (1 - fg.a)),
          b: Math.round(fg.b * fg.a + bg.b * (1 - fg.a)),
        };
      }
      return blended;
    }

    function getEffectiveBg(el) {
      let node = el;
      while (node && node !== document.body.parentElement) {
        const style = window.getComputedStyle(node);
        const bg = parseRgb(style.backgroundColor);
        if (bg && bg.a > 0) {
          if (bg.a < 1) {
            return { r: Math.round(bg.r * bg.a + 28 * (1 - bg.a)), g: Math.round(bg.g * bg.a + 28 * (1 - bg.a)), b: Math.round(bg.b * bg.a + 30 * (1 - bg.a)) };
          }
          return bg;
        }
        node = node.parentElement;
      }
      return { r: 28, g: 28, b: 30 }; // #1C1C1E default
    }

    const seen = new Set();
    const results = [];

    const elements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, a, span, li, button, label, td, th');

    elements.forEach(el => {
      if (!el.offsetParent && el.tagName !== 'BODY') return; // hidden
      const text = el.innerText?.trim();
      if (!text || text.length < 2) return;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      const style = window.getComputedStyle(el);
      const fgRaw = parseRgb(style.color);
      if (!fgRaw) return;

      const bg = getEffectiveBg(el);
      const fg = fgRaw.a < 1 ? { r: Math.round(fgRaw.r * fgRaw.a + bg.r * (1 - fgRaw.a)), g: Math.round(fgRaw.g * fgRaw.a + bg.g * (1 - fgRaw.a)), b: Math.round(fgRaw.b * fgRaw.a + bg.b * (1 - fgRaw.a)) } : fgRaw;

      const L1 = 0.2126 * (fg.r / 255 <= 0.04045 ? (fg.r / 255) / 12.92 : Math.pow(((fg.r / 255) + 0.055) / 1.055, 2.4))
               + 0.7152 * (fg.g / 255 <= 0.04045 ? (fg.g / 255) / 12.92 : Math.pow(((fg.g / 255) + 0.055) / 1.055, 2.4))
               + 0.0722 * (fg.b / 255 <= 0.04045 ? (fg.b / 255) / 12.92 : Math.pow(((fg.b / 255) + 0.055) / 1.055, 2.4));

      const L2 = 0.2126 * (bg.r / 255 <= 0.04045 ? (bg.r / 255) / 12.92 : Math.pow(((bg.r / 255) + 0.055) / 1.055, 2.4))
               + 0.7152 * (bg.g / 255 <= 0.04045 ? (bg.g / 255) / 12.92 : Math.pow(((bg.g / 255) + 0.055) / 1.055, 2.4))
               + 0.0722 * (bg.b / 255 <= 0.04045 ? (bg.b / 255) / 12.92 : Math.pow(((bg.b / 255) + 0.055) / 1.055, 2.4));

      const [hi, lo] = L1 > L2 ? [L1, L2] : [L2, L1];
      const ratio = (hi + 0.05) / (lo + 0.05);

      if (ratio < 4.5) {
        const key = `${fg.r},${fg.g},${fg.b}|${bg.r},${bg.g},${bg.b}`;
        if (!seen.has(key)) {
          seen.add(key);
          const fgHex = '#' + [fg.r, fg.g, fg.b].map(v => v.toString(16).padStart(2, '0')).join('');
          const bgHex = '#' + [bg.r, bg.g, bg.b].map(v => v.toString(16).padStart(2, '0')).join('');
          results.push({ fgHex, bgHex, ratio: Math.round(ratio * 100) / 100, sample: text.substring(0, 60) });
        }
      }
    });

    return results;
  });

  return results;
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();

  console.log('\n=== WCAG CONTRAST AUDIT ===\n');
  const allFailures = {};

  for (const { path, auth } of PAGES) {
    const page = await context.newPage();

    if (auth) {
      await page.goto(BASE + '/login');
      await page.evaluate(({ user, token }) => {
        localStorage.setItem('token', token);
        localStorage.setItem('user', JSON.stringify(user));
      }, { user: MOCK_USER, token: MOCK_TOKEN });
    }

    try {
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 15000 });
      await page.waitForTimeout(1500);
    } catch (e) {
      console.log(`[SKIP] ${path}: ${e.message}`);
      await page.close();
      continue;
    }

    const failures = await checkPage(page, path);

    if (failures.length === 0) {
      console.log(`✅ ${path}: all contrast ok`);
    } else {
      console.log(`❌ ${path}: ${failures.length} unique color pair(s) failing`);
      failures.forEach(f => {
        console.log(`   fg=${f.fgHex} bg=${f.bgHex} ratio=${f.ratio} | "${f.sample}"`);
      });
      allFailures[path] = failures;
    }

    await page.close();
  }

  console.log('\n=== SUMMARY ===');
  const totalPages = Object.keys(allFailures).length;
  if (totalPages === 0) {
    console.log('All pages pass WCAG 4.5:1 contrast!');
  } else {
    console.log(`${totalPages} page(s) have contrast failures.`);
    console.log('\nUnique failing color pairs across all pages:');
    const uniquePairs = new Map();
    Object.entries(allFailures).forEach(([path, failures]) => {
      failures.forEach(f => {
        const key = `${f.fgHex}|${f.bgHex}`;
        if (!uniquePairs.has(key)) uniquePairs.set(key, { ...f, pages: [] });
        uniquePairs.get(key).pages.push(path);
      });
    });
    uniquePairs.forEach(({ fgHex, bgHex, ratio, pages }) => {
      console.log(`  fg=${fgHex} bg=${bgHex} ratio=${ratio} → pages: ${pages.join(', ')}`);
    });
  }

  await browser.close();
})();

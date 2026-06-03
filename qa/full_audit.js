const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:3000';
const API = 'http://localhost:8000';

const PAGES = [
  '/',
  '/login',
  '/register',
  '/dashboard',
  '/dashboard/finance',
  '/dashboard/seo-cards',
  '/dashboard/seo-lab',
  '/dashboard/seo-intelligence',
  '/dashboard/action-engine',
  '/dashboard/import',
  '/dashboard/billing',
  '/dashboard/referrals',
  '/dashboard/account',
  '/dashboard/settings',
  '/profit-calculator',
  '/auto-promotions',
  '/ad-strategy',
  '/ai-agents',
  '/community',
  '/suppliers',
  '/privacy',
  '/agreement',
  '/rules',
  '/offer',
];

const SCREENSHOTS_DIR = '/c/business-pult/qa/screenshots/audit_' + Date.now();
fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

function slugify(p) {
  return p.replace(/\//g, '_').replace(/^_/, '') || 'home';
}

async function checkContrast(page) {
  return page.evaluate(() => {
    const issues = [];
    const els = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, span, button, a, label, li');
    function getLuminance(r, g, b) {
      const [rs, gs, bs] = [r, g, b].map(c => {
        c /= 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
    }
    function getContrastRatio(c1, c2) {
      const l1 = Math.max(c1, c2);
      const l2 = Math.min(c1, c2);
      return (l1 + 0.05) / (l2 + 0.05);
    }
    function parseRGB(str) {
      const m = str.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
      return m ? [+m[1], +m[2], +m[3]] : null;
    }
    let checked = 0;
    els.forEach(el => {
      if (checked > 50) return;
      const style = window.getComputedStyle(el);
      const fg = parseRGB(style.color);
      const bg = parseRGB(style.backgroundColor);
      if (fg && bg && bg[3] !== 0) {
        const ratio = getContrastRatio(
          getLuminance(...fg),
          getLuminance(...bg)
        );
        if (ratio < 4.5 && el.innerText && el.innerText.trim().length > 2) {
          issues.push({ text: el.innerText.trim().slice(0, 40), ratio: ratio.toFixed(2), tag: el.tagName });
          checked++;
        }
      }
    });
    return issues.slice(0, 10);
  });
}

async function checkBrokenLinks(page) {
  const links = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('a[href]'))
      .map(a => a.href)
      .filter(h => h.startsWith('http') && !h.includes('localhost:3000'))
      .slice(0, 5);
  });
  return links;
}

async function getConsoleErrors(page) {
  const errors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text().slice(0, 200));
  });
  return errors;
}

async function auditPage(page, url, consoleErrors) {
  const result = {
    url,
    status: null,
    loads: false,
    title: '',
    hasContent: false,
    contrastIssues: [],
    consoleErrors: [],
    brokenExternalLinks: [],
    screenshot: '',
    mobileScreenshot: '',
    notes: [],
  };

  try {
    const resp = await page.goto(BASE + url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    result.status = resp ? resp.status() : 0;
    result.loads = result.status < 400;

    await page.waitForTimeout(1500);

    result.title = await page.title();
    result.hasContent = await page.evaluate(() => document.body.innerText.trim().length > 50);

    result.contrastIssues = await checkContrast(page);
    result.brokenExternalLinks = await checkBrokenLinks(page);
    result.consoleErrors = [...consoleErrors].slice(0, 5);

    const slug = slugify(url);
    const screenshotPath = path.join(SCREENSHOTS_DIR, slug + '_desktop.png');
    await page.screenshot({ path: screenshotPath, fullPage: true });
    result.screenshot = screenshotPath;

    // Mobile
    await page.setViewportSize({ width: 375, height: 812 });
    await page.waitForTimeout(500);
    const mobileScreenshotPath = path.join(SCREENSHOTS_DIR, slug + '_mobile.png');
    await page.screenshot({ path: mobileScreenshotPath, fullPage: true });
    result.mobileScreenshot = mobileScreenshotPath;
    await page.setViewportSize({ width: 1280, height: 800 });

    if (!result.loads) result.notes.push('HTTP error: ' + result.status);
    if (!result.hasContent) result.notes.push('Empty or near-empty page');
    if (result.contrastIssues.length > 0) result.notes.push(result.contrastIssues.length + ' contrast issues');
    if (result.consoleErrors.length > 0) result.notes.push(result.consoleErrors.length + ' console errors');

  } catch (e) {
    result.loads = false;
    result.notes.push('EXCEPTION: ' + e.message.slice(0, 200));
  }

  return result;
}

async function checkAPI() {
  const results = {};
  const endpoints = [
    '/api/auth/login',
    '/api/products',
    '/api/finance/summary',
    '/api/action-engine/insights',
    '/openapi.json',
  ];
  for (const ep of endpoints) {
    try {
      const r = await fetch(API + ep, { method: 'GET' });
      results[ep] = r.status;
    } catch (e) {
      results[ep] = 'ERROR: ' + e.message;
    }
  }
  return results;
}

async function checkSecurity() {
  const issues = [];

  // Check CORS
  try {
    const r = await fetch(API + '/api/products', {
      headers: { 'Origin': 'http://evil.com' }
    });
    const acao = r.headers.get('access-control-allow-origin');
    if (acao === '*') issues.push('CORS: wildcard * — allows any origin');
    else if (acao === 'http://evil.com') issues.push('CORS: reflects arbitrary origin');
    else issues.push('CORS OK: ' + (acao || 'not set'));
  } catch (e) {
    issues.push('CORS check failed: ' + e.message);
  }

  // Check security headers on frontend
  try {
    const r = await fetch(BASE + '/');
    const headers = {
      'x-content-type-options': r.headers.get('x-content-type-options'),
      'x-frame-options': r.headers.get('x-frame-options'),
      'content-security-policy': r.headers.get('content-security-policy'),
      'strict-transport-security': r.headers.get('strict-transport-security'),
    };
    for (const [h, v] of Object.entries(headers)) {
      if (!v) issues.push('Missing security header: ' + h);
    }
  } catch (e) {
    issues.push('Header check failed: ' + e.message);
  }

  return issues;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();

  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 200));
  });

  console.log('=== BUSINESS-PULT QA AUDIT ===\n');
  console.log('Started: ' + new Date().toISOString());
  console.log('Screenshots: ' + SCREENSHOTS_DIR + '\n');

  // Page audit
  const pageResults = [];
  for (const url of PAGES) {
    process.stdout.write('Auditing ' + url + ' ... ');
    const r = await auditPage(page, url, consoleErrors);
    pageResults.push(r);
    const status = r.loads ? '✓' : '✗';
    console.log(status + ' [' + r.status + ']' + (r.notes.length ? ' — ' + r.notes.join(', ') : ''));
    consoleErrors.length = 0; // reset per page
  }

  // API check
  console.log('\n=== API ENDPOINTS ===');
  const apiResults = await checkAPI();
  for (const [ep, status] of Object.entries(apiResults)) {
    console.log('  ' + ep + ': ' + status);
  }

  // Security check
  console.log('\n=== SECURITY HEADERS ===');
  const secResults = await checkSecurity();
  secResults.forEach(s => console.log('  ' + s));

  await browser.close();

  // Summary report
  const errors = pageResults.filter(r => !r.loads);
  const contrastTotal = pageResults.reduce((s, r) => s + r.contrastIssues.length, 0);
  const consoleErrTotal = pageResults.reduce((s, r) => s + r.consoleErrors.length, 0);
  const emptyPages = pageResults.filter(r => r.loads && !r.hasContent);

  console.log('\n' + '='.repeat(50));
  console.log('SUMMARY');
  console.log('='.repeat(50));
  console.log('Total pages: ' + PAGES.length);
  console.log('Loads OK: ' + pageResults.filter(r => r.loads).length + '/' + PAGES.length);
  console.log('Broken pages: ' + errors.length + ' — ' + errors.map(r => r.url).join(', '));
  console.log('Empty/thin pages: ' + emptyPages.length + ' — ' + emptyPages.map(r => r.url).join(', '));
  console.log('Total contrast issues: ' + contrastTotal);
  console.log('Total console errors: ' + consoleErrTotal);
  console.log('Security issues: ' + secResults.filter(s => !s.includes('OK')).length);

  // Grade
  let score = 100;
  score -= errors.length * 15;
  score -= emptyPages.length * 5;
  score -= Math.min(contrastTotal * 2, 20);
  score -= Math.min(consoleErrTotal, 15);
  score -= secResults.filter(s => !s.includes('OK')).length * 5;
  const grade = score >= 90 ? 'A' : score >= 75 ? 'B' : score >= 60 ? 'C' : score >= 45 ? 'D' : 'F';
  console.log('\nOVERALL GRADE: ' + grade + ' (' + score + '/100)');

  // Detailed problems
  console.log('\n=== DETAILED FINDINGS ===');
  pageResults.forEach(r => {
    if (r.notes.length > 0 || r.contrastIssues.length > 0 || r.consoleErrors.length > 0) {
      console.log('\n[' + r.url + ']');
      r.notes.forEach(n => console.log('  ! ' + n));
      r.contrastIssues.slice(0, 3).forEach(c =>
        console.log('  CONTRAST: "' + c.text + '" ratio=' + c.ratio + ' (need 4.5)')
      );
      r.consoleErrors.slice(0, 3).forEach(e =>
        console.log('  CONSOLE ERR: ' + e.slice(0, 120))
      );
    }
  });

  // Save JSON report
  const reportPath = '/c/business-pult/qa/audit_report.json';
  fs.writeFileSync(reportPath, JSON.stringify({
    date: new Date().toISOString(),
    grade,
    score,
    pages: pageResults,
    api: apiResults,
    security: secResults,
  }, null, 2));
  console.log('\nFull JSON report: ' + reportPath);
}

main().catch(e => { console.error('FATAL:', e); process.exit(1); });

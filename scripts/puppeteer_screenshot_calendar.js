#!/usr/bin/env node
// Puppeteer script to take a screenshot of the no-JS calendar page and
// remove screenshots older than 24 hours.
// Usage:
//   node scripts/puppeteer_screenshot_calendar.js --url https://0.0.0.0:10443/html_no_js/calendar --username mbaily --password my-secret-pass --insecure

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

function parseArgs() {
  const args = require('minimist')(process.argv.slice(2));
  return {
    url: args.url || 'https://0.0.0.0:10443/html_no_js/calendar',
    username: args.username || 'mbaily',
    password: args.password || 'password',
    insecure: !!args.insecure,
    headful: !!args.headful,
  // default screenshots directory at repo root
  screenshotsDir: args.dir || path.resolve(__dirname, '..', 'screenshots'),
  };
}

async function ensureDir(dir) {
  try { await fs.promises.mkdir(dir, { recursive: true }); } catch (e) {}
}

async function cleanOldScreenshots(dir, maxAgeHours = 24) {
  try {
    const files = await fs.promises.readdir(dir);
    const now = Date.now();
    for (const f of files) {
      const fp = path.join(dir, f);
      try {
        const st = await fs.promises.stat(fp);
        if (!st.isFile()) continue;
        const ageMs = now - st.mtimeMs;
        if (ageMs > maxAgeHours * 3600 * 1000) {
          await fs.promises.unlink(fp);
          console.log('removed old screenshot', fp);
        }
      } catch (e) { /* ignore individual errors */ }
    }
  } catch (e) { console.error('failed to clean screenshots dir', e.message); }
}

async function loginPage(page, base, username, password) {
  const loginUrl = new URL('/html_no_js/login', base).toString();
  await page.goto(loginUrl, { waitUntil: 'networkidle2' });
  await page.type('input[name="username"]', username, { delay: 20 });
  await page.type('input[name="password"]', password, { delay: 20 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'networkidle2' }),
  ]);
}

async function main() {
  const opts = parseArgs();
  const base = opts.url.replace(/\/html_no_js\/.*$/, '');
  if (opts.insecure) process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
  await ensureDir(opts.screenshotsDir);
  await cleanOldScreenshots(opts.screenshotsDir, 24);

  const browser = await puppeteer.launch({
    headless: opts.headful ? false : true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    ignoreHTTPSErrors: true,
  });
  try {
    const page = await browser.newPage();
    page.setViewport({ width: 1200, height: 900 });

    // optional console forward
    page.on('console', msg => { try { console.log('PAGE:', msg.text()); } catch (e) {} });

    // login via the no-js form so cookies are set
    await loginPage(page, base, opts.username, opts.password);

    // navigate to calendar and wait for occurrences network call to complete
    await page.goto(opts.url, { waitUntil: 'networkidle2' });

    // try to wait for the occurrences list or a meta placeholder
    await page.waitForSelector('.todos-list, .meta', { timeout: 5000 }).catch(() => {});

    const filename = `calendar_${new Date().toISOString().replace(/[:.]/g,'-')}.png`;
    const outPath = path.join(opts.screenshotsDir, filename);
    await page.screenshot({ path: outPath, fullPage: true });
    console.log('screenshot saved to', outPath);
  } finally {
    await browser.close();
  }
}

main().catch(err => { console.error('unhandled error', err); process.exit(2); });

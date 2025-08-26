#!/usr/bin/env node
// Puppeteer script to load the no-JS calendar, check a task as completed,
// reload, and verify the checkbox remains checked.
// Usage:
//   node scripts/puppeteer_complete_test.js --url https://0.0.0.0:10443/html_no_js/calendar --username mbaily --password my-secret-pass --insecure

const puppeteer = require('puppeteer');

function parseArgs() {
  const args = require('minimist')(process.argv.slice(2));
  return {
    url: args.url || 'https://0.0.0.0:10443/html_no_js/calendar',
    username: args.username || 'mbaily',
    password: args.password || 'password',
    insecure: !!args.insecure,
    headful: !!args.headful
  };
}

async function getToken(base, username, password, insecure) {
  const url = new URL('/auth/token', base).toString();
  if (insecure) process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`auth failed: ${res.status} ${await res.text()}`);
  const j = await res.json();
  return j.access_token;
}

async function main() {
  const opts = parseArgs();
  const base = opts.url.replace(/\/html_no_js\/.*$/, '');
  console.log('base:', base);
  console.log('fetching token for', opts.username);
  try {
    var token = await getToken(base, opts.username, opts.password, opts.insecure);
  } catch (err) {
    console.error('failed to get token:', err.message);
    process.exit(2);
  }
  console.log('got token');
  // We'll perform a browser-based login via the HTML login form so the
  // server renders user-specific occurrences and sets cookies/CSRF.
  const browser = await puppeteer.launch({
    headless: opts.headful ? false : true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    ignoreHTTPSErrors: true,
  });
  const page = await browser.newPage();
  // Forward page console messages to the node process for easier debugging
  page.on('console', msg => {
    try { console.log('PAGE_CONSOLE:', msg.text()); } catch (e) {}
  });
  // Log network responses for occurrences endpoint to capture server-side JSON
  page.on('response', async res => {
    try {
      const url = res.url();
      if (url.includes('/calendar/occurrences')) {
        console.log('PAGE_NETWORK:', res.status(), url);
        try {
          const txt = await res.text();
          console.log('PAGE_NETWORK_BODY_SNIPPET:', txt.slice(0,2000));
        } catch (e) {
          console.log('PAGE_NETWORK_BODY_SNIPPET: <unreadable>');
        }
      }
    } catch (e) {}
  });

  // Intercept requests so we can attach Authorization header to the
  // POSTs to /occurrence/complete and /ignore/scope used by the client JS.
  await page.setRequestInterception(true);
  page.on('request', req => {
    try {
      const url = req.url();
      if (url.includes('/occurrence/complete') || url.includes('/ignore/scope')) {
        const headers = Object.assign({}, req.headers(), { Authorization: `Bearer ${token}` });
        return req.continue({ headers });
      }
    } catch (e) {
      // fall through
    }
    req.continue();
  });

  // Navigate to login page and submit form
  const loginUrl = new URL('/html_no_js/login', base).toString();
  console.log('navigating to login', loginUrl);
  await page.goto(loginUrl, { waitUntil: 'networkidle2' });
  // Fill and submit the login form
  await page.type('input[name="username"]', opts.username, { delay: 20 });
  await page.type('input[name="password"]', opts.password, { delay: 20 });
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'networkidle2' }),
  ]);

  // Now navigate to calendar page
  console.log('navigating to', opts.url);
  await page.goto(opts.url, { waitUntil: 'networkidle2' });

  // Dump a snippet of the rendered HTML to help diagnose missing elements
  try {
    const html = await page.content();
    console.log('PAGE_HTML_SNIPPET:', html.slice(0, 3000));
  } catch (e) {
    console.error('failed to capture page content', e.message);
  }

  // Wait for an occurrence checkbox to appear
  await page.waitForSelector('.occ-complete', { timeout: 10000 }).catch(async () => {
    console.error('no occurrence checkboxes found on the page');
    await browser.close();
    process.exit(3);
  });

  // Find first checkbox and its data-hash
  const { hash, checkedBefore } = await page.evaluate(() => {
    const el = document.querySelector('.occ-complete');
    if (!el) return { hash: null, checkedBefore: null };
    return { hash: el.getAttribute('data-hash'), checkedBefore: el.checked };
  });

  if (!hash) {
    console.error('failed to read occ hash from first checkbox');
    await browser.close();
    process.exit(4);
  }
  console.log('target occ_hash=', hash, 'checkedBefore=', checkedBefore);

  // Click the checkbox to check it if not already checked
  if (!checkedBefore) {
    // Instead of clicking the page control (which relies on cookies/CSRF),
    // obtain the csrf_token cookie and POST directly from the page context
    // to simulate the browser flow.
    const csrf = await page.evaluate(() => {
      const m = document.cookie.match('(^|;)\\s*csrf_token\\s*=\\s*([^;]+)');
      return m ? decodeURIComponent(m.pop()) : null;
    });
    if (!csrf) {
      console.error('no csrf_token cookie present; cannot perform form POST');
      await browser.close();
      process.exit(7);
    }

    // Perform a form-encoded POST to /occurrence/complete from the page
    await page.evaluate(async (h, csrf) => {
      const body = `_csrf=${encodeURIComponent(csrf)}&hash=${encodeURIComponent(h)}`;
      await fetch('/occurrence/complete', { method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: body, credentials: 'same-origin' });
    }, hash, csrf);
    // wait briefly for the server to persist
    await new Promise(r => setTimeout(r, 500));
  } else {
    console.log('already checked; will still reload and verify');
  }

  // Reload the page
  await page.reload({ waitUntil: 'networkidle2' });

  // After reload, locate the checkbox with same data-hash and read its checked state
  const checkedAfter = await page.evaluate(h => {
    const el = document.querySelector(`.occ-complete[data-hash="${h}"]`);
    return el ? el.checked : null;
  }, hash);

  console.log('checkedAfter=', checkedAfter);

  await browser.close();

  if (checkedAfter === true) {
    console.log('SUCCESS: occurrence remained checked after reload');
    process.exit(0);
  } else if (checkedAfter === false) {
    console.error('FAIL: occurrence not checked after reload');
    process.exit(5);
  } else {
    console.error('FAIL: occurrence checkbox not found after reload');
    process.exit(6);
  }
}

main().catch(err => { console.error('unhandled error', err); process.exit(10); });

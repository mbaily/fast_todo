#!/usr/bin/env node
const puppeteer = require('puppeteer');
const minimist = require('minimist');

(async ()=>{
  const argv = minimist(process.argv.slice(2));
  const base = argv.url || 'https://127.0.0.1:10443';
  const user = argv.username || 'dev_user';
  const pass = argv.password || 'dev';
  const targetText = argv.text || 'Testing watering pot plant 5/8 5/9 5/10 5/11';
  const year = argv.year || '2030';
  const month = argv.month || '11';
  const host = argv.host || null; // optional alternate host for diagnostics (e.g., https://0.0.0.0:10443)

  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox','--ignore-certificate-errors'] });
  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE:', msg.text()));

  try{
    const fs = require('fs');
    const cookieFile = 'screenshots/real_browser_diag.json';
    let usedRealCookies = false;
    if (fs.existsSync(cookieFile)){
      try{
        const raw = JSON.parse(fs.readFileSync(cookieFile));
        if (raw && raw.userAgent){
          try{ await page.setUserAgent(raw.userAgent); console.log('SET_USER_AGENT_FROM_FILE'); }catch(e){ console.log('SET_UA_ERR', String(e)); }
        }
        if (raw && Array.isArray(raw.cookies) && raw.cookies.length){
          const setCookies = raw.cookies.map(c=>({
            name: c.name,
            value: c.value,
            domain: c.domain || undefined,
            path: c.path || '/',
            httpOnly: !!c.httpOnly,
            secure: !!c.secure,
            sameSite: (c.sameSite && typeof c.sameSite === 'string') ? c.sameSite : undefined,
          }));
          // set cookies before navigation
          await page.setCookie(...setCookies);
          console.log('SET_COOKIES_FROM_FILE:', cookieFile);
          usedRealCookies = true;
        }
      }catch(e){ console.log('COOKIE_LOAD_ERR', String(e)); }
    }

    let finalBase = host || base;
    if (!usedRealCookies) {
      // login via token endpoint
      await page.goto(base + '/auth/token', { waitUntil: 'networkidle0' });
      const token = await page.evaluate(async (base,user,pass)=>{
        const res = await fetch(base + '/auth/token', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({username:user,password:pass}) });
        const j = await res.json(); return j.access_token;
      }, base, user, pass);

      await page.setExtraHTTPHeaders({ 'Authorization': `Bearer ${token}` });
    }
    const url = `${finalBase}/html_no_js/calendar?year=${year}&month=${month}`;
    console.log('navigating to', url);
    // collect network responses for the occurrences endpoint
    const occResponses = [];
    page.on('response', async (res) => {
      try{
        const u = res.url();
        if (u.indexOf('/calendar/occurrences') !== -1) {
          let text = '';
          try{ text = await res.text(); }catch(e){ text = '<non-text-response>'; }
          occResponses.push({url: u, status: res.status(), text: text});
        }
      }catch(e){}
    });

  await page.goto(url, { waitUntil: 'networkidle0' });
    await new Promise(r => setTimeout(r, 1200));

    // capture screenshot for debugging
    try{
      const path = 'screenshots/check2030.png';
      await page.screenshot({ path: path, fullPage: true });
      console.log('SCREENSHOT_SAVED:', path);
    }catch(e){ console.log('SCREENSHOT_ERR', String(e)); }

    // gather diagnostics: cookies, ua, localStorage, captured occResponses
    const cookies = await page.cookies();
    const userAgent = await page.evaluate(()=>navigator.userAgent);
    const localStorageDump = await page.evaluate(() => {
      try {
        const o = {};
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          o[k] = localStorage.getItem(k);
        }
        return o;
      } catch (e) {
        return { error: String(e) };
      }
    });

    // Also perform a direct fetch from page context to /calendar/occurrences using same base
    const directFetch = await page.evaluate(async (baseYear, baseMonth, baseUrl) => {
      try{
        const start = new Date(baseYear, baseMonth-1, 1).toISOString();
        const end = new Date(baseYear, baseMonth, 0); // last day
        const endIso = new Date(end.getFullYear(), end.getMonth(), end.getDate(),23,59,59).toISOString();
        const q = `${baseUrl}/calendar/occurrences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(endIso)}&expand=true`;
        const res = await fetch(q, { credentials: 'same-origin' });
        const txt = await res.text();
        return {status: res.status, text: txt};
      }catch(e){ return {error: String(e)} }
    }, parseInt(year,10), parseInt(month,10), finalBase);

    // Search the DOM for the exact todo text. Try multiple strategies: text nodes, title attributes, and list items.
  const result = await page.evaluate((txt) => {
      // Helper: search for nodes containing exact substring
      function findByText(node, search) {
        const iterator = document.createNodeIterator(document.body, NodeFilter.SHOW_TEXT, null);
        let cur;
        while (cur = iterator.nextNode()) {
          if (cur.nodeValue && cur.nodeValue.indexOf(search) !== -1) {
            // return the closest element container
            let el = cur.parentElement;
            return el ? el.outerHTML : null;
          }
        }
        return null;
      }

      // direct match
  const found = findByText(txt);
  if (found) return {found: true, html: found, selector: null, path: null};

      // fallback: search for elements with title or data-title
      const els = Array.from(document.querySelectorAll('[title],[data-title]'));
      for (const el of els) {
        if ((el.getAttribute('title')||'').indexOf(txt) !== -1 || (el.getAttribute('data-title')||'').indexOf(txt) !== -1) {
          // build a simple CSS path for the element
          function cssPath(el){
            const parts = [];
            while(el && el.nodeType===1 && el !== document.body){
              let part = el.tagName.toLowerCase();
              if (el.id) part += '#'+el.id;
              else if (el.className) part += '.'+Array.from(el.classList).join('.');
              parts.unshift(part);
              el = el.parentElement;
            }
            return parts.join(' > ');
          }
          return {found: true, html: el.outerHTML, selector: null, path: cssPath(el)};
        }
      }

      return {found: false};
    }, targetText);

    // Compact JSON output for machine parsing
  const out = { found: !!(result && result.found), text: targetText, selector: result && result.selector ? result.selector : null, elementPath: result && result.path ? result.path : null, outerHTML: result && result.html ? (result.html.length>1000? result.html.slice(0,1000)+'...': result.html) : null };
  // diagnostics
  const diag = { cookies, userAgent, localStorage: localStorageDump, occResponses, directFetch, domSearch: out };
  try{
    const fs = require('fs');
    fs.writeFileSync('screenshots/puppeteer_headless_diag.json', JSON.stringify(diag, null, 2));
    console.log('DIAG_SAVED: screenshots/puppeteer_headless_diag.json');
  }catch(e){ console.log('DIAG_SAVE_ERR', String(e)); }
  console.log(JSON.stringify(out));
  await browser.close();
  process.exit(out.found?0:1);
  }catch(e){ console.error('ERROR', e); try{ await browser.close(); }catch(_){ } process.exit(2); }
})();

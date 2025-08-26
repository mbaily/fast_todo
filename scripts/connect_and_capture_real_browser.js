#!/usr/bin/env node
const puppeteer = require('puppeteer-core');
const fs = require('fs');

(async ()=>{
  try{
    const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222' });
    const pages = await browser.pages();
    // find a page with the calendar URL (2030-11)
    let page = pages.find(p=> (p.url()||'').includes('/html_no_js/calendar') && p.url().includes('year=2030') );
    if (!page){
      // if not found, open a new page and navigate
      page = await browser.newPage();
      const url = 'https://0.0.0.0:10443/html_no_js/calendar?year=2030&month=11';
      console.log('Opening', url);
      await page.goto(url, { waitUntil: 'networkidle0' });
      await new Promise(r=>setTimeout(r, 800));
    }

    page.on('console', msg => console.log('PAGE:', msg.text()));

    const outPath = 'screenshots/real_browser_connected.png';
    await page.screenshot({ path: outPath, fullPage: true });
    console.log('SCREENSHOT_SAVED:', outPath);

    const cookies = await page.cookies();
    const userAgent = await page.evaluate(()=>navigator.userAgent);
    const localStorageDump = await page.evaluate(()=>{ try{ const o={}; for(let i=0;i<localStorage.length;i++){ const k=localStorage.key(i); o[k]=localStorage.getItem(k);} return o;}catch(e){return {error:String(e)}} });

    // fetch occurrences from the page context
    const directFetch = await page.evaluate(async ()=>{
      try{
        const start = new Date(2030,10,1).toISOString();
        const end = new Date(2030,10,30,23,59,59).toISOString();
        const q = `/calendar/occurrences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&expand=true`;
        const r = await fetch(q, { credentials: 'same-origin' });
        const txt = await r.text();
        return { status: r.status, text: txt };
      }catch(e){ return { error: String(e) }; }
    });

    const diag = { cookies, userAgent, localStorage: localStorageDump, directFetch };
    fs.writeFileSync('screenshots/real_browser_diag.json', JSON.stringify(diag, null, 2));
    console.log('DIAG_SAVED: screenshots/real_browser_diag.json');

    await browser.disconnect();
    process.exit(0);
  }catch(e){ console.error('ERR', e); process.exit(2); }
})();

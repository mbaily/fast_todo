#!/usr/bin/env node
const puppeteer = require('puppeteer-core');

(async ()=>{
  try{
    const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222' });
    const pages = await browser.pages();
    let page = pages.find(p=> (p.url()||'').includes('/html_no_js/calendar') && p.url().includes('year=2030') );
    if (!page){
      page = await browser.newPage();
      await page.goto('https://0.0.0.0:10443/html_no_js/calendar?year=2030&month=11', { waitUntil: 'networkidle0' });
      await new Promise(r=>setTimeout(r, 800));
    }
    const res = await page.evaluate(async ()=>{
      try{
        const r = await fetch('/todos/55', { credentials: 'same-origin' });
        const txt = await r.text();
        return { status: r.status, text: txt };
      }catch(e){ return { error: String(e) }; }
    });
    const fs = require('fs');
    fs.writeFileSync('screenshots/todo_55_fetch.json', JSON.stringify(res, null, 2));
    console.log('SAVED: screenshots/todo_55_fetch.json');
    await browser.disconnect();
    process.exit(0);
  }catch(e){ console.error('ERR', e); process.exit(2); }
})();

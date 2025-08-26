#!/usr/bin/env node
const puppeteer = require('puppeteer');
const minimist = require('minimist');

(async ()=>{
  const argv = minimist(process.argv.slice(2));
  const base = argv.url || 'https://127.0.0.1:10443';
  const user = argv.username || 'dev_user';
  const pass = argv.password || 'dev';
  const year = argv.year || '2030';
  const month = argv.month || '11';
  const out = argv.out || 'puppeteer_visit_2030.jsonl';

  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox','--ignore-certificate-errors'] });
  const page = await browser.newPage();
  page.on('console', msg => {
    console.log('PAGE:', msg.text());
  });

  try{
    // login via token endpoint
    await page.goto(base + '/auth/token', { waitUntil: 'networkidle0' });
    const token = await page.evaluate(async (base,user,pass)=>{
      const res = await fetch(base + '/auth/token', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({username:user,password:pass}) });
      const j = await res.json(); return j.access_token;
    }, base, user, pass);

    // set Authorization header and navigate to calendar for requested year/month
    await page.setExtraHTTPHeaders({ 'Authorization': `Bearer ${token}` });
    const url = `${base}/html_no_js/calendar?year=${year}&month=${month}`;
    console.log('navigating to', url);
    await page.goto(url, { waitUntil: 'networkidle0' });
    // small delay to allow client-side requests (and server SSE/debug) to happen
    await new Promise(r => setTimeout(r, 2000));

    await browser.close();
    console.log('done');
  }catch(e){ console.error(e); try{ await browser.close(); }catch(_){ } process.exit(2); }
})();

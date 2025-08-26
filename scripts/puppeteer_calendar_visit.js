#!/usr/bin/env node
const puppeteer = require('puppeteer');
const minimist = require('minimist');

(async ()=>{
  const argv = minimist(process.argv.slice(2));
  const base = argv.url || 'https://0.0.0.0:10443';
  const user = argv.username || 'dev_user';
  const pass = argv.password || 'dev';
  const out = argv.out || 'puppeteer_calendar_visit.jsonl';
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
    // navigate to calendar page with token as cookie to simulate logged session
    await page.setExtraHTTPHeaders({ 'Authorization': `Bearer ${token}` });
  await page.goto(base + '/html_no_js/calendar', { waitUntil: 'networkidle0' });
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  await sleep(1500);
    await browser.close();
    console.log('done');
  }catch(e){ console.error(e); await browser.close(); process.exit(2); }
})();

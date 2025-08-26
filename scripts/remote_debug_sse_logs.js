#!/usr/bin/env node
const puppeteer = require('puppeteer-core');
const fs = require('fs');

(async ()=>{
  try{
    const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222' });
    const pages = await browser.pages();
    let page = pages.find(p=> (p.url()||'').includes('/html_no_js/calendar') && p.url().includes('year=2030'));
    const base = 'https://0.0.0.0:10443';
    if (!page){ page = await browser.newPage(); await page.goto(base + '/html_no_js/calendar?year=2030&month=11', { waitUntil: 'networkidle0' }); }
    page.on('console', m => console.log('PAGE:', m.text()));

    // fetch server logs via same-origin fetch
    const logs = await page.evaluate(async (baseUrl)=>{
      try{
        const r = await fetch(baseUrl + '/server/logs?limit=200');
        return await r.json();
      }catch(e){ return {error: String(e)}; }
    }, base);
    fs.writeFileSync('screenshots/remote_server_logs.json', JSON.stringify(logs, null, 2));
    console.log('SAVED: screenshots/remote_server_logs.json');

    // attach an in-page SSE recorder for server logs stream
    await page.evaluate((sseUrl)=>{
      window.__remote_sse = [];
      try{
        const es = new EventSource(sseUrl);
        es.addEventListener('log', ev => { window.__remote_sse.push({event:'log', data: ev.data, ts: new Date().toISOString()}); });
        es.addEventListener('message', ev => { window.__remote_sse.push({event:'message', data: ev.data, ts: new Date().toISOString()}); });
        es.addEventListener('error', ev => { window.__remote_sse.push({event:'error', data: String(ev), ts: new Date().toISOString()}); });
        window.__remote_sse_es = es;
      }catch(e){ window.__remote_sse.push({event:'inject_err', err:String(e)}); }
    }, base + '/server/logs/stream');

    // trigger an occurrences fetch to cause server to emit events
    await page.evaluate(async ()=>{
      const start = new Date(2030,10,1).toISOString();
      const end = new Date(2030,10,30,23,59,59).toISOString();
      await fetch(`/calendar/occurrences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&expand=true`, { credentials: 'same-origin' });
    });

    // wait a bit for SSE messages to arrive
    await new Promise(r=>setTimeout(r, 1200));
    const sse = await page.evaluate(()=> window.__remote_sse || []);
    fs.writeFileSync('screenshots/remote_sse_events.json', JSON.stringify(sse, null, 2));
    console.log('SAVED: screenshots/remote_sse_events.json');

    // close SSE
    await page.evaluate(()=>{ try{ if(window.__remote_sse_es) window.__remote_sse_es.close(); }catch(e){} });
    await browser.disconnect();
    process.exit(0);
  }catch(e){ console.error('ERR', e); process.exit(2); }
})();

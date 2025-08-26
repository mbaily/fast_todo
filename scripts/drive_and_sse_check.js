#!/usr/bin/env node
const puppeteer = require('puppeteer-core');
const fs = require('fs');

(async ()=>{
  try{
    const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222' });
    const pages = await browser.pages();
    // prefer an existing calendar tab if present
    let page = pages.find(p=> (p.url()||'').includes('/html_no_js/calendar') && p.url().includes('year=2030') );
    const finalBase = 'https://0.0.0.0:10443';
    // If no existing calendar page, open a blank page first, attach SSE recorder, then navigate
    if (!page){
      page = await browser.newPage();
      // navigate to blank first so we can attach EventSource before the calendar page emits events
      await page.goto('about:blank');
    }

    page.on('console', msg => console.log('PAGE:', msg.text()));

  // inject SSE recorder in page context after navigation to ensure same-origin and certs match
  await page.evaluate((sseUrl) => {
      try{
        window.__sseLog = [];
        if (window.__sseRecorder) { try{ window.__sseRecorder.close(); }catch(e){} }
        let es;
        try{
          es = new EventSource(sseUrl);
        }catch(e){
          window.__sseLog = [{event:'es_construct_error', error: String(e), ts: new Date().toISOString()}];
          return;
        }
        window.__sseRecorder = es;
        function pushEvent(name, ev){
          try{
            const payload = ev.data || '';
            window.__sseLog.push({event: name, data: payload, ts: new Date().toISOString()});
          }catch(e){ window.__sseLog.push({event:name, err:String(e)}); }
        }
        es.addEventListener('message', ev => pushEvent('message', ev));
        es.addEventListener('log', ev => pushEvent('log', ev));
        es.addEventListener('error', ev => { window.__sseLog.push({event:'error', data: String(ev), ts: new Date().toISOString()}); });
      }catch(e){ window.__sseLog = [{event:'inject_error', error: String(e)}]; }
    }, finalBase + '/server/logs/stream');
    // navigate to calendar then inject SSE recorder in the page context (same-origin)
    await page.bringToFront();
    await page.goto(finalBase + '/html_no_js/calendar?year=2030&month=11', { waitUntil: 'domcontentloaded' });

    // now attach the SSE recorder in the real page origin
    await page.evaluate((sseUrl) => {
      try{
        window.__sseLog = window.__sseLog || [];
        if (window.__sseRecorder) { try{ window.__sseRecorder.close(); }catch(e){} }
        const es = new EventSource(sseUrl);
        window.__sseRecorder = es;
        function pushEvent(name, ev){ try{ const payload = ev.data || ''; window.__sseLog.push({event: name, data: payload, ts: new Date().toISOString()}); }catch(e){ window.__sseLog.push({event:name, err:String(e)}); } }
        es.addEventListener('message', ev => pushEvent('message', ev));
        es.addEventListener('log', ev => pushEvent('log', ev));
        es.addEventListener('error', ev => { window.__sseLog.push({event:'error', data: String(ev), ts: new Date().toISOString()}); });
      }catch(e){ window.__sseLog = (window.__sseLog||[]).concat([{event:'inject_error', error: String(e)}]); }
    }, finalBase + '/server/logs/stream');

    // give SSE a short window to receive messages
    await new Promise(r=>setTimeout(r, 2500));

    // capture screenshot
    const shotPath = 'screenshots/real_browser_drive.png';
    await page.screenshot({ path: shotPath, fullPage: true });
    console.log('SCREENSHOT_SAVED:', shotPath);

    // collect DOM search for target text and occurrences via page fetch
    const targetText = 'Testing watering pot plant 5/8 5/9 5/10 5/11';
    const domSearch = await page.evaluate((txt) => {
      // skip nodes under script/style/noscript
      function textNodes(root){
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
        const nodes = [];
        let n;
        while(n = walker.nextNode()){
          const p = n.parentElement;
          if (!p) continue;
          const tag = p.tagName && p.tagName.toUpperCase();
          if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') continue;
          nodes.push(n);
        }
        return nodes;
      }
      const nodes = textNodes(document.body);
      for (const n of nodes){
        if (n.nodeValue && n.nodeValue.indexOf(txt) !== -1){
          return { found:true, html: n.parentElement ? n.parentElement.outerHTML : null };
        }
      }
      // check title/data-title attributes and data-occurrence-id
      const els = Array.from(document.querySelectorAll('[title],[data-title],[data-occurrence-id]'));
      for (const el of els){
        if ((el.getAttribute('title')||'').indexOf(txt) !== -1 || (el.getAttribute('data-title')||'').indexOf(txt) !== -1) {
          return { found: true, html: el.outerHTML };
        }
        if (el.getAttribute('data-occurrence-id')){
          if ((el.textContent||'').indexOf(txt)!==-1) return { found:true, html: el.outerHTML };
        }
      }
      return { found: false, html: null };
    }, targetText);

    const directFetch = await page.evaluate(async ()=>{
      try{
        const start = new Date(2030,10,1).toISOString();
        const end = new Date(2030,10,30,23,59,59).toISOString();
        const q = `/calendar/occurrences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&expand=true`;
        const r = await fetch(q, { credentials: 'same-origin' });
        return { status: r.status, text: await r.text() };
      }catch(e){ return { error: String(e) }; }
    });

    // read SSE log from page
    const sseLog = await page.evaluate(()=> window.__sseLog || []);

    fs.writeFileSync('screenshots/drive_dom.json', JSON.stringify({ domSearch, directFetch }, null, 2));
    fs.writeFileSync('screenshots/drive_sse.json', JSON.stringify(sseLog, null, 2));
    console.log('SAVED: screenshots/drive_dom.json and screenshots/drive_sse.json');

    // close recorder but keep browser
    await page.evaluate(()=>{ try{ if(window.__sseRecorder) window.__sseRecorder.close(); }catch(e){} });
    await browser.disconnect();
    process.exit(0);
  }catch(e){ console.error('ERR', e); process.exit(2); }
})();

#!/usr/bin/env node
// Puppeteer script that connects to server SSE log stream and captures page console
const puppeteer = require('puppeteer');
const fs = require('fs');
const { request, Agent } = require('undici');

(async ()=>{
  const argv = require('minimist')(process.argv.slice(2));
  const base = argv.url || 'https://0.0.0.0:10443';
  const out = argv.out || 'puppeteer_stream_test.jsonl';
  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox','--ignore-certificate-errors'] });
  const page = await browser.newPage();
  page.on('console', msg => {
    const text = msg.text();
    const entry = {source: 'page', ts: new Date().toISOString(), text};
    console.log('PAGE:', text);
    fs.appendFileSync(out, JSON.stringify(entry) + '\n');
  });
  // create undici agent that accepts self-signed certs (dev only)
  const undiciAgent = new Agent({ connect: { rejectUnauthorized: false } });

  // open SSE via undici and parse stream
  const sseUrl = base + '/server/logs/stream';
  let sseDone = false;
  (async ()=>{
    try{
      const { body } = await request(sseUrl, { method: 'GET', headers: { Accept: 'text/event-stream' }, bodyTimeout: 0, headersTimeout: 0, keepAliveTimeout: 60000, dispatcher: undiciAgent });
      let buf = '';
      if(body && typeof body.getReader === 'function'){
        // Web ReadableStream
        const reader = body.getReader();
        while(true){
          const { done, value } = await reader.read();
          if(done) break;
          buf += Buffer.from(value).toString('utf8');
          // parse any complete SSE events
          let idx;
          while((idx = buf.indexOf('\n\n')) !== -1){
            const raw = buf.slice(0, idx).trim();
            buf = buf.slice(idx+2);
            // process raw event block
            const lines = raw.split(/\r?\n/);
            let event = null; let data = '';
            for(const l of lines){
              if(l.startsWith('event:')) event = l.slice(6).trim();
              if(l.startsWith('data:')) data += l.slice(5).trim();
            }
            if(event === 'log'){
              try{
                const obj = JSON.parse(data);
                const entry = {source: 'server', ts: new Date().toISOString(), data: obj};
                console.log('SERVER:', obj.level, obj.message);
                fs.appendFileSync(out, JSON.stringify(entry) + '\n');
              }catch(e){ console.error('SSE parse error', e); }
            }
          }
        }
      }else{
        // Node.js Readable stream - use async iterator
        for await (const chunk of body){
          buf += Buffer.from(chunk).toString('utf8');
          let idx;
          while((idx = buf.indexOf('\n\n')) !== -1){
            const raw = buf.slice(0, idx).trim();
            buf = buf.slice(idx+2);
            const lines = raw.split(/\r?\n/);
            let event = null; let data = '';
            for(const l of lines){
              if(l.startsWith('event:')) event = l.slice(6).trim();
              if(l.startsWith('data:')) data += l.slice(5).trim();
            }
            if(event === 'log'){
              try{
                const obj = JSON.parse(data);
                const entry = {source: 'server', ts: new Date().toISOString(), data: obj};
                console.log('SERVER:', obj.level, obj.message);
                fs.appendFileSync(out, JSON.stringify(entry) + '\n');
              }catch(e){ console.error('SSE parse error', e); }
            }
          }
        }
      }
    }catch(e){
      console.error('SSE connect error', e);
    }finally{
      sseDone = true;
    }
  })();

  try{
    // navigate to base to trigger any client-side console logs
    await page.goto(base + '/', { waitUntil: 'networkidle0' });
    // wait a short while to collect logs
  const sleep = (ms) => new Promise(r=>setTimeout(r, ms));
  await sleep(3000);
    // optionally run a simple script that logs to console
    await page.evaluate(() => console.log('puppeteer-test-console-log'));
  await sleep(1000);
    // close
  // no-op: SSE connection will be closed when the Node process exits or reader completes
    await browser.close();
    console.log('done');
    process.exit(0);
  }catch(e){
    console.error(e);
    es.close();
    await browser.close();
    process.exit(2);
  }
})();

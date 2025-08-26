#!/usr/bin/env node
// Puppeteer script: login and fetch /calendar/occurrences for given months, verify seeded phrases
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

async function loginAndGetToken(page, base, username, password) {
  // use API token login for simplicity
  const resp = await page.goto(base + '/auth/token', { waitUntil: 'networkidle0' });
  // fetch via navigator fetch to POST JSON
  const token = await page.evaluate(async (base, username, password) => {
    const res = await fetch(base + '/auth/token', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({username, password}), credentials: 'omit' });
    if (!res.ok) throw new Error('login failed ' + res.status);
    const j = await res.json();
    return j.access_token;
  }, base, username, password);
  return token;
}

async function fetchOccurrencesToken(base, token, year, month) {
  const url = `${base}/calendar/occurrences?start=${encodeURIComponent(new Date(Date.UTC(year, month-1,1)).toISOString())}&end=${encodeURIComponent(new Date(Date.UTC(year, month, new Date(Date.UTC(year, month,0)).getUTCDate(), 23,59,59)).toISOString())}`;
  const res = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
  if (!res.ok) throw new Error('fetch occurrences failed ' + res.status);
  return await res.json();
}

(async ()=>{
  const argv = require('minimist')(process.argv.slice(2));
  const base = argv.url || 'https://0.0.0.0:10443';
  const user = argv.username || 'dev_user';
  const pass = argv.password || 'dev';
  const months = argv.months || '2025-08,2025-09,2025-10';
  const out = argv.out || 'puppeteer_recurrence_report.json';
  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox','--ignore-certificate-errors'] });
  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE:', msg.text()));
  try {
    const token = await loginAndGetToken(page, base, user, pass);
    const monthsList = months.split(',');
    const results = {};
    for (const m of monthsList) {
      const [y, mm] = m.split('-').map(Number);
      console.log('Fetching', y, mm);
      const data = await fetchOccurrencesToken(base, token, y, mm);
      results[m] = data.occurrences || [];
    }
    fs.writeFileSync(out, JSON.stringify(results, null, 2));
    console.log('Wrote', out);
    await browser.close();
    process.exit(0);
  } catch (e) {
    console.error(e);
    await browser.close();
    process.exit(2);
  }
})();

#!/usr/bin/env node
// Use node's fetch to get an auth token and query /calendar/occurrences for months
const fs = require('fs');
const path = require('path');

async function getToken(base, username, password) {
  const res = await fetch(base + '/auth/token', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username, password}),
  });
  if (!res.ok) throw new Error('login failed ' + res.status + ' ' + (await res.text()));
  const j = await res.json();
  return j.access_token;
}

async function fetchMonth(base, token, year, month) {
  const start = new Date(Date.UTC(year, month-1, 1)).toISOString();
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const end = new Date(Date.UTC(year, month-1, lastDay, 23,59,59)).toISOString();
  const url = `${base}/calendar/occurrences?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error('fetch occurrences failed ' + res.status + ' ' + (await res.text()));
  return await res.json();
}

(async ()=>{
  try {
    const argv = require('minimist')(process.argv.slice(2));
    const base = argv.url || 'https://0.0.0.0:10443';
    const user = argv.username || 'dev_user';
    const pass = argv.password || 'dev';
    const months = argv.months ? argv.months.split(',') : ['2025-08','2025-09','2025-10'];
    const phrasesFile = argv.phrases || path.join(__dirname, '..', 'tests', 'recurrence_phrases.json');
    const out = argv.out || path.join(__dirname, 'recurrence_api_report.json');
    const phrases = JSON.parse(fs.readFileSync(phrasesFile, 'utf8')).map(x => typeof x === 'string' ? x : x.text);

    const token = await getToken(base, user, pass);
    const report = {};
    for (const p of phrases) report[p] = {};

    for (const m of months) {
      const [y, mm] = m.split('-').map(Number);
      console.log('fetching', m);
      const data = await fetchMonth(base, token, y, mm);
      const occs = data.occurrences || [];
      // for each phrase, count occurrences where title contains the phrase (case-insensitive)
      for (const p of phrases) {
        const low = p.toLowerCase();
        const count = occs.filter(o => (o.title || '').toLowerCase().includes(low)).length;
        report[p][m] = count;
      }
    }
    fs.writeFileSync(out, JSON.stringify(report, null, 2));
    console.log('Wrote', out);
    process.exit(0);
  } catch (e) {
    console.error('ERROR', e.message);
    process.exit(2);
  }
})();

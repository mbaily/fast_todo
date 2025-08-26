This folder contains a minimal Progressive Web App (PWA) client scaffold.

Files:
- index.html — simple UI that registers the service worker and uses IndexedDB to queue ops.
- static/pwa/manifest.json — web manifest (served under /static/pwa/ to be mounted by the app).
- static/pwa/service-worker.js — basic SW to serve offline shell and retry sync via background sync (if available).
- static/pwa/sw-register.js — registers the service worker and monitors network state.
- static/pwa/app.js — tiny client logic: IndexedDB queue, create op, and sync via /sync.

This is a scaffold to iterate on. It expects the server to expose /sync and accept credentialed requests from the SW (cookies).

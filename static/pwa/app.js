// Minimal client logic: IndexedDB queue for offline ops, create_todo op, and sync function

const DB_NAME = 'fast_todo_pwa';
const DB_VERSION = 1;
let db;
let serverDefaultListId = null;
let cachedLists = [];
let selectedListId = null;

// Ensure the server default list is known. If the server reports no default,
// attempt to create a simple default list so client creates have a target.
async function ensureServerDefaultList() {
  // if we've already resolved it, return quickly
  if (serverDefaultListId) return serverDefaultListId;
  try {
    const resp = await safeFetch('/server/default_list', { credentials: 'include' }, 8000);
    if (resp && resp.ok) {
      const data = await resp.json();
      if (data && data.id) {
        serverDefaultListId = Number(data.id);
        return serverDefaultListId;
      }
    }
    // not found or no default set: try to create a new list (best-effort)
    const createResp = await safeFetch('/lists', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ name: 'Default' }) }, 10000);
    if (createResp && createResp.ok) {
      const created = await createResp.json();
      if (created && created.id) {
        serverDefaultListId = Number(created.id);
        return serverDefaultListId;
      }
    }
  } catch (err) {
    console.debug('could not resolve/create server default list', err);
  }
  return null;
}

function openDb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB_NAME, DB_VERSION);
    r.onupgradeneeded = (e) => {
      const idb = e.target.result;
      if (!idb.objectStoreNames.contains('ops')) {
        const store = idb.createObjectStore('ops', { keyPath: 'id' });
        // index by next_retry_at so the scheduler can find due ops efficiently
        store.createIndex('next_retry_at', 'next_retry_at', { unique: false });
      } else {
        // ensure index exists for older DB versions
        const store = e.target.transaction.objectStore('ops');
        if (!store.indexNames.contains('next_retry_at')) {
          try { store.createIndex('next_retry_at', 'next_retry_at', { unique: false }); } catch (e) { /* best-effort */ }
        }
      }
    };
    r.onsuccess = () => { db = r.result; res(db); };
    r.onerror = () => rej(r.error);
  });
}

function addOp(op) {
  return new Promise((res, rej) => {
    const tx = db.transaction('ops', 'readwrite');
    const store = tx.objectStore('ops');
    // ensure metadata fields exist so retry state survives reloads
    if (typeof op.attempts === 'undefined') op.attempts = 0;
    if (typeof op.last_error === 'undefined') op.last_error = null;
  // next_retry_at: immediately eligible by default
  if (typeof op.next_retry_at === 'undefined') op.next_retry_at = null;
  // created_at: timestamp for UI/aging
  if (typeof op.created_at === 'undefined') op.created_at = new Date().toISOString();
    store.add(op);
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}

// wakeable scheduler: a simple promise that resolves when new ops are added
let _schedulerWake = null;
function wakeScheduler() {
  try {
    if (_schedulerWake) {
      _schedulerWake();
      _schedulerWake = null;
    }
  } catch (e) { /* ignore */ }
}

// Update op metadata (attempts, last_error, etc.) in IndexedDB
async function setOpMetadata(opId, fields) {
  await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('ops', 'readwrite');
    const store = tx.objectStore('ops');
    const req = store.get(opId);
    req.onsuccess = (e) => {
      const cur = e.target.result;
      if (!cur) {
        resolve(false);
        return;
      }
  const prevNext = cur.next_retry_at;
  Object.assign(cur, fields);
  try { store.put(cur); } catch (er) { /* best-effort */ }
  // if next_retry_at was updated, wake scheduler so it can re-evaluate
  try { if (fields && fields.next_retry_at && fields.next_retry_at !== prevNext) wakeScheduler(); } catch (e) { /* ignore */ }
    };
    tx.oncomplete = () => { updateOpsUI().catch(() => {}); resolve(true); };
    tx.onerror = () => reject(tx.error);
  });
}

// human-friendly ETA formatter for ISO timestamps
function formatEta(iso) {
  if (!iso) return 'now';
  try {
    const when = new Date(iso).getTime();
    const delta = when - Date.now();
    if (delta <= 0) return 'now';
    const s = Math.round(delta / 1000);
    if (s < 60) return `in ${s}s`;
    const m = Math.round(s / 60);
    if (m < 60) return `in ${m}m`;
    const h = Math.round(m / 60);
    return `in ${h}h`;
  } catch (e) { return iso; }
}

function formatAge(iso) {
  if (!iso) return '';
  try {
    const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 5) return 'just now';
    if (s < 60) return `${s}s ago`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    return `${h}h ago`;
  } catch (e) { return ''; }
}

// Increment attempts for a batch of ops and annotate with last_error
async function incrementAttemptsForBatch(batch, errMsg) {
  await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('ops', 'readwrite');
    const store = tx.objectStore('ops');
    const req = store.openCursor();
    req.onsuccess = (e) => {
      const cur = e.target.result;
      if (cur) {
        const val = cur.value;
        if (batch.find(b => b.id === val.id)) {
          val.attempts = (val.attempts || 0) + 1;
          val.last_error = String(errMsg).slice(0, 500);
          // schedule next retry: exponential backoff based on attempts (ms)
          try {
            const backoff = Math.min(8_000, Math.pow(2, val.attempts) * 250);
            val.next_retry_at = new Date(Date.now() + backoff).toISOString();
          } catch (e) { val.next_retry_at = null; }
          try { cur.update(val); } catch (er) { /* ignore */ }
        }
        cur.continue();
      }
    };
    req.onerror = () => reject(req.error);
    tx.oncomplete = () => { updateOpsUI().catch(() => {}); resolve(); };
  });
}

// Render a small per-op status UI into #opsList if present
async function updateOpsUI() {
  try {
    await openDb();
    const el = document.getElementById('opsList');
    if (!el) return;
    const tx = db.transaction('ops', 'readonly');
    const store = tx.objectStore('ops');
    const req = store.openCursor();
    const rows = [];
    await new Promise((resolve, reject) => {
      req.onsuccess = (e) => {
        const cur = e.target.result;
        if (cur) { rows.push(cur.value); cur.continue(); } else resolve();
      };
      req.onerror = () => reject(req.error);
    });
    el.innerHTML = '';
    for (const r of rows) {
      const div = document.createElement('div');
      div.className = 'op-row';
    const left = document.createElement('div');
    left.style.display = 'flex';
    left.style.flexDirection = 'column';
    const opText = document.createElement('div');
    opText.textContent = `${r.op} — attempts:${r.attempts || 0}`;
    const meta = document.createElement('div'); meta.className = 'op-meta';
  const payloadSummary = JSON.stringify(r.payload || {});
  const eta = r.next_retry_at ? formatEta(r.next_retry_at) : 'now';
  const age = r.created_at ? formatAge(r.created_at) : '';
  meta.textContent = `${payloadSummary} • ${eta} ${age ? '• ' + age : ''}`;
    left.appendChild(opText); left.appendChild(meta);
    div.appendChild(left);
      if (r.last_error) {
        const err = document.createElement('div'); err.className = 'op-err'; err.textContent = String(r.last_error); div.appendChild(err);
      }
      // cancel button to allow user to drop a queued op
      try {
        const right = document.createElement('div');
        right.style.display = 'flex'; right.style.gap = '.5rem';
        const sendNow = document.createElement('button'); sendNow.textContent = 'Send now';
        sendNow.addEventListener('click', async () => {
          // mark next_retry_at to now so scheduler/drain will pick it up
          try { await setOpMetadata(r.id, { next_retry_at: new Date().toISOString() }); wakeScheduler(); } catch (e) { console.debug('send now failed', e); }
        });
        const btn = document.createElement('button');
        btn.textContent = 'Cancel';
        btn.addEventListener('click', async () => {
          try { await removeOp(r.id); } catch (e) { console.debug('remove op failed', e); }
        });
        right.appendChild(sendNow);
        right.appendChild(btn);
        div.appendChild(right);
      } catch (e) { /* ignore UI errors */ }
      el.appendChild(div);
    }
  } catch (e) {
    /* non-fatal */
  }
}

// wire Force send all button to ignore next_retry_at and attempt to send everything
try {
  const fsb = document.getElementById('forceSyncBtn');
  if (fsb) fsb.addEventListener('click', async () => {
    try {
      renderStatus('force-syncing');
      await drainQueue(50, 4, false);
      await refreshServerTodos();
      renderStatus('idle');
    } catch (e) { renderStatus('sync-failed'); }
  });
} catch (e) { /* ignore wiring errors */ }

// Best-effort: try to drain the queue when the page is unloading (do not block)
window.addEventListener('beforeunload', () => {
  if (!navigator.onLine) return;
  try { drainQueue().catch(() => {}); } catch (e) { /* ignore */ }
});

async function updateQueuedCount() {
  // Debounced counter to reduce DB thrash
  if (updateQueuedCount._pending) {
    clearTimeout(updateQueuedCount._pending);
  }
  return new Promise((resolve) => {
    updateQueuedCount._pending = setTimeout(async () => {
      try {
        await openDb();
        const tx = db.transaction('ops', 'readonly');
        const store = tx.objectStore('ops');
        const req = store.count();
        req.onsuccess = () => {
          const n = req.result || 0;
          const el = document.getElementById('queueCount');
          if (el) el.textContent = String(n);
          resolve(n);
        };
        req.onerror = () => { console.warn('count error', req.error); resolve(0); };
      } catch (err) {
        console.warn('could not update queued count', err);
        resolve(0);
      }
    }, 150);
  });
}

// remove an op from the queue by id (cancellable by user)
async function removeOp(opId) {
  try {
    await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('ops', 'readwrite');
      const store = tx.objectStore('ops');
      const req = store.delete(opId);
      req.onsuccess = () => { updateQueuedCount().catch(() => {}); updateOpsUI().catch(() => {}); resolve(true); };
      req.onerror = () => reject(req.error);
    });
  } catch (e) {
    return false;
  }
}

function setCreateDisabled(disabled) {
  const btn = document.querySelector('#createForm button[type="submit"]');
  if (btn) btn.disabled = !!disabled;
}

let isDraining = false;

async function safeFetch(url, options = {}, timeout = 8000) {
  // fetch wrapper with timeout using AbortController
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  try {
    const resp = await fetch(url, { signal: controller.signal, ...options });
    clearTimeout(id);
    return resp;
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

function setSyncDisabled(disabled) {
  const btn = document.getElementById('syncBtn');
  if (btn) btn.disabled = !!disabled;
}

// eslint-disable-next-line no-unused-vars
async function enqueueCreateTodo(text, note, list_id) {
  const id = crypto.randomUUID();
  // ensure we have a sensible list id before enqueueing; try to resolve default if missing
  if (!list_id) {
    // try to resolve or create a sensible default list; fall back to null
    try {
      const resolved = await ensureServerDefaultList();
      list_id = resolved || null;
    } catch (e) {
      list_id = null;
    }
  }
  const op = { id, op: 'create_todo', payload: { text, note, list_id, client_id: id, op_id: id } };
  await addOp(op);
  // wake scheduler so new op can be sent promptly
  try { wakeScheduler(); } catch (e) { /* ignore */ }
  await updateQueuedCount();
  renderStatus('queued');
  // optimistic UI: show a pending todo entry so users get immediate feedback
  try {
    const ul = document.getElementById('todos');
    if (ul) {
      const li = document.createElement('li');
      li.dataset.clientId = id;
      li.setAttribute('data-client-id', id);
      li.classList.add('pending');
      li.textContent = `${text} (pending)`;
      ul.appendChild(li);
    }
  } catch (e) {
    /* non-fatal UI failure */
  }
}

// drainQueue: send ops in batches, retry on transient failures, and only remove ops that the server acknowledged
async function drainQueue(batchSize = 10, maxRetries = 4, onlyDue = true) {
  if (isDraining) return [];
  isDraining = true;
  setSyncDisabled(true);
  try {
    await openDb();
    const allOps = [];
    const tx = db.transaction('ops', 'readonly');
    const store = tx.objectStore('ops');
    const all = await new Promise((resolve, reject) => {
      const acc = [];
      // if onlyDue is true, iterate the index to find ops whose next_retry_at is null or <= now
      try {
        if (onlyDue && store.indexNames && store.indexNames.contains('next_retry_at')) {
          const idx = store.index('next_retry_at');
          const nowIso = new Date().toISOString();
          // range: <= now OR nulls are stored as null which won't be returned by index; so also scan whole store when no results
          const range = IDBKeyRange.upperBound(nowIso);
          const req = idx.openCursor(range);
          req.onsuccess = (e) => {
            const cur = e.target.result;
            if (cur) { acc.push(cur.value); cur.continue(); } else resolve(acc);
          };
          req.onerror = () => reject(req.error);
        } else {
          const req = store.openCursor();
          req.onsuccess = (e) => {
            const cur = e.target.result;
            if (cur) { acc.push(cur.value); cur.continue(); } else resolve(acc);
          };
          req.onerror = () => reject(req.error);
        }
      } catch (e) { reject(e); }
    });
    if (!all.length) return [];
    // send in batches to avoid huge payloads
    const batches = [];
    for (let i = 0; i < all.length; i += batchSize) batches.push(all.slice(i, i + batchSize));
    const succeededIds = new Set();
    let lastData = null;
  for (const batch of batches) {
      let attempt = 0;
      while (attempt <= maxRetries) {
        try {
          const resp = await safeFetch('/sync', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ops: batch.map(a => ({ op: a.op, payload: a.payload })) }) }, 10000);
          if (!resp.ok) throw new Error('sync failed: ' + resp.status);
          const data = await resp.json();
          lastData = data;
          const results = data.results || [];
          for (let i = 0; i < results.length && i < batch.length; i++) {
            const r = results[i];
            if (r && (r.status === 'ok' || r.status === 'not_found')) {
              succeededIds.add(batch[i].id);
            }
          }
          // reconcile optimistic pending creates: if server returned a client_id and id,
          // replace the pending DOM entry with the real server id
          for (let i = 0; i < results.length && i < batch.length; i++) {
            const r = results[i];
            const reqOp = batch[i];
            try {
              if (r && r.status === 'ok' && r.client_id && r.id) {
                const li = document.querySelector(`#todos li[data-client-id='${r.client_id}']`);
                if (li) {
                  li.dataset.id = String(r.id);
                  li.setAttribute('data-id', String(r.id));
                  li.removeAttribute('data-client-id');
                  li.classList.remove('pending');
                  // prefer server text if provided (reqOp.payload.text is the client text)
                  li.textContent = `${reqOp.payload.text} (${r.id})`;
                }
              }
            } catch (e) {
              /* ignore DOM update errors */
            }
          }
          // If server returned tombstones, process them immediately so
          // the local queue and UI reflect deletions before continuing.
          if (data.tombstones && data.tombstones.length) {
            try { await processTombstones(data.tombstones); } catch (e) { console.debug('processing tombstones failed', e); }
          }
          break;
        } catch (err) {
          // record attempt metadata per-op so retries survive reloads
          try { await incrementAttemptsForBatch(batch, err && err.message ? err.message : String(err)); } catch (e) { /* ignore */ }
          attempt += 1;
          if (attempt > maxRetries) {
            throw err;
          }
          // capped exponential backoff (max ~8s)
          const backoff = Math.min(8000, Math.pow(2, attempt) * 250);
          await new Promise(r => setTimeout(r, backoff));
        }
      }
    }
    // remove only succeeded ops from the DB
    if (succeededIds.size) {
      const tx2 = db.transaction('ops', 'readwrite');
      const store2 = tx2.objectStore('ops');
      await new Promise((resolve, reject) => {
        const req2 = store2.openCursor();
        req2.onsuccess = (ev) => {
          const cur = ev.target.result;
          if (cur) {
            if (succeededIds.has(cur.value.id)) cur.delete();
            cur.continue();
          } else { resolve(); }
        };
        req2.onerror = () => reject(req2.error);
      });
      await updateQueuedCount();
    }
  // refresh op UI after drain
  await updateOpsUI();
    renderStatus('synced');
    return lastData;
  } finally {
    isDraining = false;
    setSyncDisabled(false);
  }
}

function renderStatus(s) { const el = document.getElementById('status'); if (!el) return; el.textContent = 'Status: ' + s; }

window.addEventListener('load', async () => {
  await openDb();
  // ensure default list exists early so users' creates are smoother
  await ensureServerDefaultList();
  await updateQueuedCount();

  document.getElementById('createForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    setCreateDisabled(true);
    const text = document.getElementById('todoText').value;
    const note = document.getElementById('todoNote').value;
    // prefer selected list if present, otherwise ensure server default
    let useList = selectedListId || null;
    if (!useList) {
      if (!serverDefaultListId) {
        renderStatus('ensuring-default-list');
        await ensureServerDefaultList();
      }
      useList = serverDefaultListId || 0;
    }
    await enqueueCreateTodo(text, note, useList);
    document.getElementById('todoText').value = '';
    renderStatus('queued');
    setCreateDisabled(false);
  });

  // Create list form (PWA): POST /lists and refresh visible lists
  try {
    const clf = document.getElementById('createListForm');
    if (clf) clf.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button');
      try {
        const nameEl = document.getElementById('listName');
        const name = (nameEl && nameEl.value || '').trim();
        if (!name) return;
        if (btn) btn.disabled = true;
        if (navigator.onLine) {
          const resp = await safeFetch('/lists', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ name }) }, 8000);
          if (resp && resp.ok) {
            const created = await resp.json().catch(()=>null);
            // refresh lists from server and auto-select new list
            await refreshServerTodos();
            if (created && created.id) {
              selectedListId = created.id;
              renderLists();
              await refreshServerTodos();
            }
            if (nameEl) nameEl.value = '';
          } else {
            try { const body = resp && await resp.text(); console.warn('create list failed', resp && resp.status, body); } catch(e){}
            alert('Could not create list');
          }
        } else {
          // offline: enqueue create_list op
          const id = crypto.randomUUID();
          await addOp({ id, op: 'create_list', payload: { name, client_id: id, op_id: id } });
          wakeScheduler();
          await updateQueuedCount();
          renderStatus('queued');
          if (nameEl) nameEl.value = '';
        }
      } catch (e) {
        console.warn('create list error', e);
        alert('Error creating list');
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  } catch (e) { /* ignore wiring errors */ }

  // helper to enqueue generic update ops for todos
  async function enqueueUpdateTodoOp(todoId, fields) {
    const id = crypto.randomUUID();
    await addOp({ id, op: 'update_todo', payload: { id: todoId, ...fields, op_id: id } });
    wakeScheduler();
    await updateQueuedCount();
    renderStatus('queued');
  }

  document.getElementById('syncBtn').addEventListener('click', async () => {
    try {
      renderStatus('syncing');
      await drainQueue();
      // refresh server-backed todos
      await refreshServerTodos();
      renderStatus('idle');
    } catch (err) {
      console.warn('sync failed', err);
      renderStatus('sync-failed');
    }
  });

  // periodic background sync every 5 minutes when online
  setInterval(async () => {
    if (!navigator.onLine) return;
    try {
      await drainQueue();
      await refreshServerTodos();
    } catch (e) {
      // ignore periodic failures
      console.debug('background sync failed', e);
    }
  }, 5 * 60 * 1000);
  // attempt to register a service worker (non-fatal)
  try {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/service-worker.js').then(() => {
        console.debug('service worker registered');
      }).catch((e) => { console.debug('sw register failed', e); });
    }
  } catch (e) { /* ignore */ }
  // online/offline indicator
  const onlineEl = document.getElementById('onlineStatus');
  function setOnlineIndicator() {
    if (!onlineEl) return;
    onlineEl.textContent = navigator.onLine ? 'online' : 'offline';
    onlineEl.className = navigator.onLine ? 'online' : 'offline';
  }
  setOnlineIndicator();
  window.addEventListener('online', setOnlineIndicator);
  window.addEventListener('offline', setOnlineIndicator);
});

// auto-sync when coming online
window.addEventListener('online', async () => {
  try {
    renderStatus('online - syncing');
    await drainQueue();
    await refreshServerTodos();
    renderStatus('idle');
  } catch (err) {
    renderStatus('sync-failed');
  }
});

// initial refresh
window.addEventListener('load', () => {
  // after DB open, try to refresh server todos
  setTimeout(() => refreshServerTodos(), 1000);
});

async function refreshServerTodos() {
  try {
    const resp = await safeFetch('/sync', { credentials: 'include' }, 8000);
    if (!resp || !resp.ok) return;
    const data = await resp.json();
    // process tombstones so local queue doesn't try to act on deleted items
    if (data.tombstones && data.tombstones.length) {
      await processTombstones(data.tombstones);
    }
    const ul = document.getElementById('todos');
    if (!ul) return;
    ul.innerHTML = '';
    // cache lists returned by /sync for quick render
    if (data.lists && Array.isArray(data.lists)) {
      cachedLists = data.lists;
      renderLists();
    }
    for (const t of data.todos) {
      // if a list is selected, only render todos for that list
      if (selectedListId && Number(t.list_id) !== Number(selectedListId)) continue;
      const li = document.createElement('li');
      li.dataset.id = String(t.id);
      li.setAttribute('data-id', String(t.id));
      // create a link so the todo is directly addressable/navigable
      const a = document.createElement('a');
      a.href = `/html_no_js/todos/${t.id}`;
      a.textContent = t.text || `Todo ${t.id}`;
      a.className = 'todo-link';
      li.appendChild(a);
      // optional meta/id annotation
      const meta = document.createElement('span'); meta.className = 'meta'; meta.textContent = ` (${t.id})`;
      li.appendChild(meta);
      // click to show details when clicking outside an anchor; allow anchor clicks to navigate
      li.addEventListener('click', (ev) => {
        try {
          if (ev.target && ev.target.closest && ev.target.closest('a')) return; // allow navigation
        } catch (e) {}
        ev.preventDefault();
        showTodoDetail(t);
      });
      ul.appendChild(li);
    }
    // update queued count in case tombstones modified the queue
    await updateQueuedCount();
    // update optional server ts element
    const sts = document.getElementById('serverTs');
    if (sts && data.server_ts) sts.textContent = data.server_ts;
  } catch (err) {
    console.warn('could not refresh todos', err);
  }
}

// Render the list of lists into #lists and wire selection handlers
function renderLists() {
  const el = document.getElementById('lists');
  if (!el) return;
  el.innerHTML = '';
    for (const l of cachedLists) {
      const li = document.createElement('li');
      li.className = 'list-item';
      const left = document.createElement('div'); left.className = 'list-action-left';
      const btn = document.createElement('button'); btn.className = 'list-action-btn'; btn.textContent = '▸';
      left.appendChild(btn);
      const main = document.createElement('div'); main.className = 'list-main';
      // create an anchor so lists are navigable/bookmarkable
      const title = document.createElement('a');
      title.className = 'list-title';
      title.href = `/html_no_js/lists/${l.id}`;
      title.textContent = l.name || `List ${l.id}`;
      title.dataset.id = String(l.id);
      const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = l.owner_id ? 'private' : 'public';
      // optional uncompleted count (supplied by server as uncompleted_count)
      if (typeof l.uncompleted_count !== 'undefined' && l.uncompleted_count !== null) {
        const cnt = document.createElement('span');
        cnt.className = 'count-circle pink';
        cnt.setAttribute('aria-label', l.uncompleted_count + ' uncompleted todos');
        cnt.textContent = String(l.uncompleted_count);
        // place count before meta so it appears inline with title
        main.appendChild(title);
        main.appendChild(cnt);
        main.appendChild(meta);
      } else {
        main.appendChild(title); main.appendChild(meta);
      }
      li.appendChild(left); li.appendChild(main);
      // clicking the row should select the list, but clicking the anchor should navigate
      li.addEventListener('click', (ev) => {
        try { if (ev.target && ev.target.closest && ev.target.closest('a')) return; } catch (e) {}
        ev.preventDefault();
        selectList(l.id);
      });
      el.appendChild(li);
    }
}

// Select a list to filter todos and refresh display
async function selectList(listId) {
  selectedListId = listId;
  // update visual selection (simple)
  const items = document.querySelectorAll('#lists .list-item');
  items.forEach(it => { it.style.background = it.querySelector('.list-title') && it.querySelector('.list-title').textContent && String(listId) === String(it.querySelector('.list-title').dataset?.id) ? '#12232b' : 'transparent'; });
  await refreshServerTodos();
}

// Show a todo detail pane with actions: edit text/note, complete, delete
function showTodoDetail(todo) {
  const sec = document.getElementById('todoDetailSection');
  const box = document.getElementById('todoDetail');
  if (!box || !sec) return;
  sec.style.display = 'block';
  box.innerHTML = '';
  const title = document.createElement('h3'); title.textContent = `#${todo.id} — ${todo.text}`;
  const note = document.createElement('div'); note.className = 'note-text';
  // Render fn-tags client-side so PWA users see buttons. Keep it safe by HTML-escaping
  function escapeHtml(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function renderFnTagsToHtml(text){
    if(!text) return '';
    // find {{fn:...}} tags non-greedy
    return text.replace(/\{\{\s*fn:([^\}]+?)\s*\}\}/g, function(_, body){
      try{
        var parts = body.split('|');
        var left = parts[0].trim();
        var label = parts[1] ? parts[1].trim() : null;
        var identifier = left.split(/\s+/,1)[0];
        var args_text = left.slice(identifier.length).trim();
        var args = {};
        if(args_text){
          var pairs = []; var cur=''; var in_q=null;
          for(var i=0;i<args_text.length;i++){ var ch=args_text.charAt(i); if((ch==='"' || ch==="'") ){ if(in_q===null) in_q=ch; else if(in_q===ch) in_q=null; cur+=ch; } else if(ch===',' && in_q===null){ pairs.push(cur); cur=''; } else { cur+=ch; } }
          if(cur.trim()) pairs.push(cur);
          pairs.forEach(function(p){ if(p.indexOf('=')!==-1){ var kv=p.split('='); var k=kv[0].trim(); var v=kv.slice(1).join('=').trim(); if((v.charAt(0)==='"'&&v.charAt(v.length-1)==='"')||(v.charAt(0)==="'"&&v.charAt(v.length-1)==="'")){ v=v.slice(1,-1); } args[k]=v; } else { var v=p.trim(); if(v) { args.tags = args.tags || []; args.tags.push(v); } } });
          pairs.forEach(function(p){ if(p.indexOf('=')!==-1){ var kv=p.split('='); var k=kv[0].trim(); var v=kv.slice(1).join('=').trim(); if((v.charAt(0)==='"'&&v.charAt(v.length-1)==='"')||(v.charAt(0)==="'"&&v.charAt(v.length-1)==="'")){ v=v.slice(1,-1); } if(k==='tags'){ if(v.indexOf(',')!==-1){ args[k]=v.split(',').map(function(x){return x.trim();}).filter(Boolean); } else { args[k]=[v]; } } else { args[k]=v; } } else { var v=p.trim(); if(v) { args.tags = args.tags || []; args.tags.push(v); } } });
        }
        var dataArgs = JSON.stringify(args);
        var escLabel = escapeHtml(label || identifier);
        var escIdent = escapeHtml(identifier);
        var escArgs = escapeHtml(dataArgs);
        return '<button type="button" class="fn-button" data-fn="'+escIdent+'" data-args="'+escArgs+'">'+escLabel+'</button>';
      }catch(e){ return escapeHtml('{{fn:'+body+'}}'); }
    }).replace(/\n/g,'<br>');
  }
  note.innerHTML = renderFnTagsToHtml(todo.note || '');
  const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = `Created: ${todo.created_at || 'unknown'} Modified: ${todo.modified_at || 'unknown'}`;
  const actions = document.createElement('div'); actions.style.marginTop = '.5rem'; actions.style.display = 'flex'; actions.style.gap = '.5rem';
  const editBtn = document.createElement('button'); editBtn.textContent = 'Edit';
  editBtn.addEventListener('click', async () => {
    const newText = prompt('Edit todo text', todo.text || '');
    if (newText === null) return;
    try {
      const resp = await safeFetch(`/todos/${todo.id}`, { method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ text: newText }) }, 8000);
      if (resp && resp.ok) { await refreshServerTodos(); alert('updated'); }
      else alert('update failed');
    } catch (e) { alert('update error'); }
  });
  const completeBtn = document.createElement('button'); completeBtn.textContent = 'Toggle Complete';
  completeBtn.addEventListener('click', async () => {
    try {
      const resp = await safeFetch(`/todos/${todo.id}/complete`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ done: 'true' }) }, 8000);
      if (resp && resp.ok) { await refreshServerTodos(); alert('marked complete'); }
      else alert('complete failed');
    } catch (e) { alert('complete error'); }
  });
  const delBtn = document.createElement('button'); delBtn.textContent = 'Delete'; delBtn.style.background = 'var(--danger)';
  delBtn.addEventListener('click', async () => {
    if (!confirm('Delete this todo?')) return;
    try {
      const resp = await safeFetch(`/todos/${todo.id}`, { method: 'DELETE', credentials: 'include' }, 8000);
      if (resp && resp.ok) { await refreshServerTodos(); box.innerHTML = ''; sec.style.display = 'none'; alert('deleted'); }
      else alert('delete failed');
    } catch (e) { alert('delete error'); }
  });
  actions.appendChild(editBtn); actions.appendChild(completeBtn); actions.appendChild(delBtn);
  box.appendChild(title); box.appendChild(meta); box.appendChild(note); box.appendChild(actions);
}

async function processTombstones(tombstones) {
  if (!tombstones || !tombstones.length) return;
  try {
    // If any list tombstones exist, refresh server default list so we can
    // reassign create_todo ops that targeted a deleted list.
    const hasListTombstone = tombstones.some(t => t.item_type === 'list');
    if (hasListTombstone) {
      await ensureServerDefaultList();
    }
    await openDb();
    const tx = db.transaction('ops', 'readwrite');
    const store = tx.objectStore('ops');
    return new Promise((resolve, reject) => {
      const req = store.openCursor();
      req.onsuccess = (e) => {
        const cur = e.target.result;
        if (cur) {
          const op = cur.value;
          for (const t of tombstones) {
            const itemType = t.item_type;
            const itemId = Number(t.item_id);
            if (itemType === 'todo') {
              // drop queued ops that reference a deleted todo (updates/deletes)
              if (op.payload && (Number(op.payload.id) === itemId || Number(op.payload.todo_id) === itemId)) {
                try { cur.delete(); } catch (e) { /* ignore */ }
                break;
              }
              // also remove the todo from the UI if present
              const li = document.querySelector(`#todos li[data-id='${itemId}']`);
              if (li && li.parentNode) li.parentNode.removeChild(li);
            } else if (itemType === 'list') {
              // if an op references a deleted list, try to reassign to server default
              if (op.payload && Number(op.payload.list_id) === itemId) {
                if (serverDefaultListId) {
                  op.payload.list_id = serverDefaultListId;
                  try { cur.update(op); } catch (e) { /* ignore */ }
                } else {
                  // no available default list: drop the op to avoid bad requests
                  try { cur.delete(); } catch (e) { /* ignore */ }
                }
                // if the deleted list was the cached default, clear it so future ops re-resolve
                if (serverDefaultListId === itemId) serverDefaultListId = null;
                break;
              }
            }
          }
          cur.continue();
        } else {
          tx.oncomplete = async () => {
            renderStatus('queue-updated');
            await updateQueuedCount();
            resolve();
          };
          tx.onerror = () => reject(tx.error);
        }
      };
      req.onerror = () => reject(req.error);
    });
  } catch (err) {
    console.warn('error processing tombstones', err);
  }
}

// find the soonest next_retry_at among queued ops (ISO string) or null if none
async function getNextRetryTimestamp() {
  await openDb();
  return new Promise((resolve, reject) => {
    try {
      const tx = db.transaction('ops', 'readonly');
      const store = tx.objectStore('ops');
      if (store.indexNames && store.indexNames.contains('next_retry_at')) {
        const idx = store.index('next_retry_at');
        // open cursor ascending; first entry may be null if stored as null is not indexed
        const req = idx.openCursor();
        req.onsuccess = (e) => {
          const cur = e.target.result;
          if (cur) {
            resolve(cur.value.next_retry_at || null);
          } else resolve(null);
        };
        req.onerror = () => resolve(null);
      } else {
        // fallback: scan store
        const req = store.openCursor();
        let earliest = null;
        req.onsuccess = (e) => {
          const cur = e.target.result;
          if (cur) {
            const n = cur.value.next_retry_at;
            if (n && (!earliest || n < earliest)) earliest = n;
            cur.continue();
          } else resolve(earliest);
        };
        req.onerror = () => resolve(null);
      }
    } catch (e) { resolve(null); }
  });
}

// scheduler loop: waits until ops are due or new ops arrive, then drains due ops
async function retrySchedulerLoop() {
  while (true) {
    try {
      // find the next retry time
      const next = await getNextRetryTimestamp();
      if (!next) {
        // no scheduled retries: wait until woken or a long-poll interval
        await new Promise((resolve) => { _schedulerWake = resolve; setTimeout(resolve, 60 * 1000); });
      } else {
        const now = Date.now();
        const when = new Date(next).getTime();
        if (when <= now) {
          if (navigator.onLine) {
            try { await drainQueue(10, 4, true); } catch (e) { /* ignore */ }
          }
          // small pause to avoid spin
          await new Promise(r => setTimeout(r, 250));
        } else {
          const waitMs = Math.min(60 * 60 * 1000, when - now);
          await new Promise((resolve) => { _schedulerWake = resolve; setTimeout(resolve, waitMs); });
        }
      }
    } catch (e) {
      // scheduler should be resilient
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

// start scheduler in background (best-effort)
try { retrySchedulerLoop(); } catch (e) { console.debug('retry scheduler failed to start', e); }

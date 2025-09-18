(function(){
  function sendBookmark(url, token, value){
    const form = new URLSearchParams();
    form.set('_csrf', token || '');
    form.set('bookmarked', value ? 'true' : 'false');
    return fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
      },
      body: form.toString(),
      credentials: 'same-origin'
    }).then(async (r) => {
      const ct = r.headers.get('content-type') || '';
      if (!r.ok) throw new Error('HTTP ' + r.status);
      if (ct.includes('application/json')) return r.json();
      return { ok: true };
    });
  }

  function initBookmarkButtons(){
    // Todo bookmark buttons
    document.querySelectorAll('[data-bookmark-todo]')
      .forEach(btn => {
        btn.addEventListener('click', function(ev){
          ev.preventDefault();
          const todoId = this.getAttribute('data-bookmark-todo');
          const url = `/html_no_js/todos/${encodeURIComponent(todoId)}/bookmark`;
          const token = this.getAttribute('data-csrf') || '';
          const current = this.getAttribute('data-state') === 'on';
          const next = !current;
          const el = this;
          el.disabled = true;
          sendBookmark(url, token, next)
            .then((json) => {
              // update UI state and icon
              el.setAttribute('data-state', next ? 'on' : 'off');
              el.classList.toggle('pinned', next);
              el.textContent = next ? '🔖' : '📑';
              // Optional: dispatch a custom event so other scripts can react
              const evt = new CustomEvent('bookmark-changed', { detail: { kind: 'todo', id: Number(todoId), bookmarked: next, response: json }});
              window.dispatchEvent(evt);
            })
            .catch((e) => {
              console.error('Bookmark toggle failed', e);
              // brief visual feedback
              el.classList.add('shake');
              setTimeout(() => el.classList.remove('shake'), 600);
            })
            .finally(() => {
              el.disabled = false;
            });
        });
      });

    // List bookmark buttons
    document.querySelectorAll('[data-bookmark-list]')
      .forEach(btn => {
        btn.addEventListener('click', function(ev){
          ev.preventDefault();
          const listId = this.getAttribute('data-bookmark-list');
          const url = `/html_no_js/lists/${encodeURIComponent(listId)}/bookmark`;
          const token = this.getAttribute('data-csrf') || '';
          const current = this.getAttribute('data-state') === 'on';
          const next = !current;
          const el = this;
          el.disabled = true;
          sendBookmark(url, token, next)
            .then((json) => {
              el.setAttribute('data-state', next ? 'on' : 'off');
              el.textContent = next ? '🔖' : '📑';
              const evt = new CustomEvent('bookmark-changed', { detail: { kind: 'list', id: Number(listId), bookmarked: next, response: json }});
              window.dispatchEvent(evt);
            })
            .catch((e) => {
              console.error('Bookmark toggle failed', e);
              el.classList.add('shake');
              setTimeout(() => el.classList.remove('shake'), 600);
            })
            .finally(() => {
              el.disabled = false;
            });
        });
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBookmarkButtons);
  } else {
    initBookmarkButtons();
  }
})();

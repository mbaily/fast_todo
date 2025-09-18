// fast_todo: todo page client behaviors (served from /static)
'use strict';

// Simple mark-storage helper used by mark icon
(function(){
	try{
		var KEY='ft_marks_v1'; var TTL=5*60*1000; // 5 minutes
		function load(){ try { return JSON.parse(localStorage.getItem(KEY)||'{}')||{}; } catch(_){ return {}; } }
		function save(o){ try { localStorage.setItem(KEY, JSON.stringify(o||{})); } catch(_){ } }
		function prune(o){ var t=Date.now(); var out={todos:{},lists:{}}; if(o.todos) for(var k in o.todos){ if((t-(o.todos[k]||0))<TTL) out.todos[k]=o.todos[k]; } if(o.lists) for(var k2 in o.lists){ if((t-(o.lists[k2]||0))<TTL) out.lists[k2]=o.lists[k2]; } return out; }
		function mark(kind, id){ var o=prune(load()); if(!o[kind]) o[kind]={}; o[kind][String(id)]=Date.now(); save(o); }
		var btn = document.getElementById('mark-list-hdr');
		if (btn){ btn.addEventListener('click', function(){ try{ var tid = Number(btn.getAttribute('data-todo-id')); if (tid) { mark('todos', tid); btn.textContent='✔️'; setTimeout(function(){ try{ btn.textContent='🔖'; }catch(_){} }, 1200); } }catch(_){} }); }
	}catch(e){ }
})();

// Calendar ignored toggle: POST via fetch and keep UI state in sync
(function(){
	try{
		var cb = document.querySelector('[id^="todo-calendar-ignored-cb-"]');
		var hid = document.querySelector('[id^="todo-calendar-ignored-hidden-"]');
		if (!cb || !hid) return;
		if (cb.dataset && cb.dataset.bound === '1') return; // already wired (inline)
		var form = hid.closest('form'); if (!form) return;
		cb.dataset.bound = '1';
		cb.addEventListener('change', function(){
			try{
				hid.value = cb.checked ? 'true' : 'false';
				var fd = new FormData();
				fd.append('calendar_ignored', hid.value);
				var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
				fetch(form.action, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'Accept': 'application/json' }})
					.then(function(res){ if(!res.ok) throw new Error('calendar_ignored failed'); return res.json().catch(function(){ return null; }); })
					.then(function(){ /* success noop */ })
					.catch(function(err){ try{ console && console.error && console.error('calendar_ignored update failed', err); }catch(_){ } });
			}catch(_){ }
		});
	}catch(e){ }
})();

// Styles for hashtag suggestion box shared with list page
(function(){
	try{
		var style = document.createElement('style');
		style.textContent = [
			'.hashtag-suggest{position:fixed;z-index:9999;background:#fff;border:1px solid #cbd5e1;border-radius:6px;box-shadow:0 6px 20px rgba(0,0,0,0.15);padding:4px;display:none;max-height:200px;overflow-y:auto;min-width:160px;font-size:.95rem}',
			'.hashtag-suggest .item{padding:4px 8px;border-radius:4px;cursor:pointer;white-space:nowrap}',
			'.hashtag-suggest .item.active,.hashtag-suggest .item:hover{background:rgba(29,78,216,.12)}'
		].join('\n');
		document.head && document.head.appendChild(style);
	}catch(e){ }
})();

// Caret-based hashtag completion for the todo title input
(function(){
	try{
		var input = document.getElementById('todo-text-input');
		var box = document.getElementById('todo-hashtag-suggest');
		if (!input || !box) return;
		var ALL_TAGS = (box.getAttribute('data-tags') || '').split(',').map(function(s){return s.trim();}).filter(Boolean);
		var state = { open:false, items:[], active:0, range:[0,0] };
		function closeBox(){ try{ box.style.display='none'; state.open=false; }catch(_){}}
		function render(items){
			try{
				box.innerHTML='';
				if(!items||items.length===0){ closeBox(); return; }
				items.slice(0,20).forEach(function(tag, idx){
					var el=document.createElement('div');
					el.className='item'+(idx===state.active?' active':'');
					el.setAttribute('role','option');
					el.textContent=(typeof tag==='string' && tag.charAt(0)==='#')?tag:('#'+tag);
					el.dataset.idx=String(idx);
					el.addEventListener('mousedown', function(ev){ try{ ev.preventDefault(); commit(idx); }catch(_){ } });
					box.appendChild(el);
				});
				box.style.display='block';
			}catch(_){ }
		}
		function positionBox(caretIndex){
			try{
				var rect=input.getBoundingClientRect();
				var style=window.getComputedStyle(input);
				var leftPad=parseFloat(style.paddingLeft)||0;
				var font=[style.fontWeight, style.fontSize, style.fontFamily].filter(Boolean).join(' ');
				var text=input.value.slice(0, caretIndex);
				var canvas=positionBox._c||(positionBox._c=document.createElement('canvas'));
				var ctx=canvas.getContext('2d');
				ctx.font=font;
				var metrics=ctx.measureText(text);
				var x=rect.left+leftPad+metrics.width-(input.scrollLeft||0);
				var vw=Math.max(document.documentElement.clientWidth||0, window.innerWidth||0);
				var maxLeft=vw-200;
				box.style.left=Math.max(8, Math.min(x, maxLeft))+'px';
				box.style.top=(rect.bottom+4)+'px';
				box.style.minWidth=Math.max(160, rect.width*0.4)+'px';
			}catch(_){ try{ var r=input.getBoundingClientRect(); box.style.left=(r.left)+'px'; box.style.top=(r.bottom+4)+'px'; }catch(__){} }
		}
		function openFor(fragment, start, end){
			try{
				var frag=(fragment||'').toLowerCase();
				function body(t){ return (t&&t.charAt(0)==='#')?t.slice(1):(t||''); }
				var items=ALL_TAGS.filter(function(t){ return body(t).toLowerCase().indexOf(frag)===0; });
				state.items=items; state.active=0; state.range=[start,end];
				if(items.length===0){ closeBox(); return; }
				positionBox(end); render(items); state.open=true;
			}catch(_){ }
		}
		function findFragment(){
			try{
				var pos=input.selectionStart||0;
				var left=(input.value||'').slice(0,pos);
				var m=left.match(/(^|\s)#([A-Za-z0-9_]*)$/);
				if(!m) return null;
				var fragment=m[2]||'';
				var start=pos-fragment.length-1; // include '#'
				return { fragment:fragment, start:start, end:pos };
			}catch(_){ return null; }
		}
		function commit(idx){
			try{
				if(!state.open||idx==null||idx<0||idx>=state.items.length) return;
				var tag=state.items[idx];
				var v=input.value; var start=state.range[0]; var end=state.range[1];
				var before=v.slice(0,start); var after=v.slice(end);
				var replacement=(typeof tag==='string' && tag.charAt(0)==='#')?tag:('#'+tag);
				var needsSpace=after.length===0 || !/^\s/.test(after);
				var newVal=before+replacement+(needsSpace?' ':'')+after;
				var newCaret=(before+replacement).length+(needsSpace?1:0);
				input.value=newVal; input.setSelectionRange(newCaret,newCaret);
				closeBox();
			}catch(_){ }
		}
		input.addEventListener('input', function(){ var f=findFragment(); if(!f){ closeBox(); return; } openFor(f.fragment, f.start, f.end); });
		input.addEventListener('keydown', function(ev){ if(!state.open) return; if(ev.key==='ArrowDown'){ ev.preventDefault(); state.active=Math.min(state.active+1, state.items.length-1); render(state.items);} else if(ev.key==='ArrowUp'){ ev.preventDefault(); state.active=Math.max(state.active-1,0); render(state.items);} else if(ev.key==='Enter'||ev.key==='Tab'){ ev.preventDefault(); commit(state.active);} else if(ev.key==='Escape'){ ev.preventDefault(); closeBox(); } });
		input.addEventListener('focus', function(){ var f=findFragment(); if(f){ positionBox(f.end); } });
		window.addEventListener('scroll', function(){ if(state.open){ var f=findFragment(); if(f) positionBox(f.end); } }, true);
		window.addEventListener('resize', function(){ if(state.open){ var f=findFragment(); if(f) positionBox(f.end); } });
		document.addEventListener('click', function(ev){ if(!box.contains(ev.target) && ev.target!==box && ev.target!==input) closeBox(); });
		try { document.addEventListener('touchstart', function(ev){ if(!box.contains(ev.target) && ev.target!==box && ev.target!==input) closeBox(); }, { passive: true }); } catch(e) {}
		try { window.addEventListener('scroll', function(){ if(state.open) closeBox(); }, true); } catch(e) {}
	}catch(e){ }
})();

// Minimal autosave: debounce textarea changes and POST form via fetch to the existing edit endpoint.
(function(){
	try{
		var textarea = document.getElementById('note-textarea');
		if (!textarea) return; // nothing to do
		var form = textarea.closest('form');
		var statusEl = document.getElementById('autosave-status');
		function getCookie(name){ try{ var v = document.cookie.match('(?:^|;)\\s*' + name + '=([^;]*)'); return v ? decodeURIComponent(v[1]) : null; }catch(_){ return null; } }
		var timer = null;
		var inFlight = false;
		var debounceMs = 1000;
		function setStatus(text){ try{ if (statusEl) statusEl.textContent = text; }catch(_){ } }
		async function doSave(){
			if (inFlight) return; inFlight = true; setStatus('Saving...');
			try{
				var url = form.action;
				// Use URL-encoded form body for maximum proxy compatibility
				var body = new URLSearchParams();
				var textInput = form.querySelector('[name="text"]');
				var textVal = textInput ? textInput.value : '';
				body.append('text', textVal);
				body.append('note', textarea.value || '');
				var csrf = null; var hiddenCsrf = form.querySelector('input[name="_csrf"]');
				if (hiddenCsrf && hiddenCsrf.value) csrf = hiddenCsrf.value; if (!csrf) csrf = getCookie('csrf_token'); if (csrf) body.append('_csrf', csrf);
				try{
					var sortCb = form.querySelector('input[type="checkbox"][id^="note-sort-links-"]');
					var sortHiddenField = form.querySelector('input[name="sort_links"]');
					if (!sortHiddenField) { try { sortHiddenField = document.createElement('input'); sortHiddenField.type = 'hidden'; sortHiddenField.name = 'sort_links'; form.appendChild(sortHiddenField); } catch(e) { sortHiddenField = null; } }
					var valueToSend = null;
					if (sortCb) { valueToSend = sortCb.checked ? 'true' : 'false'; if (sortHiddenField) sortHiddenField.value = valueToSend; }
					else if (sortHiddenField) { valueToSend = sortHiddenField.value; }
					if (valueToSend !== null) { try { console && console.log && console.log('autosave: appending sort_links=', valueToSend); } catch(e) {} body.append('sort_links', valueToSend); }
				}catch(_){ }
				try { console && console.log && console.log('autosave: about to fetch', url); } catch(e) {}
				var res = await fetch(url, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' } });
				try { console && console.log && console.log('autosave: fetch completed, ok=', res && res.ok); } catch(e) {}
				if (!res.ok) throw new Error('save failed');
				setStatus('Saved');
				try {
					var tbBtn = document.querySelector('#todo-save-form button[type="submit"]');
					if (tbBtn) { if (!tbBtn.dataset._orig) tbBtn.dataset._orig = tbBtn.textContent || ''; tbBtn.textContent = 'Saved'; setTimeout(function(){ try{ if (tbBtn && tbBtn.textContent === 'Saved') tbBtn.textContent = tbBtn.dataset._orig || '💾'; }catch(_){} }, 1200); }
				} catch(e) { }
				setTimeout(function(){ try{ if (statusEl && statusEl.textContent === 'Saved') statusEl.textContent = ''; }catch(_){} }, 1200);
			}catch(err){ setStatus('Save failed'); try{ console.error('autosave error', err); }catch(_){} }
			finally{ inFlight = false; }
		}
		try { window.todo_do_save = doSave; } catch(e) { }
		function scheduleSave(){ try{ if (timer) clearTimeout(timer); timer = setTimeout(doSave, debounceMs); }catch(_){ } }
		textarea.addEventListener('input', scheduleSave);
		textarea.addEventListener('blur', function(){ try{ if (timer) clearTimeout(timer); doSave(); }catch(_){ } });
		try{ var titleInput = form.querySelector('[name="text"]'); if (titleInput){ titleInput.addEventListener('input', scheduleSave); titleInput.addEventListener('blur', function(){ try{ if (timer) clearTimeout(timer); doSave(); }catch(_){ } }); } } catch(e){}
		try {
			var toolbarBtn = document.getElementById('todo-save-btn');
			if (toolbarBtn) {
				toolbarBtn.addEventListener('click', function(ev){
					ev.preventDefault();
					try { if (window.todo_do_save) window.todo_do_save(); }
					catch(e){ try{ var editForm = document.getElementById('todo-edit-form'); if (editForm) editForm.submit(); }catch(_){} }
				});
			}
		} catch(e) { }
	}catch(e){ }
})();

// Sync the title textarea height/rows to match the rendered note height
(function(){
	try{
		function syncTitleToNote(){
			try{
				var titleEl = document.getElementById('todo-text-input');
				if (!titleEl) return;
				var noteTextarea = document.getElementById('note-textarea');
				var noteEl = document.querySelector('.note-text');
				var noteHeight = 0;
				if (noteTextarea) { noteHeight = noteTextarea.scrollHeight || noteTextarea.clientHeight || 0; }
				if (!noteHeight && noteEl) { var noteRect = noteEl.getBoundingClientRect(); noteHeight = noteRect.height || 0; }
				if (!noteHeight) return;
				var computed = window.getComputedStyle(titleEl);
				var lineHeight = parseFloat(computed.lineHeight);
				if (!lineHeight || isNaN(lineHeight)) { var fontSize = parseFloat(computed.fontSize) || 16; lineHeight = Math.round(fontSize * 1.2); }
				var desiredHeight = Math.max(24, Math.round(noteHeight));
				titleEl.style.boxSizing = 'border-box';
				titleEl.style.height = desiredHeight + 'px';
				var rows = Math.max(1, Math.round(desiredHeight / lineHeight));
				titleEl.rows = rows;
			}catch(_){ }
		}
		var t;
		window.addEventListener('load', syncTitleToNote);
		window.addEventListener('resize', function(){ try{ if (t) clearTimeout(t); t = setTimeout(syncTitleToNote, 150); }catch(_){ } });
		setTimeout(syncTitleToNote, 120);
	}catch(e){ }
})();

// Intercept the todo complete form to POST via fetch
(function(){
	try {
		var form = document.getElementById('todo-complete-form');
		if (!form) return;
		form.addEventListener('submit', function(ev){
			ev.preventDefault();
			try{
				var fd = new FormData(form);
				function getCookie(name){ try{ var m = document.cookie.match('(?:^|;)\\s*' + name + '=([^;]*)'); return m ? decodeURIComponent(m[1]) : null; }catch(_){ return null; } }
				if (!fd.has('_csrf')) { var cookie = getCookie('csrf_token'); if (cookie) fd.append('_csrf', cookie); }
				fetch(form.action, { method: 'POST', credentials: 'same-origin', body: fd, headers: { 'Accept': 'application/json' } })
					.then(function(res){ if (!res.ok) throw res; return res.json().catch(function(){ return null; }); })
					.then(function(body){
						try {
							var btn = form.querySelector('button[type="submit"]');
							if (btn) {
								var was = btn.textContent && btn.textContent.indexOf('☑') !== -1;
								btn.textContent = was ? '☐' : '☑';
								try { var doneInput = form.querySelector('input[name="done"]'); if (doneInput) { doneInput.value = was ? 'true' : 'false'; } } catch(_){ }
							}
							var status = document.getElementById('autosave-status'); if (status && body && body.message) status.textContent = body.message; else if (status) status.textContent = '';
						} catch(_){ }
					})
					.catch(function(err){ try { form.submit(); } catch(e) { try{ console.error('complete toggle failed', err); }catch(_){ } } });
			}catch(_){ }
		});
	} catch(e) { }
})();

// Fallback: ensure the insert-link helper exists
(function(){
	try{
		if (typeof window.__ft_insert_selected_link === 'function') return;
		var sel = document.querySelector('[id^="note-link-target-"]');
		var ta = document.getElementById('note-textarea');
		if (!sel || !ta) return;
		function insertAtCursor(textarea, ins){ try{ var start = textarea.selectionStart||0; var end = textarea.selectionEnd||0; var v = textarea.value||''; textarea.value = v.slice(0,start) + ins + v.slice(end); var pos = start + ins.length; textarea.setSelectionRange(pos,pos); textarea.focus(); }catch(_){ } }
		window.__ft_insert_selected_link = function(){
			try{
				var v = sel && sel.value ? sel.value : '';
				if(!v) return false;
				var parts = v.split(':'); if (parts.length !== 2) return false;
				var t = parts[0], id = parts[1];
				var opt = sel.options[sel.selectedIndex];
				var human = '';
				if (opt){
					human = opt.getAttribute('data-snippet') || '';
					if (!human){ var txt = (opt.textContent||'').trim(); var sep = ' — '; var idx = txt.indexOf(sep); human = (idx >= 0) ? txt.slice(idx+sep.length).trim() : ''; }
				}
				function esc(s){ return String(s||'').replace(/\s+/g,' ').replace(/\\/g,'\\\\').replace(/"/g,'\\"'); }
				var commentArg = human ? (', comment="'+esc(human)+'"') : '';
				var markup = '{' + '{' + 'fn:link target=' + t + ':' + id + commentArg + '}' + '}';
				insertAtCursor(ta, (ta.value && ta.value.length ? '\n' : '') + markup + '\n');
				try { ta.setAttribute('data-ft-inserted', '1'); } catch(_){ }
				try { var evt = new Event('input', { bubbles: true }); ta.dispatchEvent(evt); } catch(_){ }
				try { window.dispatchEvent(new CustomEvent('ft:insert_link', { detail: { target: t, id: id, markup: markup } })); } catch(_){ }
				return true;
			}catch(_){ return false; }
		};
	}catch(_){ }
})();

// Minimal rename for sublists (same behavior as index): prompt and POST
(function(){
	try{
		document.querySelectorAll('.edit-list-btn').forEach(function(btn){
			btn.addEventListener('click', function(){
				try{
					var id = btn.getAttribute('data-list-id');
					var current = btn.getAttribute('data-list-name') || '';
					var name = window.prompt('New list name', current);
					if (!name || name.trim() === '') return;
					var fd = new FormData();
					fd.append('name', name);
					var csrfInput = document.querySelector('input[name="_csrf"]');
					if (csrfInput && csrfInput.value) fd.append('_csrf', csrfInput.value);
					fetch('/html_no_js/lists/' + encodeURIComponent(id) + '/edit', { method: 'POST', body: fd, credentials: 'same-origin' })
						.then(function(res){ if (!res.ok) throw new Error('Rename failed'); return res.json().catch(function(){ return null; }); })
						.then(function(){ try{ if (window.__ft_onListRenamed) window.__ft_onListRenamed(id, name); } catch(_){ } })
						.catch(function(){ try{ alert('Rename failed'); }catch(_){ } });
				}catch(_){ }
			});
		});
	}catch(e){ }
})();

// Delegate todo-level forms (delete, pin, tag remove/add) to use fetch + DOM updates
(function(){
	try{
		function todoFormMatch(action){ try { return action && action.match(/\/html_no_js\/todos\/(\d+)\/(delete|pin|hashtags|hashtags\/remove)/); } catch(e){ return null; } }
		function todoJsonMatch(action){
			try {
				if (!action) return null;
				var a = String(action);
				var base = a.split('?')[0];
				var m = base.match(/\/todos\/(\d+)\/hashtags(\/remove)?$/);
				return m;
			} catch(e){ return null; }
		}
		document.body.addEventListener('submit', function(ev){
			var form = ev.target; if (!form || form.tagName !== 'FORM') return; var action = (form.action || form.getAttribute('action') || '');
			// JSON endpoint for add tag on todo
			var jm = todoJsonMatch(action);
			if (jm) {
				ev.preventDefault();
				try{
					var todoId = jm[1];
					var tagInput = form.querySelector('input[name="tag"]');
					var tagVal = tagInput ? tagInput.value : '';
					if (!tagVal || !tagVal.trim()) return;
					// Endpoint expects `tag` as a query param (FastAPI query dependency)
					var base = (action || '').split('?')[0];
					var url = base + '?tag=' + encodeURIComponent(tagVal);
					// credentials same-origin for cookie/session
					fetch(url, { method: 'POST', credentials: 'same-origin' })
						.then(function(res){ if (!res.ok) throw new Error('Add tag failed'); return res.json().catch(function(){ return null; }); })
						.then(function(){
							try{
								// Insert new tag chip before the form, matching existing markup
								var div = document.createElement('div'); div.setAttribute('role','listitem'); div.style.display='inline-block'; div.style.marginRight='0.4rem';
								var a = document.createElement('a'); a.className='tag-chip'; a.href='/html_no_js/search?q=' + encodeURIComponent(tagVal); a.textContent = tagVal; div.appendChild(a);
								var remBtn = document.createElement('button'); remBtn.type='button'; remBtn.className='tag-remove'; remBtn.innerHTML='<span aria-hidden="true">✖</span><span class="sr-only">Remove ' + tagVal + '</span>';
								remBtn.addEventListener('click', function(){
									try{
										var url = '/todos/' + encodeURIComponent(todoId) + '/hashtags?tag=' + encodeURIComponent(tagVal);
										fetch(url, { method: 'DELETE', credentials: 'same-origin' })
											.then(function(r){ if (!r.ok) throw new Error('Remove tag failed'); return r.json().catch(function(){ return null; }); })
											.then(function(){ try{ div.remove(); }catch(_){ } })
											.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
									}catch(_){ }
								});
								var remWrap = document.createElement('form'); remWrap.style.display='inline'; remWrap.appendChild(remBtn);
								div.appendChild(remWrap);
								if (form.parentElement) form.parentElement.insertBefore(div, form);
								try{ tagInput.value=''; }catch(_){ }
							}catch(_){ }
						})
						.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
				}catch(_){ }
				return;
			}
			// legacy html_no_js handlers
			var m = todoFormMatch(action); if (!m) return; ev.preventDefault();
			var todoId = m[1]; var fd = new FormData(form); var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
			fetch(action, { method: 'POST', body: fd, credentials: 'same-origin' })
				.then(function(res){ if (!res.ok) throw new Error('Request failed'); return res.text().catch(function(){ return null; }); })
				.then(function(){
					try{
						if (action.indexOf('/delete') !== -1) {
							var listInput = form.querySelector('input[name="list_id"]');
							var listId = listInput ? listInput.value : null;
							var anchor = form.querySelector('input[name="anchor"]');
							var anchorId = anchor ? anchor.value : ('todo-' + todoId);
							var li = document.getElementById(anchorId) || document.getElementById('todo-' + todoId);
							if (li) { li.remove(); return; }
							if (listId) { try { window.location.href = '/html_no_js/lists/' + encodeURIComponent(listId); return; } catch(_){ } }
							try { window.location.href = '/html_no_js/'; return; } catch(_){ }
						}
						if (action.indexOf('/hashtags/remove') !== -1) {
							var wrapper = form.closest('[role="listitem"]') || form.parentElement; if (wrapper) wrapper.remove(); return;
						}
						if (action.indexOf('/hashtags') !== -1) {
							var tag = form.querySelector('input[name="tag"]'); if (tag && tag.value) {
								var div = document.createElement('div'); div.setAttribute('role','listitem'); div.style.display='inline-block'; div.style.marginRight='0.4rem';
								var a = document.createElement('a'); a.className='tag-chip'; a.href='/html_no_js/search?q=' + encodeURIComponent(tag.value); a.textContent = tag.value; div.appendChild(a);
								var remForm = document.createElement('form'); remForm.method='post'; remForm.action = action + '/remove'; remForm.style.display='inline'; var inp = document.createElement('input'); inp.type='hidden'; inp.name='tag'; inp.value=tag.value; remForm.appendChild(inp); var btn2 = document.createElement('button'); btn2.type='submit'; btn2.className='tag-remove'; btn2.innerHTML='<span aria-hidden="true">✖</span><span class="sr-only">Remove ' + tag.value + '</span>'; remForm.appendChild(btn2); div.appendChild(remForm);
								if (form.parentElement) form.parentElement.insertBefore(div, form);
							}
						}
					}catch(_){ }
				})
				.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
		}, false);
	}catch(e){ }
})();

// Handle remove tag button clicks with JSON endpoint
(function(){
	try{
		document.body.addEventListener('click', function(ev){
			var btn = ev.target; if (!btn || btn.tagName !== 'BUTTON') return;
			if (!btn.hasAttribute('data-ft-remove-tag')) return;
			ev.preventDefault();
			ev.stopPropagation();
			try{
				var todoId = btn.getAttribute('data-todo-id');
				var tag = btn.getAttribute('data-tag');
				if (!todoId || !tag) return;
				var url = '/todos/' + encodeURIComponent(todoId) + '/hashtags?tag=' + encodeURIComponent(tag);
				fetch(url, { method: 'DELETE', credentials: 'same-origin' })
					.then(function(res){ if (!res.ok) throw new Error('Remove tag failed'); return res.json().catch(function(){ return null; }); })
					.then(function(){
						try{
							var wrapper = btn.closest('[role="listitem"]') || btn.parentElement;
							if (wrapper) wrapper.remove();
						}catch(_){ }
					})
					.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
			}catch(_){ }
		}, false);
	}catch(e){ }
})();

// Handle remove list tag button clicks with JSON endpoint
(function(){
	try{
		document.body.addEventListener('click', function(ev){
			// Check if the clicked element or any ancestor is a button with the data attribute
			var btn = ev.target;
			while (btn && btn.tagName !== 'BUTTON') {
				btn = btn.parentElement;
			}
			if (!btn || !btn.hasAttribute('data-ft-remove-list-tag')) return;
			ev.preventDefault();
			ev.stopPropagation();
			try{
				var listId = btn.getAttribute('data-list-id');
				var tag = btn.getAttribute('data-tag');
				if (!listId || !tag) return;
				var url = '/lists/' + encodeURIComponent(listId) + '/hashtags/json';
				fetch(url, { 
					method: 'DELETE', 
					body: JSON.stringify({ tag: tag }), 
					credentials: 'same-origin',
					headers: { 'Content-Type': 'application/json' }
				})
					.then(function(res){ if (!res.ok) throw new Error('Remove list tag failed'); return res.json().catch(function(){ return null; }); })
					.then(function(){
						try{
							var wrapper = btn.closest('[role="listitem"]') || btn.parentElement;
							if (wrapper) wrapper.remove();
						}catch(_){ }
					})
					.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
			}catch(_){ }
		}, false);
	}catch(e){ }
})();

// Handle add list tag form with JSON endpoint
(function(){
	try{
		document.body.addEventListener('submit', function(ev){
			var form = ev.target; if (!form || form.tagName !== 'FORM') return;
			if (!form.hasAttribute('data-ft-add-list-tag')) return;
			ev.preventDefault();
			try{
				var listId = form.getAttribute('data-list-id');
				var tagInput = form.querySelector('input[name="tag"]');
				var tagVal = tagInput ? tagInput.value : '';
				if (!tagVal || !tagVal.trim()) return;
				var url = '/lists/' + encodeURIComponent(listId) + '/hashtags/json';
				fetch(url, { 
					method: 'POST', 
					body: JSON.stringify({ tag: tagVal }), 
					credentials: 'same-origin',
					headers: { 'Content-Type': 'application/json' }
				})
					.then(function(res){ if (!res.ok) throw new Error('Add list tag failed'); return res.json().catch(function(){ return null; }); })
					.then(function(){
						try{
							// Insert new tag chip before the form, matching existing markup
							var div = document.createElement('div'); div.setAttribute('role','listitem'); div.style.display='inline-block'; div.style.marginRight='0.4rem';
							var a = document.createElement('a'); a.className='tag-chip'; a.href='/html_no_js/search?q=' + encodeURIComponent(tagVal); a.textContent = tagVal; div.appendChild(a);
							var remBtn = document.createElement('button'); remBtn.type='button'; remBtn.className='tag-remove'; remBtn.innerHTML='<span aria-hidden="true">✖</span><span class="sr-only">Remove ' + tagVal + '</span>';
							remBtn.addEventListener('click', function(){
								try{
									var url = '/lists/' + encodeURIComponent(listId) + '/hashtags/json';
									fetch(url, { 
										method: 'DELETE', 
										body: JSON.stringify({ tag: tagVal }), 
										credentials: 'same-origin',
										headers: { 'Content-Type': 'application/json' }
									})
										.then(function(r){ if (!r.ok) throw new Error('Remove list tag failed'); return r.json().catch(function(){ return null; }); })
										.then(function(){ try{ div.remove(); }catch(_){ } })
										.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
								}catch(_){ }
							});
							var remWrap = document.createElement('form'); remWrap.style.display='inline'; remWrap.appendChild(remBtn);
							div.appendChild(remWrap);
							// Find the tags container and add the new tag
							var tagsContainer = document.querySelector('.main-list-tags');
							if (tagsContainer) {
								tagsContainer.appendChild(div);
							} else {
								// Create tags container if it doesn't exist
								var newTagsContainer = document.createElement('div'); newTagsContainer.className='tags main-list-tags'; newTagsContainer.setAttribute('role','list'); newTagsContainer.setAttribute('aria-label','List tags');
								newTagsContainer.appendChild(div);
								var addForm = document.querySelector('form[data-ft-add-list-tag]');
								if (addForm && addForm.parentElement) {
									addForm.parentElement.insertBefore(newTagsContainer, addForm);
								}
							}
							try{ tagInput.value=''; }catch(_){ }
						}catch(_){ }
					})
					.catch(function(){ try{ alert('Action failed'); }catch(_){ } });
			}catch(_){ }
		}, false);
	}catch(e){ }
})();

// Priority select: POST via fetch and update header priority circle
(function(){
	try {
		var sel = document.querySelector('[id^="todo-priority-"]');
		if (!sel) return;
		sel.addEventListener('change', function(){
			try{
				var v = sel.value; var fd = new FormData(); fd.append('priority', v);
				var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
				var path = (sel.getAttribute('id')||'').split('todo-priority-')[1] || null;
				var action = path ? ('/html_no_js/todos/' + path + '/priority') : ('/html_no_js/todos/priority');
				fetch(action, { method: 'POST', body: fd, credentials: 'same-origin' })
					.then(function(res){ if (!res.ok) throw new Error('Priority update failed'); return res.json().catch(function(){ return null; }); })
					.then(function(){ try{ var circ = {1:'①',2:'②',3:'③',4:'④',5:'⑤',6:'⑥',7:'⑦',8:'⑧',9:'⑨',10:'⑩'}; var el = document.querySelector('.todo-header .priority-circle'); if (el) el.textContent = v ? (circ[v] || v) : ''; }catch(_){ } })
					.catch(function(){ try{ console && console.error && console.error('Priority update failed'); }catch(_){ } });
			}catch(_){ }
		});
	} catch(e){}
})();

// lists_up_top checkbox: POST via fetch
(function(){
	try{
		var cb = document.querySelector('[id^="lists_up_top_checkbox_"]');
		var hidden = document.querySelector('[id^="lists_up_top_hidden_"]');
		if (!cb || !hidden) return;
		cb.addEventListener('change', function(){
			try{
				hidden.value = cb.checked ? 'true' : 'false';
				var fd = new FormData(); fd.append('lists_up_top', hidden.value);
				var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
				var action = (hidden.closest('form') && hidden.closest('form').action) || '/html_no_js/todos/lists_up_top';
				fetch(action, { method: 'POST', body: fd, credentials: 'same-origin' }).then(function(res){ if (!res.ok) { try{ console && console.error && console.error('lists_up_top update failed'); }catch(_){ } } });
			}catch(_){ }
		});
	}catch(e){ }
})();

// Delegate sublist move for todo
(function(){
	try{
		function matchSublistMove(action){ try{ var m = action && action.match(/\/html_no_js\/todos\/(?:\d+)\/sublists\/(\d+)\/move/); return m; }catch(e){ return null; } }
		document.body.addEventListener('submit', function(ev){
			var form = ev.target; if (!form || form.tagName !== 'FORM') return; var action = form.getAttribute('action') || '';
			var m = matchSublistMove(action); if (!m) return; ev.preventDefault();
			try{
				var sublistId = m[1]; var fd = new FormData(form); var dir = fd.get('direction'); var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
				fetch(action, { method: 'POST', body: fd, credentials: 'same-origin' })
					.then(function(res){ if (!res.ok) throw new Error('Move failed'); return res.text().catch(function(){ return null; }); })
					.then(function(){ try{ var selector = 'a.list-title[href*="/html_no_js/lists/' + sublistId + '"]'; var a = document.querySelector(selector); if (!a) return; var liNode = a.closest('li.list-item'); if (!liNode) return; var parentUl = liNode.parentElement; if (!parentUl) return; if (dir === 'up') { var prev = liNode.previousElementSibling; if (prev) parentUl.insertBefore(liNode, prev); } else if (dir === 'down') { var next = liNode.nextElementSibling; if (next) parentUl.insertBefore(next, liNode); } }catch(_){ } })
					.catch(function(){ try{ alert('Move failed'); }catch(_){ } });
			}catch(_){ }
		}, false);
	}catch(e){ }
})();

// Intercept sublist creation and insert new sublist into the DOM using the JSON endpoint
(function(){
	try{
		function escapeHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;'); }
		document.body.addEventListener('submit', function(ev){
			var form = ev.target; if (!form || form.tagName !== 'FORM') return; var action = form.getAttribute('action') || '';
			if (action.indexOf('/sublists/create') === -1) return; ev.preventDefault();
			try{
				var fd = new FormData(form);
				var csrf = document.querySelector('input[name="_csrf"]'); if (csrf && csrf.value) fd.append('_csrf', csrf.value);
				fetch(action, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
					.then(function(res){ if (!res.ok) throw new Error('Create sublist failed'); return res.json().catch(function(){ return null; }); })
					.then(function(sub){
						if (!sub || !sub.id) return;
						try{
							var ul = form.closest('section') ? form.closest('section').querySelector('.lists-list') : null;
							if (!ul) ul = document.querySelector('.lists-list');
							if (!ul){ ul = document.createElement('ul'); ul.className = 'lists-list'; if (form.parentNode) form.parentNode.insertBefore(ul, form.nextSibling); }
							var li = document.createElement('li'); li.className = 'list-item'; li.id = 'list-' + sub.id;
							li.innerHTML = '<div class="list-action-left" style="display:flex;gap:0.25rem;align-items:center;">'
								+ '<form method="post" action="' + action.replace('/sublists/create','/sublists/' + sub.id + '/move') + '" style="display:inline">'
								+ '<input type="hidden" name="direction" value="up">'
								+ '<button type="submit" class="list-action-btn" title="Move up">⬆️</button></form>'
								+ '<form method="post" action="' + action.replace('/sublists/create','/sublists/' + sub.id + '/move') + '" style="display:inline">'
								+ '<input type="hidden" name="direction" value="down">'
								+ '<button type="submit" class="list-action-btn" title="Move down">⬇️</button></form></div>'
								+ '<div class="list-main"><a class="list-title" href="/html_no_js/lists/' + sub.id + '">' + escapeHtml(sub.name) + '</a>'
								+ ' <button type="button" class="list-action-btn edit-list-btn" data-list-id="' + sub.id + '" data-list-name="' + escapeHtml(sub.name) + '" title="Edit list name">✏️</button></div>';
							ul.appendChild(li);
							// Clear and refocus the sublist name input for faster consecutive entries
							try {
								var nameInput = form.querySelector('input[name="name"]');
								if (nameInput) { nameInput.value = ''; nameInput.focus(); }
							} catch(_) {}
						}catch(_){ }
					})
					.catch(function(){ try{ alert('Create sublist failed'); }catch(_){ } });
			}catch(_){ }
		}, false);
	}catch(e){ }
})();

// Submit lists_up_top form (non-AJAX fallback behavior trigger)
(function(){
	try{
		var cb = document.querySelector('[id^="lists_up_top_checkbox_"]');
		var hidden = document.querySelector('[id^="lists_up_top_hidden_"]');
		var form = hidden && hidden.closest('form');
		if (!cb || !hidden || !form) return;
		cb.addEventListener('change', function(){ try{ hidden.value = cb.checked ? 'true' : 'false'; form.submit(); } catch(_){ } });
	}catch(e){ }
})();

// Collation dots: toggle add/remove to active collations via JSON endpoint
(function(){
	try{
		function postJSON(url, data){
			return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data||{}) })
				.then(function(r){ return r.json().catch(function(){ return {}; }); });
		}
		var buttons = document.querySelectorAll('button.collation-dot');
		if (!buttons || !buttons.length) return;
		buttons.forEach(function(btn){
			btn.addEventListener('click', function(){
				try{
					var lid = parseInt(btn.getAttribute('data-list-id'),10);
					var tid = parseInt(btn.getAttribute('data-todo-id'),10);
					var pressed = String(btn.getAttribute('aria-pressed')) === 'true';
					var want = !pressed;
					postJSON('/client/json/collations/'+lid+'/toggle', { todo_id: tid, link: want }).then(function(j){
						if (!j || j.ok !== true) return;
						var linked = !!j.linked;
						btn.setAttribute('aria-pressed', linked ? 'true' : 'false');
						var nm = btn.getAttribute('data-name') || '';
						btn.title = (linked ? 'Remove from ' : 'Add to ') + nm;
					}).catch(function(){});
				}catch(_){ }
			});
		});
	}catch(e){ }
})();

// Link picker (Add link section): populate from local marks and wire submit
(function(){
	try{
		var KEY='ft_marks_v1'; var TTL=5*60*1000;
		function load(){ try { return JSON.parse(localStorage.getItem(KEY)||'{}')||{}; } catch(_){ return {}; } }
		function prune(o){ var t=Date.now(); var out={todos:{},lists:{}}; if(o.todos) for(var k in o.todos){ if((t-(o.todos[k]||0))<TTL) out.todos[k]=o.todos[k]; } if(o.lists) for(var k2 in o.lists){ if((t-(o.lists[k2]||0))<TTL) out.lists[k2]=o.lists[k2]; } return out; }
		var todoIdEl = document.querySelector('[id^="add-link-form-todo-"]');
		if (!todoIdEl) return;
		var formId = todoIdEl.getAttribute('id') || '';
		var todoId = (formId.split('add-link-form-todo-')[1]) || '';
		var sel = document.getElementById('link-target-todo-' + todoId);
		if (!sel) return;
		var emptyMsg = document.getElementById('link-empty-todo-' + todoId);
		var marks=prune(load()); var count=0;
		function addOpt(v,t,parent){ var o=document.createElement('option'); o.value=v; o.textContent=t; (parent||sel).appendChild(o); count++; }
		if (marks.todos && Object.keys(marks.todos).length){ var og=document.createElement('optgroup'); og.label='Todos'; sel.appendChild(og); Object.keys(marks.todos).sort(function(a,b){return (+a)-(+b);}).forEach(function(id){ addOpt('todo:'+id, 'Todo #'+id, og); }); }
		if (marks.lists && Object.keys(marks.lists).length){ var og2=document.createElement('optgroup'); og2.label='Lists'; sel.appendChild(og2); Object.keys(marks.lists).sort(function(a,b){return (+a)-(+b);}).forEach(function(id){ addOpt('list:'+id, 'List #'+id, og2); }); }
		if (!count && emptyMsg){ emptyMsg.style.display='block'; }
		var form = document.getElementById('add-link-form-todo-' + todoId);
		if (form){ form.addEventListener('submit', function(e){ try{ var v=sel.value||''; if(!v){ e.preventDefault(); alert('Select a marked item first.'); return; } var parts=v.split(':'); if(parts.length!==2){ e.preventDefault(); alert('Invalid selection'); return; } var t=parts[0], id=parts[1]; form.querySelector('input[name="tgt_type"]').value=t; form.querySelector('input[name="tgt_id"]').value=id; }catch(err){ e.preventDefault(); } }, false); }
	}catch(e){ }
})();

// Client-side sort of sublists by effective priority desc, then parent_list_position asc
(function(){
	try{
		var uls = document.querySelectorAll('ul.lists-list');
		if (!uls || !uls.length) return;
		function parseIntOrNull(v){ if (v === null || typeof v === 'undefined' || v === '') return null; var n = parseInt(v, 10); return isNaN(n) ? null : n; }
		function effPriority(el){
			var comp = (el.getAttribute('data-completed') || '').toLowerCase() === 'true';
			if (comp) return null;
			var p = parseIntOrNull(el.getAttribute('data-priority'));
			if (p !== null) return p;
			var lp = parseIntOrNull(el.getAttribute('data-list-priority'));
			var op = parseIntOrNull(el.getAttribute('data-override-priority'));
			if (op === null && lp === null) return null;
			if (op === null) return lp;
			if (lp === null) return op;
			return op > lp ? op : lp;
		}
		uls.forEach(function(ul){
			var items = Array.from(ul.querySelectorAll('li.list-item'));
			if (!items.length) return;
			items.sort(function(a, b){
				var ap = effPriority(a);
				var bp = effPriority(b);
				if (ap === null && bp !== null) return 1;
				if (bp === null && ap !== null) return -1;
				if (ap !== null && bp !== null){ if (bp !== ap) return bp - ap; }
				var ao = parseIntOrNull(a.getAttribute('data-parent-list-position'));
				var bo = parseIntOrNull(b.getAttribute('data-parent-list-position'));
				if (ao === null && bo !== null) return 1;
				if (bo === null && ao !== null) return -1;
				if (ao !== null && bo !== null){ if (ao !== bo) return ao - bo; }
				return 0;
			});
			items.forEach(function(li){ ul.appendChild(li); });
		});
	}catch(e){ }
})();

// CalcDict integration: Calculate button posts note to /client/json/calcdict and shows output
(function(){
	try{
		var ta = document.getElementById('note-textarea');
		if (!ta) return;
		function postJSON(url, data){
			return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data||{}) })
				.then(function(r){ return r.json().catch(function(){ return {}; }); });
		}
		var buttons = document.querySelectorAll('[id^="note-calc-btn-"]');
		if (!buttons || !buttons.length) return;
		buttons.forEach(function(btn){
			btn.addEventListener('click', function(){
				try{
					var todoId = (btn.id.split('note-calc-btn-')[1]) || '';
					var name = 'todo-' + (todoId || 'note');
					var input_text = ta.value || '';
					var status = document.getElementById('autosave-status'); if (status) status.textContent = 'Calculating...';
					postJSON('/client/json/calcdict', { name: name, input_text: input_text }).then(function(j){
						try{
							var wrap = document.getElementById('note-calc-output-wrap-' + todoId) || document.querySelector('[id^="note-calc-output-wrap-"]');
							var out = document.getElementById('note-calc-output-' + todoId) || document.querySelector('[id^="note-calc-output-"]');
							if (!wrap || !out){ if (status) status.textContent = 'Calc ready (UI missing)'; return; }
							if (j && j.ok && typeof j.output === 'string'){
								out.value = j.output;
								wrap.style.display = 'block';
								try{ wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }catch(_){ }
								if (status) status.textContent = 'Calculated';
								setTimeout(function(){ try{ if (status && status.textContent === 'Calculated') status.textContent = ''; }catch(_){ } }, 1500);
							} else {
								out.value = (j && j.error) ? ('Error: ' + j.error) : 'Calculation failed';
								wrap.style.display = 'block';
								if (status) status.textContent = 'Calc failed';
							}
						}catch(_){ }
					}).catch(function(){ try{ var status = document.getElementById('autosave-status'); if (status) status.textContent = 'Calc failed'; }catch(_){ } });
				}catch(_){ }
			});
		});
	}catch(e){ }
})();

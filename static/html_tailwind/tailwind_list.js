// Minimal list page client for Tailwind list view
window.tailwindList = (function () {
		async function patchJson(url, obj) {
			const resp = await fetch(url, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(obj),
				credentials: 'same-origin',
			});
			if (!resp.ok) throw new Error('request failed: ' + resp.status);
			return resp.json();
		}

		// Toast/snackbar helper
		function showToast(message, opts = {}) {
			// opts: { type: 'info'|'success'|'error', timeout: ms, undoLabel, undoCallback }
			const container = document.getElementById('toast-container');
			if (!container) {
				console.warn('toast container not found');
				return { dismiss: () => {} };
			}
			const id = 'toast-' + Math.random().toString(36).slice(2, 9);
			const el = document.createElement('div');
			el.id = id;
			el.className = 'pointer-events-auto max-w-sm w-full bg-slate-800 border border-slate-700 text-slate-100 px-4 py-2 rounded shadow-lg flex items-center gap-3';
			if (opts.type === 'error') el.classList.add('border-red-500');
			if (opts.type === 'success') el.classList.add('border-green-500');
			const msg = document.createElement('div');
			msg.className = 'flex-1 text-sm';
			msg.textContent = message;
			el.appendChild(msg);

			const actions = document.createElement('div');
			actions.className = 'flex items-center gap-2';
			if (opts.undoLabel && typeof opts.undoCallback === 'function') {
				const undoBtn = document.createElement('button');
				undoBtn.className = 'text-xs text-sky-300 hover:text-sky-400';
				undoBtn.textContent = opts.undoLabel;
				undoBtn.addEventListener('click', () => {
					try { opts.undoCallback(); } catch (e) { console.error(e); }
					container.removeChild(el);
				});
				actions.appendChild(undoBtn);
			}
			const close = document.createElement('button');
			close.className = 'text-xs text-slate-400 hover:text-slate-300';
			close.textContent = '×';
			close.addEventListener('click', () => { if (el.parentNode) el.parentNode.removeChild(el); });
			actions.appendChild(close);
			el.appendChild(actions);

			container.appendChild(el);
			const timeout = typeof opts.timeout === 'number' ? opts.timeout : 4000;
			const timer = setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, timeout);
			return { dismiss: () => { clearTimeout(timer); if (el.parentNode) el.parentNode.removeChild(el); } };
		}

	function initPriorityHandler() {
			const sel = document.getElementById('list-priority-select');
			if (!sel) return;
			const listId = sel.dataset.listId || sel.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || '';
				sel.addEventListener('change', async (e) => {
					const val = sel.value === '' ? null : Number(sel.value);
					sel.disabled = true;
					try {
						if (!listId) throw new Error('missing list id');
						await patchJson(`/lists/${encodeURIComponent(listId)}`, { priority: val });
						showToast('Priority saved', { type: 'success' });
					} catch (err) {
						showToast('Failed to save priority: ' + (err?.message || String(err)), { type: 'error' });
						console.error('failed to save priority', err);
					} finally {
						sel.disabled = false;
					}
				});
	}

		function initNameEdit() {
			const display = document.getElementById('list-name-display');
			const editWrap = document.getElementById('list-name-edit');
			const input = document.getElementById('list-name-input');
			const controls = document.getElementById('list-edit-controls');
			const saveBtn = document.getElementById('list-save-btn');
			const cancelBtn = document.getElementById('list-cancel-btn');
			if (!display || !editWrap || !input || !controls || !saveBtn || !cancelBtn) return;
			const listId = input.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || input.dataset?.listId || '';

			display.addEventListener('click', () => {
				display.classList.add('hidden');
				editWrap.classList.remove('hidden');
				controls.classList.remove('hidden');
				input.focus();
			});

			cancelBtn.addEventListener('click', () => {
				editWrap.classList.add('hidden');
				controls.classList.add('hidden');
				display.classList.remove('hidden');
				input.value = display.textContent.trim();
			});

			saveBtn.addEventListener('click', async () => {
				const newName = input.value.trim();
				if (!newName) return;
				saveBtn.disabled = true;
				try {
					const res = await patchJson(`/lists/${encodeURIComponent(listId)}`, { name: newName });
					// update display from server response if present
					if (res && res.name) display.textContent = res.name;
					} catch (err) {
						showToast('Failed to save list name', { type: 'error' });
				} finally {
					saveBtn.disabled = false;
					editWrap.classList.add('hidden');
					controls.classList.add('hidden');
					display.classList.remove('hidden');
				}
			});
		}

	async function fetchAndRenderTags() {
		const wrap = document.getElementById('list-tags');
		const listRoot = document.getElementById('list-root');
		if (!wrap || !listRoot) return;
		const listId = wrap.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || wrap.dataset?.listId || '';
		try {
			const resp = await fetch(`/lists/${encodeURIComponent(listId)}/hashtags?combine=true`, { credentials: 'same-origin' });
			if (!resp.ok) throw new Error('failed to fetch tags');
			const data = await resp.json();
			const tags = data.hashtags || data.list_hashtags || [];
			wrap.innerHTML = '';
			if (!tags || tags.length === 0) {
				const ph = document.createElement('span');
				ph.id = 'no-tags-placeholder';
				ph.className = 'text-xs text-slate-500';
				ph.textContent = 'No tags';
				wrap.appendChild(ph);
				return;
			}
			for (const t of tags) {
				// normalize incoming tag: server may return '#tag' or 'tag'.
				const raw = (typeof t === 'string') ? t : String(t);
				const tagNorm = raw.startsWith('#') ? raw.slice(1) : raw;
				const chip = document.createElement('span');
				chip.className = 'tag-chip inline-flex items-center text-xs px-2 py-1 rounded-full bg-slate-700 text-slate-200';
				const a = document.createElement('a');
				a.href = `/html_tailwind/index?tag=${encodeURIComponent(tagNorm)}`;
				a.className = 'mr-2 hover:underline';
				a.textContent = '#' + tagNorm;
				const btn = document.createElement('button');
				btn.className = 'remove-tag-btn text-xs text-slate-400 hover:text-red-400';
				// store dataset.tag without leading '#', so client POST/DELETE can send plain tag
				btn.dataset.tag = tagNorm;
				btn.innerHTML = '&times;';
				btn.addEventListener('click', async (e) => {
					if (!confirm(`Remove tag #${t}?`)) return;
								btn.disabled = true;
								let undo = null;
								try {
									const resp = await fetch(`/lists/${encodeURIComponent(listId)}/hashtags/json`, {
										method: 'DELETE',
										headers: { 'Content-Type': 'application/json' },
										body: JSON.stringify({ tag: t }),
										credentials: 'same-origin',
									});
									if (!resp.ok) throw new Error('remove failed: ' + resp.status);
									await fetchAndRenderTags();
									// show undo toast
									undo = () => {
										// re-add tag
										fetch(`/lists/${encodeURIComponent(listId)}/hashtags/json`, {
											method: 'POST',
											headers: { 'Content-Type': 'application/json' },
											body: JSON.stringify({ tag: t }),
											credentials: 'same-origin',
										}).then(() => fetchAndRenderTags()).catch(e => console.error('undo add tag failed', e));
									};
									showToast(`Removed #${t}`, { type: 'info', undoLabel: 'Undo', undoCallback: undo, timeout: 6000 });
								} catch (err) {
									showToast('Failed to remove tag', { type: 'error' });
									console.error('failed to remove tag', err);
								} finally {
									btn.disabled = false;
								}
				});
				chip.appendChild(a);
				chip.appendChild(btn);
				wrap.appendChild(chip);
			}
		} catch (err) {
			console.error('tags fetch failed', err);
		}
	}

	function initTagEditor() {
		const addInput = document.getElementById('add-tag-input');
		const addBtn = document.getElementById('add-tag-btn');
		const wrap = document.getElementById('list-tags');
		if (!addInput || !addBtn || !wrap) return;
		const listId = wrap.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || wrap.dataset?.listId || '';
		addBtn.addEventListener('click', async () => {
			const raw = addInput.value.trim();
			if (!raw) return;
			const tag = raw.startsWith('#') ? raw.slice(1) : raw;
			// prevent duplicates (case-insensitive)
			const exists = Array.from(wrap.querySelectorAll('button[data-tag]')).some(b => b.dataset.tag.toLowerCase() === tag.toLowerCase());
			if (exists) {
				alert('Tag already present');
				addInput.value = '';
				return;
			}
			addBtn.disabled = true;
			try {
				await fetch(`/lists/${encodeURIComponent(listId)}/hashtags/json`, {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ tag }),
					credentials: 'same-origin',
				});
				addInput.value = '';
				await fetchAndRenderTags();
				} catch (err) {
					showToast('Failed to add tag', { type: 'error' });
					console.error('failed to add tag', err);
			} finally {
				addBtn.disabled = false;
			}
		});
		addInput.addEventListener('keydown', (e) => {
			if (e.key === 'Enter') {
				e.preventDefault();
				addBtn.click();
			}
		});
	}

	function init() {
		try {
			initPriorityHandler();
			initNameEdit();
			initCompleteToggle();
			fetchAndRenderTags();
			initTagEditor();
		} catch (err) {
			console.error('init list page failed', err);
		}
	}

	function initCompleteToggle() {
		const cb = document.getElementById('list-complete-toggle');
		if (!cb) return;
		const listId = cb.dataset.listId || cb.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || '';
		cb.addEventListener('change', async () => {
			const val = !!cb.checked;
			cb.disabled = true;
			try {
				await patchJson(`/lists/${encodeURIComponent(listId)}`, { completed: val });
			} catch (err) {
				console.error('failed to save completed toggle', err);
			} finally {
				cb.disabled = false;
			}
		});
	}

	// Auto-init if DOM ready
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}

	return { init };
})();

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

		async function postJson(url, obj) {
			const resp = await fetch(url, {
				method: 'POST',
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

	function initEditButton() {
		const editBtn = document.getElementById('edit-list-name-btn');
		const display = document.getElementById('list-name-display');
		if (!editBtn || !display) return;

		const listId = editBtn.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId ||
		               display.closest('#list-root')?.querySelector('[data-list-id]')?.dataset?.listId || '';

		editBtn.addEventListener('click', async () => {
			const currentName = display.textContent.trim();
			const newName = prompt('Edit list name:', currentName);

			// User cancelled or entered empty string
			if (newName === null || newName.trim() === '') return;

			const trimmedName = newName.trim();

			// No change
			if (trimmedName === currentName) return;

			editBtn.disabled = true;
			try {
				const res = await patchJson(`/lists/${encodeURIComponent(listId)}`, { name: trimmedName });
				// Update display from server response if present
				if (res && res.name) {
					display.textContent = res.name;
					showToast('List name updated', { type: 'success' });
				} else {
					showToast('List name updated', { type: 'success' });
				}
			} catch (err) {
				showToast('Failed to update list name: ' + (err?.message || String(err)), { type: 'error' });
				console.error('failed to update list name', err);
			} finally {
				editBtn.disabled = false;
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
				// wrapper: anchor (label) + sibling remove button so button clicks don't trigger anchor navigation
				const wrapItem = document.createElement('div');
				wrapItem.className = 'tag-wrapper inline-flex items-center';
				const chip = document.createElement('a');
				chip.href = `/html_no_js/search?q=${encodeURIComponent(tagNorm)}`;
				chip.className = 'tag-chip inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-semibold text-slate-100 border border-sky-500/30 hover:bg-sky-700/25 focus:outline-none focus:ring-2 focus:ring-sky-400';
				const label = document.createElement('span');
				label.className = 'label truncate';
				label.textContent = '#' + tagNorm;
				chip.appendChild(label);

				const btn = document.createElement('button');
				btn.className = 'remove';
				btn.setAttribute('aria-label', 'Remove tag ' + tagNorm);
				btn.title = 'Remove #' + tagNorm;
				btn.dataset.tag = tagNorm;
				btn.type = 'button';
				btn.innerHTML = '&times;';
				btn.addEventListener('click', async (e) => {
					e.stopPropagation();
					e.preventDefault();
					if (!confirm(`Remove tag #${tagNorm}?`)) return;
					btn.disabled = true;
					let undo = null;
					try {
						const resp = await fetch(`/lists/${encodeURIComponent(listId)}/hashtags/json`, {
							method: 'DELETE',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({ tag: tagNorm }),
							credentials: 'same-origin',
						});
						if (!resp.ok) throw new Error('remove failed: ' + resp.status);
						await fetchAndRenderTags();
						undo = () => {
							fetch(`/lists/${encodeURIComponent(listId)}/hashtags/json`, {
								method: 'POST',
								headers: { 'Content-Type': 'application/json' },
								body: JSON.stringify({ tag: tagNorm }),
								credentials: 'same-origin',
							}).then(() => fetchAndRenderTags()).catch(e => console.error('undo add tag failed', e));
						};
						showToast(`Removed #${tagNorm}`, { type: 'info', undoLabel: 'Undo', undoCallback: undo, timeout: 6000 });
					} catch (err) {
						showToast('Failed to remove tag', { type: 'error' });
						console.error('failed to remove tag', err);
					} finally {
						btn.disabled = false;
					}
				});
				wrapItem.appendChild(chip);
				wrapItem.appendChild(btn);
				wrap.appendChild(wrapItem);
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

	// Todo management functions
	let currentTodos = [];
	let currentCompletionTypes = [];

	async function fetchTodos() {
		const listRoot = document.getElementById('list-root');
		if (!listRoot) return;
		const listId = listRoot.querySelector('[data-list-id]')?.dataset?.listId || '';
		if (!listId) return;

		try {
			// Fetch todos
			const resp = await fetch(`/client/json/lists/${encodeURIComponent(listId)}/todos`, { credentials: 'same-origin' });
			if (!resp.ok) throw new Error('failed to fetch todos');
			currentTodos = await resp.json();
			
			// Sort todos by creation date in reverse order (newest first)
			currentTodos.sort((a, b) => {
				const dateA = new Date(a.created_at || 0);
				const dateB = new Date(b.created_at || 0);
				return dateB - dateA; // Reverse chronological order
			});
			
			// Fetch completion types
			const typesResp = await fetch(`/client/json/lists/${encodeURIComponent(listId)}/completion_types`, { credentials: 'same-origin' });
			if (typesResp.ok) {
				currentCompletionTypes = await typesResp.json();
			} else {
				// Fallback to default completion type
				currentCompletionTypes = [{ id: 'default', name: 'default' }];
			}
			
			renderTodos();
		} catch (err) {
			console.error('failed to fetch todos', err);
			showToast('Failed to load todos', { type: 'error' });
		}
	}

	function renderTodos() {
		renderTodosFull();
		renderTodosCompact();
	}

	function renderTodosFull() {
		const tbody = document.getElementById('todo-list-full-body');
		const thead = document.getElementById('todo-list-full-head');
		if (!tbody || !thead) return;

		// Update table header with dynamic completion columns
		thead.innerHTML = '';
		const headerRow = document.createElement('tr');
		headerRow.className = 'border-b border-slate-700';
		
		// Static columns: Pin, Delete
		headerRow.innerHTML = `
			<th class="w-12 p-2 text-center text-slate-400 text-sm font-medium"></th>
			<th class="w-12 p-2 text-center text-slate-400 text-sm font-medium"></th>
		`;
		
		// Add completion type columns
		currentCompletionTypes.forEach(type => {
			const th = document.createElement('th');
			th.className = 'w-12 p-2 text-center text-slate-400 text-sm font-medium';
			th.textContent = type.name.charAt(0).toUpperCase() + type.name.slice(1);
			headerRow.appendChild(th);
		});
		
		// Text column
		const textTh = document.createElement('th');
		textTh.className = 'p-2 text-left text-slate-400 text-sm font-medium';
		textTh.textContent = 'Todo';
		headerRow.appendChild(textTh);
		
		thead.appendChild(headerRow);

		// Render table body
		tbody.innerHTML = '';

		if (currentTodos.length === 0) {
			const row = document.createElement('tr');
			const totalCols = 2 + currentCompletionTypes.length + 1; // pin + delete + completion types + text
			row.innerHTML = `<td colspan="${totalCols}" class="p-4 text-center text-slate-500">No todos yet</td>`;
			tbody.appendChild(row);
			return;
		}

		currentTodos.forEach(todo => {
			const row = document.createElement('tr');
			row.className = 'border-b border-slate-700/50 hover:bg-slate-800/30';

			// Pin column
			const pinCell = document.createElement('td');
			pinCell.className = 'p-2 text-center';
			const pinBtn = document.createElement('button');
			pinBtn.className = 'w-6 h-6 text-slate-400 hover:text-yellow-400 transition-colors';
			pinBtn.innerHTML = '📌';
			pinBtn.title = 'Pin todo';
			pinBtn.addEventListener('click', () => toggleTodoPin(todo.id));
			pinCell.appendChild(pinBtn);
			row.appendChild(pinCell);

			// Delete column
			const deleteCell = document.createElement('td');
			deleteCell.className = 'p-2 text-center';
			const deleteBtn = document.createElement('button');
			deleteBtn.className = 'w-6 h-6 text-slate-400 hover:text-red-400 transition-colors';
			deleteBtn.innerHTML = '🗑️';
			deleteBtn.title = 'Delete todo';
			deleteBtn.addEventListener('click', () => deleteTodo(todo.id));
			deleteCell.appendChild(deleteBtn);
			row.appendChild(deleteCell);

			// Completion type columns
			currentCompletionTypes.forEach(type => {
				const completeCell = document.createElement('td');
				completeCell.className = 'p-2 text-center';
				const completeBtn = document.createElement('button');

				// Safely check completion status
				const isCompleted = todo.completions && todo.completions[type.name] === true;
				completeBtn.className = `w-6 h-6 transition-colors ${isCompleted ? 'text-green-400' : 'text-slate-400 hover:text-green-400'}`;
				completeBtn.innerHTML = isCompleted ? '✅' : '⬜';
				completeBtn.title = isCompleted ? `Mark ${type.name} incomplete` : `Mark ${type.name} complete`;
				completeBtn.addEventListener('click', () => toggleTodoComplete(todo.id, type.id));
				completeCell.appendChild(completeBtn);
				row.appendChild(completeCell);
			});

			// Text column
			const textCell = document.createElement('td');
			textCell.className = 'p-2';
			const textDiv = document.createElement('div');
			// Check if any completion type is completed for strikethrough
			const anyCompleted = todo.completions && Object.values(todo.completions).some(completed => completed);
			textDiv.className = `text-slate-100 ${anyCompleted ? 'line-through text-slate-500' : ''}`;
			textDiv.textContent = todo.text || '';
			textCell.appendChild(textDiv);
			row.appendChild(textCell);

			tbody.appendChild(row);
		});
	}

	function renderTodosCompact() {
		const tbody = document.getElementById('todo-list-compact-body');
		if (!tbody) return;

		tbody.innerHTML = '';

		if (currentTodos.length === 0) {
			const row = document.createElement('tr');
			row.innerHTML = '<td colspan="2" class="p-4 text-center text-slate-500">No todos yet</td>';
			tbody.appendChild(row);
			return;
		}

		currentTodos.forEach(todo => {
			const row = document.createElement('tr');
			row.className = 'border-b border-slate-700/50 hover:bg-slate-800/30';

			// Complete column (read-only, shows if any completion type is done)
			const completeCell = document.createElement('td');
			completeCell.className = 'p-2 text-center';
			const anyCompleted = todo.completions && Object.values(todo.completions).some(completed => completed);
			const completeIcon = document.createElement('span');
			completeIcon.className = `text-lg ${anyCompleted ? 'text-green-400' : 'text-slate-500'}`;
			completeIcon.textContent = anyCompleted ? '✅' : '⬜';
			completeCell.appendChild(completeIcon);
			row.appendChild(completeCell);

			// Text and notes column
			const textCell = document.createElement('td');
			textCell.className = 'p-2';
			const textDiv = document.createElement('div');
			textDiv.className = `text-slate-100 ${anyCompleted ? 'line-through text-slate-500' : ''}`;
			textDiv.textContent = todo.text || '';
			textCell.appendChild(textDiv);

			// Add notes if they exist
			if (todo.notes) {
				const notesDiv = document.createElement('div');
				notesDiv.className = 'text-sm text-slate-400 mt-1';
				notesDiv.textContent = todo.notes;
				textCell.appendChild(notesDiv);
			}

			row.appendChild(textCell);
			tbody.appendChild(row);
		});
	}

	async function addTodo(text) {
		const listRoot = document.getElementById('list-root');
		if (!listRoot) return;
		const listId = listRoot.querySelector('[data-list-id]')?.dataset?.listId || '';
		if (!listId || !text.trim()) return;

		try {
			const newTodo = await postJson('/client/json/todos', { text: text.trim(), list_id: listId });
			currentTodos.push(newTodo);
			
			// Sort todos by creation date in reverse order (newest first) after adding
			currentTodos.sort((a, b) => {
				const dateA = new Date(a.created_at || 0);
				const dateB = new Date(b.created_at || 0);
				return dateB - dateA; // Reverse chronological order
			});
			
			renderTodos();
			showToast('Todo added', { type: 'success' });
		} catch (err) {
			console.error('failed to add todo', err);
			showToast('Failed to add todo', { type: 'error' });
		}
	}

	async function toggleTodoComplete(todoId, completionTypeId = null) {
		const todo = currentTodos.find(t => t.id === todoId);
		if (!todo) {
			console.error('Todo not found:', todoId);
			return;
		}

		// Find the completion type
		let completionType = null;
		if (completionTypeId) {
			completionType = currentCompletionTypes.find(t => t.id === completionTypeId);
		} else {
			// If no completion type specified, use the first/default one
			completionType = currentCompletionTypes.find(t => t.name === 'default') || currentCompletionTypes[0];
		}

		if (!completionType) {
			console.error('Completion type not found:', completionTypeId);
			showToast('Failed to find completion type', { type: 'error' });
			return;
		}

		// Initialize completions object if it doesn't exist
		if (!todo.completions) {
			todo.completions = {};
		}

		// Store the original state for potential rollback
		const originalCompletions = { ...todo.completions };

		try {
			// Get current state for this completion type
			const currentState = todo.completions[completionType.name] || false;
			const newState = !currentState;

			// Optimistically update the UI
			todo.completions[completionType.name] = newState;
			renderTodos();

			// Send update to server
			const updatedTodo = await patchJson(`/client/json/todos/${encodeURIComponent(todoId)}`, {
				completion_type_id: completionType.id,
				completed: newState
			});

			// Update with server response if available
			if (updatedTodo && updatedTodo.completions) {
				todo.completions = updatedTodo.completions;
			}
			renderTodos();

			// Show success message
			showToast(newState ? `${completionType.name} completed` : `${completionType.name} marked incomplete`, { type: 'success' });
		} catch (err) {
			console.error('failed to toggle todo complete', err);
			// Revert optimistic update on error
			todo.completions = originalCompletions;
			renderTodos();
			showToast('Failed to update todo', { type: 'error' });
		}
	}

	async function toggleTodoPin(todoId) {
		try {
			const todo = currentTodos.find(t => t.id === todoId);
			if (!todo) return;

			const updatedTodo = await patchJson(`/client/json/todos/${encodeURIComponent(todoId)}`, {
				pinned: !todo.pinned
			});

			// Update local state
			todo.pinned = updatedTodo.pinned;
			renderTodos();
			showToast(updatedTodo.pinned ? 'Todo pinned' : 'Todo unpinned', { type: 'success' });
		} catch (err) {
			console.error('failed to toggle todo pin', err);
			showToast('Failed to update todo', { type: 'error' });
		}
	}

	async function deleteTodo(todoId) {
		if (!confirm('Delete this todo?')) return;

		try {
			await fetch(`/client/json/todos/${encodeURIComponent(todoId)}`, {
				method: 'DELETE',
				credentials: 'same-origin'
			});

			// Remove from local state
			currentTodos = currentTodos.filter(t => t.id !== todoId);
			renderTodos();
			showToast('Todo deleted', { type: 'success' });
		} catch (err) {
			console.error('failed to delete todo', err);
			showToast('Failed to delete todo', { type: 'error' });
		}
	}

	function initAddTodo() {
		const input = document.getElementById('new-todo-input');
		const btn = document.getElementById('add-todo-btn');
		if (!input || !btn) return;

		const addTodoHandler = async () => {
			const text = input.value.trim();
			if (!text) return;

			btn.disabled = true;
			try {
				await addTodo(text);
				input.value = '';
			} finally {
				btn.disabled = false;
			}
		};

		btn.addEventListener('click', addTodoHandler);
		input.addEventListener('keydown', (e) => {
			if (e.key === 'Enter') {
				e.preventDefault();
				addTodoHandler();
			}
		});
	}

	function initViewToggle() {
		const btn = document.getElementById('toggle-view-btn');
		const fullView = document.getElementById('todo-list-full');
		const compactView = document.getElementById('todo-list-compact');
		if (!btn || !fullView || !compactView) return;

		let showCompact = false;

		btn.addEventListener('click', () => {
			showCompact = !showCompact;
			if (showCompact) {
				fullView.classList.add('hidden');
				compactView.classList.remove('hidden');
				btn.textContent = 'Show Icons';
			} else {
				compactView.classList.add('hidden');
				fullView.classList.remove('hidden');
				btn.textContent = 'Hide Icons';
			}
		});
	}

	function init() {
		try {
			initPriorityHandler();
			initNameEdit();
			initEditButton();
			initCompleteToggle();
			fetchAndRenderTags();
			initTagEditor();
			initAddTodo();
			initViewToggle();
			fetchTodos(); // Load initial todos
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

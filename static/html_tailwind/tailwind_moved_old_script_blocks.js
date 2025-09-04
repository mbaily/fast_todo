// Hydration: sorting within categories and hide-completed control (persisted in localStorage)
(function(){
    function setLocal(name, value){ try{ localStorage.setItem(name, JSON.stringify(value)); }catch(e){} }
    function getLocal(name, def){ try{ var v = localStorage.getItem(name); return v ? JSON.parse(v) : def; }catch(e){ return def; } }

    // parse ISO safely
    function parseISO(v){ try { return v ? new Date(v).getTime() : 0; } catch(_) { return 0; } }

    // priority extraction from data attribute
    function parsePri(el){ try { if (!el) return null; if (el.querySelector && el.querySelector('.done')) return null; var v = el.getAttribute('data-priority'); if (v==null || v==='') return null; var n = parseInt(v,10); return isNaN(n) ? null : n; } catch(e) { return null; } }

    function sortUl(ul, mode){
        var lis = Array.from(ul.querySelectorAll(':scope > li'));
        lis.sort(function(a,b){
            var pa = parsePri(a), pb = parsePri(b);
            var aHas = (pa !== null), bHas = (pb !== null);
            if (aHas && !bHas) return -1;
            if (!aHas && bHas) return 1;
            if (aHas && bHas && pa !== pb) return pb - pa; // higher first
            if (mode === 'modified'){
                var ad = parseISO(a.getAttribute('data-modified-at')) || parseISO(a.getAttribute('data-created-at'));
                var bd = parseISO(b.getAttribute('data-modified-at')) || parseISO(b.getAttribute('data-created-at'));
                return bd - ad;
            }
            var ad = parseISO(a.getAttribute('data-created-at'));
            var bd = parseISO(b.getAttribute('data-created-at'));
            return bd - ad;
        });
        lis.forEach(function(li){ ul.appendChild(li); });
    }

    // sort table rows (tbody > tr) by priority/date similar to sortUl
    function sortTable(tbody, mode){
        var rows = Array.from(tbody.querySelectorAll(':scope > tr'));
        rows.sort(function(a,b){
            var pa = parsePri(a), pb = parsePri(b);
            var aHas = (pa !== null), bHas = (pb !== null);
            if (aHas && !bHas) return -1;
            if (!aHas && bHas) return 1;
            if (aHas && bHas && pa !== pb) return pb - pa;
            if (mode === 'modified'){
                var ad = parseISO(a.getAttribute('data-modified-at')) || parseISO(a.getAttribute('data-created-at'));
                var bd = parseISO(b.getAttribute('data-modified-at')) || parseISO(b.getAttribute('data-created-at'));
                return bd - ad;
            }
            var ad = parseISO(a.getAttribute('data-created-at'));
            var bd = parseISO(b.getAttribute('data-created-at'));
            return bd - ad;
        });
        rows.forEach(function(r){ tbody.appendChild(r); });
    }

    function applySort(){
        var mode = document.getElementById('list-sort-order') ? document.getElementById('list-sort-order').value : 'created';
    // legacy UL lists: target ULs rendered inside the lists-by-category section
    document.querySelectorAll('.lists-by-category ul').forEach(function(ul){ sortUl(ul, mode); });
        // new table tbody rows
        document.querySelectorAll('table').forEach(function(tbl){ var tb = tbl.querySelector('tbody'); if (tb) sortTable(tb, mode); });
        // update date columns to reflect chosen mode
        document.querySelectorAll('.list-date').forEach(function(td){ try{
            var row = td.closest && (td.closest('tr') || td.closest('li'));
            if (!row) return;
            if (mode === 'modified'){
                var mod = row.getAttribute('data-modified-hum'); if (mod) td.textContent = 'mod. ' + mod; else { var cr = row.getAttribute('data-created-hum'); td.textContent = cr ? 'cr. ' + cr : ''; }
            } else {
                var cr = row.getAttribute('data-created-hum'); if (cr) td.textContent = 'cr. ' + cr; else { var mod = row.getAttribute('data-modified-hum'); td.textContent = mod ? 'mod. ' + mod : ''; }
            }
        }catch(e){} });
    }

    // restore UI state
    var hideCompleted = !!getLocal('hide_completed', false);
    var sortOrder = getLocal('list_sort_order', 'created');
    var hideBox = document.getElementById('hide-completed-checkbox');
    if (hideBox) {
        hideBox.checked = hideCompleted;
        // update UI to reflect stored value immediately
        try { applyHide(); updateCategoryCounts(); } catch(e) {}
        // listen for both input and change for broader compatibility
        var onHideChange = function(){ try{ setLocal('hide_completed', !!hideBox.checked); applyHide(); }catch(e){} };
        hideBox.addEventListener('change', onHideChange);
        hideBox.addEventListener('input', onHideChange);
    }
    var sel = document.getElementById('list-sort-order'); if (sel){ sel.value = sortOrder; sel.addEventListener('change', function(){ setLocal('list_sort_order', sel.value); applySort(); }); }

    function applyHide(){ var checked = document.getElementById('hide-completed-checkbox') ? document.getElementById('hide-completed-checkbox').checked : false; document.querySelectorAll('a.list-title, a.list-title.done').forEach(function(at){ /* noop */ }); var listItems = document.querySelectorAll('.lists-by-category li'); if (!listItems || listItems.length === 0) listItems = document.querySelectorAll('li[data-list-id]'); listItems.forEach(function(li){ try{ var title = li.querySelector && li.querySelector('.list-title'); var isDone = title && title.classList && title.classList.contains('done'); li.style.display = (checked && isDone) ? 'none' : ''; }catch(e){} }); updateCategoryCounts(); }

    // initial apply
    applySort(); applyHide();

    // update category counts to reflect hidden/completed state
    function updateCategoryCounts(){ try{
        document.querySelectorAll('.category-section').forEach(function(sec){ try{
            var total = 0, visible = 0;
            var lis = sec.querySelectorAll('ul > li');
            total = lis.length;
            lis.forEach(function(li){ if (li.style && li.style.display === 'none') return; visible += 1; });
            var span = sec.querySelector('.cat-count');
            if (span) span.textContent = (document.getElementById('hide-completed-checkbox') && document.getElementById('hide-completed-checkbox').checked) ? String(visible) : String(total);
        }catch(e){} });
    }catch(e){}
    }
        updateCategoryCounts();

        // allow external updates via localStorage event
        window.addEventListener && window.addEventListener('storage', function(ev){ try{ if (!ev || !ev.key) return; if (ev.key === 'list_sort_order'){ applySort(); } if (ev.key === 'hide_completed'){ applyHide(); } }catch(e){} });
    })();




// Persist <details> open/closed per-category and handle combined tags fetch
(function(){
    function setLocal(name, value){ try{ localStorage.setItem(name, JSON.stringify(value)); }catch(e){} }
    function getLocal(name, def){ try{ var v = localStorage.getItem(name); return v ? JSON.parse(v) : def; }catch(e){ return def; } }

    // restore details open state
    document.querySelectorAll('.category-section').forEach(function(sec){
        try{
            var cat = sec.getAttribute('data-category-id') || 'cat';
            var key = 'tw_cat_open_' + String(cat);
            var det = sec.querySelector('details');
            if (!det) return;
            var saved = getLocal(key, det.hasAttribute('open'));
            if (saved) det.setAttribute('open',''); else det.removeAttribute('open');
            det.addEventListener('toggle', function(){ setLocal(key, !!det.open); });
        }catch(e){}
    });

    // show-all-tags global checkbox persistence
    var globalShow = getLocal('tw_show_all_tags', false);
    var globalBox = document.getElementById('show-all-tags-checkbox');
    if (globalBox){
        globalBox.checked = !!globalShow;
        globalBox.addEventListener('change', async function(){
            setLocal('tw_show_all_tags', !!globalBox.checked);
            // when enabled, fetch combined tags for all visible lists; when disabled, clear them
            const containers = Array.from(document.querySelectorAll('.combined-tags[data-list-id]'));
            if (!containers || containers.length === 0) return;
            if (globalBox.checked){
                // fetch for all lists in parallel but cap concurrency if needed (simple parallel here)
                containers.forEach(function(c){ c.textContent = 'Loading...'; });
                await Promise.all(containers.map(async function(c){
                    try{
                        const lid = c.getAttribute('data-list-id');
                        const url = '/lists/' + encodeURIComponent(lid) + '/hashtags?include_todo_tags=1&combine=1';
                        const res = await fetch(url, { credentials: 'same-origin' });
                        if (!res.ok) { c.innerHTML = ''; return; }
                        const j = await res.json();
                        const tags = j && j.hashtags ? j.hashtags : null;
                        c.innerHTML = '';
                        if (tags && tags.length){
                            // collect existing tags already rendered server-side for this list to avoid duplicates
                            var existing = new Set();
                            try{
                                var parentLi = c.closest && c.closest('li');
                                if (parentLi){ parentLi.querySelectorAll('.tag-chip').forEach(function(el){ if (el && el.textContent) existing.add(String(el.textContent).trim()); }); }
                            }catch(e){}
                            // append unique tags only
                            tags.forEach(function(t){ try{ var txt = String(t).trim(); if (!txt) return; if (existing.has(txt)) return; existing.add(txt);
                                var wrapItem = document.createElement('div'); wrapItem.className = 'tag-wrapper inline-flex items-center';
                                    var a = document.createElement('a'); a.href = '/html_no_js/search?q=' + encodeURIComponent(txt); a.className = 'tag-chip inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-semibold text-slate-100 border border-sky-500/30 hover:bg-sky-700/25 focus:outline-none focus:ring-2 focus:ring-sky-400';
                                var span = document.createElement('span'); span.className = 'label truncate'; span.textContent = txt;
                                a.appendChild(span);
                                var btn = document.createElement('button'); btn.className = 'remove'; btn.setAttribute('data-tag', txt); btn.type = 'button'; btn.textContent = '\u00d7'; btn.setAttribute('aria-label','Remove tag '+txt); btn.title = 'Remove ' + txt;
                                wrapItem.appendChild(a); wrapItem.appendChild(btn); wrapItem.setAttribute('data-appended','1'); c.appendChild(wrapItem);
                            }catch(e){} });
                        }
                    }catch(e){ c.innerHTML = ''; }
                }));

            }
        });
        // initialize from saved state: trigger change to load tags if needed
        if (globalBox.checked){ globalBox.dispatchEvent(new Event('change')); }
    }
    // when unchecked, remove only appended combined tags
    if (globalBox){
        globalBox.addEventListener('change', function(){ if (!globalBox.checked){ try{ document.querySelectorAll('.combined-tags').forEach(function(c){ Array.from(c.querySelectorAll('[data-appended="1"]')).forEach(function(el){ el.parentNode && el.parentNode.removeChild(el); }); }); }catch(e){} } });
    }
})();





(function(){
    // search handler: on Enter or after small debounce
    const searchInput = document.getElementById('tw-search-input');
    let searchTimer = null;
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                doSearch(searchInput.value);
            }
        });
        searchInput.addEventListener('input', (e) => {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(() => { doSearch(searchInput.value); }, 500);
        });
    }

    async function doSearch(q) {
        try {
            const url = '/client/json/search?q=' + encodeURIComponent(q);
            const res = await fetch(url, { method: 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin' });
            if (!res.ok) return;
            const j = await res.json();
            if (j && j.ok) {
                // For now redirect to the tailwind index with q param so server-side
                // rendering or a later client render can display results.
                window.location.href = '/html_tailwind?q=' + encodeURIComponent(q);
            }
        } catch (e) {
            // noop
        }
    }

    // create-list handler
    const createBtn = document.getElementById('tw-create-list');
    const nameEl = document.getElementById('tw-newlist-name');
    
    // Function to handle list creation
    const createList = async () => {
        if (!nameEl) return;
        const name = nameEl.value && String(nameEl.value).trim();
        if (!name) { alert('Please enter a list name'); return; }
        try {
            // Optimistic UI: insert a temporary placeholder row immediately
            if (createBtn) createBtn.disabled = true;
            const placeholderId = 'tmp-list-' + String(Date.now());
            // Find the target category section - default to uncategorized
            let targetCategoryId = 'uncategorized';
            let targetUl = document.querySelector('.category-section[data-category-id="uncategorized"] ul');
            
            // If we have a category section for the target category, use it
            if (targetUl) {
                placeholderLi = document.createElement('li');
                placeholderLi.className = 'bg-black px-0 py-1 opacity-80'; // Removed border-b to avoid horizontal lines
                placeholderLi.setAttribute('data-list-id', placeholderId);
                placeholderLi.innerHTML = '<span class="list-title font-medium text-slate-400">' + (name.replace(/</g, '&lt;')) + '</span>';
                targetUl.insertBefore(placeholderLi, targetUl.firstChild);
            }

            const res = await fetch('/client/json/lists', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ name }) });
            if (!res.ok) {
                // remove placeholder and restore
                if (placeholderLi && placeholderLi.parentNode) placeholderLi.parentNode.removeChild(placeholderLi);
                if (createBtn) createBtn.disabled = false;
                alert('Failed to create list');
                return;
            }
            const j = await res.json();
            if (j && j.ok) {
                // Determine the target category for this list
                const listCategoryId = j.category_id || 'uncategorized';
                
                // if server provided id, update the placeholder with a proper link element
                if (placeholderLi && j.id) {
                    // If the category changed from our optimistic insert, move the element
                    const currentCategorySection = placeholderLi.closest('.category-section');
                    const currentCategoryId = currentCategorySection ? currentCategorySection.getAttribute('data-category-id') : 'uncategorized';
                    
                    if (currentCategoryId !== listCategoryId) {
                        // Remove from current location
                        if (placeholderLi.parentNode) {
                            placeholderLi.parentNode.removeChild(placeholderLi);
                        }
                        
                        // Find the correct category section
                        let targetSection = document.querySelector('.category-section[data-category-id="' + listCategoryId + '"]');
                        if (!targetSection && listCategoryId === 'uncategorized') {
                            targetSection = document.querySelector('.category-section[data-category-id="uncategorized"]');
                        }
                        
                        if (targetSection) {
                            const targetUl = targetSection.querySelector('ul');
                            if (targetUl) {
                                targetUl.insertBefore(placeholderLi, targetUl.firstChild);
                            }
                        }
                    }
                    
                    // replace placeholder with a proper link element
                    const a = document.createElement('a');
                    a.className = (j.completed ? 'list-title font-medium text-slate-400 line-through done' : 'list-title font-medium text-blue-600 hover:underline');
                    a.href = '/html_tailwind/list?id=' + j.id;
                    a.textContent = j.name || name;
                    placeholderLi.innerHTML = '';
                    placeholderLi.appendChild(a);
                    placeholderLi.setAttribute('data-list-id', j.id);
                    placeholderLi.className = 'bg-black px-0 py-1'; // Ensure consistent styling without border
                    if (createBtn) createBtn.disabled = false;
                    nameEl.value = ''; // Clear the input field
                    // Stay on the current page - no redirect
                    return;
                }
                // Stay on the current page - no redirect
                if (createBtn) createBtn.disabled = false;
                nameEl.value = ''; // Clear the input field
            } else {
                if (placeholderLi && placeholderLi.parentNode) placeholderLi.parentNode.removeChild(placeholderLi);
                if (createBtn) createBtn.disabled = false;
                alert('Create failed');
            }
        } catch (e) { alert('Network error'); }
    };
    
    if (createBtn) {
        createBtn.addEventListener('click', createList);
    }
    
    // Add Enter key handler for the input field
    if (nameEl) {
        nameEl.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.keyCode === 13) {
                event.preventDefault(); // Prevent form submission if inside a form
                createList();
            }
        });
    }
})();

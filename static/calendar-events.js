// Event delegation for Preact calendar
// Since HTM doesn't bind onClick handlers to DOM, we use manual event delegation

export function setupEventDelegation() {
  const root = document.getElementById('preact-calendar-root');
  if (!root) {
    console.error('Preact root not found');
    return;
  }
  
  function getCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }
  
  // Single click handler for all buttons
  root.addEventListener('click', async (e) => {
    const target = e.target;
    
    // Handle ignore button
    if (target.classList.contains('occ-ignore-occ')) {
      e.preventDefault();
      const itemId = target.getAttribute('data-item-id');
      if (!itemId) return;
      
      console.log('DEBUG: Ignore clicked for item:', itemId);
      
      const csrf = getCookie('csrf_token') || '';
      const body = `_csrf=${encodeURIComponent(csrf)}&calendar_ignored=1`;
      
      try {
        const res = await fetch(`/html_no_js/todos/${itemId}/calendar_ignored`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: body,
          credentials: 'same-origin'
        });
        
        if (res.ok) {
          // Trigger Preact re-render via custom event
          const occId = target.getAttribute('data-occ');
          const event = new CustomEvent('calendar-item-ignored', { detail: { itemId, occId } });
          root.dispatchEvent(event);
          
          // Force refresh from server
          if (window.refreshFromServer) {
            window.refreshFromServer();
          }
        } else {
          console.error('Ignore failed:', res.status);
        }
      } catch (err) {
        console.error('Ignore error:', err);
      }
    }
    
    // Handle ignore-from button (⏭️)
    if (target.classList.contains('occ-ignore-from')) {
      e.preventDefault();
      const todoId = target.getAttribute('data-todo');
      const occDt = target.getAttribute('data-occ-dt');
      
      if (!todoId || !occDt) {
        console.error('Missing data-todo or data-occ-dt for ignore-from');
        return;
      }
      
      console.log('DEBUG: Ignore-from clicked for todo:', todoId, 'from date:', occDt);
      
      const csrf = getCookie('csrf_token') || '';
      const body = `_csrf=${encodeURIComponent(csrf)}&scope_type=todo_from&scope_key=${encodeURIComponent(todoId)}&scope_value=${encodeURIComponent(occDt)}`;
      
      try {
        const res = await fetch('/ignore/scope', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: body,
          credentials: 'same-origin'
        });
        
        console.log('DEBUG: Ignore-from response:', res.status, res.ok);
        
        if (res.ok) {
          // Force refresh from server to show updated state
          if (window.refreshFromServer) {
            window.refreshFromServer();
          }
        } else {
          console.error('Ignore-from failed:', res.status);
        }
      } catch (err) {
        console.error('Ignore-from error:', err);
      }
    }
    
    // Handle unignore button
    if (target.classList.contains('occ-unignore')) {
      e.preventDefault();
      const itemId = target.getAttribute('data-item-id');
      if (!itemId) return;
      
      console.log('DEBUG: Unignore clicked for item:', itemId);
      
      const csrf = getCookie('csrf_token') || '';
      
      try {
        // Clear calendar_ignored flag
        const clearBody = `_csrf=${encodeURIComponent(csrf)}&calendar_ignored=0`;
        await fetch(`/html_no_js/todos/${itemId}/calendar_ignored`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: clearBody,
          credentials: 'same-origin'
        });
        
        // Also clear todo_from scope
        const scopeBody = `_csrf=${encodeURIComponent(csrf)}&scope_type=todo_from&scope_key=${encodeURIComponent(itemId)}`;
        await fetch('/ignore/unscope', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: scopeBody,
          credentials: 'same-origin'
        });
        
        // Force refresh from server
        if (window.refreshFromServer) {
          window.refreshFromServer();
        }
      } catch (err) {
        console.error('Unignore error:', err);
      }
    }
    
    // Handle completion checkbox
    if (target.classList.contains('occ-complete') && target.tagName === 'INPUT') {
      const isChecked = target.checked;
      const occId = target.getAttribute('data-occ-id');
      const occHash = target.getAttribute('data-hash');
      const itemType = target.getAttribute('data-item-type');
      const itemId = target.getAttribute('data-item-id');
      const occDt = target.getAttribute('data-occ-dt');
      
      console.log('DEBUG: Complete toggled:', occId, isChecked);
      
      const csrf = getCookie('csrf_token') || '';
      
      // Use the correct endpoint based on checked state
      const endpoint = isChecked ? '/occurrence/complete' : '/occurrence/uncomplete';
      const body = `_csrf=${encodeURIComponent(csrf)}&hash=${encodeURIComponent(occHash || '')}&item_type=${encodeURIComponent(itemType || '')}&item_id=${encodeURIComponent(itemId || '')}&occurrence_dt=${encodeURIComponent(occDt || '')}`;
      
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: body,
          credentials: 'same-origin'
        });
        
        console.log('DEBUG: Complete response:', res.status, res.ok);
        
        if (!res.ok) {
          console.error('Complete failed with status:', res.status);
          target.checked = !isChecked; // Revert on error
        }
      } catch (err) {
        console.error('Complete error:', err);
        target.checked = !isChecked; // Revert on error
      }
    }
  });
  
  console.log('✅ Event delegation setup complete');
}

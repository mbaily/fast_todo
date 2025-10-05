// Calendar Preact Components
// Uses htm for JSX-like syntax without build step
import { h, render } from 'preact';
import { signal, computed, effect } from '@preact/signals';
import { useSignal, useComputed } from '@preact/signals';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// Utility functions
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

const MAX_TS = Number.MAX_SAFE_INTEGER;

function sortOccurrences(list) {
  if (!Array.isArray(list)) {
    return [];
  }
  const resolveTs = (occ) => {
    if (!occ) return MAX_TS;
    const rawTs = occ.occ_ts;
    if (typeof rawTs === 'number' && !Number.isNaN(rawTs)) {
      return rawTs;
    }
    const numericTs = Number(rawTs);
    if (!Number.isNaN(numericTs)) {
      return numericTs;
    }
    const iso = occ.occurrence_dt || occ.occurrence_date;
    if (iso) {
      const parsed = Date.parse(iso);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
    return MAX_TS;
  };
  const arr = list.slice();
  arr.sort((a, b) => {
    const aTs = resolveTs(a);
    const bTs = resolveTs(b);
    if (aTs !== bTs) {
      return aTs - bTs;
    }
    const aDt = (a && (a.occurrence_dt || a.occurrence_date)) || '';
    const bDt = (b && (b.occurrence_dt || b.occurrence_date)) || '';
    const cmpDt = aDt.localeCompare(bDt);
    if (cmpDt !== 0) {
      return cmpDt;
    }
    const aTitle = (a && a.title) || '';
    const bTitle = (b && b.title) || '';
    const cmpTitle = aTitle.localeCompare(bTitle);
    if (cmpTitle !== 0) {
      return cmpTitle;
    }
    const aId = Number(a && a.id);
    const bId = Number(b && b.id);
    if (!Number.isNaN(aId) && !Number.isNaN(bId)) {
      return aId - bId;
    }
    return 0;
  });
  return arr;
}

// CalendarOccurrence component - one per event
function CalendarOccurrence({ occurrence }) {
  console.log('CalendarOccurrence component rendering for:', occurrence.title?.substring(0, 30));
  
  // Use useState for proper re-rendering
  const [ignoredScopes, setIgnoredScopes] = useState(occurrence.ignored_scopes || []);
  const [completed, setCompleted] = useState(occurrence.completed || false);
  const [phantom, setPhantom] = useState(occurrence.phantom || false);
  
  // Computed values
  const isIgnored = ignoredScopes && ignoredScopes.length > 0;
  const hasCalendarIgnored = ignoredScopes && ignoredScopes.includes('calendar_ignored');
  
  // Debug: log recurring status
  if (occurrence.title && occurrence.title.includes('Gym workout')) {
    console.log('DEBUG CalendarOccurrence render:', {
      title: occurrence.title.substring(0, 30),
      recurring: occurrence.recurring,
      is_recurring: occurrence.is_recurring,
      ignoredScopes: ignoredScopes,
      isIgnored: isIgnored
    });
  }
  
  // Event handlers - use function declarations
  function handleIgnore(e) {
    e.preventDefault();
    console.log('DEBUG handleIgnore called for:', occurrence.title.substring(0, 30));
    const csrf = getCookie('csrf_token') || '';
    const body = `_csrf=${encodeURIComponent(csrf)}&calendar_ignored=1`;
    
    fetch(`/html_no_js/todos/${occurrence.id}/calendar_ignored`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body,
      credentials: 'same-origin'
    }).then(res => {
      console.log('DEBUG ignore response:', res.status);
      if (res.ok) {
        console.log('DEBUG calling setIgnoredScopes');
        setIgnoredScopes(['calendar_ignored']);
      } else {
        console.error('Ignore failed:', res.status);
        if (res.status === 403) {
          alert('Session expired. Please refresh the page (Ctrl+Shift+R).');
        }
      }
    }).catch(err => {
      console.error('Ignore error:', err);
    });
  }
  
  async function handleUnignore(e) {
    e.preventDefault();
    console.log(`DEBUG handleUnignore called for: ${occurrence.title.substring(0, 30)}`);
    const csrf = getCookie('csrf_token') || '';
    
    try {
      // Clear calendar_ignored flag
      const clearBody = `_csrf=${encodeURIComponent(csrf)}&calendar_ignored=0`;
      await fetch(`/html_no_js/todos/${occurrence.id}/calendar_ignored`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: clearBody,
        credentials: 'same-origin'
      });
      
      // Also clear todo_from scope
      const scopeBody = `_csrf=${encodeURIComponent(csrf)}&scope_type=todo_from&scope_key=${encodeURIComponent(occurrence.id)}`;
      await fetch('/ignore/unscope', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: scopeBody,
        credentials: 'same-origin'
      });
      
      console.log('DEBUG setIgnoredScopes([]) about to be called');
      setIgnoredScopes([]);
      console.log('DEBUG setIgnoredScopes([]) completed');
    } catch (err) {
      console.error('Unignore error:', err);
    }
  }
  
  async function handleComplete(e) {
    const isChecked = e.target.checked;
    setCompleted(isChecked);
    
    const csrf = getCookie('csrf_token') || '';
    const body = `_csrf=${encodeURIComponent(csrf)}&hash=${encodeURIComponent(occurrence.occ_hash || '')}&item_type=${encodeURIComponent(occurrence.item_type || 'todo')}&item_id=${encodeURIComponent(occurrence.id || '')}&occurrence_dt=${encodeURIComponent(occurrence.occurrence_dt || '')}`;
    
    try {
      const endpoint = isChecked ? '/occurrence/complete' : '/occurrence/uncomplete';
      await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body,
        credentials: 'same-origin'
      });
    } catch (err) {
      console.error('Complete error:', err);
      setCompleted(!isChecked); // Revert on error
    }
  }
  
  const occId = occurrence.occ_id || occurrence.occ_hash || '';
  const metaText = (occurrence.occurrence_date || occurrence.occurrence_dt || '') + (isIgnored ? ' (ignored)' : '');
  const itemLink = occurrence.item_type === 'todo' 
    ? `/html_no_js/todos/${occurrence.id}`
    : `/html_no_js/lists/${occurrence.id}`;
  
  return html`
    <li class="todo" data-occ-id="${occId}">
      <div class="controls-left">
        <input 
          type="checkbox" 
          class="occ-complete"
          data-occ-id="${occId}"
          data-hash="${occurrence.occ_hash || ''}"
          data-item-type="${occurrence.item_type}"
          data-item-id="${occurrence.id}"
          data-occ-dt="${occurrence.occurrence_dt || ''}"
          data-phantom="${phantom ? '1' : ''}"
          checked="${completed}"
          disabled="${phantom}"
          title="${phantom ? 'Historic completion (read-only)' : ''}"
          aria-label="mark occurrence complete"
          onChange=${handleComplete}
        />
        
        ${isIgnored ? html`
          <button 
            type="button" 
            class="occ-unignore"
            data-occ="${occId}"
            data-item-id="${occurrence.id}"
            data-calendar-ignored="${hasCalendarIgnored ? '1' : ''}"
            title="Un-ignore (restore to calendar)"
            aria-label="Un-ignore this todo"
            style="margin-left:0.25rem;padding:0.1rem 0.25rem;font-size:0.9rem;"
            onClick=${handleUnignore}
          >↩️</button>
        ` : html`
          <button 
            type="button" 
            class="occ-ignore-occ"
            data-occ="${occId}"
            data-item-id="${occurrence.id}"
            title="Ignore in calendar altogether"
            aria-label="Ignore this todo from calendar"
            style="margin-left:0.25rem;padding:0.1rem 0.25rem;font-size:0.9rem;"
            onClick=${handleIgnore}
          >🔕</button>
          ${(occurrence.recurring || occurrence.is_recurring) ? html`
            <button 
              type="button" 
              class="occ-ignore-from"
              data-todo="${occurrence.id}"
              data-occ-dt="${occurrence.occurrence_dt || ''}"
              title="Ignore from this date onwards"
              aria-label="Ignore from this date onwards"
              style="margin-left:0.25rem;padding:0.1rem 0.25rem;font-size:0.9rem;"
            >⏭️</button>
          ` : null}
        `}
      </div>
      
      <div class="todo-content">
        <div class="todo-main wrap-text">
          <div class="wrap-text" style="font-weight:700">
            <a href="${itemLink}">${occurrence.title}</a>
          </div>
        </div>
        <div class="meta">${metaText}</div>
      </div>
    </li>
  `;
}

// CalendarOccurrenceList component
function CalendarOccurrenceList({ occurrences }) {
  if (!occurrences || occurrences.length === 0) {
    return html`
      <ul class="todos-list">
        <li style="padding: 1rem; color: #666;">No events in this time period.</li>
      </ul>
    `;
  }
  
  return html`
    <ul class="todos-list">
      ${occurrences.map(occ => html`
        <${CalendarOccurrence} key="${occ.occ_id || occ.occ_hash}" occurrence="${occ}" />
      `)}
    </ul>
  `;
}

// CalendarApp - Main app wrapper
function CalendarApp({ initialOccurrences }) {
  const [occurrences, setOccurrences] = useState(sortOccurrences(initialOccurrences || []));
  const [loading, setLoading] = useState(false);
  
  // Expose refresh function globally
  window.refreshCalendarOccurrences = (newOccurrences) => {
    setOccurrences(sortOccurrences(newOccurrences || []));
  };
  
  return html`
    <div id="calendar-occurrences-container">
      ${loading ? html`
        <div style="padding: 1rem; text-align: center;">Loading...</div>
      ` : html`
        <${CalendarOccurrenceList} occurrences="${occurrences}" />
      `}
    </div>
  `;
}

// Initialize calendar
export function initCalendar(containerId, initialOccurrences) {
  const container = document.getElementById(containerId);
  if (!container) {
    console.error('Calendar container not found:', containerId);
    return;
  }
  
  render(html`<${CalendarApp} initialOccurrences="${initialOccurrences}" />`, container);
}

// Export for use in other modules
export { CalendarOccurrence, CalendarOccurrenceList, CalendarApp };

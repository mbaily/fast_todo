// Calendar Preact initialization
// This file loads the Preact calendar and handles cookie-based filters

function getCookie(name) { 
  const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)'); 
  return v ? v.pop() : ''; 
}

function setCookie(name, value, days) {
  let expires = '';
  if (days) { 
    const d = new Date(); 
    d.setTime(d.getTime() + (days*24*60*60*1000)); 
    expires = '; expires=' + d.toUTCString(); 
  }
  document.cookie = name + '=' + (value || '') + expires + '; path=/';
}

// Initialize calendar when module loads
export async function initializeCalendar(year, month, initialOccurrences) {
  const { initCalendar } = await import('/static/calendar-preact.bundle.js');
  const { setupEventDelegation } = await import('/static/calendar-events.js');
  
  // Initialize Preact calendar
  initCalendar('preact-calendar-root', initialOccurrences);
  
  // Set up event delegation (HTM doesn't bind onClick handlers automatically)
  setupEventDelegation();
  
  console.log('✅ Preact calendar initialized');
  
  // Wire up filter checkboxes
  const showIgnoredCb = document.getElementById('show_ignored');
  const hideCompletedCb = document.getElementById('hide_completed');
  const includeHistoricCb = document.getElementById('include_historic');
  
  // Load saved preferences
  if (showIgnoredCb) {
    showIgnoredCb.checked = (getCookie('show_ignored') === '1');
  }
  if (hideCompletedCb) {
    hideCompletedCb.checked = (getCookie('hide_completed') === '1');
  }
  if (includeHistoricCb) {
    includeHistoricCb.checked = (getCookie('include_historic') === '1');
  }
  
  // Fetch updated occurrences when filters change
  async function refreshOccurrences() {
    const start = new Date(year, month - 1, 1);
    const end = new Date(year, month, 1);
    const showIgnored = showIgnoredCb ? showIgnoredCb.checked : false;
    const includeHistoric = includeHistoricCb ? includeHistoricCb.checked : false;
    const maxTotal = 3000;
    
    const q = `/calendar/occurrences?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}&include_ignored=${showIgnored ? '1' : '0'}&include_historic=${includeHistoric ? '1' : '0'}&max_total=${maxTotal}`;
    
    try {
      const res = await fetch(q, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        if (data && Array.isArray(data.occurrences)) {
          window.refreshCalendarOccurrences(data.occurrences);
        }
      }
    } catch (err) {
      console.error('Failed to refresh calendar:', err);
    }
  }
  
  // Expose globally for event handlers
  window.refreshFromServer = refreshOccurrences;
  
  // Attach event listeners
  if (showIgnoredCb) {
    showIgnoredCb.addEventListener('change', function() {
      setCookie('show_ignored', showIgnoredCb.checked ? '1' : '0', 365);
      refreshOccurrences();
    });
  }
  
  if (hideCompletedCb) {
    hideCompletedCb.addEventListener('change', function() {
      setCookie('hide_completed', hideCompletedCb.checked ? '1' : '0', 365);
      refreshOccurrences();
    });
  }
  
  if (includeHistoricCb) {
    includeHistoricCb.addEventListener('change', function() {
      setCookie('include_historic', includeHistoricCb.checked ? '1' : '0', 365);
      refreshOccurrences();
    });
  }
}

/**
 * Terraform Graphical Manager — Main JavaScript
 * Handles global Socket.IO events, queue badge updates, and shared utilities.
 */

'use strict';

// ============================================================
// Socket.IO global connection
// ============================================================

let _socket = null;

function getSocket() {
  if (!_socket) {
    // Force polling transport: the Werkzeug dev server does not support
    // native WebSocket upgrades, so we skip the upgrade attempt entirely.
    _socket = io({ transports: ['polling'] });
    _socket.on('connect', () => console.debug('[TGM] Socket connected'));
    _socket.on('disconnect', () => console.debug('[TGM] Socket disconnected'));
    _socket.on('execution_status', handleGlobalStatusUpdate);
  }
  return _socket;
}

// Initialize socket on page load
document.addEventListener('DOMContentLoaded', () => {
  getSocket();
  updateQueueBadge();
});


// ============================================================
// Queue badge
// ============================================================

async function updateQueueBadge() {
  try {
    // We don't have a dedicated /api/queue-stats endpoint; derive from workspace
    // executions by checking all tracked execution IDs in the DOM
    const badge = document.getElementById('queue-badge');
    const countEl = document.getElementById('queue-count');
    if (!badge || !countEl) return;

    // Could be enhanced to do a real API call if needed
    // For now badge is updated via Socket.IO events
  } catch (e) {
    console.debug('[TGM] Could not update queue badge:', e.message);
  }
}

function handleGlobalStatusUpdate(data) {
  // Update any status badges on the page that match this execution
  const badge = document.querySelector(`[data-execution-id="${data.execution_id}"] .status-badge`);
  if (badge) {
    badge.textContent = data.status;
    badge.className = `status-badge status-${data.status}`;
  }
}


// ============================================================
// Log colorisation
// ============================================================

/**
 * Apply color classes to a log line element based on content.
 * @param {HTMLElement} el - the log line DOM element
 * @param {string} line - the text content
 */
function coloriseLine(el, line) {
  const l = line.toLowerCase();
  if (l.includes('error') || l.includes('failed')) {
    el.classList.add('log-error');
  } else if (l.includes('warning') || l.includes('warn')) {
    el.classList.add('log-warning');
  } else if (l.startsWith('apply complete') || l.includes('created') || l.includes('success')) {
    el.classList.add('log-success');
  } else if (line.startsWith('===')) {
    el.classList.add('log-section');
  }
}


// ============================================================
// Polling helper (fallback for environments without WS)
// ============================================================

/**
 * Poll an API endpoint until a terminal condition is met.
 *
 * @param {string} url - endpoint to poll
 * @param {function} predicate - (data) => boolean, returns true to stop
 * @param {function} callback - called on each response with (data)
 * @param {number} intervalMs - polling interval in ms
 * @returns {function} stop function
 */
function startPolling(url, predicate, callback, intervalMs = 2000) {
  const id = setInterval(async () => {
    try {
      const res = await fetch(url);
      if (!res.ok) { clearInterval(id); return; }
      const data = await res.json();
      callback(data);
      if (predicate(data)) clearInterval(id);
    } catch (e) {
      console.debug('[TGM] Poll error:', e.message);
      clearInterval(id);
    }
  }, intervalMs);
  return () => clearInterval(id);
}


// ============================================================
// Toast notifications
// ============================================================

function showToast(message, type = 'info') {
  const container = getOrCreateToastContainer();
  const toast = document.createElement('div');
  const colors = {
    info:    'bg-blue-50 border-blue-200 text-blue-800',
    success: 'bg-green-50 border-green-200 text-green-800',
    warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    error:   'bg-red-50 border-red-200 text-red-800',
  };
  toast.className = `flex items-center px-4 py-3 rounded-lg border shadow-sm text-sm ${colors[type] || colors.info} transition-all duration-300`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(8px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function getOrCreateToastContainer() {
  let c = document.getElementById('toast-container');
  if (!c) {
    c = document.createElement('div');
    c.id = 'toast-container';
    c.className = 'fixed bottom-5 right-5 z-50 flex flex-col space-y-2 max-w-sm';
    document.body.appendChild(c);
  }
  return c;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}


// ============================================================
// Expose utilities globally
// ============================================================

window.TGM = {
  getSocket,
  updateQueueBadge,
  coloriseLine,
  startPolling,
  showToast,
};

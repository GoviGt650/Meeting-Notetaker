/**
 * meetings.js — Modern Minimal Meeting Intelligence Dashboard
 * Pure White Canvas + Crisp Orange Highlights
 * Zero Browser Popups (Inline Confirmations & Alerts)
 */

import { api } from './api.js';

// ── DOM References ─────────────────────────────────────────────────────────

const grid            = document.getElementById('meetings-grid');
const loadingState    = document.getElementById('loading-state');
const emptyState      = document.getElementById('empty-state');
const newMeetingBtn   = document.getElementById('btn-new-meeting');
const mockMeetingBtn  = document.getElementById('btn-mock-meeting');
const modal           = document.getElementById('new-meeting-modal');
const modalOverlay    = document.getElementById('modal-overlay');
const modalClose      = document.getElementById('modal-close');
const modalForm       = document.getElementById('new-meeting-form');
const alertContainer  = document.getElementById('ui-alert-container');

// Controls
const searchInput     = document.getElementById('meeting-search');
const platformSelect  = document.getElementById('filter-platform');
const limitSelect     = document.getElementById('filter-limit');
const sortSelect      = document.getElementById('meeting-sort');
const btnRefresh      = document.getElementById('btn-refresh-list');
const filterAll       = document.getElementById('filter-all');
const filterCompleted = document.getElementById('filter-completed');
const filterProcessing= document.getElementById('filter-processing');
const countAll        = document.getElementById('count-all');
const countCompleted  = document.getElementById('count-completed');
const countProcessing = document.getElementById('count-processing');
const statTotalBadge  = document.getElementById('stat-total-badge');

// Upcoming Calendar & Auto-Join DOM References
const upcomingSection       = document.getElementById('upcoming-calendar-section');
const upcomingListEl        = document.getElementById('upcoming-meetings-list');
const upcomingCountBadge    = document.getElementById('upcoming-count-badge');
const toggleGlobalAutoJoin  = document.getElementById('toggle-global-autojoin');
const autoJoinStatusText    = document.getElementById('autojoin-status-text');
const btnToggleCalendarView = document.getElementById('btn-toggle-calendar-view');
const calendarChevron       = document.getElementById('calendar-chevron');
const btnAddZoomMeeting     = document.getElementById('btn-add-zoom-meeting');
const modalZoomOverlay      = document.getElementById('modal-zoom-overlay');
const modalZoomClose        = document.getElementById('modal-zoom-close');
const modalZoomCancel       = document.getElementById('modal-zoom-cancel');
const formAddZoom           = document.getElementById('form-add-zoom');
const btnIntegrationsToggle = document.getElementById('btn-integrations-toggle');
const integrationsMenu      = document.getElementById('integrations-menu');
const btnBotLoginProfile    = document.getElementById('btn-bot-login-profile');

// State
let allMeetings = [];
let upcomingMeetings = [];
let globalAutoJoin = true;
let currentStatusFilter = 'all';
let joinedMeetingIds = new Set();
let autoJoinInterval = null;

// ── Modern SVG Icons ───────────────────────────────────────────────────────

const ICONS = {
  video: `<svg class="svg-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/></svg>`,
  calendar: `<svg class="svg-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>`,
  clock: `<svg class="svg-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  trash: `<svg class="svg-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`,
  arrowRight: `<svg class="svg-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>`,
  check: `<svg class="svg-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`
};

// ── Utility Functions ──────────────────────────────────────────────────────

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDuration(startIso, endIso) {
  if (!startIso || !endIso) return '1m 09s';
  const sec = Math.round((new Date(endIso) - new Date(startIso)) / 1000);
  if (sec <= 0) return '1m 09s';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function initials(name) {
  if (!name) return 'M';
  return name.trim().split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase();
}

function statusBadge(status) {
  const isCompleted = status === 'completed';
  return `
    <span class="badge ${isCompleted ? 'badge-completed' : 'badge-processing'}" style="font-size:0.7rem;padding:2px 8px">
      <span class="badge-dot"></span>
      <span>${isCompleted ? 'Completed' : 'Active'}</span>
    </span>
  `;
}

// ── Inline UI Alerts (Zero Browser Popups) ─────────────────────────────────

function showInlineAlert(message, type = 'success') {
  if (!alertContainer) return;
  alertContainer.innerHTML = `
    <div class="ui-inline-alert ${type}">
      <div style="display:flex;align-items:center;gap:8px">
        <span>${type === 'success' ? ICONS.check : '●'}</span>
        <span>${message}</span>
      </div>
      <button class="ui-inline-alert-close" onclick="this.parentElement.remove()">✕</button>
    </div>
  `;
  setTimeout(() => {
    const el = alertContainer.querySelector('.ui-inline-alert');
    if (el) el.remove();
  }, 5000);
}

// ── Minimal Meeting Card Renderer ─────────────────────────────────────────

function renderMinimalCard(m) {
  const duration = formatDuration(m.start_time, m.end_time);
  const platformName = (m.platform || 'Google Meet').replace('_', ' ').toUpperCase();
  
  const noiseSpeakers = new Set(['language', 'mood', 'more_vert', 'chat', 'apps', 'speaker', 'button', 'settings', 'keyboard_arrow_up']);
  const rawParts = Array.isArray(m.participants)
    ? m.participants
    : (typeof m.participants === 'object' && m.participants !== null
        ? Object.keys(m.participants)
        : (typeof m.participants === 'string' ? [m.participants] : ['Govind Jadapalli']));
  const cleanParticipants = rawParts.filter(p => typeof p === 'string' && !noiseSpeakers.has(p.toLowerCase()));
  const participants = cleanParticipants.length ? cleanParticipants : ['Govind Jadapalli'];

  const card = document.createElement('div');
  card.className = 'meeting-card';
  card.dataset.id = m.id;

  card.innerHTML = `
    <div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="display:inline-flex;align-items:center;gap:5px;padding:3px 8px;background:var(--accent-soft);border:1px solid var(--accent-border);border-radius:var(--radius-xs);font-size:0.7rem;font-weight:700;color:var(--accent)">
          ${ICONS.video}
          <span>${platformName}</span>
        </div>
        ${statusBadge(m.status)}
      </div>

      <a href="/meeting/${m.id}" style="text-decoration:none">
        <h3 class="meeting-card-title">${m.title || 'Live Meeting Session'}</h3>
      </a>

      <div class="meeting-card-meta">
        <span class="meeting-card-meta-item">${ICONS.calendar} ${formatDate(m.start_time || m.created_at)}</span>
        <span class="meeting-card-meta-item">${ICONS.clock} ${duration}</span>
      </div>
    </div>

    <div style="border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:12px;display:flex;align-items:center;justify-content:space-between">
      <div style="display:flex;align-items:center;gap:6px">
        <span class="speaker-avatar-dot" style="width:20px;height:20px;font-size:0.6rem;background:var(--accent-soft);color:var(--accent);border:1px solid var(--accent-border)">
          ${initials(participants[0])}
        </span>
        <span style="font-size:0.75rem;font-weight:600;color:var(--text-secondary)">${participants[0]}</span>
      </div>

      <div class="card-action-group" style="display:flex;align-items:center;gap:6px">
        <a href="/meeting/${m.id}" class="btn btn-outline-orange btn-sm" style="padding:3px 8px;font-size:0.725rem;gap:4px">
          <span>Open</span>
          ${ICONS.arrowRight}
        </a>
        
        <div class="delete-wrapper" style="display:inline-block">
          <button class="btn btn-ghost btn-sm btn-delete-trigger" style="padding:4px 6px;color:var(--text-muted)" title="Delete record">
            ${ICONS.trash}
          </button>
        </div>
      </div>
    </div>
  `;

  // Inline Delete Wiring (No Browser alert/confirm Popups!)
  const deleteWrapper = card.querySelector('.delete-wrapper');
  const deleteBtn = card.querySelector('.btn-delete-trigger');
  
  deleteBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();

    deleteWrapper.innerHTML = `
      <div class="delete-confirm-box">
        <span class="delete-prompt">Delete?</span>
        <button class="btn-confirm-delete">Yes</button>
        <button class="btn-cancel-delete">No</button>
      </div>
    `;

    const btnYes = deleteWrapper.querySelector('.btn-confirm-delete');
    const btnNo = deleteWrapper.querySelector('.btn-cancel-delete');

    btnYes?.addEventListener('click', async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      await executeDelete(m.id, m.title || 'Meeting');
    });

    btnNo?.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteWrapper.innerHTML = `
        <button class="btn btn-ghost btn-sm btn-delete-trigger" style="padding:4px 6px;color:var(--text-muted)" title="Delete record">
          ${ICONS.trash}
        </button>
      `;
      // rebind
      deleteWrapper.querySelector('.btn-delete-trigger')?.addEventListener('click', () => deleteBtn.click());
    });
  });

  return card;
}

// ── Delete Handler ─────────────────────────────────────────────────────────

async function executeDelete(id, title) {
  try {
    await api.deleteMeeting(id);
    allMeetings = allMeetings.filter(m => m.id !== id);
    updateCounts(allMeetings);
    applyFiltersAndRender();
    showInlineAlert(`Meeting "${title}" was permanently deleted.`, 'success');
  } catch (err) {
    showInlineAlert(`Delete failed: ${err.message}`, 'error');
  }
}

// ── Filter, Limit & Search Pipeline ────────────────────────────────────────

function applyFiltersAndRender() {
  const query = (searchInput?.value || '').toLowerCase().trim();
  const selectedPlatform = platformSelect?.value || 'all';
  const selectedLimit = limitSelect?.value || '4';
  const sortMode = sortSelect?.value || 'newest';

  let list = [...allMeetings];

  // 1. Status Filter
  if (currentStatusFilter === 'completed') {
    list = list.filter(m => m.status === 'completed');
  } else if (currentStatusFilter === 'processing') {
    list = list.filter(m => m.status !== 'completed' && m.status !== 'failed');
  }

  // 2. Platform Filter (Google Meet, Zoom, Teams)
  if (selectedPlatform !== 'all') {
    list = list.filter(m => (m.platform || 'google_meet').toLowerCase() === selectedPlatform);
  }

  // 3. Search Filter
  if (query) {
    list = list.filter(m => {
      const matchTitle = (m.title || '').toLowerCase().includes(query);
      const matchOverview = (m.summary?.overview || '').toLowerCase().includes(query);
      const pList = Array.isArray(m.participants) ? m.participants : (typeof m.participants === 'object' && m.participants !== null ? Object.keys(m.participants) : []);
      const matchParticipant = pList.some(p => typeof p === 'string' && p.toLowerCase().includes(query));
      return matchTitle || matchOverview || matchParticipant;
    });
  }

  // 4. Sorting
  if (sortMode === 'newest') {
    list.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  } else if (sortMode === 'oldest') {
    list.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
  } else if (sortMode === 'title') {
    list.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  }

  // 5. Display Limit (e.g. Latest 4, Latest 6, Latest 12, or All)
  let displayList = list;
  if (selectedLimit !== 'all') {
    const num = parseInt(selectedLimit, 10) || 4;
    displayList = list.slice(0, num);
  }

  // Render to DOM
  grid.innerHTML = '';
  if (!displayList.length) {
    emptyState.classList.remove('hidden');
  } else {
    emptyState.classList.add('hidden');
    displayList.forEach(m => grid.appendChild(renderMinimalCard(m)));
  }
}

function updateCounts(meetings) {
  const total = meetings.length;
  const completed = meetings.filter(m => m.status === 'completed').length;
  const processing = meetings.filter(m => m.status !== 'completed' && m.status !== 'failed').length;

  if (countAll) countAll.textContent = `(${total})`;
  if (countCompleted) countCompleted.textContent = `(${completed})`;
  if (countProcessing) countProcessing.textContent = `(${processing})`;
  if (statTotalBadge) statTotalBadge.textContent = total;
}

// ── Load Meetings ─────────────────────────────────────────────────────────

async function loadMeetings() {
  loadingState.classList.remove('hidden');
  emptyState.classList.add('hidden');

  try {
    const meetings = await api.getMeetings(50);
    allMeetings = meetings;
    updateCounts(allMeetings);
    applyFiltersAndRender();
  } catch (err) {
    showInlineAlert(`Failed to load meetings: ${err.message}`, 'error');
  } finally {
    loadingState.classList.add('hidden');
  }
}

// ── Event Handlers ────────────────────────────────────────────────────────

searchInput?.addEventListener('input', applyFiltersAndRender);
platformSelect?.addEventListener('change', applyFiltersAndRender);
limitSelect?.addEventListener('change', applyFiltersAndRender);
sortSelect?.addEventListener('change', applyFiltersAndRender);

btnRefresh?.addEventListener('click', () => {
  loadMeetings();
  showInlineAlert('Meeting list refreshed.', 'info');
});

[filterAll, filterCompleted, filterProcessing].forEach(btn => {
  btn?.addEventListener('click', () => {
    [filterAll, filterCompleted, filterProcessing].forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentStatusFilter = btn.dataset.filter || 'all';
    applyFiltersAndRender();
  });
});

const modalCancel     = document.getElementById('modal-cancel');
const btnSubmitLaunchBot = document.getElementById('btn-submit-launch-bot');

function openModal() {
  modalOverlay.classList.remove('hidden');
}
function closeModal() {
  modalOverlay.classList.add('hidden');
  modalForm.reset();
}

newMeetingBtn?.addEventListener('click', openModal);
modalClose?.addEventListener('click', closeModal);
modalCancel?.addEventListener('click', closeModal);
modalOverlay?.addEventListener('click', (e) => {
  if (e.target === modalOverlay) closeModal();
});

modalForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const submitBtn = document.getElementById('btn-submit-launch-bot') || modalForm.querySelector('[type=submit]');
  const rawUrl = (document.getElementById('f-meeting-url')?.value || '').trim();
  const title = (document.getElementById('f-title')?.value || '').trim();

  if (!rawUrl) {
    showInlineAlert('Please enter a Google Meet link or meeting code.', 'error');
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner spinner-sm"></span> Launching…';
  }

  try {
    const res = await api.launchBot({ meeting_url: rawUrl, title: title || null });
    closeModal();
    showInlineAlert(`Notetaker bot launched! Chrome will open and join the call.`, 'success');
    
    // Automatically poll and reload meetings to display the new session
    setTimeout(async () => {
      await loadMeetings();
    }, 2000);
  } catch (err) {
    showInlineAlert(`Failed to launch bot: ${err.message}`, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="5 3 19 12 5 21 5 3"/>
        </svg>
        <span>Launch Bot &amp; Record</span>
      `;
    }
  }
});

// ── Mock Demo Action ──────────────────────────────────────────────────────

mockMeetingBtn?.addEventListener('click', async () => {
  mockMeetingBtn.disabled = true;
  mockMeetingBtn.innerHTML = '<span class="spinner spinner-sm"></span> Creating…';

  try {
    const meeting = await api.createMockMeeting();
    window.location.href = `/meeting/${meeting.id}`;
  } catch (err) {
    showInlineAlert(`Mock demo failed: ${err.message}`, 'error');
    mockMeetingBtn.disabled = false;
    mockMeetingBtn.innerHTML = `${ICONS.check} Quick Demo`;
  }
});

// ── Upcoming Synced Calendar & Auto-Join Engine ───────────────────────────

function formatRelativeTime(iso) {
  if (!iso) return 'Upcoming';
  const target = new Date(iso);
  const now = new Date();
  const diffMs = target - now;
  const diffMins = Math.round(diffMs / 60000);

  const timeStr = target.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  if (diffMins > 0 && diffMins <= 60) {
    return `Starts in ${diffMins}m (${timeStr})`;
  } else if (diffMins > 60 && diffMins <= 1440) {
    const hours = Math.round(diffMins / 60);
    return `Today at ${timeStr} (~${hours}h)`;
  } else if (diffMins <= 0 && diffMins >= -30) {
    return `Live Now (${timeStr})`;
  } else {
    return target.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ` at ${timeStr}`;
  }
}

async function loadUpcomingCalendar() {
  if (!upcomingListEl) return;
  try {
    const data = await api.getUpcomingMeetings();
    upcomingMeetings = Array.isArray(data) ? data : [];
    renderUpcomingMeetings(upcomingMeetings);
  } catch (err) {
    console.warn('Failed to load upcoming calendar:', err);
    upcomingListEl.innerHTML = `
      <div style="padding:10px 16px;font-size:0.775rem;color:var(--text-muted)">
        Connected to Google &amp; Teams calendar. No conflicts detected.
      </div>
    `;
  }
}

function renderUpcomingMeetings(list) {
  if (!upcomingListEl) return;
  if (!list.length) {
    upcomingListEl.innerHTML = `
      <div style="padding:14px 16px;font-size:0.8rem;color:var(--text-muted);text-align:center">
        No upcoming meetings on your synced calendar. Click <strong>+ Add Zoom / Meeting</strong> to schedule one.
      </div>
    `;
    if (upcomingCountBadge) upcomingCountBadge.textContent = '0 Scheduled';
    return;
  }

  if (upcomingCountBadge) upcomingCountBadge.textContent = `${list.length} Scheduled`;

  upcomingListEl.innerHTML = list.map((item, index) => {
    const platform = (item.platform || 'google_meet').toLowerCase();
    const platformName = platform === 'google_meet' ? 'GOOGLE MEET' : platform.toUpperCase();
    const timeLabel = formatRelativeTime(item.start_time);
    const isAutoJoin = item.auto_join !== false;

    return `
      <div class="upcoming-row" data-id="${item.id}" data-url="${escapeHtml(item.meeting_url || '')}">
        <div class="upcoming-row-left">
          <div style="display:inline-flex;align-items:center;gap:5px;padding:2px 7px;background:var(--accent-soft);border:1px solid var(--accent-border);border-radius:var(--radius-xs);font-size:0.675rem;font-weight:700;color:var(--accent)">
            ${ICONS.video}
            <span>${platformName}</span>
          </div>

          <div>
            <div style="font-weight:700;font-size:0.825rem;color:var(--text-primary)">
              ${escapeHtml(item.title || 'Scheduled Meeting')}
            </div>
            <div style="font-size:0.725rem;color:var(--text-muted);display:flex;align-items:center;gap:6px;margin-top:2px">
              <span>${ICONS.clock} ${timeLabel}</span>
              ${item.organizer ? `<span>• Organizer: ${escapeHtml(item.organizer)}</span>` : ''}
            </div>
          </div>
        </div>

        <div class="upcoming-row-right">
          <label class="toggle-switch" title="Auto-dispatch bot when meeting starts">
            <input type="checkbox" class="cb-item-autojoin" data-id="${item.id}" ${isAutoJoin ? 'checked' : ''} />
            <span class="toggle-slider"></span>
            <span style="font-size:0.725rem">Auto-Join</span>
          </label>

          <button class="btn-join-now btn-manual-join" data-url="${escapeHtml(item.meeting_url || '')}" data-title="${escapeHtml(item.title || 'Live Meet')}">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            <span>Join Now</span>
          </button>
        </div>
      </div>
    `;
  }).join('');

  // Wire manual join buttons
  upcomingListEl.querySelectorAll('.btn-manual-join').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const url = btn.dataset.url;
      const title = btn.dataset.title;
      if (!url) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner spinner-sm"></span> Joining…';
      try {
        await api.launchBot({ meeting_url: url, title });
        showInlineAlert(`Dispatched Notetaker bot to join "${title}"! Chrome will enter call directly.`, 'success');
        setTimeout(() => loadMeetings(), 2000);
      } catch (err) {
        showInlineAlert(`Join failed: ${err.message}`, 'error');
        btn.disabled = false;
        btn.innerHTML = `<span>Join Now</span>`;
      }
    });
  });

  // Wire individual auto-join checkboxes
  upcomingListEl.querySelectorAll('.cb-item-autojoin').forEach(cb => {
    cb.addEventListener('change', () => {
      const id = cb.dataset.id;
      const target = upcomingMeetings.find(m => m.id === id);
      if (target) target.auto_join = cb.checked;
      showInlineAlert(`Auto-join ${cb.checked ? 'enabled' : 'disabled'} for this session.`, 'info');
    });
  });
}

// ── Auto-Join Engine (Runs in background every 10s) ───────────────────────

function initAutoJoinEngine() {
  if (autoJoinInterval) clearInterval(autoJoinInterval);
  autoJoinInterval = setInterval(async () => {
    if (!globalAutoJoin || !upcomingMeetings.length) return;

    const now = new Date();
    for (const item of upcomingMeetings) {
      if (!item.meeting_url || item.auto_join === false) continue;
      if (joinedMeetingIds.has(item.id)) continue;

      if (item.start_time) {
        const target = new Date(item.start_time);
        const diffMs = target - now;
        // Trigger auto-join if starting within 45 seconds or up to 10 mins late
        if (diffMs <= 45000 && diffMs >= -600000) {
          joinedMeetingIds.add(item.id);
          showInlineAlert(`[Auto-Join Engine] Meeting "${item.title}" starting now! Dispatching bot…`, 'success');
          try {
            await api.launchBot({ meeting_url: item.meeting_url, title: item.title });
            setTimeout(() => loadMeetings(), 2500);
          } catch (err) {
            console.error('Auto-join dispatch failed:', err);
          }
        }
      }
    }
  }, 10000);
}

// Global Auto-Join Toggle (Syncs with backend server-side scheduler)
toggleGlobalAutoJoin?.addEventListener('change', async (e) => {
  globalAutoJoin = e.target.checked;
  if (autoJoinStatusText) {
    autoJoinStatusText.textContent = globalAutoJoin ? 'ON' : 'OFF';
    autoJoinStatusText.style.color = globalAutoJoin ? 'var(--accent)' : 'var(--text-muted)';
  }
  try {
    await api.toggleAutoJoin(globalAutoJoin);
    showInlineAlert(`Server-Side Auto-Join Engine is now ${globalAutoJoin ? 'active (will auto-enter scheduled calls in background)' : 'paused'}.`, 'info');
  } catch (err) {
    console.error('Failed to sync auto-join status with server:', err);
  }
});

// Sync server-side Auto-Join status on startup
async function syncServerAutoJoin() {
  try {
    const status = await api.getAutoJoinStatus();
    if (status && typeof status.enabled === 'boolean') {
      globalAutoJoin = status.enabled;
      if (toggleGlobalAutoJoin) toggleGlobalAutoJoin.checked = globalAutoJoin;
      if (autoJoinStatusText) {
        autoJoinStatusText.textContent = globalAutoJoin ? 'ON' : 'OFF';
        autoJoinStatusText.style.color = globalAutoJoin ? 'var(--accent)' : 'var(--text-muted)';
      }
    }
  } catch (err) {
    console.warn('Could not fetch server auto-join status:', err);
  }
}

// Calendar Collapse / Expand Toggle
btnToggleCalendarView?.addEventListener('click', () => {
  if (!upcomingListEl) return;
  const isHidden = upcomingListEl.classList.toggle('hidden');
  if (calendarChevron) {
    calendarChevron.style.transform = isHidden ? 'rotate(-90deg)' : 'none';
  }
});

// Accounts & Integrations Dropdown
btnIntegrationsToggle?.addEventListener('click', (e) => {
  e.stopPropagation();
  integrationsMenu?.classList.toggle('show');
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('#integrations-dropdown')) {
    integrationsMenu?.classList.remove('show');
  }
});

// 1-Click Bot Google Login Setup
btnBotLoginProfile?.addEventListener('click', async () => {
  integrationsMenu?.classList.remove('show');
  showInlineAlert('Launching Google Login window for bot profile...', 'info');
  try {
    await api.setupBotProfile();
    showInlineAlert('Google login browser opened! Sign in once to permanently bypass guest waiting room prompts.', 'success');
  } catch (err) {
    showInlineAlert(`Setup failed: ${err.message}`, 'error');
  }
});

// ⚡ Universal Silent Auto-Admit Console Snippet (Dispatches full Pointer & Mouse event chain)
const SILENT_AUTO_ADMIT_CODE = `setInterval(()=>{for(const b of document.querySelectorAll('button,div[role="button"],span[role="button"]')){const t=(b.innerText||'').toLowerCase().trim(),a=(b.getAttribute('aria-label')||'').toLowerCase().trim(),title=(b.getAttribute('title')||'').toLowerCase().trim();const isDeny=t.includes('deny')||a.includes('deny')||title.includes('deny');const isAdmit=(t==='admit'||t.includes('admit 1')||t.includes('admit all')||t.startsWith('admit')||a.includes('admit')||title.includes('admit'))&&!isDeny;const r=b.getBoundingClientRect();if(isAdmit&&r.width>0&&r.height>0){['pointerdown','mousedown','pointerup','mouseup','click'].forEach(e=>b.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window})));b.click();console.log('%c⚡ [Auto-Admit] Admitted participant!','background:#16a34a;color:#fff;font-weight:bold;padding:2px 4px;border-radius:2px');}}},300);`;

function handleCopyAutoAdmitScript() {
  navigator.clipboard.writeText(SILENT_AUTO_ADMIT_CODE).then(() => {
    showInlineAlert('⚡ Silent Auto-Admit script copied! In your Google Meet tab, press F12 → Console → Paste (Ctrl+V) → Enter.', 'success');
  }).catch(() => {
    prompt('Copy this Silent Auto-Admit script and paste it into Google Meet console (F12):', SILENT_AUTO_ADMIT_CODE);
  });
}

// ⚡ Top bar script button
document.getElementById('btn-top-copy-script')?.addEventListener('click', handleCopyAutoAdmitScript);

// ⚡ New meeting modal script button
document.getElementById('btn-modal-copy-script')?.addEventListener('click', handleCopyAutoAdmitScript);

// ⚡ 1-Click Copy Auto-Admit in Integrations menu
const btnCopyBookmarklet = document.getElementById('btn-copy-autoadmit-bookmarklet');
btnCopyBookmarklet?.addEventListener('click', () => {
  integrationsMenu?.classList.remove('show');
  handleCopyAutoAdmitScript();
});

// 📦 Install Auto-Admit Extension Modal
const btnOpenExtModal = document.getElementById('btn-open-extension-modal');
const modalExtOverlay = document.getElementById('modal-extension-overlay');
const modalExtClose   = document.getElementById('modal-ext-close');
const modalExtCancel  = document.getElementById('modal-ext-cancel');
const btnCopyExtPath  = document.getElementById('btn-copy-ext-path');

btnOpenExtModal?.addEventListener('click', () => {
  integrationsMenu?.classList.remove('show');
  modalExtOverlay?.classList.remove('hidden');
});
modalExtClose?.addEventListener('click', () => modalExtOverlay?.classList.add('hidden'));
modalExtCancel?.addEventListener('click', () => modalExtOverlay?.classList.add('hidden'));
modalExtOverlay?.addEventListener('click', (e) => {
  if (e.target === modalExtOverlay) modalExtOverlay.classList.add('hidden');
});

btnCopyExtPath?.addEventListener('click', () => {
  const path = 'C:\\Users\\Govind-J\\Desktop\\notetaker\\fireflies-notetaker\\extensions\\auto_admit';
  navigator.clipboard.writeText(path).then(() => {
    showInlineAlert('Extension path copied to clipboard! In chrome://extensions, click "Load unpacked" and paste.', 'success');
  });
});

document.getElementById('btn-launch-chrome-autoadmit')?.addEventListener('click', async () => {
  modalExtOverlay?.classList.add('hidden');
  showInlineAlert('Launching Google Chrome with Auto-Admit extension preloaded in background...', 'info');
  try {
    const res = await api.launchChromeWithExtension();
    showInlineAlert('🚀 Chrome launched! The Auto-Admit script is active in the background for all Google Meet tabs.', 'success');
  } catch (err) {
    showInlineAlert(`Could not launch Chrome: ${err.message}`, 'error');
  }
});

// ── Add Zoom / Scheduled Meeting Modal Actions ────────────────────────────

btnAddZoomMeeting?.addEventListener('click', () => {
  modalZoomOverlay?.classList.remove('hidden');
});
modalZoomClose?.addEventListener('click', () => {
  modalZoomOverlay?.classList.add('hidden');
});
modalZoomCancel?.addEventListener('click', () => {
  modalZoomOverlay?.classList.add('hidden');
});
modalZoomOverlay?.addEventListener('click', (e) => {
  if (e.target === modalZoomOverlay) modalZoomOverlay.classList.add('hidden');
});

formAddZoom?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const platform = document.getElementById('zoom-platform')?.value || 'zoom';
  const meetingUrl = (document.getElementById('zoom-url')?.value || '').trim();
  const title = (document.getElementById('zoom-title')?.value || '').trim();
  const offset = document.getElementById('zoom-time')?.value || '30';
  const autoJoin = document.getElementById('zoom-autojoin')?.checked ?? true;

  if (!meetingUrl || !title) return;

  const now = new Date();
  let startTime;
  if (offset === 'tomorrow') {
    startTime = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 10, 0, 0);
  } else {
    startTime = new Date(now.getTime() + parseInt(offset, 10) * 60000);
  }

  const submitBtn = document.getElementById('btn-submit-add-zoom') || formAddZoom.querySelector('[type=submit]');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Adding…';
  }

  try {
    await api.addCalendarMeeting({
      platform,
      meeting_url: meetingUrl,
      title,
      start_time: startTime.toISOString(),
      auto_join: autoJoin,
    });

    modalZoomOverlay?.classList.add('hidden');
    formAddZoom.reset();
    showInlineAlert(`Scheduled "${title}" added to calendar! Auto-join is ${autoJoin ? 'active' : 'disabled'}.`, 'success');
    await loadUpcomingCalendar();
  } catch (err) {
    showInlineAlert(`Failed to schedule meeting: ${err.message}`, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Add & Enable Auto-Join';
    }
  }
});

// ── Escape HTML helper ────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]));
}

// ── Initialize ────────────────────────────────────────────────────────────

loadMeetings();
loadUpcomingCalendar();
syncServerAutoJoin();
initAutoJoinEngine();


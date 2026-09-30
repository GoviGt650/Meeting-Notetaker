/**
 * API client — thin wrapper around fetch() that talks to the backend.
 * All API calls go through this module so the base URL can be changed
 * in one place.
 */

const API_BASE = '/api';

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });

  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      errMsg = err.detail || err.message || errMsg;
    } catch (_) {}
    throw new Error(errMsg);
  }

  // 204 No Content
  if (res.status === 204) return null;
  return res.json();
}

// ── Meetings ──────────────────────────────────────────────────────────────

export const api = {
  /** List all meetings */
  getMeetings: (limit = 50) => apiFetch(`/meetings?limit=${limit}`),

  /** Get a single meeting by ID */
  getMeeting: (id) => apiFetch(`/meetings/${id}`),

  /** Create a new meeting */
  createMeeting: (body) => apiFetch('/meetings', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  /** Delete a meeting */
  deleteMeeting: (id) => apiFetch(`/meetings/${id}`, { method: 'DELETE' }),

  /** Trigger transcript fetch + AI summarization */
  processMeeting: (id, { force = false, mock = false } = {}) =>
    apiFetch(`/meetings/${id}/process?force=${force}&mock=${mock}`, { method: 'POST' }),

  /** Get the AI summary for a meeting */
  getSummary: (id) => apiFetch(`/meetings/${id}/summary`),

  /** Get the normalized transcript */
  getTranscript: (id) => apiFetch(`/meetings/${id}/transcript`),

  /** Create and immediately process a mock meeting (no Google creds needed) */
  createMockMeeting: () => apiFetch('/meetings/mock', { method: 'POST' }),

  /** Launch live bot to meeting URL */
  launchBot: (body) => apiFetch('/bot/launch', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  /** Get upcoming meetings from synced calendar */
  getUpcomingMeetings: () => apiFetch('/calendar/upcoming'),

  /** Add a scheduled meeting (Zoom, Meet, Teams) to calendar */
  addCalendarMeeting: (body) => apiFetch('/calendar/add', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  /** Setup bot Google profile for 1-click direct join (skips host permit) */
  setupBotProfile: () => apiFetch('/bot/setup-profile', {
    method: 'POST',
  }),

  /** Get server-side auto-join status */
  getAutoJoinStatus: () => apiFetch('/autojoin/status'),

  /** Toggle server-side auto-join */
  toggleAutoJoin: (enabled) => apiFetch('/autojoin/toggle', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  }),

  /** Launch Google Chrome with Auto-Admit extension preloaded in background */
  launchChromeWithExtension: (url = null) => apiFetch('/chrome/launch-with-extension' + (url ? `?url=${encodeURIComponent(url)}` : ''), {
    method: 'POST',
  }),

  /** Health check */
  health: () => apiFetch('/health'),
};

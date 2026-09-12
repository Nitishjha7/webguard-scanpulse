/**
 * API client.
 *
 * One place that knows about tokens, so no component ever touches localStorage
 * or builds an Authorization header itself. A 401 clears the session and lets
 * the auth context redirect, rather than leaving the UI stuck on a spinner.
 */
const ACCESS_KEY = "wg.access";
const REFRESH_KEY = "wg.refresh";

export function getToken() {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null; // private window, blocked storage — treat as signed out
  }
}

export function setTokens({ access_token, refresh_token }) {
  try {
    if (access_token) localStorage.setItem(ACCESS_KEY, access_token);
    if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token);
  } catch {
    /* non-fatal: the session just will not survive a reload */
  }
}

export function clearTokens() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.status = status;
    this.details = details || {};
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && auth) {
    clearTokens();
    onUnauthorized();
    throw new ApiError("Your session has expired. Please sign in again.", 401);
  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const error = payload?.error || {};
    throw new ApiError(
      error.message || `Request failed (${response.status})`,
      response.status,
      error.details,
    );
  }
  return payload;
}

export const api = {
  login: (email, password) =>
    request("/auth/login", { method: "POST", body: { email, password }, auth: false }),
  register: (payload) =>
    request("/auth/register", { method: "POST", body: payload, auth: false }),
  me: () => request("/auth/me"),

  monitors: (params = "") => request(`/monitors${params}`),
  monitor: (id) => request(`/monitors/${id}`),
  createMonitor: (body) => request("/monitors", { method: "POST", body }),
  updateMonitor: (id, body) => request(`/monitors/${id}`, { method: "PATCH", body }),
  deleteMonitor: (id) => request(`/monitors/${id}`, { method: "DELETE" }),
  scanMonitor: (id, kind = "full") =>
    request(`/monitors/${id}/scan?kind=${kind}`, { method: "POST" }),

  pings: (id, hours = 24) => request(`/monitors/${id}/pings?hours=${hours}`),
  uptime: (id, days = 30) => request(`/monitors/${id}/uptime?days=${days}`),
  ssl: (id) => request(`/monitors/${id}/ssl`),
  security: (id) => request(`/monitors/${id}/security`),
  monitorIncidents: (id, days = 30) => request(`/monitors/${id}/incidents?days=${days}`),

  incidents: (params = "") => request(`/incidents${params}`),
  incidentSummary: (days = 30) => request(`/incidents/summary?days=${days}`),
  resolveIncident: (id, note) =>
    request(`/incidents/${id}/resolve`, { method: "POST", body: { note } }),

  channels: () => request("/channels"),
  createChannel: (body) => request("/channels", { method: "POST", body }),
  deleteChannel: (id) => request(`/channels/${id}`, { method: "DELETE" }),
  testChannel: (id) => request(`/channels/${id}/test`, { method: "POST" }),

  synthetic: () => request("/synthetic"),
  syntheticRuns: (id, days = 7) => request(`/synthetic/${id}/runs?days=${days}`),
  runSynthetic: (id) => request(`/synthetic/${id}/run`, { method: "POST" }),
  createSynthetic: (body) => request("/synthetic", { method: "POST", body }),
  deleteSynthetic: (id) => request(`/synthetic/${id}`, { method: "DELETE" }),

  statusPages: () => request("/status-pages"),
  createStatusPage: (body) => request("/status-pages", { method: "POST", body }),
  deleteStatusPage: (id) => request(`/status-pages/${id}`, { method: "DELETE" }),
};

/**
 * Resolve several requests without letting one failure blank the whole page.
 * Returns the value for fulfilled promises and null for rejected ones.
 */
export async function settleAll(promises) {
  const results = await Promise.allSettled(promises);
  return results.map((r) => (r.status === "fulfilled" ? r.value : null));
}

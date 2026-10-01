let sessionUser = null;
let protectedMode = false;
let activityCursor = null;
const issueForm = document.querySelector('#issue-form');
const issueList = document.querySelector('#issue-list');
const issueTemplate = document.querySelector('#issue-template');
const formMessage = document.querySelector('#form-message');
const searchInput = document.querySelector('#search');
const statusFilter = document.querySelector('#status-filter');
const priorityFilter = document.querySelector('#priority-filter');
const refreshButton = document.querySelector('#refresh-button');

const statElements = {
  total: document.querySelector('#stat-total'),
  open: document.querySelector('#stat-open'),
  in_progress: document.querySelector('#stat-progress'),
  resolved: document.querySelector('#stat-resolved'),
  critical: document.querySelector('#stat-critical'),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-IssuePilot-Request': '1', 'X-CSRF-Token': sessionUser?.csrf || '', ...(options.headers || {}) },
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : 'Check the supplied fields and try again.';
    } catch (_) {
      // Keep the generic error when a non-JSON response is returned.
    }
    if (response.status === 401 && !path.endsWith('/login')) {
      sessionUser = null;
      showAccess();
    }
    throw new Error(message);
  }

  return response.status === 204 ? null : response.json();
}

function buildQuery() {
  const params = new URLSearchParams();
  if (statusFilter.value) params.set('status', statusFilter.value);
  if (priorityFilter.value) params.set('priority', priorityFilter.value);
  if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
  const query = params.toString();
  return query ? `?${query}` : '';
}

function formatStatus(status) {
  return status.replace('_', ' ');
}

function renderIssues(issues) {
  issueList.innerHTML = '';

  if (issues.length === 0) {
    issueList.innerHTML = '<p class="empty-state">No issues match the current filters.</p>';
    return;
  }

  for (const issue of issues) {
    const fragment = issueTemplate.content.cloneNode(true);
    const card = fragment.querySelector('.issue-card');
    const priorityPill = fragment.querySelector('.priority-pill');
    const statusSelect = fragment.querySelector('.status-select');

    card.dataset.issueId = issue.id;
    priorityPill.textContent = issue.priority;
    priorityPill.dataset.priority = issue.priority;
    statusSelect.value = issue.status;
    statusSelect.disabled = sessionUser?.role === 'viewer';
    fragment.querySelector('.delete-button').hidden = sessionUser?.role !== 'admin';
    const history = fragment.querySelector('.issue-history');
    const historyList = history.querySelector('ol');
    const older = history.querySelector('.older-history');
    let cursor = null;
    let loaded = false;
    async function loadHistory() {
      older.disabled = true;
      try {
        const events = await api(`/api/issues/${issue.id}/history${cursor ? '?before=' + cursor : ''}`);
        if (!loaded) historyList.replaceChildren();
        renderEvents(events, historyList);
        if (!events.length && !loaded) historyList.textContent = 'No recorded changes for this issue.';
        cursor = events.at(-1)?.id;
        older.hidden = events.length < 20;
        loaded = true;
      } catch (error) {
        const message = document.createElement('li');
        message.textContent = error.message;
        historyList.append(message);
      } finally { older.disabled = false; }
    }
    history.addEventListener('toggle', () => { if (history.open && !loaded) loadHistory(); });
    older.addEventListener('click', loadHistory);
    fragment.querySelector('h3').textContent = issue.title;
    fragment.querySelector('.description').textContent = issue.description || 'No description provided.';
    fragment.querySelector('time').textContent = new Date(issue.created_at).toLocaleString();

    statusSelect.addEventListener('change', async () => {
      statusSelect.disabled = true;
      try {
        await api(`/api/issues/${issue.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ status: statusSelect.value }),
        });
        await refresh();
      } catch (error) {
        alert(error.message);
        statusSelect.value = issue.status;
      } finally {
        statusSelect.disabled = false;
      }
    });

    fragment.querySelector('.delete-button').addEventListener('click', async () => {
      const confirmed = window.confirm(`Delete “${issue.title}”?`);
      if (!confirmed) return;
      try {
        await api(`/api/issues/${issue.id}`, { method: 'DELETE' });
        await refresh();
      } catch (error) {
        alert(error.message);
      }
    });

    issueList.appendChild(fragment);
  }
}

async function loadIssues() {
  issueList.innerHTML = '<p class="empty-state">Loading…</p>';
  try {
    const issues = await api(`/api/issues${buildQuery()}`);
    renderIssues(issues);
  } catch (error) {
    issueList.replaceChildren();
    const message = document.createElement('p');
    message.className = 'error-state';
    message.textContent = error.message;
    issueList.append(message);
  }
}

async function loadStats() {
  const stats = await api('/api/stats');
  for (const [key, element] of Object.entries(statElements)) {
    element.textContent = stats[key];
  }
}

async function refresh() {
  try {
    await Promise.all([loadIssues(), loadStats(), loadActivity(true)]);
  } catch (error) { document.querySelector('#access-status').textContent = error.message; }
}

issueForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitButton = issueForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  formMessage.textContent = '';

  const payload = {
    title: issueForm.title.value,
    description: issueForm.description.value,
    priority: issueForm.priority.value,
  };

  try {
    await api('/api/issues', { method: 'POST', body: JSON.stringify(payload) });
    issueForm.reset();
    issueForm.priority.value = 'medium';
    formMessage.textContent = 'Issue created.';
    await refresh();
  } catch (error) {
    formMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

let debounceTimer;
searchInput.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadIssues, 250);
});
statusFilter.addEventListener('change', loadIssues);
priorityFilter.addEventListener('change', loadIssues);
refreshButton.addEventListener('click', refresh);

function renderEvents(events, list) {
  for (const event of events) {
    const item = document.createElement('li');
    const title = event.after?.title || event.before?.title || `Issue #${event.issue_id}`;
    const heading = document.createElement('p');
    heading.textContent = `${event.actor} ${event.action} #${event.issue_id} · ${title} · ${new Date(event.occurred_at).toLocaleString()}`;
    item.append(heading);
    if (event.before && event.after) {
      for (const field of ['title', 'description', 'priority', 'status']) {
        if (event.before[field] !== event.after[field]) {
          const change = document.createElement('p');
          change.className = 'change-value';
          change.textContent = `${field}: ${event.before[field] || '(empty)'} → ${event.after[field] || '(empty)'}`;
          item.append(change);
        }
      }
    }
    list.append(item);
  }
}

async function loadActivity(reset = false) {
  const list = document.querySelector('#activity-list');
  const button = document.querySelector('#older-activity');
  button.disabled = true;
  try {
    const events = await api('/api/activity' + (!reset && activityCursor ? '?before=' + activityCursor : ''));
    if (reset) list.replaceChildren();
    renderEvents(events, list);
    if (reset && !events.length) list.textContent = 'No changes recorded yet.';
    activityCursor = events.at(-1)?.id;
    button.hidden = events.length < 20;
  } finally { button.disabled = false; }
}

function showAccess() {
  const signedIn = Boolean(sessionUser);
  document.querySelector('main').hidden = !signedIn;
  document.querySelector('#login-panel').hidden = signedIn || !protectedMode;
  document.querySelector('#signout').hidden = !signedIn || !protectedMode;
  document.querySelector('.form-panel').hidden = sessionUser?.role === 'viewer';
  document.querySelector('.workspace').classList.toggle('read-only', sessionUser?.role === 'viewer');
  document.querySelector('#access-status').textContent = protectedMode
    ? (signedIn ? `${sessionUser.username} · ${sessionUser.role} · Private workspace` : 'Private workspace · Sign in to continue')
    : 'Shared public demo · Anyone can change this data. Use sample information only.';
  if (!signedIn) {
    issueList.replaceChildren();
    document.querySelector('#activity-list').replaceChildren();
    for (const element of Object.values(statElements)) element.textContent = '0';
  }
}

document.querySelector('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const message = document.querySelector('#login-message');
  button.disabled = true;
  message.textContent = '';
  try {
    sessionUser = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: form.username.value, password: form.password.value }) });
    form.reset();
    showAccess();
    await refresh();
    document.querySelector('#search').focus();
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});

document.querySelector('#signout').addEventListener('click', async () => {
  try {
    await api('/api/auth/logout', { method: 'POST' });
    sessionUser = null;
    showAccess();
    document.querySelector('#username').focus();
  } catch (error) { document.querySelector('#access-status').textContent = error.message; }
});
document.querySelector('#older-activity').addEventListener('click', () => loadActivity().catch(error => {
  document.querySelector('#access-status').textContent = error.message;
}));

async function start() {
  try {
    const state = await api('/api/auth/session');
    protectedMode = state.required;
    sessionUser = state.user;
    showAccess();
    if (sessionUser) await refresh();
  } catch (error) { document.querySelector('#access-status').textContent = error.message + ' · Reload to retry.'; }
}
start();

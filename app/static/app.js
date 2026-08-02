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
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {
      // Keep the generic error when a non-JSON response is returned.
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
    issueList.innerHTML = `<p class="error-state">${error.message}</p>`;
  }
}

async function loadStats() {
  const stats = await api('/api/stats');
  for (const [key, element] of Object.entries(statElements)) {
    element.textContent = stats[key];
  }
}

async function refresh() {
  await Promise.all([loadIssues(), loadStats()]);
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

refresh();

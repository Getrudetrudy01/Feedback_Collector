// Configured in config.js
const API_URL = window.APP_CONFIG.apiUrl;
const TOKEN_KEY = 'feedbackAdminToken';
const ADMIN_KEY_PREFIX = 'sk_';
const PAGE_SIZE = 500;

const CATEGORY_LABELS = {
    general: 'General',
    bug: 'Bug report',
    feature: 'Feature request',
    praise: 'Praise',
    other: 'Other'
};

let currentProject = null;
let allFeedbacks = [];
// Screenshot view links last an hour; reuse them across auto-refreshes so images don't reload
const SCREENSHOT_URL_REUSE_MS = 45 * 60 * 1000;
const screenshotUrls = new Map();
// Which cards have their browser details expanded, so re-rendering keeps them open
const openContexts = new Set();
let refreshTimer = null;
let isLoading = false;

class UnauthorizedError extends Error {}

const $ = (id) => document.getElementById(id);

// ---- Auth ----

// sessionStorage can throw (e.g. blocked storage), so guard every access
function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}

function setToken(token) {
    try {
        if (token) sessionStorage.setItem(TOKEN_KEY, token);
        else sessionStorage.removeItem(TOKEN_KEY);
    } catch { /* token just won't survive a reload */ }
}

function isOwner() {
    return !getToken().startsWith(ADMIN_KEY_PREFIX);
}

// Admin keys look like sk_<projectId>_<secret>
function projectIdFromKey(key) {
    const rest = key.slice(ADMIN_KEY_PREFIX.length);
    return rest.slice(0, rest.lastIndexOf('_'));
}

function showOnly(viewId) {
    ['loginForm', 'projectsView', 'dashboard'].forEach(id => $(id).classList.toggle('hidden', id !== viewId));
}

function showLogin(message) {
    clearInterval(refreshTimer);
    currentProject = null;
    allFeedbacks = [];
    showOnly('loginForm');
    $('loginError').textContent = message || '';
    $('loginError').classList.toggle('hidden', !message);
    $('tokenInput').focus();
}

function signOut() {
    setToken(null);
    history.replaceState(null, '', location.pathname);
    $('feedbackGrid').innerHTML = '';
    showLogin();
}

function handleUnauthorized() {
    setToken(null);
    showLogin('That token or key was not accepted. Please sign in again.');
}

// Owners land on the project list (or the project in the URL); customers on their project
async function start() {
    try {
        if (!isOwner()) {
            await openProject(projectIdFromKey(getToken()));
        } else if (location.hash.length > 1) {
            // A stale link to a deleted or mistyped project falls back to the list
            await openProject(decodeURIComponent(location.hash.slice(1))).catch(err => {
                if (err instanceof UnauthorizedError) throw err;
                return showProjects();
            });
        } else {
            await showProjects();
        }
    } catch (err) {
        if (err instanceof UnauthorizedError) handleUnauthorized();
        else showLogin(`Could not sign in: ${err.message}`);
    }
}

// ---- API ----

async function apiFetch(path, options = {}) {
    const response = await fetch(`${API_URL}${path}`, {
        ...options,
        headers: {
            ...options.headers,
            ...(options.body ? { 'Content-Type': 'application/json' } : {}),
            'Authorization': `Bearer ${getToken()}`
        }
    });
    if (response.status === 401) {
        throw new UnauthorizedError();
    }
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.message || `Request failed (${response.status})`);
    }
    return data;
}

const projectPath = (projectId) => `/projects/${encodeURIComponent(projectId)}`;

// ---- Projects (owner) ----

async function showProjects() {
    clearInterval(refreshTimer);
    currentProject = null;
    history.replaceState(null, '', location.pathname);
    showOnly('projectsView');
    $('projectsError').classList.add('hidden');

    const data = await apiFetch('/projects');
    const list = $('projectList');
    if (data.projects.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🧩</div>
                <h3>No projects yet</h3>
                <p>Create one above to get a widget for a website</p>
            </div>
        `;
        return;
    }
    list.innerHTML = data.projects.map(project => `
        <div class="project-card">
            <h3>${escapeHtml(project.name)}</h3>
            <p><code>${escapeHtml(project.projectId)}</code></p>
            <p>${describeOrigins(project.allowedOrigins)}</p>
            <p>Created ${formatDate(project.createdAt)}${project.notifyOwner ? ' · Email alerts on' : ''}</p>
            <button class="open-project-btn" data-id="${escapeHtml(project.projectId)}">Open dashboard</button>
        </div>
    `).join('');
}

function describeOrigins(origins) {
    if (!origins || origins.length === 0) return 'Accepts feedback from any website';
    return `Accepts feedback from ${origins.map(escapeHtml).join(', ')}`;
}

function parseOrigins(text) {
    return text.split(/[\n,]/).map(line => line.trim()).filter(Boolean);
}

async function createProject(e) {
    e.preventDefault();
    const button = $('createProjectBtn');
    button.disabled = true;
    $('projectsError').classList.add('hidden');
    try {
        const data = await apiFetch('/projects', {
            method: 'POST',
            body: JSON.stringify({
                name: $('newProjectName').value,
                allowedOrigins: parseOrigins($('newProjectOrigins').value),
                notifyOwner: $('newProjectNotify').checked
            })
        });
        $('createProjectForm').reset();
        showNewKey($('newProjectResult'), data.adminKey, data.project);
        await showProjects();
    } catch (err) {
        if (err instanceof UnauthorizedError) return handleUnauthorized();
        $('projectsError').textContent = `Could not create project: ${err.message}`;
        $('projectsError').classList.remove('hidden');
    } finally {
        button.disabled = false;
    }
}

// Shown right after creating a project or rotating a key: the only time the key is visible
function showNewKey(container, adminKey, project) {
    container.innerHTML = `
        <h3>${project ? `Project "${escapeHtml(project.name)}" created` : 'New admin key created'}</h3>
        <p><strong>Copy this admin key now. It won't be shown again.</strong>
            ${project ? 'Send it to the customer so they can sign in to this dashboard.' : 'The old key no longer works.'}</p>
        <pre><code>${escapeHtml(adminKey)}</code></pre>
        <button class="secondary small copy-btn" data-copy="${escapeHtml(adminKey)}">Copy key</button>
        ${project ? `
            <p>Widget snippet for their site:</p>
            <pre><code>${escapeHtml(embedSnippet(project.projectId))}</code></pre>
            <button class="secondary small copy-btn" data-copy="${escapeHtml(embedSnippet(project.projectId))}">Copy snippet</button>
        ` : ''}
    `;
    container.classList.remove('hidden');
}

// ---- One project's dashboard ----

async function openProject(projectId) {
    const data = await apiFetch(projectPath(projectId));
    currentProject = data.project;
    allFeedbacks = [];
    screenshotUrls.clear();
    openContexts.clear();
    $('feedbackGrid').innerHTML = '';
    if (isOwner()) history.replaceState(null, '', `#${encodeURIComponent(projectId)}`);

    showOnly('dashboard');
    $('backToProjectsBtn').classList.toggle('hidden', !isOwner());
    document.querySelectorAll('.owner-only').forEach(el => el.classList.toggle('hidden', !isOwner()));
    renderProjectInfo();
    $('rotateResult').classList.add('hidden');
    $('settingsStatus').textContent = '';

    loadFeedbacks();
    // Auto-refresh every 30 seconds
    clearInterval(refreshTimer);
    refreshTimer = setInterval(loadFeedbacks, 30000);
}

function renderProjectInfo() {
    const project = currentProject;
    $('projectName').textContent = project.name;
    $('projectIdLabel').textContent = project.projectId;
    document.title = `${project.name} · Feedback Dashboard`;

    $('embedSnippet').textContent = embedSnippet(project.projectId);
    // Secure sites refuse http:// scripts; local testing over http is fine
    const isLocal = ['localhost', '127.0.0.1'].includes(location.hostname);
    $('insecureWarning').classList.toggle('hidden', location.protocol === 'https:' || isLocal);
    const hosted = siteUrl(`index.html?project=${encodeURIComponent(project.projectId)}`);
    $('hostedLink').href = hosted;
    $('hostedLink').textContent = hosted;
    $('previewLink').href = siteUrl(`demo.html?project=${encodeURIComponent(project.projectId)}`);

    $('originsInput').value = (project.allowedOrigins || []).join('\n');
    $('notifyInput').checked = Boolean(project.notifyOwner);
}

// The dashboard is served from the same site as widget.js and the hosted form
function siteUrl(path) {
    return new URL(path, location.href).href;
}

function embedSnippet(projectId) {
    return `<script src="${siteUrl('widget.js')}" data-project="${projectId}" data-api="${API_URL}" async></script>`;
}

async function saveSettings() {
    const button = $('saveSettingsBtn');
    button.disabled = true;
    $('settingsStatus').textContent = 'Saving...';
    const settings = { allowedOrigins: parseOrigins($('originsInput').value) };
    if (isOwner()) settings.notifyOwner = $('notifyInput').checked;
    try {
        const data = await apiFetch(projectPath(currentProject.projectId), {
            method: 'PATCH',
            body: JSON.stringify(settings)
        });
        currentProject = data.project;
        renderProjectInfo();
        $('settingsStatus').textContent = 'Saved ✓';
    } catch (err) {
        if (err instanceof UnauthorizedError) return handleUnauthorized();
        $('settingsStatus').textContent = `Not saved: ${err.message}`;
    } finally {
        button.disabled = false;
    }
}

async function rotateKey() {
    const confirmed = confirm(
        'Create a new admin key? The current key stops working immediately, ' +
        'so anyone using it will need the new one.'
    );
    if (!confirmed) return;

    const button = $('rotateKeyBtn');
    button.disabled = true;
    try {
        const data = await apiFetch(`${projectPath(currentProject.projectId)}/rotate-key`, { method: 'POST' });
        // A customer just replaced the key they're signed in with
        if (!isOwner()) setToken(data.adminKey);
        showNewKey($('rotateResult'), data.adminKey);
    } catch (err) {
        if (err instanceof UnauthorizedError) return handleUnauthorized();
        showError(`Could not rotate the key: ${err.message}`);
    } finally {
        button.disabled = false;
    }
}

// ---- Feedback ----

// Older items may lack fields; ratings are optional
function normalize(fb) {
    return {
        ...fb,
        rating: Number(fb.rating) || 0,
        category: fb.category || 'general',
        status: fb.status || 'new',
        timestamp: fb.timestamp || ''
    };
}

async function fetchAllFeedbacks(projectId) {
    const feedbacks = [];
    let nextToken = null;
    do {
        const params = new URLSearchParams({ limit: PAGE_SIZE });
        if (nextToken) params.set('nextToken', nextToken);
        const data = await apiFetch(`${projectPath(projectId)}/feedback?${params}`);
        feedbacks.push(...data.feedbacks);
        nextToken = data.nextToken;
    } while (nextToken);
    // The API returns newest first
    return feedbacks.map(normalize);
}

async function loadFeedbacks() {
    if (isLoading || !currentProject) return;
    isLoading = true;
    const projectId = currentProject.projectId;

    // Only show the spinner on the first load, so auto-refresh doesn't flicker
    $('loading').classList.toggle('hidden', allFeedbacks.length > 0);
    hideError();

    try {
        const feedbacks = await fetchAllFeedbacks(projectId);
        // Ignore the result if the owner switched projects meanwhile
        if (currentProject && currentProject.projectId === projectId) {
            reuseScreenshotUrls(feedbacks);
            allFeedbacks = feedbacks;
            render();
        }
    } catch (err) {
        if (err instanceof UnauthorizedError) {
            handleUnauthorized();
        } else {
            showError(`Error loading feedbacks: ${err.message}`);
            console.error('Error loading feedbacks:', err);
        }
    } finally {
        $('loading').classList.add('hidden');
        isLoading = false;
    }
}

function reuseScreenshotUrls(feedbacks) {
    const now = Date.now();
    feedbacks.forEach(fb => {
        if (!fb.screenshotUrl) return;
        const cached = screenshotUrls.get(fb.feedbackId);
        if (cached && now - cached.at < SCREENSHOT_URL_REUSE_MS) {
            fb.screenshotUrl = cached.url;
        } else {
            screenshotUrls.set(fb.feedbackId, { url: fb.screenshotUrl, at: now });
        }
    });
}

async function setStatus(feedbackId, status, button) {
    button.disabled = true;
    try {
        const data = await apiFetch(
            `${projectPath(currentProject.projectId)}/feedback/${encodeURIComponent(feedbackId)}`,
            { method: 'PATCH', body: JSON.stringify({ status }) }
        );
        const updated = normalize(data.feedback);
        // The update response has no view link; keep the one the card already shows
        allFeedbacks = allFeedbacks.map(fb => fb.feedbackId === feedbackId
            ? { ...updated, screenshotUrl: fb.screenshotUrl } : fb);
        hideError();
        render();
    } catch (err) {
        if (err instanceof UnauthorizedError) {
            handleUnauthorized();
        } else {
            showError(`Could not update feedback: ${err.message}`);
            button.disabled = false;
        }
    }
}

// ---- Rendering ----

function getFilters() {
    return {
        search: $('searchInput').value.trim().toLowerCase(),
        rating: $('ratingFilter').value,
        category: $('categoryFilter').value,
        status: $('statusFilter').value
    };
}

function matchesRating(fb, rating) {
    if (!rating) return true;
    if (rating === 'none') return fb.rating === 0;
    return fb.rating === Number(rating);
}

function applyFilters(feedbacks) {
    const f = getFilters();
    return feedbacks.filter(fb =>
        matchesRating(fb, f.rating) &&
        (!f.category || fb.category === f.category) &&
        (!f.status || fb.status === f.status) &&
        (!f.search || [fb.name, fb.email, fb.message, fb.pageUrl]
            .some(text => String(text || '').toLowerCase().includes(f.search)))
    );
}

function render() {
    const filtered = applyFilters(allFeedbacks);
    updateStats(allFeedbacks);
    renderBreakdown(allFeedbacks);
    displayFeedbacks(filtered);
    $('resultCount').textContent = `Showing ${filtered.length} of ${allFeedbacks.length} feedbacks`;
}

function updateStats(feedbacks) {
    $('totalCount').textContent = feedbacks.length;
    $('unresolvedCount').textContent = feedbacks.filter(fb => fb.status !== 'resolved').length;

    const rated = feedbacks.filter(fb => fb.rating > 0);
    $('avgRating').textContent = rated.length
        ? (rated.reduce((sum, fb) => sum + fb.rating, 0) / rated.length).toFixed(1)
        : '–';
}

function renderBreakdown(feedbacks) {
    const counts = [0, 0, 0, 0, 0, 0];
    feedbacks.forEach(fb => { if (fb.rating >= 1 && fb.rating <= 5) counts[fb.rating]++; });
    const max = Math.max(1, ...counts);
    const activeRating = $('ratingFilter').value;

    // Each row is a button that filters by that rating (click again to clear)
    $('ratingBreakdown').innerHTML = [5, 4, 3, 2, 1].map(rating => `
        <button class="breakdown-row ${String(rating) === activeRating ? 'active' : ''}" data-rating="${rating}"
                aria-label="Show ${rating}-star feedback (${counts[rating]})">
            <span>${rating} ★</span>
            <span class="bar-track"><span class="bar-fill" style="display:block;width:${(counts[rating] / max) * 100}%"></span></span>
            <span class="bar-count">${counts[rating]}</span>
        </button>
    `).join('');
}

function displayFeedbacks(feedbacks) {
    const grid = $('feedbackGrid');

    if (feedbacks.length === 0) {
        const filtered = allFeedbacks.length > 0;
        grid.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">${filtered ? '🔍' : '📭'}</div>
                <h3>${filtered ? 'No matching feedback' : 'No feedback yet'}</h3>
                <p>${filtered ? 'Try changing the search or filters'
                    : 'Install the widget (see "Install & settings" above) and feedback will appear here'}</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = feedbacks.map(fb => {
        const resolved = fb.status === 'resolved';
        return `
            <div class="feedback-card ${resolved ? 'resolved' : ''}">
                <div class="feedback-header">
                    <div class="feedback-name">${fb.name ? escapeHtml(fb.name) : '<span class="muted">Anonymous</span>'}</div>
                    <div class="feedback-rating">${fb.rating ? `${fb.rating}/5` : 'No rating'}</div>
                </div>
                <div class="badges">
                    <span class="badge">${escapeHtml(CATEGORY_LABELS[fb.category] || fb.category)}</span>
                    <span class="badge status-${resolved ? 'resolved' : 'new'}">${resolved ? 'Resolved' : 'Unresolved'}</span>
                </div>
                ${fb.email ? `<div class="feedback-email">${escapeHtml(fb.email)}</div>` : ''}
                ${isSafeUrl(fb.pageUrl) ? `<div class="feedback-page">From page:
                    <a href="${escapeHtml(fb.pageUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(fb.pageUrl)}</a></div>` : ''}
                <div class="feedback-message">${escapeHtml(fb.message)}</div>
                ${isSafeUrl(fb.screenshotUrl) ? `
                    <a class="feedback-shot" href="${escapeHtml(fb.screenshotUrl)}" target="_blank" rel="noopener noreferrer"
                       title="Open the full screenshot">
                        <img src="${escapeHtml(fb.screenshotUrl)}" alt="Screenshot attached to this feedback" loading="lazy">
                    </a>` : ''}
                ${renderContext(fb.context, fb.feedbackId)}
                <div class="feedback-meta">
                    <span>${formatDate(fb.timestamp)}</span>
                    <button class="status-btn ${resolved ? 'secondary' : ''}" data-id="${escapeHtml(fb.feedbackId)}"
                            data-status="${resolved ? 'new' : 'resolved'}">
                        ${resolved ? 'Reopen' : 'Mark resolved'}
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// A short, readable browser and OS name from the user agent, e.g. "Chrome 130 on Windows"
function describeBrowser(userAgent) {
    if (!userAgent) return '';
    const browsers = [
        [/Edg(?:e|A|iOS)?\/(\d+)/, 'Edge'],
        [/OPR\/(\d+)/, 'Opera'],
        [/SamsungBrowser\/(\d+)/, 'Samsung Internet'],
        [/(?:Firefox|FxiOS)\/(\d+)/, 'Firefox'],
        [/(?:Chrome|CriOS)\/(\d+)/, 'Chrome'],
        [/Version\/(\d+)[\d.]*.*Safari/, 'Safari']
    ];
    const systems = [
        [/Windows/, 'Windows'], [/iPhone|iPad|iPod/, 'iOS'], [/Android/, 'Android'],
        [/Mac OS X|Macintosh/, 'macOS'], [/CrOS/, 'ChromeOS'], [/Linux/, 'Linux']
    ];
    const browser = browsers.find(([pattern]) => pattern.test(userAgent));
    const system = systems.find(([pattern]) => pattern.test(userAgent));
    const name = browser ? `${browser[1]} ${userAgent.match(browser[0])[1]}` : 'Unknown browser';
    return system ? `${name} on ${system[1]}` : name;
}

function renderContext(context, feedbackId) {
    if (!context) return '';
    const browser = describeBrowser(context.userAgent);
    const errors = context.errors || [];
    const rows = [
        ['Browser', browser],
        ['Screen', context.screen],
        ['Window', context.viewport],
        ['Language', context.language],
        ['Time zone', context.timezone]
    ].filter(([, value]) => value);
    const summary = [browser, context.viewport && `window ${context.viewport}`,
        errors.length && `${errors.length} page error${errors.length === 1 ? '' : 's'}`].filter(Boolean).join(' \u00b7 ');

    return `
        <details class="feedback-context" data-id="${escapeHtml(feedbackId)}" ${openContexts.has(feedbackId) ? 'open' : ''}>
            <summary>${escapeHtml(summary || 'Browser details')}</summary>
            <dl>
                ${rows.map(([label, value]) => `<dt>${label}</dt><dd>${escapeHtml(value)}</dd>`).join('')}
                ${context.userAgent ? `<dt>User agent</dt><dd class="small">${escapeHtml(context.userAgent)}</dd>` : ''}
            </dl>
            ${errors.length ? `
                <p class="context-heading">Recent page errors</p>
                <ol class="page-errors">
                    ${errors.map(error => `<li><code>${escapeHtml(error.message)}</code>${error.source
                        ? `<div class="small">${escapeHtml(error.source)}${error.line ? `:${error.line}` : ''}</div>` : ''}</li>`).join('')}
                </ol>` : ''}
        </details>
    `;
}

// The API only accepts http(s) page URLs, but never trust stored data in an href
function isSafeUrl(url) {
    return typeof url === 'string' && /^https?:\/\//i.test(url);
}

function showError(message) {
    $('error').textContent = message;
    $('error').classList.remove('hidden');
}

function hideError() {
    $('error').classList.add('hidden');
}

// ---- CSV export ----

function exportCSV() {
    // Exports what's currently shown, so filters apply
    const feedbacks = applyFilters(allFeedbacks);
    if (feedbacks.length === 0) {
        alert('No feedbacks to export');
        return;
    }

    const headers = ['Name', 'Email', 'Rating', 'Category', 'Status', 'Message', 'Page URL', 'Browser', 'Screen',
        'Window', 'Page errors', 'Screenshot', 'Timestamp', 'Feedback ID'];
    const rows = feedbacks.map(fb => {
        const context = fb.context || {};
        return [
            fb.name,
            fb.email,
            fb.rating || '',
            CATEGORY_LABELS[fb.category] || fb.category,
            fb.status,
            fb.message,
            fb.pageUrl,
            describeBrowser(context.userAgent),
            context.screen,
            context.viewport,
            (context.errors || []).map(error => error.message).join(' | '),
            // View links expire, so the CSV only says whether there is one
            fb.screenshotKey ? 'yes (view in dashboard)' : '',
            fb.timestamp,
            fb.feedbackId
        ];
    });

    let csv = headers.join(',') + '\n';
    rows.forEach(row => {
        csv += row.map(csvField).join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const slug = currentProject.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'feedback';
    a.download = `${slug}_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
}

function csvField(value) {
    let text = String(value ?? '');
    // Stop spreadsheets from running submitted text as a formula
    if (/^[=+\-@\t\r]/.test(text)) {
        text = "'" + text;
    }
    // Double any quotes, then wrap the field in quotes
    return `"${text.replace(/"/g, '""')}"`;
}

// ---- Helpers ----

function formatDate(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    // textContent doesn't escape quotes, which matters inside attributes
    return div.innerHTML.replace(/"/g, '&quot;');
}

async function copyText(text, button) {
    try {
        await navigator.clipboard.writeText(text);
        const original = button.textContent;
        button.textContent = 'Copied ✓';
        setTimeout(() => { button.textContent = original; }, 1500);
    } catch {
        prompt('Copy this:', text);
    }
}

// ---- Event wiring ----

$('loginForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const token = $('tokenInput').value.trim();
    $('tokenInput').value = '';
    if (token.startsWith(ADMIN_KEY_PREFIX) && !projectIdFromKey(token)) {
        showLogin('That admin key looks incomplete. Please paste the whole key.');
        return;
    }
    setToken(token);
    start();
});

document.querySelectorAll('.sign-out-btn').forEach(button => button.addEventListener('click', signOut));
$('backToProjectsBtn').addEventListener('click', () => {
    showProjects().catch(err => {
        if (err instanceof UnauthorizedError) handleUnauthorized();
        else showError(err.message);
    });
});
$('createProjectForm').addEventListener('submit', createProject);
$('projectList').addEventListener('click', (e) => {
    const button = e.target.closest('.open-project-btn');
    if (!button) return;
    openProject(button.dataset.id).catch(err => {
        if (err instanceof UnauthorizedError) return handleUnauthorized();
        $('projectsError').textContent = `Could not open project: ${err.message}`;
        $('projectsError').classList.remove('hidden');
    });
});

$('saveSettingsBtn').addEventListener('click', saveSettings);
$('rotateKeyBtn').addEventListener('click', rotateKey);
$('copySnippetBtn').addEventListener('click', (e) => copyText($('embedSnippet').textContent, e.target));
document.addEventListener('click', (e) => {
    const button = e.target.closest('.copy-btn');
    if (button) copyText(button.dataset.copy, button);
});

$('refreshBtn').addEventListener('click', loadFeedbacks);
$('exportBtn').addEventListener('click', exportCSV);

$('searchInput').addEventListener('input', render);
['ratingFilter', 'categoryFilter', 'statusFilter'].forEach(id => {
    $(id).addEventListener('change', render);
});

$('ratingBreakdown').addEventListener('click', (e) => {
    const row = e.target.closest('.breakdown-row');
    if (!row) return;
    const ratingFilter = $('ratingFilter');
    ratingFilter.value = ratingFilter.value === row.dataset.rating ? '' : row.dataset.rating;
    render();
});

// 'toggle' doesn't bubble, so listen in the capture phase
$('feedbackGrid').addEventListener('toggle', (e) => {
    const details = e.target;
    if (!details.classList || !details.classList.contains('feedback-context')) return;
    if (details.open) openContexts.add(details.dataset.id);
    else openContexts.delete(details.dataset.id);
}, true);

$('feedbackGrid').addEventListener('click', (e) => {
    const button = e.target.closest('.status-btn');
    if (button) setStatus(button.dataset.id, button.dataset.status, button);
});

// Start where the user left off if already signed in this session
if (getToken()) {
    start();
} else {
    showLogin();
}

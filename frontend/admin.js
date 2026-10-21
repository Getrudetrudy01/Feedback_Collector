// Configured in config.js
const API_URL = window.APP_CONFIG.apiUrl;
const TOKEN_KEY = 'feedbackAdminToken';
const PAGE_SIZE = 500;

const CATEGORY_LABELS = {
    general: 'General',
    bug: 'Bug report',
    feature: 'Feature request',
    praise: 'Praise',
    other: 'Other'
};

let allFeedbacks = [];
let refreshTimer = null;
let isLoading = false;

class UnauthorizedError extends Error {}

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

function showLogin(message) {
    clearInterval(refreshTimer);
    document.getElementById('dashboard').classList.add('hidden');
    document.getElementById('loginForm').classList.remove('hidden');
    const loginError = document.getElementById('loginError');
    loginError.textContent = message || '';
    loginError.classList.toggle('hidden', !message);
    document.getElementById('tokenInput').focus();
}

function showDashboard() {
    document.getElementById('loginForm').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    loadFeedbacks();
    // Auto-refresh every 30 seconds
    clearInterval(refreshTimer);
    refreshTimer = setInterval(loadFeedbacks, 30000);
}

function signOut() {
    setToken(null);
    allFeedbacks = [];
    document.getElementById('feedbackGrid').innerHTML = '';
    showLogin();
}

function handleUnauthorized() {
    setToken(null);
    allFeedbacks = [];
    showLogin('That token was not accepted. Please sign in again.');
}

// ---- API ----

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: { ...options.headers, 'Authorization': `Bearer ${getToken()}` }
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

// Older items may lack category/status or store the rating as text
function normalize(fb) {
    return {
        ...fb,
        rating: Number(fb.rating) || 0,
        category: fb.category || 'general',
        status: fb.status || 'new',
        timestamp: fb.timestamp || ''
    };
}

async function fetchAllFeedbacks() {
    const feedbacks = [];
    let nextToken = null;
    do {
        const url = new URL(API_URL);
        url.searchParams.set('limit', PAGE_SIZE);
        if (nextToken) url.searchParams.set('nextToken', nextToken);
        const data = await apiFetch(url);
        feedbacks.push(...data.feedbacks);
        nextToken = data.nextToken;
    } while (nextToken);

    // Newest first
    return feedbacks.map(normalize).sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}

async function loadFeedbacks() {
    if (isLoading) return;
    isLoading = true;

    const loading = document.getElementById('loading');
    // Only show the spinner on the first load, so auto-refresh doesn't flicker
    loading.classList.toggle('hidden', allFeedbacks.length > 0);
    hideError();

    try {
        allFeedbacks = await fetchAllFeedbacks();
        render();
    } catch (err) {
        if (err instanceof UnauthorizedError) {
            handleUnauthorized();
        } else {
            showError(`Error loading feedbacks: ${err.message}`);
            console.error('Error loading feedbacks:', err);
        }
    } finally {
        loading.classList.add('hidden');
        isLoading = false;
    }
}

async function setStatus(feedbackId, status, button) {
    button.disabled = true;
    try {
        const data = await apiFetch(`${API_URL}/${encodeURIComponent(feedbackId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        const updated = normalize(data.feedback);
        allFeedbacks = allFeedbacks.map(fb => fb.feedbackId === feedbackId ? updated : fb);
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
        search: document.getElementById('searchInput').value.trim().toLowerCase(),
        rating: document.getElementById('ratingFilter').value,
        category: document.getElementById('categoryFilter').value,
        status: document.getElementById('statusFilter').value
    };
}

function applyFilters(feedbacks) {
    const f = getFilters();
    return feedbacks.filter(fb =>
        (!f.rating || fb.rating === Number(f.rating)) &&
        (!f.category || fb.category === f.category) &&
        (!f.status || fb.status === f.status) &&
        (!f.search || [fb.name, fb.email, fb.message].some(text => String(text || '').toLowerCase().includes(f.search)))
    );
}

function render() {
    const filtered = applyFilters(allFeedbacks);
    updateStats(allFeedbacks);
    renderBreakdown(allFeedbacks);
    displayFeedbacks(filtered);
    document.getElementById('resultCount').textContent =
        `Showing ${filtered.length} of ${allFeedbacks.length} feedbacks`;
}

function updateStats(feedbacks) {
    document.getElementById('totalCount').textContent = feedbacks.length;
    document.getElementById('unresolvedCount').textContent = feedbacks.filter(fb => fb.status !== 'resolved').length;

    const rated = feedbacks.filter(fb => fb.rating > 0);
    const avgRating = rated.length ? rated.reduce((sum, fb) => sum + fb.rating, 0) / rated.length : 0;
    document.getElementById('avgRating').textContent = avgRating.toFixed(1);
}

function renderBreakdown(feedbacks) {
    const counts = [0, 0, 0, 0, 0, 0];
    feedbacks.forEach(fb => { if (fb.rating >= 1 && fb.rating <= 5) counts[fb.rating]++; });
    const max = Math.max(1, ...counts);
    const activeRating = document.getElementById('ratingFilter').value;

    // Each row is a button that filters by that rating (click again to clear)
    document.getElementById('ratingBreakdown').innerHTML = [5, 4, 3, 2, 1].map(rating => `
        <button class="breakdown-row ${String(rating) === activeRating ? 'active' : ''}" data-rating="${rating}"
                aria-label="Show ${rating}-star feedback (${counts[rating]})">
            <span>${rating} ★</span>
            <span class="bar-track"><span class="bar-fill" style="display:block;width:${(counts[rating] / max) * 100}%"></span></span>
            <span class="bar-count">${counts[rating]}</span>
        </button>
    `).join('');
}

function displayFeedbacks(feedbacks) {
    const grid = document.getElementById('feedbackGrid');

    if (feedbacks.length === 0) {
        const filtered = allFeedbacks.length > 0;
        grid.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">${filtered ? '🔍' : '📭'}</div>
                <h3>${filtered ? 'No matching feedback' : 'No feedback yet'}</h3>
                <p>${filtered ? 'Try changing the search or filters' : 'Feedbacks will appear here once submitted'}</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = feedbacks.map(fb => {
        const resolved = fb.status === 'resolved';
        return `
            <div class="feedback-card ${resolved ? 'resolved' : ''}">
                <div class="feedback-header">
                    <div class="feedback-name">${escapeHtml(fb.name)}</div>
                    <div class="feedback-rating">${fb.rating}/5</div>
                </div>
                <div class="badges">
                    <span class="badge">${escapeHtml(CATEGORY_LABELS[fb.category] || fb.category)}</span>
                    <span class="badge status-${resolved ? 'resolved' : 'new'}">${resolved ? 'Resolved' : 'Unresolved'}</span>
                </div>
                <div class="feedback-email">${escapeHtml(fb.email)}</div>
                <div class="feedback-message">${escapeHtml(fb.message)}</div>
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

function showError(message) {
    const error = document.getElementById('error');
    error.textContent = message;
    error.classList.remove('hidden');
}

function hideError() {
    document.getElementById('error').classList.add('hidden');
}

// ---- CSV export ----

function exportCSV() {
    // Exports what's currently shown, so filters apply
    const feedbacks = applyFilters(allFeedbacks);
    if (feedbacks.length === 0) {
        alert('No feedbacks to export');
        return;
    }

    const headers = ['Name', 'Email', 'Rating', 'Category', 'Status', 'Message', 'Timestamp', 'Feedback ID'];
    const rows = feedbacks.map(fb => [
        fb.name,
        fb.email,
        fb.rating,
        CATEGORY_LABELS[fb.category] || fb.category,
        fb.status,
        fb.message,
        fb.timestamp,
        fb.feedbackId
    ]);

    let csv = headers.join(',') + '\n';
    rows.forEach(row => {
        csv += row.map(csvField).join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `feedbacks_${new Date().toISOString().split('T')[0]}.csv`;
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

// ---- Event wiring ----

document.getElementById('loginForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = document.getElementById('tokenInput');
    setToken(input.value.trim());
    input.value = '';
    showDashboard();
});

document.getElementById('refreshBtn').addEventListener('click', loadFeedbacks);
document.getElementById('exportBtn').addEventListener('click', exportCSV);
document.getElementById('signOutBtn').addEventListener('click', signOut);

document.getElementById('searchInput').addEventListener('input', render);
['ratingFilter', 'categoryFilter', 'statusFilter'].forEach(id => {
    document.getElementById(id).addEventListener('change', render);
});

document.getElementById('ratingBreakdown').addEventListener('click', (e) => {
    const row = e.target.closest('.breakdown-row');
    if (!row) return;
    const ratingFilter = document.getElementById('ratingFilter');
    ratingFilter.value = ratingFilter.value === row.dataset.rating ? '' : row.dataset.rating;
    render();
});

document.getElementById('feedbackGrid').addEventListener('click', (e) => {
    const button = e.target.closest('.status-btn');
    if (button) setStatus(button.dataset.id, button.dataset.status, button);
});

// Start on the dashboard if already signed in this session
if (getToken()) {
    showDashboard();
} else {
    showLogin();
}

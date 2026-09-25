
const $ = (id) => document.getElementById(id);

let currentProfile = null;
let resumeAttachmentId = localStorage.getItem("jobAgentResumeAttachmentId");
let selectedJob = null;
let currentPreferences = {};
let searchSessionId = null;
let trackerEntries = [];
let searchBusy = false;
let resumeBusy = false;
let pendingDraftId = null;
let pendingTurn = null;
let pendingResumeTurn = null;
let applicationsByJob = new Map();
const jobsById = new Map();
const conversation = [];
const applicationStatuses = ['DISCOVERED', 'SAVED', 'APPLIED', 'OUTREACH_PREPARED', 'OUTREACH_SENT', 'INTERVIEW', 'REJECTED', 'OFFER', 'SKIPPED'];
const dialogReturnFocus = new WeakMap();
let jobDrawerReturnFocus = null;
let jobDrawerReturnJobId = null;
let jobDrawerReturnScope = '';

function showModalWithFocusReturn(dialog) {
  const opener = document.activeElement;
  if (opener && typeof opener.focus === 'function') dialogReturnFocus.set(dialog, opener);
  dialog.showModal();
}

function restoreDialogFocus(dialog) {
  const opener = dialogReturnFocus.get(dialog);
  dialogReturnFocus.delete(dialog);
  if (opener && typeof opener.focus === 'function') opener.focus();
}

$("emailDialog").addEventListener("close", () => restoreDialogFocus($("emailDialog")));
$("draftDialog").addEventListener("close", () => restoreDialogFocus($("draftDialog")));

function uniqueId(prefix) {
  const value = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

function newChatThread() {
  const value = uniqueId('career');
  sessionStorage.setItem('jobAgentChatThreadId', value);
  return value;
}

let chatThreadId = sessionStorage.getItem('jobAgentChatThreadId') || newChatThread();

const titles = {
  search: "Career Agent",
  results: "Recommendations",
  profile: "Profile",
  tracker: "Tracker",
  email: "Outreach",
  settings: "Connections",
  preferences: "Settings"
};

async function api(path, options = {}) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const detail = data?.detail || data?.message || `Request failed (${res.status})`;
    throw new Error(typeof detail === 'string' ? detail : 'Please check the entered values and try again.');
  }
  return data;
}

function showStatus(el, text, type = "") {
  el.textContent = text;
  el.className = `status ${type}`.trim();
}

function hideStatus(el) {
  el.className = "status hidden";
}

function setView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.querySelectorAll(".nav").forEach(v => v.classList.remove("active"));

  $(`${name}View`).classList.add("active");
  document.querySelector(`.nav[data-view="${name}"]`)?.classList.add("active");
  document.querySelectorAll('.nav').forEach(button => {
    if (button.dataset.view === name) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $("pageTitle").textContent = titles[name];
  $('recentConversation').classList.toggle('active', name === 'search');
  closeSidebar();

  if (name === "email") loadDrafts();
  if (name === "settings") refreshConnections();
  if (name === "profile") loadProfile();
  if (name === "tracker") loadTracker();
  if (name === "preferences") loadPreferences();
}

document.querySelectorAll(".nav").forEach(btn => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});

$("refreshBtn").addEventListener("click", async () => {
  await Promise.all([loadProfile(), refreshConnections()]);
  if ($("emailView").classList.contains("active")) await loadDrafts();
  if ($("trackerView").classList.contains("active")) await loadTracker();
  if ($("preferencesView").classList.contains("active")) await loadPreferences();
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parseDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}/.test(value)) return null;
  // Some providers send 7 fractional digits (Jooble); JavaScript accepts at most 3.
  const date = new Date(value.replace(/(\.\d{3})\d+/, '$1'));
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseDate(value);
  return date ? `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}` : String(value ?? '');
}

function postedLabel(value) {
  const date = parseDate(value);
  if (!date) return String(value ?? '');
  const days = Math.floor((Date.now() - date.getTime()) / 86400000);
  if (days < 0 || days > 30) return `Posted ${formatDate(value)}`;
  const relative = days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
  return `Posted ${relative} · ${formatDate(value)}`;
}

function safeExternalUrl(value) {
  if (typeof value !== 'string' || /[\u0000-\u0020\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (!host.includes('.') || host.includes(':') || /(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(host)) return null;
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      const [a, b] = host.split('.').map(Number);
      if (a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127)) return null;
    }
    return url.href;
  } catch { return null; }
}

const jsonOptions = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const commaList = value => String(value || '').split(',').map(item => item.trim()).filter(Boolean);
const textList = values => (Array.isArray(values) ? values : []).map(value => `<li>${escapeHtml(value)}</li>`).join('');
const tags = (values, style = '') => (Array.isArray(values) ? values : []).map(value => `<span class="tag ${style}">${escapeHtml(value)}</span>`).join('');
function readObject(id) {
  const value = JSON.parse($(id).value || '{}');
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Advanced fields must be a JSON object.');
  return value;
}

async function loadProfile() {
  try {
    currentProfile = await api("/profile/current");
    renderProfile(currentProfile);
  } catch (err) {
    $("profileCard").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

function renderProfile(profile) {
  const skillTags = (profile.skills || []).map(s => `<span class="tag">${escapeHtml(s)}</span>`).join("");
  const roles = (profile.preferred_roles || []).map(s => `<span class="tag">${escapeHtml(s)}</span>`).join("");
  const locations = (profile.preferred_locations || []).map(s => `<span class="tag">${escapeHtml(s)}</span>`).join("");

  $("profileCard").innerHTML = `
    <h3>${escapeHtml(profile.name || "Candidate")}</h3>
    <p class="muted">${escapeHtml(profile.degree || "")} · Graduation ${escapeHtml(profile.graduation_year || "")}</p>
    <div class="profile-grid">
      <div class="profile-block">
        <strong>Skills</strong>
        <div class="skill-row">${skillTags || '<span class="muted">No skills parsed.</span>'}</div>
      </div>
      <div class="profile-block">
        <strong>Target roles</strong>
        <div class="skill-row">${roles}</div>
      </div>
      <div class="profile-block">
        <strong>Preferred locations</strong>
        <div class="skill-row">${locations}</div>
      </div>
      <div class="profile-block">
        <strong>Projects / research</strong>
        <div class="muted">${escapeHtml([...(profile.projects || []), ...(profile.research || [])].join(" · "))}</div>
      </div>
    </div>
  `;
  const warnings = profile.parsing_warnings || [];
  const categories = Object.entries(profile.skill_categories || {}).map(([name, values]) => `<div class="profile-block"><strong>${escapeHtml(name)}</strong><div class="skill-row">${tags(values)}</div></div>`).join('');
  const sections = ['education', 'experience', 'internships', 'certifications', 'domain_knowledge'].filter(key => profile[key]?.length).map(key => `<div class="profile-block"><strong>${escapeHtml(key.replaceAll('_', ' '))}</strong><ul>${textList(profile[key])}</ul></div>`).join('');
  $("profileCard").innerHTML += `${warnings.length ? `<div class="status"><strong>Please review</strong><ul>${textList(warnings)}</ul></div>` : ''}<div class="profile-grid section-gap">${categories}${sections}</div><details class="section-gap"><summary>Supporting evidence</summary><pre class="preview-box">${escapeHtml(JSON.stringify(profile.evidence || {}, null, 2))}</pre></details>`;
  $('profileName').value = profile.name || '';
  $('profileDegree').value = profile.degree || '';
  $('profileYear').value = profile.graduation_year ?? '';
  $('profileExperience').value = profile.experience_years ?? '';
  $('profileSkills').value = (profile.skills || []).join(', ');
  $('profileRoles').value = (profile.preferred_roles || []).join(', ');
  $('profileLocations').value = (profile.preferred_locations || []).join(', ');
  $('profileRelocation').value = profile.relocation_preference == null ? '' : String(profile.relocation_preference);
  const additional = { ...profile };
  ['schema_version', 'name', 'degree', 'graduation_year', 'experience_years', 'skills', 'preferred_roles', 'preferred_locations', 'relocation_preference'].forEach(key => delete additional[key]);
  $('profileAdvanced').value = JSON.stringify(additional, null, 2);
}

$('profileForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('saveProfileBtn').disabled = true;
  try {
    const profile = { ...currentProfile, ...readObject('profileAdvanced'),
      name: $('profileName').value.trim() || null, degree: $('profileDegree').value.trim() || null,
      graduation_year: $('profileYear').value === '' ? null : Number($('profileYear').value),
      experience_years: $('profileExperience').value === '' ? null : Number($('profileExperience').value),
      skills: commaList($('profileSkills').value), preferred_roles: commaList($('profileRoles').value),
      preferred_locations: commaList($('profileLocations').value),
      relocation_preference: $('profileRelocation').value === '' ? null : $('profileRelocation').value === 'true'
    };
    delete profile.schema_version;
    currentProfile = await api('/profile/current', jsonOptions('PUT', profile));
    renderProfile(currentProfile);
    showStatus($('profileEditStatus'), 'Profile saved. Future searches will use these details.', 'success');
  } catch (error) { showStatus($('profileEditStatus'), error.message, 'error'); }
  finally { $('saveProfileBtn').disabled = false; }
});

async function loadPreferences() {
  try {
    currentPreferences = await api('/preferences/current');
    const prefs = currentPreferences;
    for (const mode of ['remote', 'hybrid', 'onsite']) $('preference' + mode[0].toUpperCase() + mode.slice(1)).checked = (prefs.allowed_work_modes || []).includes(mode);
    $('preferenceLocations').value = (prefs.preferred_locations || []).join(', ');
    $('preferenceRoles').value = (prefs.preferred_role_families || []).join(', ');
    $('preferenceExperience').value = prefs.max_required_experience_years ?? 1;
    $('preferenceScore').value = prefs.minimum_match_score ?? 0;
    $('preferenceYear').value = prefs.required_graduation_year ?? '';
    $('preferenceQueries').value = prefs.search_query_limit ?? 4;
    $('preferenceStrict').checked = Boolean(prefs.require_active_application);
    $('strictSearch').checked = Boolean(prefs.require_active_application);
    $('preferenceFresher').checked = Boolean(prefs.require_fresher_or_recent_grad_language);
    $('preferenceOfficial').checked = Boolean(prefs.prefer_official_application_url);
    const extra = { ...prefs };
    ['allowed_work_modes', 'preferred_locations', 'preferred_role_families', 'max_required_experience_years', 'minimum_match_score', 'required_graduation_year', 'search_query_limit', 'require_active_application', 'require_fresher_or_recent_grad_language', 'prefer_official_application_url'].forEach(key => delete extra[key]);
    $('preferenceAdvanced').value = JSON.stringify(extra, null, 2);
  } catch (error) { showStatus($('preferencesStatus'), error.message, 'error'); }
}

$('preferencesForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('savePreferencesBtn').disabled = true;
  try {
    const allowed = ['remote', 'hybrid', 'onsite'].filter(mode => $('preference' + mode[0].toUpperCase() + mode.slice(1)).checked);
    if (!allowed.length) throw new Error('Select at least one work arrangement.');
    const preferences = { ...currentPreferences, ...readObject('preferenceAdvanced'), allowed_work_modes: allowed,
      preferred_locations: commaList($('preferenceLocations').value), preferred_role_families: commaList($('preferenceRoles').value),
      max_required_experience_years: Number($('preferenceExperience').value), minimum_match_score: Number($('preferenceScore').value),
      required_graduation_year: $('preferenceYear').value === '' ? null : Number($('preferenceYear').value),
      search_query_limit: Number($('preferenceQueries').value), require_active_application: $('preferenceStrict').checked,
      require_fresher_or_recent_grad_language: $('preferenceFresher').checked, prefer_official_application_url: $('preferenceOfficial').checked
    };
    currentPreferences = await api('/preferences/current', jsonOptions('PUT', preferences));
    $('strictSearch').checked = preferences.require_active_application;
    showStatus($('preferencesStatus'), 'Search preferences saved.', 'success');
  } catch (error) { showStatus($('preferencesStatus'), error.message, 'error'); }
  finally { $('savePreferencesBtn').disabled = false; }
});

$("cvInput").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;

  showStatus($("cvStatus"), `Uploading and parsing ${file.name}…`);
  const form = new FormData();
  form.append("file", file);

  try {
    const data = await api("/cv/upload", { method: "POST", body: form });
    currentProfile = data.profile;
    resumeAttachmentId = data.attachment.id;
    localStorage.setItem("jobAgentResumeAttachmentId", resumeAttachmentId);
    renderProfile(currentProfile);
    showStatus(
      $("cvStatus"),
      `CV updated. ${data.attachment.original_name} is also ready to attach to approved emails.`,
      "success"
    );
  } catch (err) {
    showStatus($("cvStatus"), err.message, "error");
  }
});

function renderSearchReport(data) {
  const intent = data.intent || {};
  const fields = [
    ['Roles', intent.roles_requested?.join(', ') || intent.role_families?.join(', ') || 'Discover roles from your profile'],
    ['Locations', intent.locations?.join(', ') || 'No location restriction'],
    ['Work arrangements', ['remote', 'hybrid', 'onsite'].filter(mode => intent[mode + '_allowed'] !== false).join(', ')],
    ['Maximum experience', intent.experience_max == null ? 'Not specified' : intent.experience_max + ' years'],
    ['Minimum match', String(intent.minimum_match_score ?? 0) + '%'],
    ['Application verification', intent.strict_mode ? 'Verified active links required' : 'Verification status shown for each listing']
  ];
  $('searchUnderstanding').innerHTML = `<dl class="understanding-grid">${fields.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>${intent.warnings?.length ? `<ul class="muted">${textList(intent.warnings)}</ul>` : ''}`;
  $('roleSuggestions').innerHTML = (data.role_suggestions || []).map(role => `<div class="suggestion"><strong>${escapeHtml((role.titles || []).join(', ') || role.family)}</strong><p>${escapeHtml(role.reason)}</p><div class="skill-row">${tags(role.evidence)}</div></div>`).join('');
  $('searchQueries').innerHTML = (data.queries || []).map(query => `<div class="query-item"><strong>${escapeHtml(query.query)}</strong><p>${escapeHtml(query.reason)}</p></div>`).join('') || '<p class="muted">No additional provider queries were needed.</p>';
  $('executionSummary').textContent = typeof data.summary === 'string' ? data.summary : 'Search completed. Review the counts and any provider errors below.';
  const diagnostics = data.diagnostics || {};
  const countFields = [['generated_queries', 'Queries'], ['provider_results', 'Provider results'], ['unique_jobs', 'Unique jobs'], ['already_seen', 'Already seen'], ['active_verified', 'Verified active'], ['likely_active', 'Likely active'], ['unverified', 'Unverified'], ['closed', 'Closed'], ['eligibility_rejected', 'Eligibility exclusions'], ['ranked_results', 'Ranked'], ['final_recommendations', 'Recommendations']];
  const aliases = { generated_queries: (data.queries || []).length, provider_results: diagnostics.fetched, unique_jobs: diagnostics.deduplicated, ranked_results: diagnostics.ranked, final_recommendations: data.result_count };
  $('searchDiagnostics').innerHTML = countFields.map(([key, label]) => `<div class="metric"><strong>${escapeHtml(diagnostics[key] ?? aliases[key] ?? 0)}</strong><span>${label}</span></div>`).join('') +
    `<div class="diagnostic-details"><p>Provider counts: ${escapeHtml(Object.entries(diagnostics.provider_counts || {}).map(([name, count]) => name + ': ' + count).join(' / ') || 'None')}</p><p>Elapsed: ${escapeHtml(diagnostics.latency_ms ?? 0)} ms</p>${diagnostics.errors?.length ? `<ul class="provider-errors">${textList(diagnostics.errors.map(error => typeof error === 'string' ? error : JSON.stringify(error)))}</ul>` : '<p>No provider errors reported.</p>'}</div>`;
  $('searchReport').classList.remove('hidden');
}

function renderConversation() {
  $('conversation').innerHTML = conversation.length
    ? conversation.map(message => {
      if (message.role === 'artifact') {
        return `<section class="job-artifact" aria-label="Stored job recommendations"><div class="artifact-heading"><strong>Recommended jobs</strong><span>${message.jobs.length} ${message.jobs.length === 1 ? 'result' : 'results'}</span></div><div class="artifact-job-list">${jobCardsMarkup(message.jobs, true)}</div></section>`;
      }
      const roleClass = message.role === 'user' ? 'user-message' : message.role === 'status' ? 'status-message' : 'agent-message';
      const label = message.role === 'user' ? 'You' : message.role === 'status' ? 'Action' : 'Career Agent';
      return `<div class="message ${roleClass}"><strong>${label}</strong><p>${escapeHtml(message.text)}</p></div>`;
    }).join('')
    : '';
  const started = conversation.length > 0;
  $('searchView').classList.toggle('conversation-started', started);
  $('welcomeState').setAttribute('aria-hidden', String(started));
  if (started) $('conversation').scrollTop = $('conversation').scrollHeight;
}

function addConversation(role, text) {
  conversation.push({ role, text: String(text || '') });
  if (role === 'user' && $('recentConversation').textContent === 'New conversation') {
    const title = String(text || '').replace(/\s+/g, ' ').trim();
    $('recentConversation').textContent = title.length > 46 ? `${title.slice(0, 45)}…` : title;
  }
  renderConversation();
}

function addRecommendationArtifact(jobs) {
  if (!jobs.length) return;
  conversation.push({ role: 'artifact', jobs });
  renderConversation();
}

async function refreshApplicationIndex() {
  const data = await api('/applications');
  trackerEntries = Array.isArray(data) ? data : data.applications || [];
  applicationsByJob = new Map(trackerEntries.filter(entry => entry.job_id).map(entry => [entry.job_id, entry]));
  return trackerEntries;
}

async function hydrateRecommendations(response) {
  if (!response.career_session_id || !(response.recommendation_ids || []).length) return;
  const stored = await api(`/agent/sessions/${encodeURIComponent(response.career_session_id)}`);
  const search = stored?.response || {};
  const available = new Map((search.results || []).filter(job => job?.id).map(job => [job.id, job]));
  const ordered = [];
  for (const id of response.recommendation_ids) {
    let job = available.get(id);
    if (!job) {
      try { job = await api(`/career/jobs/${encodeURIComponent(id)}`); } catch { job = null; }
    }
    if (job) ordered.push(job);
  }
  await refreshApplicationIndex();
  renderJobs(ordered);
  addRecommendationArtifact(ordered);
  $('recommendationsPane').classList.remove('hidden');
  renderSearchReport(search);
  $('workspaceResultCount').textContent = `${ordered.length} ${ordered.length === 1 ? 'job' : 'jobs'}`;
  $('resultsSummary').innerHTML = `<span><strong>${escapeHtml(ordered.length)}</strong> recommendations</span><span>Provider: ${escapeHtml(search.provider || 'None')}</span>`;
  $('resultsSummary').classList.remove('hidden');
}

async function showPendingConfirmation(draftId) {
  const draft = await api(`/email/drafts/${encodeURIComponent(draftId)}`);
  pendingDraftId = draftId;
  $('confirmationSubject').value = draft.subject || '';
  $('confirmationBody').value = draft.body || '';
  $('sendConfirmationPanel').classList.remove('hidden');
  showStatus($('contextActionStatus'), 'Review the final draft. Confirming will approve and send it once.', 'success');
  if ($('jobDetailDrawer').classList.contains('hidden')) openJobDrawer();
  $('sendConfirmationHeading').focus?.();
}

async function handleChatResponse(response) {
  $('chatReplayStatus').classList.toggle('hidden', !response.replayed);
  addConversation(response.stage === 'completed' ? 'agent' : 'status', response.message);
  if (response.career_session_id) searchSessionId = response.career_session_id;
  if (response.application_id && selectedJob) selectedJob.application_id = response.application_id;
  if ((response.recommendation_ids || []).length) await hydrateRecommendations(response);
  if (response.application_id) {
    await refreshApplicationIndex();
    renderJobs(window.lastJobs || []);
    renderSelectedJobContext();
  }
  if (response.stage === 'needs_clarification') $('searchQuery').focus();
  if (response.stage === 'awaiting_send_confirmation' && response.draft_id) {
    await showPendingConfirmation(response.draft_id);
  }
  if (response.stage === 'cancelled') {
    pendingDraftId = null;
    $('sendConfirmationPanel').classList.add('hidden');
  }
}

// Ask before a search that will spend many provider requests or most of the
// remaining quota. The preview is advisory: if it fails, the request proceeds.
async function confirmSearchCost(message) {
  let preview = null;
  try {
    preview = await api('/agent/search/preview', jsonOptions('POST', {
      message, career_session_id: searchSessionId, strict_mode: $('strictSearch').checked
    }));
  } catch {
    return true;
  }
  if (!preview?.warning || typeof window.confirm !== 'function') return true;
  return window.confirm(`${preview.warning}\n\nRun this search?`);
}

async function runChat(message, extra = {}) {
  const value = String(message || '').trim();
  if (!value || searchBusy) return null;
  const fingerprint = JSON.stringify({ value, extra, searchSessionId, selected: selectedJob?.id || null });
  const retryingSameTurn = Boolean(pendingTurn && pendingTurn.fingerprint === fingerprint);
  if (!retryingSameTurn) {
    pendingTurn = { fingerprint, turnId: uniqueId('turn') };
  }
  searchBusy = true;
  $('searchSubmit').disabled = true;
  $('newSearchBtn').disabled = true;
  $('searchForm').setAttribute('aria-busy', 'true');
  $('searchSubmit').textContent = 'Working…';
  showStatus($('searchStatus'), 'Career Agent is processing this request…');
  if (!retryingSameTurn && !(await confirmSearchCost(value))) {
    pendingTurn = null;
    searchBusy = false;
    $('searchSubmit').disabled = false;
    $('newSearchBtn').disabled = false;
    $('searchForm').removeAttribute('aria-busy');
    $('searchSubmit').textContent = 'Send';
    showStatus($('searchStatus'), 'Search not run. No provider requests were sent.');
    return null;
  }
  if (!retryingSameTurn) addConversation('user', value);
  const application = selectedJob?.id ? applicationsByJob.get(selectedJob.id) : null;
  const payload = {
    thread_id: chatThreadId,
    turn_id: pendingTurn.turnId,
    message: value,
    career_session_id: searchSessionId,
    selected_job_id: selectedJob?.id || null,
    application_id: selectedJob?.application_id || application?.id || null,
    draft_id: pendingDraftId,
    include_seen: $('includeSeen').checked,
    strict_mode: $('strictSearch').checked,
    ...extra
  };
  try {
    const response = await api('/chat/run', jsonOptions('POST', payload));
    pendingTurn = null;
    await handleChatResponse(response);
    $('searchQuery').value = '';
    showStatus($('searchStatus'), response.replayed ? 'This turn was already processed safely.' : 'Request completed.', 'success');
    return response;
  } catch (error) {
    showStatus($('searchStatus'), error.message, 'error');
    return null;
  } finally {
    searchBusy = false;
    $('searchSubmit').disabled = false;
    $('newSearchBtn').disabled = false;
    $('searchForm').removeAttribute('aria-busy');
    $('searchSubmit').textContent = 'Send';
  }
}

$('newSearchBtn').addEventListener('click', () => {
  if (searchBusy) return;
  chatThreadId = newChatThread();
  searchSessionId = null;
  pendingTurn = null;
  pendingDraftId = null;
  selectedJob = null;
  window.lastJobs = [];
  conversation.length = 0;
  renderConversation();
  $('searchQuery').value = '';
  $('recentConversation').textContent = 'New conversation';
  $('searchReport').classList.add('hidden');
  $('recommendationsPane').classList.add('hidden');
  $('workspaceResultCount').textContent = '0 jobs';
  renderJobs([]);
  $('sendConfirmationPanel').classList.add('hidden');
  $('chatReplayStatus').classList.add('hidden');
  renderSelectedJobContext();
  closeJobDrawer();
  setView('search');
  hideStatus($('searchStatus'));
  $('searchQuery').focus();
});

$('recentConversation').addEventListener('click', () => setView('search'));

function openSidebar() {
  $('sidebar').classList.add('open');
  $('sidebarBackdrop').classList.remove('hidden');
  $('mobileMenuBtn').setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
  $('sidebar').classList.remove('open');
  $('sidebarBackdrop').classList.add('hidden');
  $('mobileMenuBtn').setAttribute('aria-expanded', 'false');
}

$('mobileMenuBtn').addEventListener('click', openSidebar);
$('sidebarBackdrop').addEventListener('click', closeSidebar);
$('sidebarToggle').addEventListener('click', () => {
  const collapsed = $('appShell').classList.toggle('sidebar-collapsed');
  $('sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
  $('sidebarToggle').setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (!$('jobDetailDrawer').classList.contains('hidden')) closeJobDrawer();
  closeSidebar();
});

$('searchForm').addEventListener('submit', async event => {
  event.preventDefault();
  const query = $('searchQuery').value.trim();
  await runChat(query);
});

$('searchQuery').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    $('searchForm').requestSubmit();
  }
});

document.querySelectorAll('.suggestion-chip').forEach(button => {
  button.addEventListener('click', async () => {
    $('searchQuery').value = button.dataset.suggestion;
    await runChat(button.dataset.suggestion);
  });
});

// Credit required by provider terms. Adzuna: label each displayed job "Jobs by Adzuna"
// (at least 116 x 23 px) with the words linked to Adzuna.
const PROVIDER_CREDITS = {
  Adzuna: url => `<a href="${url}" target="_blank" rel="noopener noreferrer">Jobs</a> by <a href="${url}" target="_blank" rel="noopener noreferrer">Adzuna</a>`,
  Jooble: url => `Jobs via <a href="${url}" target="_blank" rel="noopener noreferrer">Jooble</a>`,
};
const PROVIDER_SITES = { Adzuna: 'https://www.adzuna.co.uk', Jooble: 'https://in.jooble.org' };

function providerCredit(job) {
  const credit = PROVIDER_CREDITS[job?.source];
  return credit ? `<span class="provider-credit">${credit(PROVIDER_SITES[job.source])}</span>` : '';
}

function jobCardsMarkup(jobs, compact = false) {
  return jobs.map(job => {
    if (job?.id) jobsById.set(job.id, job);
    const match = job.match || {};
    const eligibility = job.eligibility || {};
    const score = Math.max(0, Math.min(100, Number(match.overall_score ?? job.total_score) || 0));
    const url = safeExternalUrl(job.application_url);
    const state = job.verification_state || 'UNVERIFIED';
    const eligible = eligibility.eligible ?? job.eligible;
    const application = applicationsByJob.get(job.id) || (job.application_id ? { id: job.application_id, status: 'SAVED', outreach_state: 'NONE' } : null);
    const trackerState = application?.status || 'NOT SAVED';
    const outreachState = application?.outreach_state || 'NONE';
    const storedId = /^[0-9a-f]{64}$/.test(job.id || '') ? job.id : '';
    return `<article class="job-card ${compact ? 'compact' : ''} ${selectedJob?.id === job.id ? 'selected' : ''}">
      <div class="job-head"><div><h3>${escapeHtml(job.title)}</h3><div class="company">${escapeHtml(job.company)}</div>${providerCredit(job)}</div><div class="score" style="--score:${score}"><span>${Math.round(score)}%</span></div></div>
      <div class="meta">${tags([job.location || 'Location unknown', job.work_mode || 'Work arrangement unknown'])}${job.salary ? tags([job.salary], 'good') : ''}${job.official_application ? '<span class="tag good">Official application</span>' : ''}${job.posted_date ? tags([postedLabel(job.posted_date)]) : ''}</div>
      <div class="card-state-row"><span class="tag ${state === 'ACTIVE_VERIFIED' ? 'good' : 'warn'}">Verification: ${escapeHtml(state.replaceAll('_', ' '))}</span><span class="tag ${eligible === false ? 'bad' : 'good'}">Eligibility: ${eligible === false ? 'Not eligible' : 'No exclusion'}</span><span class="tag">Tracker: ${escapeHtml(trackerState.replaceAll('_', ' '))}</span>${outreachState !== 'NONE' ? `<span class="tag">Outreach: ${escapeHtml(outreachState)}</span>` : ''}</div>
      <div><strong class="muted">Matched skills</strong><div class="skill-row">${tags(match.matched_skills || job.matched_skills, 'good') || '<span class="muted">No explicit skill match</span>'}</div></div>
      ${compact ? '' : `<div class="verification"><p>${escapeHtml(job.verification_reason || 'Application page has not been verified.')}</p></div><p>${escapeHtml(match.explanation || 'Review the listed requirements before applying.')}</p>${match.transferable_skills?.length ? `<div><strong class="muted">Transferable skills</strong><div class="skill-row">${tags(match.transferable_skills)}</div></div>` : ''}${(match.missing_skills || job.missing_skills)?.length ? `<div><strong class="muted">Missing skills</strong><div class="skill-row">${tags(match.missing_skills || job.missing_skills, 'warn')}</div></div>` : ''}<details><summary>Match evidence and eligibility</summary><p>${eligible === false ? 'Eligibility requirements are not met.' : 'No confirmed eligibility exclusion.'} ${escapeHtml(eligibility.confidence ? 'Confidence: ' + eligibility.confidence : '')}</p><ul>${textList([...(match.strengths || []), ...(match.gaps || []), ...(eligibility.positive_signals || []), ...(eligibility.warnings || []), ...(eligibility.hard_rejections || []), ...(job.reasons || [])])}</ul></details>`}
      <div class="card-actions"><button class="primary" data-job-id="${storedId}" onclick="selectJobById('${storedId}')" ${storedId ? '' : 'disabled'}>View details</button><button class="secondary" onclick="saveJobById('${storedId}')" ${storedId ? '' : 'disabled'}>${application ? 'Saved' : 'Save'}</button>${url ? `<a class="secondary" target="_blank" rel="noopener noreferrer" href="${escapeHtml(url)}">Open job</a>` : '<span class="muted">Application link unavailable</span>'}<button class="secondary" onclick="startJobOutreach('${storedId}')" ${storedId ? '' : 'disabled'}>Prepare outreach</button></div>
    </article>`;
  }).join('');
}

function renderJobs(jobs) {
  window.lastJobs = jobs;
  const roots = [$('workspaceJobResults'), $('jobResults')];
  if (!jobs.length) {
    const empty = '<div class="empty-state"><strong>No matches in this search</strong><span>Broaden a location or lower the minimum match score.</span></div>';
    roots.forEach(root => { root.innerHTML = empty; });
    return;
  }
  const markup = jobCardsMarkup(jobs);
  roots.forEach(root => { root.innerHTML = markup; });
}

function renderSelectedJobContext() {
  const root = $('selectedJobContext');
  if (!selectedJob) {
    root.innerHTML = '<div class="empty-state"><strong>No job selected</strong><span>Select a recommendation to inspect its evidence, tracker state and outreach actions.</span></div>';
    $('contextOutreachPanel').classList.add('hidden');
    return;
  }
  const match = selectedJob.match || {};
  const eligibility = selectedJob.eligibility || {};
  const application = applicationsByJob.get(selectedJob.id) || (selectedJob.application_id ? { id: selectedJob.application_id, status: 'SAVED', outreach_state: 'NONE' } : null);
  const url = safeExternalUrl(selectedJob.application_url);
  root.innerHTML = `<div class="selected-title"><h4>${escapeHtml(selectedJob.title || 'Opportunity')}</h4><p>${escapeHtml(selectedJob.company || '')}</p>${providerCredit(selectedJob)}</div>
    <dl class="context-facts"><div><dt>Location</dt><dd>${escapeHtml(selectedJob.location || 'Unknown')}</dd></div><div><dt>Verification</dt><dd>${escapeHtml((selectedJob.verification_state || 'UNVERIFIED').replaceAll('_', ' '))}</dd></div><div><dt>Eligibility</dt><dd>${eligibility.eligible === false ? 'Not eligible' : 'No confirmed exclusion'}</dd></div><div><dt>Tracker</dt><dd>${escapeHtml((application?.status || 'NOT SAVED').replaceAll('_', ' '))}</dd></div><div><dt>Outreach</dt><dd>${escapeHtml(application?.outreach_state || 'NONE')}</dd></div></dl>
    <section class="drawer-section"><h3>Match evidence</h3><p>${escapeHtml(match.explanation || 'Review the requirements before acting.')}</p></section>
    <section class="drawer-section"><h3>Matched skills</h3><div class="skill-row">${tags(match.matched_skills || [], 'good') || '<span class="muted">No explicit skill match</span>'}</div></section>
    ${match.transferable_skills?.length ? `<section class="drawer-section"><h3>Transferable skills</h3><div class="skill-row">${tags(match.transferable_skills)}</div></section>` : ''}
    ${(match.missing_skills || selectedJob.missing_skills)?.length ? `<section class="drawer-section"><h3>Missing skills</h3><div class="skill-row">${tags(match.missing_skills || selectedJob.missing_skills, 'warn')}</div></section>` : ''}
    <section class="drawer-section"><h3>Eligibility</h3><p>${eligibility.eligible === false ? 'This role has a confirmed eligibility conflict.' : 'No confirmed eligibility exclusion.'} ${escapeHtml(eligibility.confidence ? `Confidence: ${eligibility.confidence}.` : '')}</p>${eligibility.warnings?.length ? `<ul>${textList(eligibility.warnings)}</ul>` : ''}</section>
    <section class="drawer-section"><h3>Verification</h3><p>${escapeHtml(selectedJob.verification_reason || 'Application page has not been verified.')}</p></section>
    <div class="context-actions">${url ? `<a class="secondary" target="_blank" rel="noopener noreferrer" href="${escapeHtml(url)}">Open job</a>` : ''}<button class="secondary" type="button" onclick="saveSelectedJob()">${application ? 'Refresh saved state' : 'Save to tracker'}</button></div>`;
  selectedJob.application_id = application?.id || selectedJob.application_id || null;
  $('contextOutreachPanel').classList.remove('hidden');
}

function openJobDrawer(opener = document.activeElement, jobId = selectedJob?.id || null) {
  jobDrawerReturnFocus = opener;
  jobDrawerReturnJobId = jobId;
  jobDrawerReturnScope = opener?.closest?.('#conversation') ? '#conversation ' : '';
  $('jobDetailDrawer').classList.remove('hidden');
  $('jobDrawerBackdrop').classList.remove('hidden');
  $('jobDetailDrawer').setAttribute('aria-hidden', 'false');
  $('selectedJobHeading').focus();
}

function closeJobDrawer() {
  $('jobDetailDrawer').classList.add('hidden');
  $('jobDrawerBackdrop').classList.add('hidden');
  $('jobDetailDrawer').setAttribute('aria-hidden', 'true');
  const fallback = jobDrawerReturnJobId
    ? document.querySelector(`${jobDrawerReturnScope}[data-job-id="${jobDrawerReturnJobId}"]`)
    : null;
  const returnTarget = jobDrawerReturnFocus?.isConnected === false ? fallback : jobDrawerReturnFocus;
  if (returnTarget && typeof returnTarget.focus === 'function') returnTarget.focus();
  jobDrawerReturnFocus = null;
  jobDrawerReturnJobId = null;
  jobDrawerReturnScope = '';
}

$('closeJobDrawer').addEventListener('click', closeJobDrawer);
$('jobDrawerBackdrop').addEventListener('click', closeJobDrawer);

window.selectJobById = function(id) {
  const opener = document.activeElement;
  selectedJob = jobsById.get(id) || null;
  renderJobs(window.lastJobs || []);
  renderSelectedJobContext();
  if (selectedJob) openJobDrawer(opener, id);
};

window.selectJob = function(index) {
  const job = window.lastJobs?.[index];
  if (job?.id) {
    jobsById.set(job.id, job);
    window.selectJobById(job.id);
  }
};

window.saveSelectedJob = async function() {
  if (selectedJob?.id) await window.saveJobById(selectedJob.id);
};

window.saveJobById = async function(id) {
  const job = jobsById.get(id);
  if (job) await saveStoredJob(job);
};

window.saveJob = async function(index) {
  const job = window.lastJobs?.[index];
  if (job) await saveStoredJob(job);
};

async function saveStoredJob(job) {
  if (!job?.id) return;
  try {
    const entry = await api('/applications', jsonOptions('POST', { job_id: job.id, status: 'SAVED', notes: '' }));
    job.application_id = entry.id;
    applicationsByJob.set(job.id, entry);
    if (selectedJob?.id === job.id) selectedJob.application_id = entry.id;
    renderJobs(window.lastJobs || []);
    renderConversation();
    renderSelectedJobContext();
    showStatus($('resultActionStatus'), `${job.title} saved to Tracker.`, 'success');
    showStatus($('searchStatus'), `${job.title} saved to Applications.`, 'success');
  } catch (error) { showStatus($('resultActionStatus'), error.message, 'error'); }
};

async function loadTracker() {
  try {
    await refreshApplicationIndex();
    $('trackerList').innerHTML = trackerEntries.length ? trackerEntries.map((entry, index) => {
      const job = entry.job || entry;
      const url = safeExternalUrl(job.application_url);
      return `<article class="tracker-card"><div class="panel-head"><div><h3>${escapeHtml(job.title || entry.job_title || 'Saved opportunity')}</h3><p>${escapeHtml(job.company || entry.company || '')}</p></div>${url ? `<a class="secondary" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open job</a>` : ''}</div><label for="trackerState${index}">Application status</label><select id="trackerState${index}">${applicationStatuses.map(status => `<option value="${status}" ${entry.status === status ? 'selected' : ''}>${status.replaceAll('_', ' ')}</option>`).join('')}</select><label for="trackerNotes${index}">Your notes</label><textarea id="trackerNotes${index}" rows="3">${escapeHtml(entry.notes || '')}</textarea><div class="card-actions section-gap"><button class="primary" onclick="updateApplication(${index})">Save changes</button><button class="secondary" onclick="composeTrackedEmail(${index})">Prepare outreach</button></div><p class="muted">Last updated: ${escapeHtml(formatDate(entry.updated_at || entry.created_at) || 'Unknown')}</p></article>`;
    }).join('') : '<p class="muted">No saved applications yet. Save an opportunity from Recommendations to start tracking it.</p>';
    if (window.lastJobs) renderJobs(window.lastJobs);
    renderConversation();
    renderSelectedJobContext();
  } catch (error) { showStatus($('trackerStatus'), error.message, 'error'); }
}
window.updateApplication = async function(index) {
  const entry = trackerEntries[index];
  if (!entry) return;
  try {
    await api('/applications/' + encodeURIComponent(entry.id), jsonOptions('PATCH', { status: $('trackerState' + index).value, notes: $('trackerNotes' + index).value }));
    showStatus($('trackerStatus'), 'Application updated.', 'success');
    await loadTracker();
  } catch (error) { showStatus($('trackerStatus'), error.message, 'error'); }
};
window.composeTrackedEmail = function(index) {
  const entry = trackerEntries[index];
  if (!entry) return;
  const job = entry.job || entry;
  selectedJob = { ...job, id: entry.job_id || job.id, title: job.title || entry.job_title || 'Opportunity', company: job.company || entry.company || '', application_id: entry.id };
  showEmailComposer();
};
$('reloadTracker').addEventListener('click', loadTracker);

function activateWorkspacePanel(panelId) {
  document.querySelectorAll('.workspace-tab').forEach(button => {
    const selected = button.dataset.workspacePanel === panelId;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  document.querySelectorAll('.workspace-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === panelId);
  });
}

document.querySelectorAll('.workspace-tab').forEach(button => {
  button.addEventListener('click', () => activateWorkspacePanel(button.dataset.workspacePanel));
});

$('prepareContextOutreachBtn').addEventListener('click', async () => {
  if (!selectedJob || searchBusy) return;
  const recipient = $('contextRecipient').value.trim();
  if (!recipient || !$('contextRecipient').reportValidity()) {
    $('contextRecipient').focus();
    return;
  }
  const application = applicationsByJob.get(selectedJob.id);
  await runChat('Prepare outreach for the selected job.', {
    recipient,
    selected_job_id: selectedJob.id,
    application_id: selectedJob.application_id || application?.id || null
  });
});

async function resumeChat(confirmed) {
  if (!pendingDraftId || resumeBusy) return;
  const activeDraftId = pendingDraftId;
  if (!pendingResumeTurn) pendingResumeTurn = uniqueId('turn');
  resumeBusy = true;
  $('confirmSendBtn').disabled = true;
  $('cancelSendBtn').disabled = true;
  showStatus($('contextActionStatus'), confirmed ? 'Approving and sending through the protected send boundary…' : 'Cancelling send confirmation…');
  const payload = {
    thread_id: chatThreadId,
    turn_id: pendingResumeTurn,
    draft_id: activeDraftId,
    confirmed
  };
  if (confirmed) {
    payload.edited_subject = $('confirmationSubject').value;
    payload.edited_body = $('confirmationBody').value;
  }
  try {
    const response = await api('/chat/resume', jsonOptions('POST', payload));
    pendingResumeTurn = null;
    await handleChatResponse(response);
    let draft = null;
    try { draft = await api(`/email/drafts/${encodeURIComponent(activeDraftId)}`); } catch { draft = null; }
    $('sendConfirmationPanel').classList.add('hidden');
    if (draft?.status === 'send_failed') {
      showStatus($('contextActionStatus'), 'The send outcome is uncertain. This draft cannot be retried; create a new draft for another attempt.', 'error');
    } else {
      showStatus($('contextActionStatus'), confirmed ? response.message : 'Send cancelled. The draft was not approved or sent.', confirmed ? 'success' : '');
    }
    pendingDraftId = null;
    await refreshApplicationIndex();
    renderJobs(window.lastJobs || []);
    renderSelectedJobContext();
  } catch (error) {
    showStatus($('contextActionStatus'), error.message, 'error');
  } finally {
    resumeBusy = false;
    $('confirmSendBtn').disabled = false;
    $('cancelSendBtn').disabled = false;
  }
}

$('confirmSendBtn').addEventListener('click', () => resumeChat(true));
$('cancelSendBtn').addEventListener('click', () => resumeChat(false));

window.openEmailComposer = function(index) {
  selectedJob = window.lastJobs?.[index];
  if (!selectedJob) return;
  showEmailComposer();
}

window.startJobOutreach = function(id) {
  window.selectJobById(id);
  $('contextRecipient').focus();
};

function showEmailComposer() {
  $("emailJobTitle").textContent = selectedJob.title;
  $("emailCompany").textContent = selectedJob.company;
  $("recipientEmail").value = "";
  $("customNote").value = "";
  $("resumeAttachmentNote").textContent = resumeAttachmentId
    ? "Your uploaded CV will be attached to this draft."
    : "No CV attachment is selected yet. Upload your CV in Profile if you want it attached.";
  hideStatus($('emailComposeStatus'));
  showModalWithFocusReturn($("emailDialog"));
}

$("createDraftBtn").addEventListener("click", async () => {
  if (!selectedJob) return;
  const recipient = $("recipientEmail").value.trim();

  if (!recipient || !$('recipientEmail').reportValidity()) {
    $("recipientEmail").focus();
    return;
  }

  $("createDraftBtn").disabled = true;
  $("createDraftBtn").textContent = "Creating…";

  try {
    const draft = await api("/agent/prepare-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recipient,
        company: selectedJob.company,
        title: selectedJob.title,
        application_url: safeExternalUrl(selectedJob.application_url),
        job_id: selectedJob.id || null,
        application_id: selectedJob.application_id || null,
        attachment_id: resumeAttachmentId || null,
        custom_note: $("customNote").value.trim() || null
      })
    });

    $("emailDialog").close();
    await window.openDraft(draft.id);
  } catch (err) {
    showStatus($('emailComposeStatus'), err.message, 'error');
  } finally {
    $("createDraftBtn").disabled = false;
    $("createDraftBtn").textContent = "Create draft for approval";
  }
});

async function loadDrafts() {
  const root = $("draftList");

  try {
    const drafts = await api("/email/drafts");
    if (!drafts.length) {
      root.innerHTML = `<p class="muted">No email drafts yet.</p>`;
      return;
    }

    root.innerHTML = drafts.map(d => `
      <div class="draft-card">
        <div>
          <div class="status-badge ${escapeHtml(d.status)}">${escapeHtml(d.status)}</div>
          <h3>${escapeHtml(d.subject)}</h3>
          <p>To: ${escapeHtml(d.recipient)} · ${escapeHtml(formatDate(d.created_at))}</p>
        </div>
        <button class="secondary" onclick="openDraft(${Number(d.id) || 0})">Review</button>
      </div>
    `).join("");
  } catch (err) {
    root.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

window.openDraft = async function(id) {
  const opener = document.activeElement;
  try {
    const d = await api(`/email/drafts/${encodeURIComponent(id)}`);
    $("draftPreview").innerHTML = `
      <div class="preview-meta">
        <strong>To:</strong> ${escapeHtml(d.recipient)}<br>
        <strong>Subject:</strong> ${escapeHtml(d.subject)}<br>
        <strong>Status:</strong> ${escapeHtml(d.status)}<br>
        <strong>Attachment:</strong> ${escapeHtml(d.attachment_id ? "CV/document attached" : "None")}
      </div>
      <div class="preview-box">${escapeHtml(d.body)}</div>
    `;
    const short = d.short_message;
    $('shortMessage').textContent = short || '';
    $('shortMessagePanel').classList.toggle('hidden', !short);

    const actions = [];
    if (d.status === "draft") {
      actions.push(`<button class="secondary" onclick="cancelDraft(${Number(d.id) || 0})">Cancel draft</button>`);
      actions.push(`<button class="primary" onclick="approveDraft(${Number(d.id) || 0})">Approve</button>`);
    } else if (d.status === "approved") {
      actions.push(`<button class="secondary" onclick="cancelDraft(${Number(d.id) || 0})">Cancel</button>`);
      actions.push(`<button class="primary" onclick="sendDraft(${Number(d.id) || 0})">Send with Gmail</button>`);
    }

    $("draftActions").innerHTML = actions.join("");
    if (opener && typeof opener.focus === 'function') dialogReturnFocus.set($("draftDialog"), opener);
    $("draftDialog").showModal();
  } catch (err) {
    alert(err.message);
  }
};

window.approveDraft = async function(id) {
  try {
    await api(`/email/drafts/${id}/approve`, { method: "POST" });
    await window.openDraft(id);
    await loadDrafts();
  } catch (err) { alert(err.message); }
};

window.cancelDraft = async function(id) {
  try {
    await api(`/email/drafts/${id}/cancel`, { method: "POST" });
    $("draftDialog").close();
    await loadDrafts();
  } catch (err) { alert(err.message); }
};

window.sendDraft = async function(id) {
  const ok = confirm("Send this approved email now through your connected Gmail account?");
  if (!ok) return;

  try {
    const sent = await api(`/email/drafts/${id}/send`, { method: "POST" });
    alert(`Email sent.${sent.gmail_message_id ? " Gmail message ID: " + sent.gmail_message_id : ""}`);
    $("draftDialog").close();
    await loadDrafts();
  } catch (err) { alert(err.message); }
};

$("closeDraftDialog").addEventListener("click", () => $("draftDialog").close());
$("reloadDrafts").addEventListener("click", loadDrafts);

async function refreshConnections() {
  await Promise.all([refreshLinkedIn(), refreshExistingConnections()]);
}

async function refreshLinkedIn() {
  try {
    const linkedin = await api("/auth/linkedin/status");
    const message = linkedin.connected
      ? [linkedin.name, linkedin.email].filter(Boolean).join(" | ") || "Your LinkedIn account is connected."
      : linkedin.error
        ? "The saved connection could not be read. Reconnect LinkedIn or check your local configuration."
        : linkedin.configured
          ? "Connect your LinkedIn account to share your profile and email."
          : "LinkedIn setup is incomplete. Check the local credentials, redirect URL, and encryption key.";
    $("linkedinStatusBox").innerHTML = `<strong>${linkedin.connected ? "Connected" : "Not connected"}</strong><p>${escapeHtml(message)}</p>`;
    $("connectLinkedIn").classList.toggle("hidden", linkedin.connected);
    $("disconnectLinkedIn").classList.toggle("hidden", !linkedin.connected && !linkedin.error);
  } catch {
    $("linkedinStatusBox").textContent = "LinkedIn status is unavailable. Please refresh and try again.";
  }
}

async function refreshExistingConnections() {
  try {
    const gmail = await api("/auth/google/status");
    const gmailText = gmail.connected
      ? "Connected — Gmail send permission is ready."
      : gmail.configured
        ? "OAuth app configured, but Gmail is not connected yet."
        : "Google OAuth credentials are not configured yet.";

    $("gmailStatusBox").innerHTML = `<strong>${gmail.connected ? "Connected" : "Not connected"}</strong><p>${escapeHtml(gmailText)}</p>`;
    $("gmailPill").textContent = `Gmail: ${gmail.connected ? "connected" : "not connected"}`;
  } catch (err) {
    $("gmailStatusBox").textContent = err.message;
    $("gmailPill").textContent = "Gmail: unavailable";
  }

  try {
    const search = await api("/connections/search/status");
    const installed = (search.providers || []).filter(provider => provider.installed);
    const ready = installed.filter(provider => provider.configured);
    const list = installed.map(provider => `<li>${escapeHtml(provider.name)}: ${provider.configured ? "configured" : `skipped (add ${escapeHtml(provider.requires)} to .env)`}</li>`).join("");
    $("searchApiStatusBox").innerHTML = `
      <strong>${search.configured ? "Connected" : "Not configured"}</strong>
      <p>${search.configured ? "Searches use every configured provider. A provider without its key is skipped." : "Add at least one job provider key (JSearch, Adzuna or Jooble) to the local .env to enable job search."}</p>
      ${list ? `<ul>${list}</ul>` : ""}
    `;
    $("providerPill").textContent = installed.length
      ? `Search API: ${ready.length} of ${installed.length} providers`
      : `Search API: ${search.configured ? "connected" : "not configured"}`;
  } catch {
    $("searchApiStatusBox").textContent = "Job Search API status is unavailable. Please try again.";
    $("providerPill").textContent = "Search API: unavailable";
  }
}

$("disconnectGmail").addEventListener("click", async () => {
  try {
    await api("/auth/google/disconnect", { method: "DELETE" });
    await refreshConnections();
  } catch (err) { alert(err.message); }
});

$("disconnectLinkedIn").addEventListener("click", async () => {
  $("disconnectLinkedIn").disabled = true;
  try {
    await api("/auth/linkedin/disconnect", { method: "POST" });
    showStatus($("linkedinFeedback"), "LinkedIn disconnected from this app.", "success");
    await refreshLinkedIn();
  } catch {
    showStatus($("linkedinFeedback"), "Could not disconnect LinkedIn. Please try again.", "error");
  } finally {
    $("disconnectLinkedIn").disabled = false;
  }
});

loadProfile();
loadPreferences();
refreshConnections();

const params = new URLSearchParams(location.search);
if (params.get("gmail") === "connected") {
  setView("settings");
}
if (params.has("linkedin")) {
  const result = params.get("linkedin");
  const messages = {
    connected: "LinkedIn connected successfully.",
    denied: "LinkedIn connection was cancelled. You can try again whenever you are ready.",
    invalid_state: "This LinkedIn sign-in expired or could not be verified. Start again using Connect LinkedIn.",
    not_configured: "LinkedIn setup is incomplete. Check the local credentials, redirect URL, and encryption key.",
    failed: "LinkedIn could not be connected. Please try again."
  };
  showStatus($("linkedinFeedback"), messages[result] || messages.failed, result === "connected" ? "success" : "error");
  params.delete("linkedin");
  const remaining = params.toString();
  history.replaceState(null, "", location.pathname + (remaining ? "?" + remaining : "") + location.hash);
  setView("settings");
}

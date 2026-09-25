// Offline DOM contract checks for the real dashboard script; no browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('app/static/app.js', 'utf8');
const html = fs.readFileSync('app/static/index.html', 'utf8');
const css = fs.readFileSync('app/static/styles.css', 'utf8');

assert.match(html, /id="careerWorkspace"/);
assert.match(html, /id="workspaceJobResults"/);
assert.match(html, /id="selectedJobContext"/);
assert.match(html, /id="sendConfirmationPanel"/);
assert.match(html, /id="welcomeState"/);
assert.match(html, /id="mobileMenuBtn"/);
assert.match(html, /id="sidebarBackdrop"/);
assert.match(html, /id="jobDetailDrawer"/);
assert.match(html, /id="closeJobDrawer"/);
assert.match(html, /placeholder="Ask Career Agent anything\.\.\."/);
assert.match(html, /data-suggestion="Find jobs suitable for my CV"/);
assert.doesNotMatch(html, /Gemini/i);
assert.match(css, /\.icon-btn\.mobile-menu\s*\{\s*display:\s*none/);
assert.match(html, /class="inline-results-head sr-only"/);

// Let pending promise chains (e.g. the search-cost preview) finish.
const settle = () => new Promise(resolve => setImmediate(resolve));

async function dashboard(linkedin, query = '', disconnectFails = false, responses = {}) {
  const elements = new Map();
  let activeElement = null;
  function element(id) {
    const el = { id, className: '', textContent: '', innerHTML: '', disabled: false, value: '', checked: false, handlers: {}, attributes: {}, isConnected: true,
      showModal() { this.open = true; }, close() { this.open = false; }, focus() { this.focused = true; activeElement = this; }, scrollIntoView() {}, reportValidity() { return true; },
      requestSubmit() { return this.handlers.submit?.({ preventDefault() {} }); },
      setAttribute(name, value) { this.attributes[name] = String(value); }, removeAttribute(name) { delete this.attributes[name]; },
      addEventListener(event, callback) { this.handlers[event] = callback; } };
    el.classList = {
      add(value) { if (!this.contains(value)) el.className += ' ' + value; },
      remove(value) { el.className = el.className.split(/\s+/).filter(x => x !== value).join(' '); },
      contains(value) { return el.className.split(/\s+/).includes(value); },
      toggle(value, force) { if (force) this.add(value); else this.remove(value); }
    };
    return el;
  }
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], element(match[1]));
  const navigation = ['search', 'results', 'profile', 'tracker', 'email', 'settings', 'preferences'].map(name => {
    const el = element(name); el.dataset = { view: name }; return el;
  });
  const workspaceTabs = ['conversationPane', 'recommendationsPane', 'contextPane'].map(name => {
    const el = element(`tab-${name}`); el.dataset = { workspacePanel: name }; return el;
  });
  const suggestionChips = ['Find jobs suitable for my CV', 'Search AI/ML roles', 'Analyze a job description', 'Show my saved jobs'].map((prompt, index) => {
    const el = element(`suggestion-${index}`); el.dataset = { suggestion: prompt }; return el;
  });
  const documentHandlers = {};
  const detachedJobOpener = element('detached-job-opener');
  detachedJobOpener.isConnected = false;
  detachedJobOpener.closest = selector => selector === '#conversation' ? {} : null;
  const connectedJobOpener = element('connected-job-opener');
  const calls = [];
  let replacement;
  let uuid = 0;
  const sessionValues = new Map();
  const context = vm.createContext({
    document: {
      get activeElement() { return activeElement; },
      getElementById(id) { assert.ok(elements.has(id), `Missing HTML element ${id}`); return elements.get(id); },
      querySelectorAll(selector) {
        if (selector === '.nav') return navigation;
        if (selector === '.workspace-tab') return workspaceTabs;
        if (selector === '.workspace-panel') return ['conversationPane', 'recommendationsPane', 'contextPane'].map(id => elements.get(id));
        if (selector === '.suggestion-chip') return suggestionChips;
        return [...elements.values()].filter(el => el.id.endsWith('View'));
      },
      querySelector(selector) {
        if (selector.includes('[data-job-id=')) return connectedJobOpener;
        return navigation.find(el => selector.includes(`"${el.dataset.view}"`));
      },
      addEventListener(event, callback) { documentHandlers[event] = callback; }
    },
    localStorage: { getItem() { return null; }, setItem() {} },
    sessionStorage: { getItem(key) { return sessionValues.get(key) || null; }, setItem(key, value) { sessionValues.set(key, String(value)); }, removeItem(key) { sessionValues.delete(key); } },
    crypto: { randomUUID() { uuid += 1; return `00000000-0000-4000-8000-${String(uuid).padStart(12, '0')}`; } },
    window: {}, URLSearchParams, URL,
    location: { search: query, pathname: '/app/', hash: '' },
    history: { replaceState(_a, _b, path) { replacement = path; } },
    fetch: async (path, options = {}) => {
      calls.push([path, options.method, options.body ? JSON.parse(options.body) : null]);
      if (path === '/auth/linkedin/disconnect') {
        if (disconnectFails) throw Error('private provider exception');
        linkedin = { configured: true, connected: false };
      }
      if (path === '/auth/linkedin/status' && linkedin instanceof Error) throw linkedin;
      const data = Object.hasOwn(responses, path) ? (typeof responses[path] === 'function' ? responses[path](options) : responses[path])
        : path === '/auth/linkedin/status' ? linkedin
        : path === '/auth/google/status' ? { configured: false, connected: false }
        : path === '/connections/search/status' ? { configured: true }
        : path === '/email/drafts' || path === '/applications' ? [] : {};
      return { ok: true, json: async () => data };
    },
    alert() { throw Error('Unexpected alert'); }
  });
  vm.runInContext(script, context);
  await vm.runInContext('refreshConnections()', context);
  return { elements, calls, replacement, context, suggestionChips, documentHandlers, detachedJobOpener, connectedJobOpener };
}

(async () => {
  let d = await dashboard({ configured: true, connected: false });
  assert.equal(d.elements.get('connectLinkedIn').classList.contains('hidden'), false);
  assert.equal(d.elements.get('disconnectLinkedIn').classList.contains('hidden'), true);
  assert.match(d.elements.get('searchApiStatusBox').innerHTML, /Connected/);
  assert.match(d.elements.get('gmailStatusBox').innerHTML, /Not connected/);

  d = await dashboard({ configured: true, connected: true, name: '<img onerror=alert(1)>', email: 'test@example.com' }, '?linkedin=connected');
  assert.match(d.elements.get('linkedinStatusBox').innerHTML, /&lt;img/);
  assert.doesNotMatch(d.elements.get('linkedinStatusBox').innerHTML, /<img/);
  assert.equal(d.elements.get('connectLinkedIn').classList.contains('hidden'), true);
  assert.equal(d.elements.get('disconnectLinkedIn').classList.contains('hidden'), false);
  assert.equal(d.elements.get('settingsView').classList.contains('active'), true);
  assert.match(d.elements.get('linkedinFeedback').textContent, /successfully/);
  assert.equal(d.replacement, '/app/');
  await d.elements.get('disconnectLinkedIn').handlers.click();
  assert.ok(d.calls.some(([path, method]) => path === '/auth/linkedin/disconnect' && method === 'POST'));
  assert.equal(d.elements.get('disconnectLinkedIn').classList.contains('hidden'), true);
  assert.equal(d.elements.get('connectLinkedIn').classList.contains('hidden'), false);

  d = await dashboard({ configured: true, connected: false }, '?linkedin=private-provider-error');
  assert.doesNotMatch(d.elements.get('linkedinFeedback').textContent, /private-provider-error/);
  assert.match(d.elements.get('linkedinFeedback').textContent, /try again/);

  d = await dashboard(Error('private token error'));
  assert.match(d.elements.get('linkedinStatusBox').textContent, /unavailable/);
  assert.doesNotMatch(d.elements.get('linkedinStatusBox').textContent, /private token/);
  assert.match(d.elements.get('searchApiStatusBox').innerHTML, /Connected/);

  d = await dashboard({ configured: true, connected: true }, '', true);
  await d.elements.get('disconnectLinkedIn').handlers.click();
  assert.match(d.elements.get('linkedinFeedback').textContent, /Could not disconnect/);
  assert.doesNotMatch(d.elements.get('linkedinFeedback').textContent, /private provider/);
  assert.equal(d.elements.get('disconnectLinkedIn').disabled, false);
  const jobId = 'a'.repeat(64);
  const job = { id: jobId, title: 'Junior Designer', company: 'Sample', location: 'Pune', application_url: 'https://jobs.example.org/1',
    verification_state: 'ACTIVE_VERIFIED', verification_reason: 'Application page is active', total_score: 76,
    match: { overall_score: 76, matched_skills: ['Figma'], transferable_skills: ['Communication'], missing_skills: ['Sketch'], strengths: ['Portfolio work'], gaps: ['Sketch not listed'], explanation: 'Design skills align' },
    eligibility: { eligible: true, confidence: 'high', warnings: [] }, next_action: 'Review requirements' };
  const storedSearch = {
    results: [job], result_count: 1, provider: 'JSearch',
    intent: { roles_requested: ['Designer'], locations: ['Pune'], remote_allowed: true, strict_mode: false },
    queries: [{ query: 'Junior designer Pune', reason: 'Matches your design skills' }],
    role_suggestions: [{ family: 'Design', titles: ['UX Designer'], reason: 'Figma in your CV', evidence: ['Figma'] }],
    diagnostics: { fetched: 12, normalized: 10, deduplicated: 8, eligible: 1, ranked: 1, provider_counts: { jsearch: 12 }, errors: [], latency_ms: 20 },
    summary: 'One recommendation is ready.'
  };
  let chatCall = 0;
  const chatReplies = [
    { thread_id: 'ignored', turn_id: 'ignored', stage: 'completed', message: '<img onerror=alert(1)> One recommendation is ready.', career_session_id: 'session-1', recommendation_ids: [jobId], advisory_roles: [], replayed: false },
    { thread_id: 'ignored', turn_id: 'ignored', stage: 'needs_clarification', message: 'Which job do you mean?', recommendation_ids: [], advisory_roles: [], replayed: false },
    { thread_id: 'ignored', turn_id: 'ignored', stage: 'completed', message: 'Started a new conversation.', recommendation_ids: [], advisory_roles: [], replayed: true }
  ];
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': () => chatReplies[Math.min(chatCall++, chatReplies.length - 1)],
    '/agent/sessions/session-1': { id: 'session-1', intent: storedSearch.intent, response: storedSearch },
    '/applications': [{ id: 'application-1', job_id: jobId, status: 'SAVED', outreach_state: 'NONE', notes: '', job }]
  });
  assert.equal(d.elements.get('searchView').classList.contains('conversation-started'), false);
  await d.suggestionChips[0].handlers.click();
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 1);
  assert.equal(d.calls.find(([path]) => path === '/chat/run')[2].message, 'Find jobs suitable for my CV');
  assert.equal(d.elements.get('searchView').classList.contains('conversation-started'), true);
  chatCall = 0;
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': () => chatReplies[Math.min(chatCall++, chatReplies.length - 1)],
    '/agent/sessions/session-1': { id: 'session-1', intent: storedSearch.intent, response: storedSearch },
    '/applications': [{ id: 'application-1', job_id: jobId, status: 'SAVED', outreach_state: 'NONE', notes: '', job }]
  });
  d.elements.get('searchQuery').value = 'Find design roles';
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  assert.match(d.elements.get('searchUnderstanding').innerHTML, /Designer/);
  assert.match(d.elements.get('searchDiagnostics').innerHTML, /12/);
  assert.match(d.elements.get('searchQueries').innerHTML, /Junior designer Pune/);
  assert.match(d.elements.get('executionSummary').textContent, /One recommendation/);
  assert.match(d.elements.get('workspaceJobResults').innerHTML, /Junior Designer/);
  assert.match(d.elements.get('conversation').innerHTML, /job-artifact|Junior Designer/);
  assert.doesNotMatch(d.elements.get('conversation').innerHTML, /<img/);
  let chatCalls = d.calls.filter(([path]) => path === '/chat/run');
  assert.equal(chatCalls[0][2].career_session_id, null);
  assert.match(chatCalls[0][2].thread_id, /^career-/);
  assert.match(chatCalls[0][2].turn_id, /^turn-/);
  const firstThread = chatCalls[0][2].thread_id;
  const firstTurn = chatCalls[0][2].turn_id;
  d.elements.get('searchQuery').value = 'What about that one?';
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  chatCalls = d.calls.filter(([path]) => path === '/chat/run');
  assert.equal(chatCalls[1][2].career_session_id, 'session-1');
  assert.equal(chatCalls[1][2].thread_id, firstThread);
  assert.notEqual(chatCalls[1][2].turn_id, firstTurn);
  assert.match(d.elements.get('conversation').innerHTML, /Which job do you mean/);
  assert.equal(d.elements.get('searchQuery').focused, true);
  await d.elements.get('newSearchBtn').handlers.click();
  d.elements.get('searchQuery').value = 'Find analyst roles';
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  chatCalls = d.calls.filter(([path]) => path === '/chat/run');
  assert.equal(chatCalls[2][2].career_session_id, null);
  assert.notEqual(chatCalls[2][2].thread_id, firstThread);
  assert.equal(d.elements.get('chatReplayStatus').classList.contains('hidden'), false);

  for (const unsafe of ['javascript:alert(1)', 'http://localhost/job', 'http://127.0.0.1/job', 'http://10.0.0.1/job', 'http://192.168.1.2/job', 'http://[::1]/job', 'file:///etc/passwd']) {
    assert.equal(vm.runInContext(`safeExternalUrl(${JSON.stringify(unsafe)})`, d.context), null);
  }
  const jobs = [{ id: jobId, title: '<img onerror=alert(1)>', company: 'Sample', location: 'Pune', salary: 'INR 600000 yearly', application_url: 'javascript:alert(1)',
    verification_state: 'UNVERIFIED', verification_reason: 'The page could not be checked', total_score: 76,
    match: { overall_score: 76, matched_skills: ['Figma'], transferable_skills: ['Communication'], missing_skills: ['Sketch'], strengths: ['Portfolio work'], gaps: ['Sketch not listed'], explanation: 'Design skills align' },
    eligibility: { eligible: true, warnings: ['Experience unknown'] }, next_action: 'Review requirements' }];
  vm.runInContext(`renderJobs(${JSON.stringify(jobs)})`, d.context);
  assert.doesNotMatch(d.elements.get('jobResults').innerHTML, /<img|href="javascript/);
  assert.match(d.elements.get('jobResults').innerHTML, /UNVERIFIED|Unverified/);
  assert.match(d.elements.get('jobResults').innerHTML, /Communication/);
  assert.match(d.elements.get('jobResults').innerHTML, /INR 600000 yearly/);
  assert.match(d.elements.get('jobResults').innerHTML, /Experience unknown/);
  assert.equal(d.calls.some(([, method, body]) => method === 'POST' && body?.status === 'APPLIED'), false);
  d.detachedJobOpener.focus();
  d.context.window.selectJob(0);
  assert.equal(d.elements.get('jobDetailDrawer').classList.contains('hidden'), false);
  assert.match(d.elements.get('selectedJobContext').innerHTML, /Tracker/);
  assert.match(d.elements.get('selectedJobContext').innerHTML, /Application page has not been verified|UNVERIFIED/);
  const resumesBeforeEscape = d.calls.filter(([path]) => path === '/chat/resume').length;
  d.documentHandlers.keydown({ key: 'Escape' });
  assert.equal(d.elements.get('jobDetailDrawer').classList.contains('hidden'), true);
  assert.equal(d.connectedJobOpener.focused, true);
  assert.equal(d.calls.filter(([path]) => path === '/chat/resume').length, resumesBeforeEscape);
  d.context.window.selectJob(0);
  await d.context.window.saveJob(0);
  assert.ok(d.calls.some(([path, method, body]) => path === '/applications' && method === 'POST' && body.job_id === jobId && body.status === 'SAVED'));
  d.elements.get('closeJobDrawer').handlers.click();
  assert.equal(d.elements.get('jobDetailDrawer').classList.contains('hidden'), true);

  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': { thread_id: 'keyboard', turn_id: 'turn', stage: 'completed', message: 'Done', recommendation_ids: [], advisory_roles: [], replayed: false },
    '/applications': []
  });
  d.elements.get('searchQuery').value = 'Keyboard request';
  await d.elements.get('searchQuery').handlers.keydown({ key: 'Enter', shiftKey: true, preventDefault() { throw Error('Shift+Enter must not submit'); } });
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 0);
  await d.elements.get('searchQuery').handlers.keydown({ key: 'Enter', shiftKey: false, preventDefault() {} });
  await settle();
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 1);
  d.elements.get('mobileMenuBtn').handlers.click();
  assert.equal(d.elements.get('sidebar').classList.contains('open'), true);
  d.documentHandlers.keydown({ key: 'Escape' });
  assert.equal(d.elements.get('sidebar').classList.contains('open'), false);

  let releaseChat;
  const waitingChat = new Promise(resolve => { releaseChat = resolve; });
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': () => waitingChat,
    '/applications': []
  });
  d.elements.get('searchQuery').value = 'Find analyst jobs';
  const firstSubmit = d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  const duplicateSubmit = d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  await settle();
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 1);
  releaseChat({ thread_id: 'x', turn_id: 'y', stage: 'completed', message: 'Done', recommendation_ids: [], advisory_roles: [], replayed: false });
  await Promise.all([firstSubmit, duplicateSubmit]);

  let draftRead = 0;
  const draftForReview = { id: 77, recipient: 'recruiter@example.org', subject: 'Initial subject', body: 'Initial body', status: 'draft' };
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': { thread_id: 'thread', turn_id: 'turn', stage: 'awaiting_send_confirmation', message: 'Review the editable draft.', recommendation_ids: [], advisory_roles: [], draft_id: 77, application_id: 'application-1', replayed: false },
    '/chat/resume': { thread_id: 'thread', turn_id: 'resume', stage: 'completed', message: 'The approved draft was sent.', recommendation_ids: [], advisory_roles: [], draft_id: 77, application_id: 'application-1', replayed: false },
    '/email/drafts/77': () => (++draftRead === 1 ? draftForReview : { ...draftForReview, status: 'sent' }),
    '/applications': [{ id: 'application-1', job_id: jobId, status: 'SAVED', outreach_state: 'PREPARED', notes: '', job }]
  });
  await vm.runInContext('loadTracker()', d.context);
  vm.runInContext(`window.lastJobs = [${JSON.stringify(job)}]`, d.context);
  d.context.window.selectJob(0);
  d.elements.get('contextRecipient').value = 'recruiter@example.org';
  await d.elements.get('prepareContextOutreachBtn').handlers.click();
  const prepareChat = d.calls.find(([path]) => path === '/chat/run');
  assert.equal(prepareChat[2].recipient, 'recruiter@example.org');
  assert.equal(prepareChat[2].selected_job_id, jobId);
  assert.equal(d.elements.get('confirmationSubject').value, 'Initial subject');
  assert.equal(d.elements.get('sendConfirmationPanel').classList.contains('hidden'), false);
  d.elements.get('confirmationSubject').value = 'Edited subject';
  d.elements.get('confirmationBody').value = 'Edited body';
  const confirmOnce = d.elements.get('confirmSendBtn').handlers.click();
  const duplicateConfirm = d.elements.get('confirmSendBtn').handlers.click();
  await Promise.all([confirmOnce, duplicateConfirm]);
  const resumes = d.calls.filter(([path]) => path === '/chat/resume');
  assert.equal(resumes.length, 1);
  assert.equal(resumes[0][2].confirmed, true);
  assert.equal(resumes[0][2].edited_subject, 'Edited subject');
  assert.equal(resumes[0][2].edited_body, 'Edited body');
  assert.equal(d.calls.some(([path]) => /\/email\/drafts\/77\/(approve|send)/.test(path)), false);
  assert.equal(d.elements.get('sendConfirmationPanel').classList.contains('hidden'), true);

  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/chat/run': { thread_id: 'cancel-thread', turn_id: 'turn', stage: 'awaiting_send_confirmation', message: 'Review the editable draft.', recommendation_ids: [], advisory_roles: [], draft_id: 77, application_id: 'application-1', replayed: false },
    '/chat/resume': { thread_id: 'cancel-thread', turn_id: 'cancel-turn', stage: 'cancelled', message: 'Send cancelled.', recommendation_ids: [], advisory_roles: [], draft_id: 77, application_id: 'application-1', replayed: false },
    '/email/drafts/77': draftForReview,
    '/applications': [{ id: 'application-1', job_id: jobId, status: 'SAVED', outreach_state: 'PREPARED', notes: '', job }]
  });
  await vm.runInContext('loadTracker()', d.context);
  vm.runInContext(`window.lastJobs = [${JSON.stringify(job)}]`, d.context);
  d.context.window.selectJob(0);
  d.elements.get('contextRecipient').value = 'recruiter@example.org';
  await d.elements.get('prepareContextOutreachBtn').handlers.click();
  await d.elements.get('cancelSendBtn').handlers.click();
  assert.equal(d.calls.some(([path]) => path === '/email/drafts/null'), false);

  const persistedDraft = {
    id: 77, recipient: 'recruiter@example.org', subject: 'Stored role', body: 'Grounded body',
    status: 'draft', created_at: '2026-09-20T00:00:00Z', attachment_id: null,
    job_id: 'stored-job-id', application_id: 'stored-application-id', contact_id: null,
    short_message: 'Persisted short message'
  };
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/agent/prepare-email': persistedDraft,
    '/email/drafts/77': persistedDraft
  });
  vm.runInContext(`window.lastJobs = [{
    id: 'stored-job-id', application_id: 'stored-application-id',
    title: 'Stored Analyst', company: 'StoredCo', application_url: 'https://jobs.example.org/1'
  }]`, d.context);
  d.context.window.openEmailComposer(0);
  const dialogOpener = d.elements.get('newSearchBtn');
  dialogOpener.focus();
  d.context.window.openEmailComposer(0);
  dialogOpener.focused = false;
  d.elements.get('emailDialog').handlers.close();
  assert.equal(dialogOpener.focused, true);
  d.elements.get('recipientEmail').value = 'recruiter@example.org';
  await d.elements.get('createDraftBtn').handlers.click();
  const prepareCall = d.calls.find(([path]) => path === '/agent/prepare-email');
  assert.equal(prepareCall[2].job_id, 'stored-job-id');
  assert.equal(prepareCall[2].application_id, 'stored-application-id');
  assert.equal(d.elements.get('shortMessage').textContent, 'Persisted short message');

  d = await dashboard({ configured: false, connected: false }, '', false, {
    '/profile/current': { schema_version: 2, name: 'Test Candidate', skills: ['Excel'], projects: ['Portfolio'], parsing_warnings: ['Confirm graduation year'] },
    '/preferences/current': { allowed_work_modes: ['remote'], preferred_locations: ['Pune'], max_required_experience_years: 2, minimum_match_score: 40, search_query_limit: 4, verification_limit: 20 }
  });
  await vm.runInContext('loadProfile()', d.context);
  assert.match(d.elements.get('profileCard').innerHTML, /Confirm graduation year/);
  d.elements.get('profileName').value = 'Updated Candidate';
  await d.elements.get('profileForm').handlers.submit({ preventDefault() {} });
  assert.ok(d.calls.some(([path, method, body]) => path === '/profile/current' && method === 'PUT' && body.name === 'Updated Candidate' && body.projects[0] === 'Portfolio' && !Object.hasOwn(body, 'schema_version')));
  await vm.runInContext('loadPreferences()', d.context);
  d.elements.get('preferenceScore').value = '65';
  await d.elements.get('preferencesForm').handlers.submit({ preventDefault() {} });
  assert.ok(d.calls.some(([path, method, body]) => path === '/preferences/current' && method === 'PUT' && body.minimum_match_score === 65));
  // Connections lists each provider; one without its key is shown as skipped.
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/connections/search/status': { configured: true, providers: [
      { id: 'jsearch', name: 'JSearch/RapidAPI', installed: true, configured: true, requires: 'RAPIDAPI_KEY' },
      { id: 'adzuna', name: 'Adzuna', installed: true, configured: false, requires: 'ADZUNA_APP_ID and ADZUNA_APP_KEY' },
      { id: 'linkedin_jobs', name: 'linkedin_jobs', installed: false, configured: false, reason: 'n/a' }] }
  });
  assert.match(d.elements.get('searchApiStatusBox').innerHTML, /Adzuna: skipped \(add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env\)/);
  assert.doesNotMatch(d.elements.get('searchApiStatusBox').innerHTML, /linkedin_jobs/);
  assert.equal(d.elements.get('providerPill').textContent, 'Search API: 1 of 2 providers');

  // Jobs from aggregators carry the credit their terms require.
  const markup = vm.runInContext(`jobCardsMarkup([
    { id: '${'a'.repeat(64)}', title: 'Data Analyst', company: 'Example', source: 'Adzuna', application_url: 'https://www.adzuna.in/land/ad/1' },
    { id: '${'b'.repeat(64)}', title: 'Data Analyst', company: 'Example', source: 'Jooble', application_url: 'https://in.jooble.org/desc/1' },
    { id: '${'c'.repeat(64)}', title: 'Data Analyst', company: 'Example', source: 'JSearch/RapidAPI', application_url: 'https://example.com/jobs/1' }])`, d.context);
  assert.match(markup, /<a href="https:\/\/www\.adzuna\.co\.uk"[^>]*>Jobs<\/a> by <a href="https:\/\/www\.adzuna\.co\.uk"[^>]*>Adzuna<\/a>/);
  assert.match(markup, /Jobs via <a href="https:\/\/in\.jooble\.org"[^>]*>Jooble<\/a>/);
  assert.equal((markup.match(/provider-credit/g) || []).length, 2);

  // A costly search asks first; cancelling sends nothing to /chat/run.
  const costly = { will_search: true, provider_requests: 8, warning: 'This search will send 8 requests to JSearch/RapidAPI.' };
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/agent/search/preview': costly,
    '/chat/run': { thread_id: 'cost', turn_id: 'turn', stage: 'completed', message: 'Done', recommendation_ids: [], advisory_roles: [], replayed: false },
    '/applications': []
  });
  const prompts = [];
  d.context.window.confirm = text => { prompts.push(text); return false; };
  d.elements.get('searchQuery').value = 'Find python developer jobs';
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  assert.match(prompts[0], /8 requests to JSearch\/RapidAPI/);
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 0);
  assert.match(d.elements.get('searchStatus').textContent, /No provider requests were sent/);
  assert.equal(d.elements.get('searchSubmit').disabled, false);
  assert.equal(d.calls.find(([path]) => path === '/agent/search/preview')[2].message, 'Find python developer jobs');
  d.context.window.confirm = () => true;
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 1);

  // A preview failure never blocks the request.
  d = await dashboard({ configured: true, connected: false }, '', false, {
    '/agent/search/preview': () => { throw Error('preview unavailable'); },
    '/chat/run': { thread_id: 'cost2', turn_id: 'turn', stage: 'completed', message: 'Done', recommendation_ids: [], advisory_roles: [], replayed: false },
    '/applications': []
  });
  d.context.window.confirm = () => { throw Error('must not ask without a warning'); };
  d.elements.get('searchQuery').value = 'Find python developer jobs';
  await d.elements.get('searchForm').handlers.submit({ preventDefault() {} });
  assert.equal(d.calls.filter(([path]) => path === '/chat/run').length, 1);

  console.log('Dashboard JavaScript: LinkedIn, search/follow-up, empty diagnostics, safe results, save, profile, preferences and search-cost warning passed (DOM simulation, no browser).');
})().catch(error => { console.error(error); process.exitCode = 1; });

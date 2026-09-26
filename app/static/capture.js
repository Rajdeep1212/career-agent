// "Save to Career Agent" form: pre-filled from the bookmarklet's URL and title, saved to the local app.
const byId = id => document.getElementById(id);

// Page titles often carry the site name ("Data Analyst - Example | LinkedIn"); keep the first part.
function cleanTitle(title) {
  return String(title || '').split(/\s+[|·–—]\s+/)[0].replace(/\s+-\s+(?:LinkedIn|Naukri(?:\.com)?|Indeed(?:\.com)?)$/i, '').trim();
}

function prefill(search) {
  const params = new URLSearchParams(search);
  byId('captureUrl').value = params.get('url') || '';
  byId('captureTitle').value = cleanTitle(params.get('title'));
}

function showCaptureStatus(text, kind) {
  const box = byId('captureStatus');
  box.textContent = text;
  box.className = `status ${kind || ''}`.trim();
}

async function saveCapture(event) {
  event.preventDefault();
  byId('captureSave').disabled = true;
  const payload = {
    url: byId('captureUrl').value.trim(), title: byId('captureTitle').value.trim(), company: byId('captureCompany').value.trim(),
    location: byId('captureLocation').value.trim(), description: byId('captureDescription').value.trim()
  };
  try {
    const response = await fetch('/capture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof body.detail === 'string' ? body.detail : 'Check the link, title and company, then try again.';
      showCaptureStatus(detail, 'error');
      return;
    }
    const where = body.matched_official ? 'Matched to the official listing in your Company Radar. ' : '';
    const again = body.already_saved ? 'Already saved; updated. ' : 'Saved. ';
    showCaptureStatus(`${again}${where}Eligibility: ${body.eligibility_summary}. Heuristic fit ${body.total_score}/100 (L0). ` +
      'It now appears in your searches and can be added to the tracker.', 'success');
  } catch {
    showCaptureStatus('Could not reach Career Agent. Is it running on this computer?', 'error');
  } finally {
    byId('captureSave').disabled = false;
  }
}

if (typeof document !== 'undefined' && byId('captureForm')) {
  prefill(location.search);
  byId('captureForm').addEventListener('submit', saveCapture);
}

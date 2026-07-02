// ── CONFIG ── Change this to your Render URL after deployment ──────────────
const API = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8000'
  : 'https://edugrade-ai-ha0n.onrender.com'; // ← Replace this after deploying to Render

// NOTE: this project uses a single secret — GROQ_API_KEY — kept only in the
// backend's .env and never sent to the browser. Access control on the API
// comes from the backend's CORS allow-list (ALLOWED_ORIGINS) and its
// per-IP rate limiter, not from a client-side key.

let files = [];
let rubrics = [
  { criterion: 'Correctness', points: 30, desc: 'Does the code produce correct output?' },
  { criterion: 'Readability', points: 20, desc: 'Is the code clear and well-commented?' },
  { criterion: 'Efficiency', points: 20, desc: 'Is the solution efficient and optimized?' },
  { criterion: 'Modularity', points: 30, desc: 'Is the code well-structured into functions?' },
];
let jobId = null, pollTimer = null, expandedIdx = null;

// ── FIX: escape any string before it goes into innerHTML ───────────────────
// Filenames are user-controlled and reasoning/summary/viva text comes from
// an LLM that has seen (untrusted) submission content — both need escaping
// before being inserted into the DOM, or they can execute as HTML/JS.
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── Init ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderRubrics();
  setupDrop();
  checkHealth();

  document.getElementById('rubricFile').addEventListener('change', e => {
    if (e.target.files[0]) uploadRubricFile(e.target.files[0]);
  });
  document.getElementById('fileInput').addEventListener('change', e => {
    addFiles([...e.target.files]); e.target.value = '';
  });
});

async function checkHealth() {
  const dot = document.getElementById('apiStatus');
  try {
    const r = await fetch(`${API}/health`);
    if (r.ok) { dot.className = 'status-dot ok'; dot.title = 'Backend connected'; }
    else { dot.className = 'status-dot err'; dot.title = 'Backend error'; }
  } catch {
    dot.className = 'status-dot err';
    dot.title = 'Backend not reachable';
  }
}

// ── Rubric ───────────────────────────────────────────────────────────────
function renderRubrics() {
  const list = document.getElementById('rubricList');
  list.innerHTML = rubrics.map((r, i) => `
    <div class="rubric-row">
      <input type="text" value="${escapeHtml(r.criterion)}" placeholder="Criterion"
        oninput="rubrics[${i}].criterion=this.value">
      <input type="text" value="${escapeHtml(r.desc)}" placeholder="Description"
        oninput="rubrics[${i}].desc=this.value">
      <input type="number" value="${r.points}" min="1" max="100"
        oninput="rubrics[${i}].points=parseInt(this.value)||0;updateTotal()"
        style="text-align:center">
      <button class="btn-remove" onclick="removeRubric(${i})">✕</button>
    </div>`).join('');
  updateTotal();
}
function addRubric() { rubrics.push({ criterion: '', points: 10, desc: '' }); renderRubrics(); }
function removeRubric(i) { rubrics.splice(i, 1); renderRubrics(); }
function updateTotal() {
  const total = rubrics.reduce((s, r) => s + (parseInt(r.points) || 0), 0);
  const el = document.getElementById('totalPts');
  el.textContent = total;
  el.style.color = total === 100 ? 'var(--success)' : total > 100 ? 'var(--danger)' : 'var(--warn)';
}

async function uploadRubricFile(file) {
  const btn = document.querySelector('.upload-rubric-btn');
  btn.textContent = '⏳ Parsing…';
  try {
    const fd = new FormData(); fd.append('file', file);
    const res = await fetch(`${API}/parse-rubric`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.rubrics && data.rubrics.length) {
      rubrics = data.rubrics.map(r => ({
        criterion: r.criterion || '',
        points: parseInt(r.points) || 10,
        desc: r.desc || ''
      }));
      renderRubrics();
      btn.textContent = `✓ ${data.rubrics.length} criteria loaded`;
    } else {
      btn.textContent = '⚠ No criteria found';
    }
  } catch (e) {
    btn.textContent = '✕ Parse failed';
    console.error(e);
  }
  setTimeout(() => btn.textContent = '📄 Upload rubric file', 3000);
}

// ── File drop ────────────────────────────────────────────────────────────
const MAX_CLIENT_FILE_MB = 15; // keep in sync with backend MAX_UPLOAD_BYTES

function setupDrop() {
  const zone = document.getElementById('dropZone');
  zone.addEventListener('click', () => document.getElementById('fileInput').click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('drag');
    addFiles([...e.dataTransfer.files]);
  });
}

function addFiles(newFiles) {
  const names = new Set(files.map(f => f.name));
  const tooBig = [];
  const valid = newFiles.filter(f => {
    if (names.has(f.name)) return false;
    const typeOk = f.type === 'application/pdf' || f.name.match(/\.(docx|txt|py|js|ts|java|c|cpp|cs|go|rb)$/i);
    if (!typeOk) return false;
    if (f.size > MAX_CLIENT_FILE_MB * 1024 * 1024) { tooBig.push(f.name); return false; }
    return true;
  });
  if (tooBig.length) showToast(`Skipped (too large, >${MAX_CLIENT_FILE_MB}MB): ${tooBig.join(', ')}`, 'error');
  files = [...files, ...valid];
  renderFiles();
}

function renderFiles() {
  const list = document.getElementById('fileList');
  const badge = document.getElementById('fileCountBadge');
  if (badge) {
    badge.textContent = files.length ? `${files.length} file${files.length > 1 ? 's' : ''}` : '';
    badge.style.display = files.length ? 'inline-block' : 'none';
  }
  list.innerHTML = files.map((f, i) => `
    <div class="file-item">
      <span class="file-ext">${escapeHtml(f.name.split('.').pop().toUpperCase())}</span>
      <span class="file-name">${escapeHtml(f.name)}</span>
      <span class="file-size">${fmtSize(f.size)}</span>
      <button class="file-remove" onclick="removeFile(${i})">✕</button>
    </div>`).join('');
  const btn = document.getElementById('evalBtn');
  btn.disabled = !files.length;
  btn.textContent = files.length
    ? `Evaluate ${files.length} submission${files.length > 1 ? 's' : ''}`
    : 'Evaluate submissions';
}
function removeFile(i) { files.splice(i, 1); renderFiles(); }
function fmtSize(b) { return b < 1024 ? `${b}B` : b < 1048576 ? `${(b / 1024).toFixed(1)}KB` : `${(b / 1048576).toFixed(1)}MB`; }

// ── Evaluation ───────────────────────────────────────────────────────────
async function startEval() {
  if (!files.length) return;

  const total = rubrics.reduce((s, r) => s + (parseInt(r.points) || 0), 0);
  if (total !== 100) {
    showToast(`Rubric must total 100 points (currently ${total})`, 'error');
    return;
  }

  const btn = document.getElementById('evalBtn');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Uploading…`;

  try {
    const fd = new FormData();
    files.forEach(f => fd.append('files', f));
    const upRes = await fetch(`${API}/upload`, { method: 'POST', body: fd });
    if (!upRes.ok) throw new Error(`Upload failed: ${upRes.status}`);
    const upData = await upRes.json();
    const fileIds = upData.files.map(f => f.file_id);

    btn.innerHTML = `<span class="spinner"></span> Starting evaluation…`;

    const evRes = await fetch(`${API}/evaluate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rubrics, file_ids: fileIds })
    });
    if (!evRes.ok) throw new Error(`Evaluation failed: ${evRes.status}`);
    const evData = await evRes.json();
    jobId = evData.job_id;

    showResultsPage(files.length);
    pollTimer = setInterval(pollJob, 1800);
  } catch (e) {
    showToast(e.message || 'Something went wrong. Is the backend running?', 'error');
    btn.disabled = false;
    btn.textContent = `Evaluate ${files.length} submission${files.length > 1 ? 's' : ''}`;
  }
}

function showResultsPage(total) {
  document.getElementById('setupPage').hidden = true;
  document.getElementById('resultsPage').hidden = false;
  renderStats({ total, completed: 0, errors: 0, plagCount: 0 });
  setProgress(0, total);
}

async function pollJob() {
  try {
    const res = await fetch(`${API}/job/${jobId}`);
    if (!res.ok) throw new Error(`Poll failed: ${res.status}`);
    const job = await res.json();
    renderResults(job);
    if (job.status === 'done') { clearInterval(pollTimer); renderClassSummary(job); }
  } catch (e) { console.error(e); }
}

// ── Render results ───────────────────────────────────────────────────────
function renderStats({ total, completed, errors, plagCount }) {
  document.getElementById('statsGrid').innerHTML = [
    { label: 'Total', value: total },
    { label: 'Evaluated', value: completed },
    { label: 'Errors', value: errors, color: errors ? 'var(--danger)' : null },
    { label: 'Plagiarism', value: plagCount, color: plagCount ? 'var(--warn)' : null },
  ].map(m => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(m.label)}</div>
      <div class="stat-value" ${m.color ? `style="color:${m.color}"` : ''}>${m.value}</div>
    </div>`).join('');
}

function setProgress(done, total) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = `${done} of ${total} files processed`;
  document.getElementById('progressBar').hidden = (done >= total);
}

function renderResults(job) {
  const done = job.results.filter(r => r.status === 'done' || r.status === 'plagiarism').length;
  const errors = job.results.filter(r => r.status === 'error').length;

  document.getElementById('resultsSubtitle').textContent =
    job.status === 'done'
      ? `Completed — ${done} evaluated, ${errors} error${errors !== 1 ? 's' : ''}`
      : `Processing ${job.completed} of ${job.total}…`;

  renderStats({ total: job.total, completed: done, errors, plagCount: job.plagiarism_pairs.length });
  setProgress(job.completed, job.total);

  // Plagiarism panel
  const pp = document.getElementById('plagPanel');
  if (job.plagiarism_pairs.length) {
    pp.hidden = false;
    pp.innerHTML = `<div class="plag-title">⚠ ${job.plagiarism_pairs.length} Plagiarism Alert${job.plagiarism_pairs.length > 1 ? 's' : ''} Detected</div>` +
      job.plagiarism_pairs.map(p => `
        <div class="plag-pair">
          <span>${escapeHtml(p.file_a)}</span>
          <span class="plag-sim">${p.similarity}% match</span>
          <span>${escapeHtml(p.file_b)}</span>
        </div>`).join('');
  }

  // Results list
  const list = document.getElementById('resultsList');
  const rows = job.results.map((r, i) => resultRow(r, i)).join('');
  const pending = Array.from({ length: Math.max(0, job.total - job.results.length) })
    .map(() => `<div class="result-row pending-row">⏳ Waiting…</div>`).join('');
  list.innerHTML = rows + pending;
}

function gradeColor(g) {
  return { A: '#16a34a', B: '#65a30d', C: '#d97706', D: '#ea580c', F: '#dc2626' }[g] || '#94a3b8';
}
function statusColor(s) {
  return { done: '#16a34a', plagiarism: '#d97706', processing: '#4f46e5', error: '#dc2626', pending: '#cbd5e1' }[s] || '#cbd5e1';
}
function statusLabel(s) {
  return { done: 'Done', plagiarism: 'Plagiarism', processing: 'Evaluating…', error: 'Error', pending: 'Waiting' }[s] || s;
}

function resultRow(r, i) {
  const isOpen = expandedIdx === i;
  const gc = r.grade ? gradeColor(r.grade) : '#94a3b8';

  return `
  <div class="result-row" id="row-${i}">
    <div class="result-header" onclick="toggleRow(${i})">
      <span class="result-dot" style="background:${statusColor(r.status)}"></span>
      <span class="result-name">${escapeHtml(r.name)}</span>
      <span class="result-status" style="color:${statusColor(r.status)}">${statusLabel(r.status)}</span>
      ${r.grade ? `<span class="grade-badge" style="background:${gc}22;color:${gc}">${escapeHtml(r.grade)} ${r.pct}%</span>` : ''}
      <div class="dl-group" onclick="event.stopPropagation()">
        ${r.status !== 'error' && r.result ? `
          <a class="dl-btn" href="${API}/report/${jobId}/${r.file_id}" download title="Download PDF Report">↓ PDF</a>
          <a class="dl-btn" href="${API}/feedback-txt/${jobId}/${r.file_id}" download title="Download Text Feedback" style="margin-left:4px">↓ TXT</a>
        ` : ''}
      </div>
      ${r.result ? `<span class="chevron${isOpen ? ' open' : ''}">▾</span>` : ''}
    </div>
    ${r.plag_note ? `<div class="plag-note">⚠ ${escapeHtml(r.plag_note)}</div>` : ''}
    ${r.status === 'error' ? `<div class="error-note">${escapeHtml(r.error)}</div>` : ''}
    ${isOpen && r.result ? detailPanel(r) : ''}
  </div>`;
}

function toggleRow(i) {
  expandedIdx = expandedIdx === i ? null : i;
  fetch(`${API}/job/${jobId}`).then(r => r.json()).then(renderResults);
}

function detailPanel(r) {
  const scores = r.result.scores || [];
  return `
  <div class="result-detail">
    <div class="scores-grid">
      ${scores.map(s => {
        const p = s.maxPoints ? Math.round((s.earned / s.maxPoints) * 100) : 0;
        const c = p >= 70 ? '#16a34a' : p >= 50 ? '#d97706' : '#dc2626';
        return `<div class="score-card">
          <div class="score-crit">${escapeHtml(s.criterion)}</div>
          <div class="score-val" style="color:${c}">${s.earned}<span style="font-size:13px;color:#94a3b8;font-weight:400">/${s.maxPoints}</span></div>
          <div class="score-bar"><div class="score-bar-fill" style="width:${p}%;background:${c}"></div></div>
        </div>`;
      }).join('')}
    </div>
    <div class="feedback-section">
      <div class="feedback-label">Detailed Feedback</div>
      ${scores.map(s => `
        <div class="feedback-item">
          <div class="feedback-crit">${escapeHtml(s.criterion)} — ${s.earned}/${s.maxPoints}</div>
          <div class="feedback-text">${escapeHtml(s.reasoning)}</div>
        </div>`).join('')}
    </div>
    <div class="summary-box">${escapeHtml(r.result.summary)}</div>
    <div class="feedback-label">Viva Questions</div>
    ${(r.result.vivaQuestions || []).map((q, i) => `
      <div class="viva-item">
        <span class="viva-num">Q${i + 1}</span>
        <span class="viva-q">${escapeHtml(q)}</span>
      </div>`).join('')}
  </div>`;
}

function renderClassSummary(job) {
  const scored = job.results.filter(r => r.pct != null);
  if (!scored.length) return;
  const avg = Math.round(scored.reduce((s, r) => s + r.pct, 0) / scored.length);
  const avgGrade = avg >= 90 ? 'A' : avg >= 80 ? 'B' : avg >= 70 ? 'C' : avg >= 60 ? 'D' : 'F';
  const el = document.getElementById('classSummary');
  el.hidden = false;
  el.innerHTML = `
    <div class="class-summary-title">Class Summary</div>
    <div class="class-stats">
      <div class="class-stat"><span>Average score </span><strong>${avg}%</strong></div>
      <div class="class-stat"><span>Average grade </span><strong style="color:${gradeColor(avgGrade)}">${avgGrade}</strong></div>
      <div class="class-stat"><span>Submissions </span><strong>${scored.length}</strong></div>
      <div class="class-stat"><span>Plagiarism flags </span><strong style="color:${job.plagiarism_pairs.length ? '#d97706' : '#16a34a'}">${job.plagiarism_pairs.length}</strong></div>
    </div>`;
}

function reset() {
  clearInterval(pollTimer);
  files = []; jobId = null; expandedIdx = null;
  document.getElementById('fileList').innerHTML = '';
  const badge = document.getElementById('fileCountBadge');
  if (badge) { badge.textContent = ''; badge.style.display = 'none'; }
  document.getElementById('evalBtn').disabled = true;
  document.getElementById('evalBtn').textContent = 'Evaluate submissions';
  document.getElementById('setupPage').hidden = false;
  document.getElementById('resultsPage').hidden = true;
  document.getElementById('plagPanel').hidden = true;
  document.getElementById('classSummary').hidden = true;
  checkHealth();
}

// ── Toast notifications ──────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const existing = document.getElementById('toast');
  if (existing) existing.remove();
  const t = document.createElement('div');
  t.id = 'toast';
  t.className = `toast toast-${type}`;
  t.textContent = msg; // textContent, not innerHTML — already safe
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('toast-show'), 10);
  setTimeout(() => { t.classList.remove('toast-show'); setTimeout(() => t.remove(), 300); }, 4000);
}

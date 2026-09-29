let latest = { extracted: {}, memories: [], conversation: '' };

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value;
  return element.innerHTML;
}

function selectedExtractions() {
  return [...document.querySelectorAll('.chip.active')].map((chip) => chip.textContent);
}

function renderResults(data) {
  latest = {
    extracted: data.extracted,
    memories: data.memories_recalled,
    conversation: $('#conversation').value,
  };

  $('#extracted').innerHTML = Object.entries(data.extracted).map(([category, facts]) => `
    <div class="extract-group">
      <h4>${escapeHtml(category)}</h4>
      <ul>${facts.map((fact) => `<li>${escapeHtml(fact)}</li>`).join('')}</ul>
    </div>
  `).join('');

  $('#personalization').textContent = data.personalization_note;
  $('#memories').innerHTML = data.memories_recalled.length
    ? data.memories_recalled.map((memory) => `
        <article class="memory-card"><small>MEMORY RECALLED</small>${escapeHtml(memory)}</article>
      `).join('')
    : '<div class="memory-empty">No relevant previous memory yet. This conversation’s extracted facts were retained in Hindsight.</div>';
}

function toggleChipSelection() {
  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => chip.classList.toggle('active'));
  });
}

function setupDemos() {
  $('#demo1').addEventListener('click', () => {
    $('#conversation').value = 'User: Keep technical explanations concise and use tables for comparisons.';
    document.querySelectorAll('.chip').forEach((chip) => chip.classList.toggle('active', chip.textContent === 'Preferences'));
  });

  $('#demo2').addEventListener('click', () => {
    $('#conversation').value = 'User: I need a short overview of our AI knowledge platform for leadership. Create a document from this.';
    document.querySelectorAll('.chip').forEach((chip) => chip.classList.toggle('active', chip.textContent === 'Knowledge'));
  });
}

function setupUpload() {
  $('#file').addEventListener('change', async (event) => {
    if (!event.target.files[0]) return;
    const form = new FormData();
    form.append('file', event.target.files[0]);
    const response = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await response.json();
    if (data.conversation) $('#conversation').value = data.conversation;
    else alert(data.detail || 'Upload failed');
  });
}

function setupAnalysis() {
  $('#analyze').addEventListener('click', async () => {
    const button = $('#analyze');
    const conversation = $('#conversation').value.trim();
    if (!conversation) return alert('Paste a conversation first.');

    button.textContent = 'Forging…';
    button.disabled = true;
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation, selections: selectedExtractions(), focus: $('#focus').value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not reach Hindsight');
      renderResults(data);
    } catch (error) {
      alert(error.message);
    } finally {
      button.innerHTML = 'Extract &amp; retain <b>→</b>';
      button.disabled = false;
    }
  });
}

const FORMAT_LABELS = { md: 'Reusable Skill', txt: 'Plain text', docx: 'Word document', pptx: 'Presentation' };

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function setupExport() {
  $('#export').addEventListener('click', async () => {
    if (!Object.keys(latest.extracted).length) return alert('Extract a conversation first.');
    const button = $('#export');
    const personalization = latest.memories.length ? 'Personalization applied: This output follows the recalled context below.\n\n' : '';
    const content = personalization + Object.entries(latest.extracted)
      .map(([category, facts]) => `${category}\n${facts.map((fact) => `• ${fact}`).join('\n')}`)
      .join('\n\n');
    button.disabled = true;
    button.innerHTML = 'Generating…';
    try {
      const response = await fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: $('#format').value, title: 'ContextForge — Personalized Output', content, memories: latest.memories }),
      });
      const data = await response.json();
      if (!response.ok) return alert(data.detail || 'Export failed');
      const applied = (data.memories_applied && data.memories_applied.length)
        ? `<p class="download-note">✦ ${data.memories_applied.length} recalled memory(ies) applied</p>` : '';
      $('#downloads').innerHTML = `
        <div class="download-card">
          <div class="download-meta">
            <span class="download-badge">${escapeHtml((FORMAT_LABELS[data.format] || data.format).toUpperCase())}</span>
            <strong>${escapeHtml(data.filename)}</strong>
            <small>Saved on backend: <code>${escapeHtml(data.saved_path)}</code> · ${formatSize(data.size)}</small>
            ${applied}
          </div>
          <a class="button primary" href="${data.url}" download>Download ↓</a>
        </div>`;
      loadOutputs();
    } finally {
      button.disabled = false;
      button.innerHTML = 'Generate <b>↗</b>';
    }
  });
}

function loadOutputs() {
  fetch('/api/outputs')
    .then((response) => response.json())
    .then((data) => {
      const list = $('#output-list');
      if (!data.outputs || !data.outputs.length) {
        list.innerHTML = '<div class="memory-empty">No files generated yet.</div>';
        return;
      }
      list.innerHTML = data.outputs.map((file) => `
        <div class="output-row">
          <span class="download-badge">${escapeHtml((FORMAT_LABELS[file.format] || file.format).toUpperCase())}</span>
          <span class="output-name">${escapeHtml(file.filename)}</span>
          <span class="output-size">${formatSize(file.size)}</span>
          <a class="button ghost small" href="${file.url}" download>Download ↓</a>
        </div>
      `).join('');
    })
    .catch(() => {});
}

function checkMemoryConnection() {
  fetch('/api/health')
    .then((response) => response.json())
    .then((data) => {
      const ready = data.hindsight === 'configured';
      $('#status').textContent = ready ? '● Hindsight configured' : '○ Hindsight needs setup';
      $('#setup').hidden = ready;
    })
    .catch(() => { $('#status').textContent = '○ Service unavailable'; });
}

toggleChipSelection();
setupDemos();
setupUpload();
setupAnalysis();
setupExport();
checkMemoryConnection();
loadOutputs();
$('#refresh-outputs').addEventListener('click', loadOutputs);

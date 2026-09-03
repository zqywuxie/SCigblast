(() => {
  const $ = (selector) => document.querySelector(selector);
  const statusLabel = { QUEUED: '排队中', RUNNING: '运行中', MATCHING: '匹配中', WAITING_REVIEW: '待审核', SUCCEEDED: '已完成', FAILED: '失败', STOPPED: '已停止', STOPPING: '停止中', INTERRUPTED: '已中断', COMPLETED_WITHOUT_MARKER: '需检查' };
  const activeStates = new Set(['QUEUED', 'RUNNING', 'MATCHING', 'STOPPING']);
  const doneStates = new Set(['SUCCEEDED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'COMPLETED_WITHOUT_MARKER']);
  let pipelineInfo = {}, allJobs = [], filter = 'all', page = 0, limit = 50, busy = false;

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const statusText = (value) => statusLabel[value] || value;
  const isActive = (job) => activeStates.has(job.status);
  const isDone = (job) => doneStates.has(job.status);
  const rememberedFields = ['operator', 'input_path', 'submission_path', 'barcode_csv', 'output_root'];
  function restoreForm() { rememberedFields.forEach((name) => { const value = localStorage.getItem(`scigblast.${name}`); const input = document.querySelector(`[name="${name}"]`); if (value && input) input.value = value; }); }
  function rememberForm(body) { rememberedFields.forEach((name) => { if (body[name]) localStorage.setItem(`scigblast.${name}`, body[name]); }); }
  const browseLabels = { input: '原始数据目录', submission: 'Submission 文件或目录', barcode: 'Barcode CSV', output: '输出根目录' };
  let pickerKind = '', pickerTarget = '', pickerPath = '', pickerBusy = false;

  function closePicker() { $('#file-picker-shell').classList.remove('open'); $('#file-picker-shell').setAttribute('aria-hidden', 'true'); pickerKind = ''; pickerTarget = ''; pickerPath = ''; }
  function pickerEntry(entry) {
    const icon = entry.type === 'directory' ? '▱' : '▤';
    const size = entry.size == null ? '' : ` · ${(entry.size / 1024).toFixed(1)} KB`;
    return `<button type="button" class="file-tree-entry ${entry.type}" data-path="${esc(entry.path)}" data-entry-type="${entry.type}" ${entry.selectable ? '' : 'aria-disabled="true"'}><span class="file-tree-icon">${icon}</span><span class="file-tree-copy"><strong>${esc(entry.name)}</strong><small>${entry.type === 'directory' ? '目录' : `文件${size}`}</small></span><span class="file-tree-action">${entry.type === 'directory' ? '打开 ›' : (entry.selectable ? '选择' : '')}</span></button>`;
  }
  function renderPicker(data) {
    pickerPath = data.path || '';
    $('#file-picker-current').textContent = data.path || '允许的根目录';
    $('#file-picker-up').disabled = !data.parent;
    $('#file-picker-up').dataset.path = data.parent || '';
    $('#file-picker-select').disabled = !data.can_select_current || !data.path;
    $('#file-picker-select').classList.toggle('hidden', pickerKind === 'barcode');
    const entries = data.entries || [];
    $('#file-picker-entries').innerHTML = entries.length ? entries.map(pickerEntry).join('') : '<div class="file-tree-empty">当前目录没有可选择的文件</div>';
  }
  async function loadPicker(path = '') {
    if (pickerBusy) return;
    pickerBusy = true;
    $('#file-picker-message').textContent = '读取目录中…';
    try {
      const params = new URLSearchParams({ kind: pickerKind });
      if (path) params.set('path', path);
      const response = await fetch(`/api/browse?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '无法读取目录');
      renderPicker(data);
      $('#file-picker-message').textContent = `${browseLabels[pickerKind]} · 只显示允许根目录下的内容`;
    } catch (error) {
      $('#file-picker-message').textContent = error.message || '读取失败';
      $('#file-picker-entries').innerHTML = '<div class="file-tree-empty error-text">无法读取该目录，请手动输入服务器绝对路径</div>';
      $('#file-picker-up').disabled = true;
      $('#file-picker-select').disabled = true;
    } finally { pickerBusy = false; }
  }
  function openPicker(kind, target) { pickerKind = kind; pickerTarget = target; $('#file-picker-title').textContent = `选择${browseLabels[kind] || '路径'}`; $('#file-picker-shell').classList.add('open'); $('#file-picker-shell').setAttribute('aria-hidden', 'false'); loadPicker(); }
  function choosePickerPath(path) { const input = document.querySelector(`[name="${pickerTarget}"]`); if (input) { input.value = path; input.dispatchEvent(new Event('input', { bubbles: true })); } closePicker(); }

  function openDrawer() { $('#drawer-shell').classList.add('open'); $('#drawer-shell').setAttribute('aria-hidden', 'false'); setTimeout(() => $('#drawer-shell input[name="operator"]')?.focus(), 120); }
  function closeDrawer() { $('#drawer-shell').classList.remove('open'); $('#drawer-shell').setAttribute('aria-hidden', 'true'); }
  function selectedPipeline() { return $('#pipeline').value; }

  function renderPipelineCards() {
    const cards = Object.entries(pipelineInfo).map(([key, info]) => `<button type="button" class="pipeline-card ${key === selectedPipeline() ? 'selected' : ''}" data-pipeline="${key}"><span class="pipeline-dot ${info.accent || 'teal'}"></span><span class="pipeline-card-copy"><strong>${esc(info.label)}</strong><small>${esc(info.description || '')}</small></span><span class="pipeline-card-check">✓</span></button>`).join('');
    $('#pipeline-cards').innerHTML = cards;
    document.querySelectorAll('.pipeline-card').forEach((card) => card.addEventListener('click', () => { $('#pipeline').value = card.dataset.pipeline; updatePipelineFields(); }));
  }

  function updatePipelineFields() {
    const info = pipelineInfo[selectedPipeline()] || {};
    $('#barcode-row').classList.toggle('hidden', !info.requires_barcode);
    $('#ir-options').classList.toggle('hidden', !info.ir_options);
    document.querySelectorAll('.pipeline-card').forEach((card) => card.classList.toggle('selected', card.dataset.pipeline === selectedPipeline()));
  }

  function formatTime(value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  function renderJobs(data) {
    const jobs = data.jobs || [];
    const query = $('#job-search').value.trim().toLowerCase();
    const visible = jobs.filter((job) => {
      const byFilter = filter === 'active' ? isActive(job) : filter === 'review' ? job.status === 'WAITING_REVIEW' : filter === 'done' ? isDone(job) : true;
      const byPipeline = !$('#pipeline-filter').value || job.pipeline === $('#pipeline-filter').value;
      const byQuery = !query || `${job.id} ${job.dataset} ${job.pipeline_label}`.toLowerCase().includes(query);
      return byFilter && byPipeline && byQuery;
    });
    $('#jobs').innerHTML = visible.map((job) => `<tr>
      <td><a class="job-link" href="/jobs/${job.id}"><span class="job-id">${esc(job.id)}</span><strong>${esc(job.dataset)}</strong><small>${esc(job.input_path)}</small></a></td>
      <td><span class="pipeline-chip ${job.pipeline}"><i></i>${esc(job.pipeline_label)}</span></td>
      <td><span class="stage-name">${esc(job.stage_labels?.[job.current_stage] || job.current_stage || '等待启动')}</span><small class="stage-code">${esc(job.current_stage || '')}</small></td>
      <td><div class="progress-cell"><div class="progress-track"><span style="width:${Math.max(0, Math.min(100, job.progress))}%"></span></div><b>${job.progress}%</b></div></td>
      <td><span class="operator-name">${esc(job.operator)}</span><small class="time-text">${formatTime(job.started_at || job.created_at)}</small></td>
      <td><span class="status-badge ${job.status.toLowerCase()}"><i></i>${esc(statusText(job.status))}</span>${job.last_error ? `<small class="error-text" title="${esc(job.last_error)}">${esc(job.last_error)}</small>` : ''}</td>
      <td class="row-actions">${job.status === 'WAITING_REVIEW' ? `<button class="button button-review button-small" data-action="confirm" data-id="${job.id}">审核 Match</button>` : ''}${isActive(job) ? `<button class="button button-danger-ghost button-small" data-action="stop" data-id="${job.id}">停止</button>` : ''}<a class="button button-ghost button-small" href="/jobs/${job.id}">详情</a></td>
    </tr>`).join('');
    $('#empty-state').classList.toggle('hidden', visible.length !== 0);
    const counts = data.counts || {}; $('#stat-active').textContent = counts.active ?? data.active_jobs ?? jobs.filter(isActive).length; $('#stat-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#stat-done').textContent = counts.done ?? jobs.filter(isDone).length; $('#stat-total').textContent = counts.total ?? data.total ?? jobs.length; $('#stat-capacity').textContent = `并行上限 ${data.max_active_jobs ?? '—'}`;
    $('#count-all').textContent = counts.total ?? data.total ?? jobs.length; $('#count-active').textContent = counts.active ?? jobs.filter(isActive).length; $('#count-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#count-done').textContent = counts.done ?? jobs.filter(isDone).length;
    const start = data.total ? data.offset + 1 : 0; const end = Math.min((data.offset || 0) + jobs.length, data.total || 0); $('#page-summary').textContent = data.total ? `显示 ${start}–${end} / 共 ${data.total} 条` : '暂无任务'; $('#page-prev').disabled = page === 0; $('#page-next').disabled = end >= (data.total || 0);
    document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => runAction(button.dataset.id, button.dataset.action)));
  }

  async function refreshJobs() { if (busy) return; busy = true; const params = new URLSearchParams({ limit: String(limit), offset: String(page * limit) }); if (filter === 'active') params.set('status', 'QUEUED,RUNNING,MATCHING,STOPPING'); if (filter === 'review') params.set('status', 'WAITING_REVIEW'); if (filter === 'done') params.set('status', 'SUCCEEDED,FAILED,STOPPED,INTERRUPTED,COMPLETED_WITHOUT_MARKER'); if ($('#pipeline-filter').value) params.set('pipeline', $('#pipeline-filter').value); if ($('#job-search').value.trim()) params.set('query', $('#job-search').value.trim()); try { const data = await (await fetch(`/api/jobs?${params}`)).json(); allJobs = data.jobs || []; renderJobs(data); } finally { busy = false; } }

  async function runAction(id, action) { if (action === 'stop' && !window.confirm('停止这个任务？中间产物会保留，可稍后续跑。')) return; const response = await fetch(`/api/jobs/${id}/${action === 'confirm' ? 'confirm-match' : action}`, { method: 'POST' }); if (!response.ok) { const error = await response.json().catch(() => ({})); showMessage(error.detail || '操作失败', true); } else { showMessage(action === 'confirm' ? 'Match 已确认，任务继续运行' : '操作已提交'); } await refreshJobs(); }
  function showMessage(message, error = false) { const node = $('#form-message'); node.textContent = message; node.classList.toggle('error', error); clearTimeout(showMessage.timer); showMessage.timer = setTimeout(() => { node.textContent = ''; node.classList.remove('error'); }, 4500); }

  async function validatePaths() { const form = $('#new-job-form'); const body = Object.fromEntries(new FormData(form).entries()); if (!body.barcode_csv) body.barcode_csv = null; const response = await fetch('/api/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const result = await response.json(); const summary = $('#validation-summary'); document.querySelectorAll('.path-state').forEach((state) => state.classList.toggle('ok', response.ok)); document.querySelectorAll('.path-state').forEach((state) => state.classList.toggle('bad', !response.ok)); if (response.ok) { summary.textContent = `路径可用 · Dataset: ${result.dataset} · 输出: ${result.output_root}`; summary.parentElement.parentElement.classList.add('valid'); } else { summary.textContent = result.detail || '路径验证失败'; summary.parentElement.parentElement.classList.remove('valid'); } }

  $('#new-job-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.target.querySelector('button[type="submit"]'); button.disabled = true; button.classList.add('loading'); const body = Object.fromEntries(new FormData(event.target).entries()); if (!body.barcode_csv) body.barcode_csv = null; const response = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const result = await response.json(); button.disabled = false; button.classList.remove('loading'); if (response.ok) { rememberForm(body); showMessage(`任务 ${result.id} 已加入队列`); closeDrawer(); event.target.reset(); restoreForm(); updatePipelineFields(); await refreshJobs(); } else { showMessage(result.detail || '创建失败，请检查路径', true); } });
  $('#open-drawer').addEventListener('click', openDrawer); $('#close-drawer').addEventListener('click', closeDrawer); $('#drawer-backdrop').addEventListener('click', closeDrawer); $('#validate-button').addEventListener('click', validatePaths); $('#pipeline').addEventListener('change', updatePipelineFields); $('#refresh-jobs').addEventListener('click', refreshJobs); $('#job-search').addEventListener('input', () => { page = 0; refreshJobs(); }); $('#pipeline-filter').addEventListener('change', () => { page = 0; refreshJobs(); }); $('#page-prev').addEventListener('click', () => { if (page > 0) { page -= 1; refreshJobs(); } }); $('#page-next').addEventListener('click', () => { page += 1; refreshJobs(); }); document.querySelectorAll('.field input').forEach((input) => input.addEventListener('input', () => { const state = document.querySelector(`.path-state[data-for="${input.name}"]`); if (state) state.classList.remove('ok', 'bad'); }));
  document.querySelectorAll('.browse-button').forEach((button) => button.addEventListener('click', () => openPicker(button.dataset.browseKind, button.dataset.browseTarget)));
  $('#close-file-picker').addEventListener('click', closePicker); $('#file-picker-cancel').addEventListener('click', closePicker); $('#file-picker-backdrop').addEventListener('click', closePicker); $('#file-picker-up').addEventListener('click', () => loadPicker($('#file-picker-up').disabled ? '' : $('#file-picker-up').dataset.path)); $('#file-picker-select').addEventListener('click', () => { if (pickerPath) choosePickerPath(pickerPath); });
  $('#file-picker-entries').addEventListener('click', (event) => { const entry = event.target.closest('.file-tree-entry'); if (!entry) return; const path = entry.dataset.path; if (entry.dataset.entryType === 'directory') { loadPicker(path); } else { choosePickerPath(path); } });
  document.querySelectorAll('.filter-tab').forEach((tab) => tab.addEventListener('click', () => { document.querySelectorAll('.filter-tab').forEach((item) => { item.classList.remove('active'); item.setAttribute('aria-selected', 'false'); }); tab.classList.add('active'); tab.setAttribute('aria-selected', 'true'); filter = tab.dataset.filter; page = 0; refreshJobs(); }));
  document.addEventListener('keydown', (event) => { if (event.key !== 'Escape') return; if ($('#file-picker-shell').classList.contains('open')) closePicker(); else closeDrawer(); });
  loadPipelines().then(() => { Object.entries(pipelineInfo).forEach(([key, info]) => { $('#pipeline-filter').insertAdjacentHTML('beforeend', `<option value="${key}">${esc(info.label)}</option>`); }); renderPipelineCards(); restoreForm(); refreshJobs(); });
  setInterval(refreshJobs, 5000);
})();

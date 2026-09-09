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
  let formDefaults = {};
  function resetForm() {
    $('#new-job-form').reset();
    resetSubmission();
    $('[name="barcode_csv"]').value = formDefaults.barcode_csv || '';
    $('#submission-status').textContent = '选择 .xlsx 文件或目录后，可检查和修改 Note；原文件不变。';
    $('#validation-summary').textContent = '填写路径后可验证任务配置';
    $('#form-message').textContent = '';
    clearTimeout(showMessage.timer);
    document.querySelectorAll('.path-state').forEach(n => n.classList.remove('ok', 'bad'));
    updatePipelineFields();
  }
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
      const data = await Submission.api(`/api/browse?${params}`);
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

  function openDrawer() { resetForm(); document.body.classList.add('task-dialog-open'); $('#drawer-shell').classList.add('open'); $('#drawer-shell').setAttribute('aria-hidden', 'false'); setTimeout(() => { $('#pipeline-cards .selected')?.focus({preventScroll:true}); $('#drawer-shell .drawer-body').scrollTop=0; }, 120); }
  function closeDrawer() { document.body.classList.remove('task-dialog-open'); $('#drawer-shell').classList.remove('open'); $('#drawer-shell').setAttribute('aria-hidden', 'true'); $('#open-drawer').focus(); }
  function selectedPipeline() { return $('#pipeline').value; }
  async function loadPipelines() {
    pipelineInfo = await Submission.api('/api/pipelines');
    const defaults = await Submission.api('/api/defaults');
    formDefaults = defaults;
    $('[name="barcode_csv"]').value = defaults.barcode_csv;
    $('#output-hint').textContent = `留空自动创建 ${defaults.output_base}/姓名拼音首字母_pipeline_日期时间（如 zqy_10x_split_20260909_150443）。网页保留完整姓名，已有目录不改名。填写路径时使用该路径本身。`;
    $('#pipeline-cards').insertAdjacentHTML('beforebegin', '<p id="pipeline-root-hint" class="field-hint"></p>');
    $('#pipeline-root-hint').textContent = `项目目录：${defaults.pipeline_root}`;
    $('#pipeline').innerHTML = Object.entries(pipelineInfo).map(([key, info]) => `<option value="${esc(key)}">${esc(info.label)}</option>`).join('');
    $('#help-pipeline').innerHTML = $('#pipeline').innerHTML;
    $('#pipeline-help-open').disabled = false;
    updatePipelineFields();
  }

  function renderPipelineCards() {
    const cards = Object.entries(pipelineInfo).map(([key, info]) => `<button type="button" class="pipeline-card ${key === selectedPipeline() ? 'selected' : ''}" data-pipeline="${key}" aria-pressed="${key === selectedPipeline()}"><span class="pipeline-dot ${info.accent || 'teal'}"></span><span class="pipeline-card-copy"><strong>${esc(info.label)}</strong></span><span class="pipeline-card-check" aria-hidden="true">✓</span></button>`).join('');
    $('#pipeline-cards').innerHTML = cards;
    document.querySelectorAll('.pipeline-card').forEach((card) => card.addEventListener('click', () => { $('#pipeline').value = card.dataset.pipeline; updatePipelineFields(); }));
  }

  function updatePipelineFields() {
    const info = pipelineInfo[selectedPipeline()] || {};
    $('#barcode-row').classList.toggle('hidden', !info.requires_barcode);
    $('#ir-options').classList.toggle('hidden', !info.ir_options);
    $('[name="barcode_csv"]').disabled = !info.requires_barcode;
    document.querySelectorAll('.pipeline-card').forEach((card) => {
      const selected = card.dataset.pipeline === selectedPipeline();
      card.classList.toggle('selected', selected);
      card.setAttribute('aria-pressed', String(selected));
    });
  }

  function renderHelp() {
    const key = $('#help-pipeline').value, info = pipelineInfo[key] || {};
    const details = {
      ir_split: '用于 Bulk IR 数据，barcode 用于定位样本。支持原始数据和带 UMI 标签的已拆分数据；先进行 fastp 质量过滤，已拆分数据保留原有 barcode 信息。代表序列流程在样本内按 UMI 选择代表序列，再完成 IgBLAST 比对。',
      '10x_split': '用于单细胞数据，barcode 标识细胞。R1 检查 TSO、R2 检查 barcode 后进行序列合并，再按样本内的 barcode + UMI 选择代表序列，完成 IgBLAST 比对。',
      igblast_base: '不进行 barcode 拆分或 UMI 代表序列提取。按 Submission 的 Note 匹配样本，并按 Chain 选择数据库。',
      pig_igblast: '核对 Submission 中的 Pig 物种、样本与 Chain，使用猪专用链数据库比对，不进行 IR/10X 拆分。'
    };
    const finalStages = {
      ir_split: {
        title: '结果整理 · Preprocessing',
        input: 'IgBLAST 的 AIRR 注释结果，以及前序 UMI 代表序列记录。',
        processing: '校验序列的样本归属，过滤并计算 UMI 支持数，按样本汇总链表达与多样性；有 BCR 数据时，还计算抗体类别比例、类别转换（CSR）、体细胞高频突变（SHM）和 B 细胞多样性。',
        output: 'Datapoint.csv 样本指标总表，以及按样本和链整理的 junction 序列结果。',
        note: '结果整理用于代表序列流程，以样本为单位汇总；不同来源的样本分别保留，这里的 barcode 不代表单个细胞。'
      },
      '10x_split': {
        title: '链聚类 · Chain clustering',
        input: 'IgBLAST 的 AIRR 注释结果，以及每条代表序列已有的 UMI 支持数。',
        processing: '在同一样本、同一细胞 barcode 和同一链类型内，按 V、J 与 CDR3 归组并累加 UMI 支持。默认最多保留两个独立链候选，次要候选需满足支持比例条件；候选内再选择序列变体和代表注释。',
        output: '按原样本目录保存的链结果表，包含 barcode、AIRR 注释与 umi_counts；另输出筛选明细和 stage8_summary.csv 汇总表。',
        note: '“最多两个”针对每个细胞的每种链类型；该步骤整理已有代表序列，不重新构建共识序列。'
      }
    };
    const finalStage = finalStages[key];
    const finalSection = finalStage ? `<section class="pipeline-final-stage" aria-labelledby="pipeline-final-title"><span class="pipeline-final-label">最后一步</span><h3 id="pipeline-final-title">${esc(finalStage.title)}</h3><dl><dt>输入</dt><dd>${esc(finalStage.input)}</dd><dt>处理</dt><dd>${esc(finalStage.processing)}</dd><dt>输出</dt><dd>${esc(finalStage.output)}</dd></dl><p class="pipeline-final-note">${esc(finalStage.note)}</p></section>` : '';
    const representative = ['ir_split', '10x_split'].includes(key) ? '<p><strong>代表序列：</strong>reads 数最高者优先；仅在并列时按平均期望错误数更低、平均质量更高排序。质量缺失或仍并列时采用确定性规则，并记录混淆和备选序列。</p>' : '';
    $('#pipeline-help-content').innerHTML = `<p class="pipeline-help-summary">${esc(info.description)}</p><div class="flow-nodes" aria-label="分析步骤">${(info.stages || []).map((s, i) => `<span${finalStage && i === info.stages.length - 1 ? ' class="final-node"' : ''}>${esc(info.stage_labels?.[s] || s)}</span>`).join('<b aria-hidden="true">→</b>')}</div><p>${esc(details[key] || info.description)}</p>${finalSection}${representative}<p class="pipeline-help-review">首次运行完成 Match 后，请先审核样本匹配结果，再继续后续分析。</p>`;
  }
  $('#pipeline-help-open').addEventListener('click', () => { $('#help-pipeline').value = selectedPipeline(); renderHelp(); $('#pipeline-help').showModal(); });
  $('#pipeline-help-close').addEventListener('click', () => $('#pipeline-help').close());
  $('#pipeline-help').addEventListener('close', () => $('#pipeline-help-open').focus());
  $('#help-pipeline').addEventListener('change', renderHelp);

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
    visible.forEach((job, i) => { if (!isActive(job)) $('#jobs').children[i].querySelector('.row-actions').insertAdjacentHTML('beforeend', `<button class="button button-danger-ghost button-small" data-action="delete" data-id="${job.id}">删除</button>`); });
    $('#empty-state').classList.toggle('hidden', visible.length !== 0);
    const counts = data.counts || {}; $('#stat-active').textContent = counts.active ?? data.active_jobs ?? jobs.filter(isActive).length; $('#stat-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#stat-done').textContent = counts.done ?? jobs.filter(isDone).length; $('#stat-total').textContent = counts.total ?? data.total ?? jobs.length; $('#stat-capacity').textContent = `并行上限 ${data.max_active_jobs ?? '—'}`;
    $('#count-all').textContent = counts.total ?? data.total ?? jobs.length; $('#count-active').textContent = counts.active ?? jobs.filter(isActive).length; $('#count-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#count-done').textContent = counts.done ?? jobs.filter(isDone).length;
    const start = data.total ? data.offset + 1 : 0; const end = Math.min((data.offset || 0) + jobs.length, data.total || 0); $('#page-summary').textContent = data.total ? `显示 ${start}–${end} / 共 ${data.total} 条` : '暂无任务'; $('#page-prev').disabled = page === 0; $('#page-next').disabled = end >= (data.total || 0);
    document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => runAction(button.dataset.id, button.dataset.action)));
  }

  async function refreshJobs() { if (busy) return; busy = true; const params = new URLSearchParams({ limit: String(limit), offset: String(page * limit) }); if (filter === 'active') params.set('status', 'QUEUED,RUNNING,MATCHING,STOPPING'); if (filter === 'review') params.set('status', 'WAITING_REVIEW'); if (filter === 'done') params.set('status', 'SUCCEEDED,FAILED,STOPPED,INTERRUPTED,COMPLETED_WITHOUT_MARKER'); if ($('#pipeline-filter').value) params.set('pipeline', $('#pipeline-filter').value); if ($('#job-search').value.trim()) params.set('query', $('#job-search').value.trim()); try { const data = await Submission.api(`/api/jobs?${params}`); allJobs = data.jobs || []; renderJobs(data); } catch(error) { showMessage(error.message,true); } finally { busy = false; } }

  async function runAction(id, action) { if (action === 'confirm') { location.href = `/jobs/${id}#match`; return; } if (action === 'stop' && !window.confirm('停止这个任务？中间产物会保留，可稍后续跑。')) return; try { if(action === 'delete') { const job = allJobs.find(j => j.id === id); if(!job || !await Submission.deleteJob(job)) return; } else await Submission.api(`/api/jobs/${id}/${action}`, {}); await refreshJobs(); } catch(error) { alert(error.message); } }
  function showMessage(message, error = false) { const node = $('#form-message'); node.textContent = message; node.classList.toggle('error', error); clearTimeout(showMessage.timer); showMessage.timer = setTimeout(() => { node.textContent = ''; node.classList.remove('error'); }, 4500); }

  async function validatePaths() {
    const button=$('#validate-button'); button.disabled=true;
    try {
      const body=Object.fromEntries(new FormData($('#new-job-form')).entries());if(!body.barcode_csv)body.barcode_csv=null;
      const result=await Submission.api('/api/validate',body);
      $('#validation-summary').textContent=`路径可用 · Dataset: ${result.dataset} · 输出: ${result.output_root}`;
      const check=await Submission.api(`/api/preflight?pipeline=${encodeURIComponent(body.pipeline)}`);
      const missing=[...Object.entries(check.commands).filter(([,v])=>!v).map(([k])=>k),...check.missing_modules];
      $('#validation-summary').textContent+= missing.length ? `。环境缺项：${missing.join('、')}；请管理员补齐后再分析。` : '。入口工具和 Python 库检查通过；数据库仍需核对。';
    }catch(error){$('#validation-summary').textContent=error.message;}finally{button.disabled=false;}
  }

  $('#new-job-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.target.querySelector('button[type="submit"]'); button.disabled = true; button.classList.add('loading'); try { const body = Object.fromEntries(new FormData(event.target).entries()); if (!body.barcode_csv) body.barcode_csv = null; const result = await Submission.api('/api/jobs', body); resetForm(); closeDrawer(); location.href = `/jobs/${result.id}`; } catch(error) { showMessage(error.message, true); } finally { button.disabled = false; button.classList.remove('loading'); } });
  $('#open-drawer').addEventListener('click', openDrawer); $('#close-drawer').addEventListener('click', closeDrawer); $('#drawer-backdrop').addEventListener('click', closeDrawer); $('#validate-button').addEventListener('click', validatePaths); $('#pipeline').addEventListener('change', updatePipelineFields); $('#refresh-jobs').addEventListener('click', refreshJobs); $('#job-search').addEventListener('input', () => { page = 0; refreshJobs(); }); $('#pipeline-filter').addEventListener('change', () => { page = 0; refreshJobs(); }); $('#page-prev').addEventListener('click', () => { if (page > 0) { page -= 1; refreshJobs(); } }); $('#page-next').addEventListener('click', () => { page += 1; refreshJobs(); }); document.querySelectorAll('.field input').forEach((input) => input.addEventListener('input', () => { const state = document.querySelector(`.path-state[data-for="${input.name}"]`); if (state) state.classList.remove('ok', 'bad'); }));
  document.querySelectorAll('.browse-button').forEach((button) => button.addEventListener('click', () => openPicker(button.dataset.browseKind, button.dataset.browseTarget)));
  $('#close-file-picker').addEventListener('click', closePicker); $('#file-picker-cancel').addEventListener('click', closePicker); $('#file-picker-backdrop').addEventListener('click', closePicker); $('#file-picker-up').addEventListener('click', () => loadPicker($('#file-picker-up').disabled ? '' : $('#file-picker-up').dataset.path)); $('#file-picker-select').addEventListener('click', () => { if (pickerPath) choosePickerPath(pickerPath); });
  $('#file-picker-entries').addEventListener('click', (event) => { const entry = event.target.closest('.file-tree-entry'); if (!entry) return; const path = entry.dataset.path; if (entry.dataset.entryType === 'directory') { loadPicker(path); } else { choosePickerPath(path); } });
  document.querySelectorAll('.filter-tab').forEach((tab) => tab.addEventListener('click', () => { document.querySelectorAll('.filter-tab').forEach((item) => { item.classList.remove('active'); item.setAttribute('aria-selected', 'false'); }); tab.classList.add('active'); tab.setAttribute('aria-selected', 'true'); filter = tab.dataset.filter; page = 0; refreshJobs(); }));
  document.addEventListener('keydown', (event) => { if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return; if ($('#file-picker-shell').classList.contains('open')) closePicker(); else closeDrawer(); });
  let submissionDraft = null, submissionGeneration = 0;
  function acceptSubmission(data) { submissionDraft = data; $('[name="submission_revision"]').value = data.revision; $('[name="submission_path"]').required = false; $('#submission-status').textContent = `工作副本已就绪 · ${data.sheets.reduce((n, s) => n + s.rows.length, 0)} 行`; }
  function resetSubmission() { submissionGeneration++; submissionDraft = null; $('[name="submission_revision"]').value = ''; $('[name="submission_path"]').required = true; }
  async function loadSubmission(load) {
    const generation = submissionGeneration;
    $('#submission-edit').disabled = true;
    $('#submission-status').textContent = '正在读取 Submission…';
    try {
      const data = await load();
      if (generation !== submissionGeneration) return;
      submissionDraft = data;
      $('#submission-status').textContent = '工作副本已载入，可再次查看 / 编辑；请保存并使用后创建任务';
      Submission.open(data, acceptSubmission);
    } catch(error) {
      if (generation === submissionGeneration) $('#submission-status').textContent = `读取失败：${error.message}`;
    } finally { $('#submission-edit').disabled = false; }
  }
  $('#submission-edit').onclick = () => loadSubmission(async () => {
    if (submissionDraft) return submissionDraft;
    const token = $('[name="submission_revision"]').value;
    if (token) return Submission.api(`/api/submissions?revision=${encodeURIComponent(token)}`);
    const path = $('[name="submission_path"]').value.trim();
    if (!path) throw new Error('请先选择服务器上的 Submission 文件 / 目录');
    return Submission.api('/api/submissions/import', {path});
  });
  $('[name="submission_path"]').addEventListener('input', () => { resetSubmission(); $('#submission-status').textContent = '路径已变更，可查看 / 编辑新选择的 Submission'; });
  $('[name="ir_variant"]').addEventListener('change', updatePipelineFields);
  loadPipelines().then(() => { Object.entries(pipelineInfo).forEach(([key, info]) => { $('#pipeline-filter').insertAdjacentHTML('beforeend', `<option value="${key}">${esc(info.label)}</option>`); }); renderPipelineCards(); resetForm(); refreshJobs(); }).catch(error => showMessage(error.message, true));
  window.addEventListener('pageshow', event => { if(event.persisted) { resetForm(); closeDrawer(); refreshJobs(); } });
  setInterval(refreshJobs, 5000);
})();

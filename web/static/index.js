(() => {
  const $ = (selector) => document.querySelector(selector);
  const statusLabel = { QUEUED: '排队中', RUNNING: '运行中', MATCHING: '匹配中', WAITING_REVIEW: '待审核', SUCCEEDED: '已完成', ARCHIVING: '归档中', ARCHIVED: '已归档', FAILED: '失败', STOPPED: '已停止', STOPPING: '停止中', INTERRUPTED: '已中断', COMPLETED_WITHOUT_MARKER: '需检查' };
  const activeStates = new Set(['QUEUED', 'RUNNING', 'MATCHING', 'STOPPING', 'ARCHIVING']);
  const stoppableStates = new Set(['QUEUED', 'RUNNING', 'MATCHING']);
  const doneStates = new Set(['SUCCEEDED', 'ARCHIVED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'COMPLETED_WITHOUT_MARKER']);
  let pipelineInfo = {}, allJobs = [], filter = 'all', page = 0, limit = 50, busy = false, refreshPending = false;
  const jobActions = new Map(), jobActionErrors = new Map();
  let failedAction = null, pollTimer = null, activeJobCount = 0;

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const statusText = (value) => statusLabel[value] || value;
  const isActive = (job) => activeStates.has(job.status);
  const isDone = (job) => doneStates.has(job.status);
  let formDefaults = {};
  let scanGeneration = 0, scannedInput = '', validationGeneration = 0, lastJobsUpdate = '';
  const pipelineDescriptions = {ir_split:'Bulk IR FASTQ → 样本 UMI 与免疫指标', '10x_split':'单细胞 FASTQ → 细胞链与 UMI 结果', igblast_base:'通用 FASTQ → IgBLAST 注释', pig_igblast:'猪 FASTQ → 专属参考库注释', mapping:'免疫组库 CSV → 各样本 UMI count'};
  function fieldFeedback(name, message, error = false) {
    const input = document.querySelector(`[name="${name}"]`); if (!input || input.disabled) return;
    input.setAttribute('aria-invalid', String(error));
    const field = input.closest('.field'); if (!field) return;
    let feedback = field.querySelector('.field-feedback');
    if (!feedback) { feedback = document.createElement('small'); feedback.className = 'field-feedback'; feedback.id = `feedback-${name}`; feedback.setAttribute('role','status'); field.append(feedback); input.setAttribute('aria-describedby', feedback.id); }
    feedback.textContent = message; feedback.classList.toggle('error', error);
    const state = field.querySelector('.path-state'); if (state) { state.classList.toggle('ok', Boolean(message) && !error); state.classList.toggle('bad', error); state.setAttribute('aria-hidden','true'); }
  }
  function invalidateValidation() {
    validationGeneration++;
    ['input_path','submission_path','barcode_csv'].forEach(name => fieldFeedback(name, ''));
    $('#validation-summary').textContent = '配置已变更，请检查后创建。'; $('#validation-details').classList.add('hidden');
    $('#form-message').textContent = '';
  }
  function focusInvalidField(){const input=$('#new-job-form [aria-invalid="true"]');if(!input)return;for(let node=input.parentElement;node&&node.id!=='new-job-form';node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;input.focus();}
  function validationError(error) {
    let details; try { details = JSON.parse(error.message); } catch { /* Plain server error. */ }
    const issues = details?.errors || [];
    issues.forEach(issue => fieldFeedback(issue.field, issue.message || issue.code, true));
    const text = issues.map(issue => issue.message || issue.code).filter(Boolean).join('；') || details?.message || error.message;
    $('#validation-summary').textContent = text; showMessage(text, true);
    focusInvalidField();
  }
  function connectionError(error) { $('#jobs-connection span').textContent = `连接暂时中断，${lastJobsUpdate ? `当前显示为 ${lastJobsUpdate} 更新的数据` : '尚未获取任务列表'}。${error.message || ''}`; $('#jobs-connection').classList.remove('hidden'); }
  function mappingSelection() { return [...document.querySelectorAll('#mapping-chains input:checked')].map(n => n.value); }
  function mappingSampleSelection() { return [...document.querySelectorAll('#mapping-samples input:checked')].map(n => n.value); }
  function syncMappingSubmit() {
    const chains = mappingSelection(), samples = mappingSampleSelection();
    let fileCount = 0, sampleCount = 0;
    document.querySelectorAll('#mapping-samples input').forEach(n => {
      const counts = JSON.parse(n.dataset.chainCounts || '{}');
      const count = chains.reduce((sum, chain) => sum + (counts[chain] ?? (JSON.parse(n.dataset.chains).includes(chain) ? 1 : 0)), 0);
      if (n.checked && count) { sampleCount++; fileCount += count; }
      n.closest('label').classList.toggle('out-of-scope', n.checked && !count);
      n.closest('label').querySelector('.sample-scope').textContent = n.checked && !count ? '本次不处理（不含所选链）' : '';
    });
    const matches = fileCount > 0;
    $('#mapping-sample-count').textContent = `已选 ${samples.length} / ${document.querySelectorAll('#mapping-samples input').length} 个样本`;
    $('#mapping-scope').textContent = !scannedInput ? '' : !chains.length ? '请选择要处理的链。' : !samples.length ? '请选择至少一个样本。' : matches ? `实际处理 ${sampleCount} 个样本、${fileCount} 个样本/链文件。` : '所选样本与链没有交集，请调整选择。';
    $('#mapping-scope').classList.toggle('error', Boolean(scannedInput) && !matches);
    $('#new-job-form button[type="submit"]').disabled = !DirectoryInput.ready() || selectedPipeline() === 'mapping' && (!scannedInput || scannedInput !== $('[name="input_path"]').value.trim() || !chains.length || !samples.length || !matches);
  }
  function clearMappingScan() {
    invalidateValidation();
    scanGeneration++; scannedInput = '';
    $('#mapping-chains').textContent = '';
    $('#mapping-samples').textContent = '';
    $('#mapping-scan-status').textContent = '先扫描目录，再选择要处理的链和样本。';
    $('#mapping-scan').disabled = false;
    $('#mapping-ignored').classList.add('hidden');
    syncMappingSubmit();
  }
  function taskBody(form) {
    const body = Object.fromEntries(new FormData(form).entries());
    if (body.pipeline === 'mapping') {
      body.mapping_chains = mappingSelection();
      body.mapping_samples = mappingSampleSelection();
      body.submission_path = ''; body.submission_revision = '';
      if (!scannedInput || scannedInput !== body.input_path.trim() || !body.mapping_chains.length || !body.mapping_samples.length) throw new Error('请先扫描输入目录并选择至少一条链和一个样本');
    }
    if (DirectoryInput.active()) Object.assign(body,DirectoryInput.body());
    if (!pipelineInfo[selectedPipeline()]?.supports_directory_input) delete body.sample_source;
    if (!body.barcode_csv) body.barcode_csv = null;
    return body;
  }
  function resetForm() {
    $('#new-job-form').reset();
    DirectoryInput.reset();
    $('[name=input_path]').readOnly=false;
    $('#drawer-title').textContent='新建任务';
    document.querySelectorAll('#new-job-form .task-advanced').forEach(node=>node.open=false);
    if (pipelineInfo.ir_split) $('#pipeline').value = 'ir_split';
    resetSubmission();
    clearMappingScan();
    $('[name="barcode_csv"]').value = '';
    invalidateValidation();
    $('#submission-status').textContent = '可编辑 Note 路径，原文件不变。';
    $('#validation-summary').textContent = '填写路径后可验证任务配置';
    $('#form-message').textContent = '';
    clearTimeout(showMessage.timer);
    document.querySelectorAll('.path-state').forEach(n => n.classList.remove('ok', 'bad'));
    updatePipelineFields();
  }
  const browseLabels = { input: '原始数据目录', submission: 'Submission 文件或目录', barcode: 'Barcode CSV', output: '输出根目录' };
  let pickerKind = '', pickerTarget = '', pickerPath = '', pickerBusy = false;

  let pickerReturnFocus=null;
  function closePicker() { $('#drawer-shell').inert = false; $('#drawer-shell').setAttribute('aria-hidden', String(!$('#drawer-shell').classList.contains('open'))); pickerReturnFocus?.focus(); $('#file-picker-shell').classList.remove('open'); $('#file-picker-shell').setAttribute('aria-hidden', 'true'); pickerKind = ''; pickerTarget = ''; pickerPath = ''; }
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
  function openPicker(kind, target) { pickerKind = kind; pickerTarget = target; $('#file-picker-title').textContent = `选择${browseLabels[kind] || '路径'}`; pickerReturnFocus=document.activeElement;$('#file-picker-shell').classList.add('open'); $('#file-picker-shell').setAttribute('aria-hidden', 'false'); $('#close-file-picker').focus(); $('#drawer-shell').inert = true; $('#drawer-shell').setAttribute('aria-hidden', 'true'); loadPicker(); }
  function choosePickerPath(path) { const input = document.querySelector(`[name="${pickerTarget}"]`); if (input) { input.value = path; input.dispatchEvent(new Event('input', { bubbles: true })); } closePicker(); }

  function openDrawer() { resetForm(); document.body.classList.add('task-dialog-open'); $('#drawer-shell').classList.add('open'); $('#drawer-shell').setAttribute('aria-hidden', 'false'); $('.app-shell').inert = true; setTimeout(() => { $('#pipeline-cards .selected')?.focus({preventScroll:true}); document.querySelectorAll('#drawer-shell .drawer-body,.task-type-panel,.task-form-scroll').forEach(node=>node.scrollTop=0); }, 120); }
  function closeDrawer() { if ($('#file-picker-shell').classList.contains('open')) closePicker(); $('.app-shell').inert = false; $('#open-drawer').focus(); document.body.classList.remove('task-dialog-open'); $('#drawer-shell').classList.remove('open'); $('#drawer-shell').setAttribute('aria-hidden', 'true'); }
  function selectedPipeline() { return $('#pipeline').value; }
  async function loadPipelines() {
    pipelineInfo = await Submission.api('/api/pipelines');
    const defaults = await Submission.api('/api/defaults');
    formDefaults = defaults;
    $('#barcode-hint').textContent = `留空使用默认 Barcode：${defaults.barcode_csv || '由服务器配置'}`;
    $('#output-hint').textContent = `${defaults.output_base}/姓名_短编号/Pipeline类型/YYYYMMDD_HHMMSS/（北京时间）`;
    $('#pipeline').innerHTML = Object.entries(pipelineInfo).map(([key, info]) => `<option value="${esc(key)}">${esc(info.label)}</option>`).join('');
    $('#pipeline').value = 'ir_split';
    $('#help-pipeline').innerHTML = $('#pipeline').innerHTML;
    $('#pipeline-help-open').disabled = false;
    updatePipelineFields();
  }

  function renderPipelineCards() {
    const cards = Object.entries(pipelineInfo).map(([key, info]) => `<button type="button" class="pipeline-card ${key === selectedPipeline() ? 'selected' : ''}" data-pipeline="${key}" aria-pressed="${key === selectedPipeline()}"><span class="pipeline-dot ${info.accent || 'teal'}"></span><span class="pipeline-card-copy"><strong>${esc(info.label)}</strong><small>${esc(pipelineDescriptions[key] || info.description)}</small></span><span class="pipeline-card-check" aria-hidden="true">✓</span></button>`).join('');
    $('#pipeline-cards').innerHTML = cards;
    document.querySelectorAll('.pipeline-card').forEach((card) => card.addEventListener('click', () => { $('#pipeline').value = card.dataset.pipeline; updatePipelineFields(); }));
  }

  function updatePipelineFields() {
    invalidateValidation();
    const info = pipelineInfo[selectedPipeline()] || {};
    const mapping = selectedPipeline() === 'mapping';
    const directory=DirectoryInput.active();
    $('#sample-source-field').classList.toggle('hidden',!info.supports_directory_input);
    $('#sample-source-field').disabled=!info.supports_directory_input||Boolean(DirectoryInput.editingJob());
    $('[data-browse-target=input_path]').disabled=Boolean(DirectoryInput.editingJob());
    $('#directory-fields').classList.toggle('hidden',!directory);$('#directory-fields').disabled=!directory;
    DirectoryInput.setContext();
    $('#task-selected-name').textContent=info.label||'选择流程';
    $('#task-selected-description').textContent=pipelineDescriptions[selectedPipeline()]||'选择服务器上的目录或文件';
    $('#mapping-fields').classList.toggle('hidden', !mapping);
    $('#mapping-fields').disabled = !mapping;
    $('.submission-field').classList.toggle('hidden', mapping||directory);
    $('.submission-field').querySelectorAll('input,button').forEach(n => n.disabled = mapping||directory);
    $('[name="submission_revision"]').disabled = mapping||directory;
    if (mapping||directory) { resetSubmission(); $('[name="submission_path"]').required = false; }
    else $('[name="submission_path"]').required = !$('[name="submission_revision"]').value;
    $('[name="input_path"]').closest('.field').querySelector('.field-label').textContent = mapping ? '免疫组库 CSV 目录' : '原始数据目录';
    $('#new-job-form button[type="submit"] span').textContent = mapping ? '创建任务并开始分析' : '创建任务并执行 Match';
    if(DirectoryInput.editingJob())$('#new-job-form button[type="submit"] span').textContent='保存配置并重新 Match';
    $('#create-next-step').textContent = mapping ? '按所选样本和链执行分析，最终查看 UMI count。' : '先匹配并核对样本，再开始分析。';
    syncMappingSubmit();
    $('#barcode-row').classList.toggle('hidden', !info.requires_barcode);
    $('[name="barcode_csv"]').disabled = !info.requires_barcode;
    document.querySelectorAll('.pipeline-card').forEach((card) => {
      card.disabled=Boolean(DirectoryInput.editingJob());
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
    const teaching = {
      mapping: {input:'Human 免疫组库 CSV', output:'按样本与链整理的 UMI count 结果', folder:'remapping/input', expected:'识别 Demo_A、Demo_B 两个样本，IGH、IGK 两种链，共 3 个文件、6 条输入序列。默认全选样本；只选 IGK 时应处理 Demo_A 的 1 个文件。'},
      ir_split: {input:'Bulk IR 配对 FASTQ + Submission + Barcode', output:'样本 UMI、链结果与 Datapoint.csv 指标', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      '10x_split': {input:'单细胞配对 FASTQ + Submission + Barcode', output:'按细胞 Barcode 整理的链与 UMI 结果', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      igblast_base: {input:'配对 FASTQ + Submission', output:'IgBLAST AIRR 注释与比对汇总', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      pig_igblast: {input:'猪配对 FASTQ + Species=Pig 的 Submission', output:'使用猪参考库生成的 IgBLAST 注释', folder:'fastq/input', expected:'使用 pig_igblast.xlsx，保持 Species=Pig。将 Note 改为 fastq/input 的服务器绝对路径；Match 应识别 2 对 FASTQ。'}
    };
    const guide=teaching[key] || {input:info.description,output:'分析结果',folder:'',expected:''};
    const isMapping=key==='mapping';
    let steps=isMapping ? ['下载并解压示例包，将 remapping/input 放到服务器允许的输入目录。','新建 reMapping 任务，选择该目录，点击“扫描输入目录”；选择样本与链。','检查配置后创建任务，在结果页查看样本状态、条数及 UMI count 文件。'] : ['下载并解压示例包，将 fastq/input 和 submission 放到服务器允许的目录。',`选择 ${info.label}；填写输入目录，选择 submission/${key}.xlsx；通过“查看 / 编辑”替换 Note 路径。${info.requires_barcode ? '在可选设置中选择包内 barcode.csv。' : ''}`,'检查配置并创建任务；Match 完成后核对 2 个样本的匹配记录。教学 FASTQ 建议练习到审核步骤后停止任务。'];
    if(info.supports_directory_input){
      steps=['下载并解压示例包，将 fastq/input 放到服务器允许的输入目录。',`新建 ${info.label}，样本信息来源选择“扫描目录，无需 Submission”；扫描后全选有效配对，统一设置 Chain，也可逐样本修改。`,'检查配置后创建任务；核对 Match 的样本、Chain 和配对路径。教学 FASTQ 建议练习到审核步骤后停止任务。'];
      guide.expected='应识别 2 个有效配对，默认候选名为 Demo_A_A01_L01 和 Demo_B_A02_L01；可改成 Demo_A、Demo_B。指定 IGH 后应提交 2 个样本、2 对 FASTQ，无需工作簿。';
    }
    $('#pipeline-help-content').innerHTML = `<section class="guide-intro"><span class="guide-kicker">流程说明</span><h3>${esc(info.label)}</h3><p>${esc(pipelineDescriptions[key] || info.description)}</p><div class="guide-facts"><div><span>需要准备</span><strong>${esc(guide.input)}</strong></div><div><span>正式分析主要结果</span><strong>${esc(guide.output)}</strong></div></div></section>
      <section class="guide-section"><h4>处理步骤 <span>${(info.stages || []).length} 个阶段</span></h4><ol class="guide-flow">${(info.stages || []).map((stage,i)=>`<li><span>${String(i+1).padStart(2,'0')}</span>${esc(info.stage_labels?.[stage] || stage)}</li>`).join('')}</ol><p class="guide-review">${isMapping ? '无需 Submission 和 Match 审核；先扫描目录，按所选样本与链提交。' : 'Match 完成后需要审核样本匹配结果，再继续正式分析。'}</p></section>
      <section class="guide-demo" aria-labelledby="guide-demo-title"><div class="guide-demo-heading"><div><span class="guide-kicker">动手练习</span><h4 id="guide-demo-title">合成教学数据 <span>v1 · 操作练习</span></h4></div><a class="button button-primary button-small" href="/static/demos/scigblast-teaching-v1.zip" download>下载示例包 ZIP ↓</a></div><p>包含 2 个样本的 CSV、配对 FASTQ、4 份 Submission 模板及 Barcode CSV。</p><ol class="guide-instructions">${steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><div class="guide-expected"><strong>练习检查点</strong><p>${esc(guide.expected)}</p></div><p class="guide-demo-limit">合成数据用于学习格式、选择与提交流程，不是完整生物学验证数据。FASTQ 未模拟 IR / 10X 建库结构；继续分析可能为空或失败。reMapping 的注释可为空，不能以输入条数推断最终结果。</p><a class="guide-readme" href="/static/demos/README.txt" download>下载详细教程与文件清单 TXT ↗</a></section>
      <details class="guide-technical"><summary>了解处理逻辑与结果字段</summary><p>${esc(details[key] || info.description)}</p>${finalSection}${representative}${isMapping ? '<p>CSV 文件名：样本__链.csv；必需列：CDR3(pep)、copy、joinedSeq。umi_count 从序列标识末尾还原原始 copy；结果条数不等于 copy 总和。</p>' : ''}</details>`;
    $('#help-pipeline-nav').innerHTML=Object.entries(pipelineInfo).map(([id,pipeline])=>`<button type="button" data-help-pipeline="${esc(id)}" aria-pressed="${id===key}"><strong>${esc(pipeline.label)}</strong><small>${esc(pipelineDescriptions[id] || pipeline.description)}</small></button>`).join('');
    $('#pipeline-help-content').scrollTop=0;

  }
  $('#pipeline-help-open').addEventListener('click', () => { $('#help-pipeline').value = selectedPipeline(); renderHelp(); $('#pipeline-help').showModal(); });
  $('#pipeline-help-close').addEventListener('click', () => $('#pipeline-help').close());
  $('#pipeline-help').addEventListener('close', () => $('#pipeline-help-open').focus());
  $('#help-pipeline').addEventListener('change', renderHelp);
  $('#help-pipeline-nav').addEventListener('click', event => { const button=event.target.closest('[data-help-pipeline]');if(!button)return;const key=button.dataset.helpPipeline;$('#help-pipeline').value=key;renderHelp();$('#help-pipeline-nav').querySelector(`[data-help-pipeline="${key}"]`)?.focus(); });
  $('#help-create-task').addEventListener('click', () => {const key=$('#help-pipeline').value;$('#pipeline-help').close();openDrawer();$('#pipeline').value=key;updatePipelineFields();});

  function formatTime(value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  function renderJobs(data) {
    const jobs = data.jobs || [];
    const focused = document.activeElement?.closest('#jobs [data-action]');
    const focusedAction = focused ? {id:focused.dataset.id, action:focused.dataset.action} : null;
    const operatorSelect = $('#operator-filter'), selectedOperator = operatorSelect.value;
    operatorSelect.innerHTML = '<option value="">全部操作者</option>' + [...new Set([...(data.operators || []), selectedOperator].filter(Boolean))].map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
    operatorSelect.value = selectedOperator;
    // Filtering and pagination are both server-side; filtering this page again
    // would hide valid rows and make the page count disagree with the API.
    const visible = jobs;
    $('#jobs').innerHTML = visible.map((job) => `<tr data-job-id="${esc(job.id)}">
      <td><a class="job-link" href="/jobs/${job.id}"><strong>${esc(job.dataset)}</strong><span class="job-id">${esc(job.id)}</span></a></td>
      <td><span class="pipeline-chip ${job.pipeline}"><i></i>${esc(job.pipeline_label)}</span></td>
      <td><span class="stage-name">${esc(job.execution_phase === 'waiting_resource' ? '等待运行资源' : job.stage_labels?.[job.current_stage] || job.current_stage || '等待启动')}</span>${job.status === 'ARCHIVING' ? '<small>归档完成后可下载</small>' : ''}</td>
      <td><div class="progress-cell"><div class="progress-track"><span style="width:${Math.max(0, Math.min(100, job.progress))}%"></span></div><b>${job.progress}%</b></div></td>
      <td><span class="operator-name">${esc(job.operator_display_name||job.operator)}</span><small class="time-text">${formatTime(job.started_at || job.created_at)}</small></td>
      <td><span class="status-badge ${job.status.toLowerCase()}"><i></i>${esc(statusText(job.status))}</span>${job.last_error ? `<small class="error-text" title="${esc(job.last_error)}">${esc(job.last_error)}</small>` : ''}</td>
      <td class="row-actions">${job.status === 'WAITING_REVIEW' ? `<button class="button button-review button-small" data-action="confirm" data-id="${job.id}">核对样本</button>` : ''}${stoppableStates.has(job.status) ? `<button class="button button-danger-ghost button-small" data-action="stop" data-id="${job.id}">停止</button>` : ''}<a class="button button-ghost button-small" href="/jobs/${job.id}">${['SUCCEEDED','ARCHIVED'].includes(job.status) ? '查看结果' : ['FAILED','INTERRUPTED','COMPLETED_WITHOUT_MARKER'].includes(job.status) ? '查看原因 / 续跑' : '详情'}</a></td>
    </tr>`).join('');
    visible.forEach((job, i) => { if (!isActive(job)) $('#jobs').children[i].querySelector('.row-actions').insertAdjacentHTML('beforeend', `<button class="button button-danger-ghost button-small" data-action="delete" data-id="${job.id}">删除</button>`); });
    $('#empty-state').classList.toggle('hidden', visible.length !== 0);
    const counts = data.counts || {}; $('#stat-active').textContent = counts.active ?? data.active_jobs ?? jobs.filter(isActive).length; $('#stat-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#stat-done').textContent = counts.done ?? jobs.filter(isDone).length; $('#stat-total').textContent = counts.total ?? data.total ?? jobs.length; $('#stat-capacity').textContent = `并行上限 ${data.max_active_jobs ?? '—'}`;
    $('#count-all').textContent = counts.total ?? data.total ?? jobs.length; $('#count-active').textContent = counts.active ?? jobs.filter(isActive).length; $('#count-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#count-done').textContent = counts.done ?? jobs.filter(isDone).length;
    const start = data.total ? data.offset + 1 : 0; const end = Math.min((data.offset || 0) + jobs.length, data.total || 0); $('#page-summary').textContent = data.total ? `当前筛选 ${start}–${end} / 共 ${data.total} 条任务` : '当前筛选无任务'; $('#page-prev').disabled = page === 0; $('#page-next').disabled = end >= (data.total || 0);
    document.querySelectorAll('#jobs [data-action]').forEach((button) => button.addEventListener('click', () => runAction(button.dataset.id, button.dataset.action)));
    visible.forEach(job => renderActionState(job.id));
    if (focusedAction && !document.querySelector('dialog[open]')) actionButton(focusedAction)?.focus({preventScroll:true});
  }

  function actionButton({id, action}) {
    return [...document.querySelectorAll('#jobs [data-action]')].find(button => button.dataset.id === id && button.dataset.action === action);
  }
  function renderActionState(id) {
    const row = [...$('#jobs').children].find(node => node.dataset.jobId === id);
    if (!row) return;
    const pending = jobActions.get(id), error = jobActionErrors.get(id);
    row.querySelectorAll('[data-action]').forEach(button => {
      UI.setBusy(button, false);
      button.disabled = false;
      if (pending) {
        if (button.dataset.action === pending.action) UI.setBusy(button, true, pending.phase === 'confirm' ? '等待确认…' : pending.action === 'stop' ? '正在停止…' : '正在删除…');
        else button.disabled = true;
      }
    });
    row.querySelector('.row-action-error')?.remove();
    if (error) {
      const note = document.createElement('small'); note.className = 'row-action-error'; note.textContent = error;
      row.children[5].append(note);
    }
  }
  function scheduleJobsRefresh() {
    clearTimeout(pollTimer);
    if (!document.hidden) pollTimer = setTimeout(refreshJobs, activeJobCount > 0 ? 5000 : 30000);
  }

  function jobsQuery() {
    const params = new URLSearchParams({limit:String(limit),offset:String(page*limit)});
    if(filter==='active')params.set('status',[...activeStates].join(','));
    if(filter==='review')params.set('status','WAITING_REVIEW');
    if(filter==='done')params.set('status',[...doneStates].join(','));
    for(const [name,selector] of [['pipeline','#pipeline-filter'],['operator','#operator-filter'],['query','#job-search']])if($(selector).value.trim())params.set(name,$(selector).value.trim());
    return params.toString();
  }
  async function refreshJobs() {
    clearTimeout(pollTimer);
    if(document.hidden)return;
    if(busy){refreshPending=true;return;}busy=true;const requested=jobsQuery();
    try{
      const data=await Submission.api(`/api/jobs?${requested}`);
      if(jobsQuery()!==requested){refreshPending=true;return;}
      allJobs=data.jobs||[];activeJobCount=data.counts?.active ?? data.active_jobs ?? allJobs.filter(isActive).length;renderJobs(data);lastJobsUpdate=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
      $('#jobs-updated').textContent=`最近更新 ${lastJobsUpdate}`;$('#jobs-connection').classList.add('hidden');
    }catch(error){connectionError(error);}finally{busy=false;if(refreshPending){refreshPending=false;refreshJobs();}else scheduleJobsRefresh();}
  }

  async function runAction(id, action) {
    if (jobActions.has(id)) return;
    if (action === 'confirm') { location.href = `/jobs/${id}#match`; return; }
    const job = allJobs.find(j => j.id === id) || (failedAction?.id === id ? failedAction.job : null);
    if (!job) return;
    jobActions.set(id, {action, phase:'confirm'}); jobActionErrors.delete(id); renderActionState(id);
    if (failedAction?.id === id) { failedAction = null; $('#jobs-action-message').classList.add('hidden'); }
    try {
      if (action === 'delete') {
        if (!await Submission.deleteJob(job)) return;
      } else {
        if (action === 'stop' && !await UI.confirm({title:'停止任务', message:`${job.dataset}\n停止后保留中间产物，可稍后续跑。`, confirmLabel:'停止任务', danger:true})) return;
        jobActions.set(id, {action, phase:'request'}); renderActionState(id);
        await Submission.api(`/api/jobs/${id}/${action}`, {});
      }
      await refreshJobs();
    } catch(error) {
      jobActionErrors.set(id, error.message); failedAction = {id, action, job};
      $('#jobs-action-message span').textContent = `${job.dataset}：${error.message}`;
      $('#jobs-action-message').classList.remove('hidden');
    } finally {
      jobActions.delete(id); renderActionState(id);
      if (!document.querySelector('dialog[open]') && document.activeElement === document.body) (actionButton({id, action}) || $('#refresh-jobs')).focus({preventScroll:true});
    }
  }
  function showMessage(message, error = false) { const node = $('#form-message'); node.textContent = message; node.classList.toggle('error', error); clearTimeout(showMessage.timer); if (!error) showMessage.timer = setTimeout(() => { node.textContent = ''; node.classList.remove('error'); }, 4500); }

  async function validatePaths() {
    const button=$('#validate-button'), generation=validationGeneration; button.disabled=true;
    try {
      const body=taskBody($('#new-job-form'));
      const result=await Submission.api('/api/validate',body);
      if(generation!==validationGeneration)return;
      ['input_path','submission_path','barcode_csv'].forEach(name=>fieldFeedback(name, name==='barcode_csv'&&!body.barcode_csv ? `使用默认 Barcode：${result.barcode_csv || formDefaults.barcode_csv || '服务器默认值'}` : '✓ 路径检查通过'));
      const issues=[...(result.errors||[]),...(result.warnings||[])];
      $('#validation-summary').textContent=result.valid===false?'检查发现问题，请按下方提示调整。':`${result.dataset} · ${body.pipeline==='mapping'?'可创建并开始分析':'可创建任务，下一步核对样本匹配结果'}。${body.pipeline==='ir_split'?' IR 输入类型会在 Match 时自动识别。':''}`;
      $('#validation-details').classList.toggle('hidden', !issues.length);
      $('#validation-details div').textContent=issues.map(i=>i.message||i.code||String(i)).join('；');
      (result.errors||[]).forEach(i=>fieldFeedback(i.field,i.message||i.code,true));
      if(result.valid===false){$('#validation-details').open=true;focusInvalidField();}
    }catch(error){if(generation===validationGeneration)validationError(error);}finally{button.disabled=false;}
  }

  $('#new-job-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.target.querySelector('button[type="submit"]'); button.disabled = true; button.classList.add('loading'); try { const body = taskBody(event.target); const result = await Submission.api(DirectoryInput.editingJob()?`/api/jobs/${DirectoryInput.editingJob()}/input-rematch`:'/api/jobs', body); resetForm(); closeDrawer(); location.href = `/jobs/${result.id}`; } catch(error) { validationError(error); } finally { button.disabled = false; button.classList.remove('loading'); syncMappingSubmit(); } });
  $('[name="input_path"]').addEventListener('input', clearMappingScan);
  $('#mapping-chains').addEventListener('change', syncMappingSubmit);
  DirectoryInput.init(() => {syncMappingSubmit();invalidateValidation();});
  document.querySelectorAll('[name=sample_source]').forEach(n=>n.addEventListener('change',updatePipelineFields));
  $('[name=input_path]').addEventListener('input',()=>DirectoryInput.setContext());
  $('#mapping-samples').addEventListener('change', syncMappingSubmit);
  $('#new-job-form').addEventListener('input', invalidateValidation);
  $('#new-job-form').addEventListener('change', invalidateValidation);
  $('#jobs-retry').onclick=refreshJobs;
  $('#jobs-action-retry').onclick=() => { if (failedAction) runAction(failedAction.id, failedAction.action); };
  $('#jobs-action-dismiss').onclick=() => { if (failedAction) { const {id}=failedAction; jobActionErrors.delete(id); renderActionState(id); } failedAction=null; $('#jobs-action-message').classList.add('hidden'); };
  $('#mapping-samples-all').onclick = () => { document.querySelectorAll('#mapping-samples input').forEach(n => n.checked = true); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-samples-none').onclick = () => { document.querySelectorAll('#mapping-samples input').forEach(n => n.checked = false); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-all').onclick = () => { document.querySelectorAll('#mapping-chains input').forEach(n => n.checked = true); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-none').onclick = () => { document.querySelectorAll('#mapping-chains input').forEach(n => n.checked = false); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-scan').onclick = async () => {
    clearMappingScan(); const generation = scanGeneration, path = $('[name="input_path"]').value.trim();
    $('#mapping-scan').disabled = true; $('#mapping-scan-status').textContent = '正在扫描 CSV 文件…';
    try {
      const result = await Submission.api('/api/mapping/scan', {input_path:path});
      if (generation !== scanGeneration) return;
      scannedInput = path;
      $('#mapping-chains').innerHTML = result.chains.map(c => `<label><input type="checkbox" value="${esc(c.chain)}" ${c.chain === 'IGH' ? 'checked' : ''}> ${esc(c.chain)} · ${c.file_count} 个文件</label>`).join('');
      $('#mapping-samples').innerHTML = (result.samples||[]).map(s=>`<label><input type="checkbox" value="${esc(s.sample_key)}" data-chains="${esc(JSON.stringify(s.chains))}" data-chain-counts="${esc(JSON.stringify(s.chain_counts||{}))}" checked><span><strong>${esc(s.sample_id)}</strong><small>${esc(s.sample_key)} · ${esc(s.chains.join(', '))} · ${s.file_count} 个文件</small><small class="sample-scope"></small></span></label>`).join('');
      $('#mapping-scan-status').textContent = result.chains.length ? `发现 ${(result.samples||[]).length} 个样本、${result.chains.length} 种链，未识别命名 ${result.unrecognized_files} 个 CSV。${result.chains.some(c=>c.chain==='IGH')?'默认选择 IGH，样本全部选中。':'未发现 IGH，请选择要处理的链。'}` : '未找到有效链；文件需命名为 样本__链.csv。';
      $('#mapping-ignored').classList.toggle('hidden', !result.unrecognized_files);
      $('#mapping-ignored ul').innerHTML=(result.ignored_files||[]).map(f=>`<li>${esc(f)}：文件名或链不符合 样本__链.csv 约定。</li>`).join('');
    } catch (error) { if (generation === scanGeneration) $('#mapping-scan-status').textContent = error.message; }
    finally { if (generation === scanGeneration) { $('#mapping-scan').disabled = false; syncMappingSubmit(); } }
  };
  let searchTimer; $('#operator-filter').addEventListener('change', () => { page = 0; refreshJobs(); });
  $('#cancel-new-job').addEventListener('click', closeDrawer);
  $('#open-drawer').addEventListener('click', openDrawer); $('#close-drawer').addEventListener('click', closeDrawer); $('#drawer-backdrop').addEventListener('click', closeDrawer); $('#validate-button').addEventListener('click', validatePaths); $('#pipeline').addEventListener('change', updatePipelineFields); $('#refresh-jobs').addEventListener('click', refreshJobs); $('#job-search').addEventListener('input', () => { page = 0; clearTimeout(searchTimer); searchTimer = setTimeout(refreshJobs, 300); }); $('#job-search').addEventListener('keydown', (event) => { if (event.key === 'Enter') { clearTimeout(searchTimer); page = 0; refreshJobs(); } }); $('#pipeline-filter').addEventListener('change', () => { page = 0; refreshJobs(); }); $('#page-prev').addEventListener('click', () => { if (page > 0) { page -= 1; refreshJobs(); } }); $('#page-next').addEventListener('click', () => { page += 1; refreshJobs(); }); document.querySelectorAll('.field input').forEach((input) => input.addEventListener('input', () => { const state = document.querySelector(`.path-state[data-for="${input.name}"]`); if (state) state.classList.remove('ok', 'bad'); }));
  document.querySelectorAll('.browse-button').forEach((button) => button.addEventListener('click', () => openPicker(button.dataset.browseKind, button.dataset.browseTarget)));
  $('#close-file-picker').addEventListener('click', closePicker); $('#file-picker-cancel').addEventListener('click', closePicker); $('#file-picker-backdrop').addEventListener('click', closePicker); $('#file-picker-up').addEventListener('click', () => loadPicker($('#file-picker-up').disabled ? '' : $('#file-picker-up').dataset.path)); $('#file-picker-select').addEventListener('click', () => { if (pickerPath) choosePickerPath(pickerPath); });
  $('#file-picker-entries').addEventListener('click', (event) => { const entry = event.target.closest('.file-tree-entry'); if (!entry) return; const path = entry.dataset.path; if (entry.dataset.entryType === 'directory') { loadPicker(path); } else { choosePickerPath(path); } });
  document.querySelectorAll('.filter-tab').forEach((tab) => tab.addEventListener('click', () => { document.querySelectorAll('.filter-tab').forEach((item) => { item.classList.remove('active'); item.setAttribute('aria-selected', 'false'); }); tab.classList.add('active'); tab.setAttribute('aria-selected', 'true'); filter = tab.dataset.filter; page = 0; refreshJobs(); }));
  document.addEventListener('keydown', event => {
    if(event.key!=='Tab'||document.querySelector('dialog[open]'))return;
    const shell=$('#file-picker-shell.open')||$('#drawer-shell.open');if(!shell)return;
    const nodes=[...shell.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]')].filter(n=>n.getClientRects().length);
    const first=nodes[0],last=nodes[nodes.length-1];if(!first)return;
    if(event.shiftKey&&(document.activeElement===first||!shell.contains(document.activeElement))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!shell.contains(document.activeElement))){event.preventDefault();first.focus();}
  });
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
  loadPipelines().then(() => { Object.entries(pipelineInfo).forEach(([key, info]) => { $('#pipeline-filter').insertAdjacentHTML('beforeend', `<option value="${key}">${esc(info.label)}</option>`); }); renderPipelineCards(); resetForm(); refreshJobs();
    const editJob=new URLSearchParams(location.search).get('edit_input_job');
    if(editJob){Submission.api(`/api/jobs/${encodeURIComponent(editJob)}/input-assignments`).then(async saved=>{openDrawer();$('#pipeline').value=saved.pipeline;$('[name=input_path]').value=saved.input_path;$('[name=input_path]').readOnly=true;$('[name=dataset_label]').value=saved.dataset_label;$('[name=sample_source][value=directory]').checked=true;DirectoryInput.edit({...saved,job_id:editJob});$('#drawer-title').textContent='修改目录样本 / 重新 Match';updatePipelineFields();await DirectoryInput.scan();}).catch(e=>showMessage(e.message,true));} }).catch(connectionError);
  window.addEventListener('pageshow', event => { if(event.persisted) { resetForm(); closeDrawer(); refreshJobs(); } });
  document.addEventListener('visibilitychange', () => { clearTimeout(pollTimer); if (!document.hidden) refreshJobs(); });
})();

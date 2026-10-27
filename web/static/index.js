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
  let formDefaults = {}, mappingRows = [], mappingSelected = new Set(), mappingPage = 0, taskSubmitting = false;
  const mappingPageSize = 20;
  let scanGeneration = 0, scannedInput = '', mappingCurrent=false, mappingScanning=false, validationGeneration = 0, lastJobsUpdate = '';
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
    $('#task-check-status').textContent='配置已变更 · 可重新检查';
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
    $('#validation-summary').textContent = text; $('#task-check-status').textContent='检查发现问题'; showMessage(text, true);
    focusInvalidField();
  }
  function connectionError(error) { $('#jobs-connection span').textContent = `连接暂时中断，${lastJobsUpdate ? `当前显示为 ${lastJobsUpdate} 更新的数据` : '尚未获取任务列表'}。${error.message || ''}`; $('#jobs-connection').classList.remove('hidden'); }
  function mappingSelection() { return [...document.querySelectorAll('#mapping-chains input:checked')].map(n => n.value); }
  function mappingSampleSelection() { return mappingRows.filter(row=>mappingSelected.has(row.sample_key)).map(row=>row.sample_key); }
  function renderMappingSamples(){
    const query=$('#mapping-sample-search').value.trim().toLocaleLowerCase();
    const filtered=mappingRows.filter(row=>`${row.sample_id} ${row.sample_key}`.toLocaleLowerCase().includes(query));
    mappingPage=Math.min(mappingPage,Math.max(0,Math.ceil(filtered.length/mappingPageSize)-1));
    $('#mapping-samples').innerHTML=filtered.slice(mappingPage*mappingPageSize,(mappingPage+1)*mappingPageSize).map(row=>`<label><input type="checkbox" value="${esc(row.sample_key)}" data-chains="${esc(JSON.stringify(row.chains))}" data-chain-counts="${esc(JSON.stringify(row.chain_counts||{}))}" ${mappingSelected.has(row.sample_key)?'checked':''}><span class="mapping-sample-copy"><strong>${esc(row.sample_id)}</strong>${row.sample_key!==row.sample_id?`<small>${esc(row.sample_key)}</small>`:''}<small class="sample-scope"></small></span><span class="mapping-sample-meta">${esc(row.chains.join(' / '))}<small>${row.file_count} 个文件</small></span></label>`).join('')||`<div class="mapping-samples-empty">${mappingRows.length?'未找到匹配样本，请调整搜索关键词。':'此目录下没有可处理的样本。'}</div>`;
    $('#mapping-sample-pager').classList.toggle('hidden',filtered.length<=mappingPageSize);
    $('#mapping-sample-page').textContent=`${filtered.length} 个样本 · 第 ${mappingPage+1} / ${Math.max(1,Math.ceil(filtered.length/mappingPageSize))} 页`;
    $('#mapping-sample-prev').disabled=mappingPage===0;
    $('#mapping-sample-next').disabled=(mappingPage+1)*mappingPageSize>=filtered.length;
    syncMappingSubmit();
  }
  function syncMappingSubmit() {
    const chains = mappingSelection(), samples = mappingSampleSelection();
    $('#mapping-selection').classList.toggle('hidden',!scannedInput||!mappingRows.length);
    $('#mapping-fields').setAttribute('aria-busy',String(mappingScanning));
    $('#mapping-scan').textContent=mappingScanning?'正在扫描…':scannedInput?'重新扫描':'扫描输入目录';
    $('#mapping-samples-all').disabled=$('#mapping-samples-none').disabled=taskSubmitting||mappingScanning||!mappingRows.length;
    $('#mapping-sample-search').disabled=taskSubmitting||mappingScanning||!mappingRows.length;
    $('#mapping-sample-prev').disabled=taskSubmitting||mappingScanning||mappingPage===0;
    $('#mapping-sample-next').disabled=taskSubmitting||mappingScanning||(mappingPage+1)*mappingPageSize>=mappingRows.filter(row=>`${row.sample_id} ${row.sample_key}`.toLocaleLowerCase().includes($('#mapping-sample-search').value.trim().toLocaleLowerCase())).length;
    $('#mapping-samples').querySelectorAll('input').forEach(input=>input.disabled=taskSubmitting||mappingScanning);
    $('#mapping-chain-selection').textContent=!scannedInput?'扫描目录后选择处理链':chains.length?`已选 ${chains.length} 条链 · ${chains.join('、')}`:'尚未选择处理链';
    $('#mapping-all').disabled=taskSubmitting||mappingScanning||!scannedInput||!$('#mapping-chains input:not(:disabled)');
    $('#mapping-none').disabled=taskSubmitting||mappingScanning||!chains.length;
    let fileCount = 0, sampleCount = 0;
    const countFor=row=>chains.reduce((sum,chain)=>sum+(row.chain_counts?.[chain]??(row.chains.includes(chain)?1:0)),0);
    mappingRows.forEach(row=>{const count=countFor(row);if(mappingSelected.has(row.sample_key)&&count){sampleCount++;fileCount+=count;}});
    document.querySelectorAll('#mapping-samples input').forEach(node=>{
      const row=mappingRows.find(row=>row.sample_key===node.value),outside=node.checked&&row&&!countFor(row);
      node.closest('label').classList.toggle('out-of-scope',Boolean(outside));
      node.closest('label').querySelector('.sample-scope').textContent=outside?'本次不处理（不含所选链）':'';
    });
    $('#mapping-sample-count').textContent = `已选 ${samples.length} / ${mappingRows.length} 个样本`;
    const scope=mappingScanning?'正在扫描 CSV 目录…':!mappingCurrent?scannedInput?'请重新扫描成功后提交，原选择已保留':'请先扫描 CSV 目录':!chains.length?'请选择要处理的链':!samples.length?'请选择至少一个样本':fileCount?`实际处理 ${sampleCount} 个样本 · ${fileCount} 个样本/链文件`:'所选样本与链没有交集，请调整选择';
    $('#mapping-scope').textContent=scannedInput&&mappingRows.length?scope:'';
    $('#mapping-scope').classList.toggle('error',Boolean(scannedInput)&&!fileCount);
    const mapping=selectedPipeline()==='mapping';
    $('#new-job-form button[type="submit"]').disabled=taskSubmitting||!DirectoryInput.ready()||mapping&&(!mappingCurrent||mappingScanning||!scannedInput||scannedInput!==$('[name="input_path"]').value.trim()||!chains.length||!samples.length||!fileCount);
    $('#task-scope-message').textContent=taskSubmitting?'正在提交任务…':DirectoryInput.active()?DirectoryInput.scope():mapping?scope:$('[name=input_path]').value.trim()?'创建后先执行 Match，再核对样本':'请填写服务器上的输入目录';
  }
  function clearMappingScan() {
    invalidateValidation();
    scanGeneration++; scannedInput = '';mappingCurrent=false;mappingScanning=false;
    $('#mapping-chains').textContent = '';
    mappingRows=[];mappingSelected.clear();mappingPage=0;$('#mapping-sample-search').value='';
    renderMappingSamples();
    $('#mapping-scan-status').textContent = '扫描 CSV 目录后，选择要处理的链与样本。';
    $('#mapping-scan-status').classList.remove('error');
    $('#mapping-scan').disabled = false;
    $('#mapping-ignored').classList.add('hidden');
    syncMappingSubmit();
  }
  function taskBody(form) {
    const body = Object.fromEntries(new FormData(form).entries());
    body.generate_treemap = form.elements.generate_treemap.checked;
    if (body.pipeline === 'mapping') {
      body.mapping_chains = mappingSelection();
      body.mapping_samples = mappingSampleSelection();
      body.submission_path = ''; body.submission_revision = '';
      if (!mappingCurrent || mappingScanning || !scannedInput || scannedInput !== body.input_path.trim() || !body.mapping_chains.length || !body.mapping_samples.length) throw new Error('请先扫描输入目录并选择至少一条链和一个样本');
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
    $('#task-check-status').textContent='尚未检查配置';
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
    return `<button type="button" class="file-tree-entry ${entry.type}" data-path="${esc(entry.path)}" data-entry-type="${entry.type}" ${entry.type==='file'&&!entry.selectable ? 'disabled' : ''}><span class="file-tree-icon">${icon}</span><span class="file-tree-copy"><strong>${esc(entry.name)}</strong><small>${entry.type === 'directory' ? '目录' : `文件${size}`}</small></span><span class="file-tree-action">${entry.type === 'directory' ? '打开 ›' : (entry.selectable ? '选择' : '')}</span></button>`;
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
  function closeDrawer() { if(taskSubmitting)return; if ($('#file-picker-shell').classList.contains('open')) closePicker(); $('.app-shell').inert = false; $('#open-drawer').focus(); document.body.classList.remove('task-dialog-open'); $('#drawer-shell').classList.remove('open'); $('#drawer-shell').setAttribute('aria-hidden', 'true'); }
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
      igblast_base: '不进行 barcode 拆分或 UMI 代表序列提取。支持扫描目录指定样本与 Chain，或按 Submission 的 Note 匹配样本；按 Chain 选择数据库，比对后按 locus 保留该样本所选链的记录。',
      pig_igblast: '支持扫描目录指定样本与 Chain，或使用 Species=Pig 的 Submission。每条链使用猪专用参考库，比对结束后立即筛选该链 locus，不进行 IR/10X 拆分。'
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
    for(const pipeline of ['igblast_base','pig_igblast']) finalStages[pipeline]={
      title:'IgBLAST 与链筛选',
      input:'每个样本的合并序列，以及该样本选择的 Chain。',
      processing:'每条链使用对应参考库比对，完成后立即保留 V 命中、productive 且 locus 与目标链一致的记录，无需等待其他链完成。',
      output:'05.igblastn_out 下的样本/链 TSV，以及 chain_summary.csv 的保留与排除统计。',
      note:'克隆树图和 Clone Top 直接使用第五阶段的最终链结果。'
    };
    const finalStage = finalStages[key];
    const finalSection = finalStage ? `<section class="pipeline-final-stage" aria-labelledby="pipeline-final-title"><span class="pipeline-final-label">最后一步</span><h3 id="pipeline-final-title">${esc(finalStage.title)}</h3><dl><dt>输入</dt><dd>${esc(finalStage.input)}</dd><dt>处理</dt><dd>${esc(finalStage.processing)}</dd><dt>输出</dt><dd>${esc(finalStage.output)}</dd></dl><p class="pipeline-final-note">${esc(finalStage.note)}</p></section>` : '';
    const representative = ['ir_split', '10x_split'].includes(key) ? '<p><strong>代表序列：</strong>reads 数最高者优先；仅在并列时按平均期望错误数更低、平均质量更高排序。质量缺失或仍并列时采用确定性规则，并记录混淆和备选序列。</p>' : '';
    const teaching = {
      mapping: {input:'Human 免疫组库 CSV', output:'按样本与链整理的 UMI count 结果', folder:'remapping/input', expected:'识别 Demo_A、Demo_B 两个样本，IGH、IGK 两种链，共 3 个文件、6 条输入序列。默认全选样本；只选 IGK 时应处理 Demo_A 的 1 个文件。'},
      ir_split: {input:'Bulk IR 配对 FASTQ + Submission + Barcode', output:'样本 UMI、链结果与 Datapoint.csv 指标', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      '10x_split': {input:'单细胞配对 FASTQ + Submission + Barcode', output:'按细胞 Barcode 整理的链与 UMI 结果', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      igblast_base: {input:'配对 FASTQ；扫描目录或使用 Submission', output:'IgBLAST AIRR 注释与比对汇总', folder:'fastq/input', expected:'将 Submission 的 Note 改为解压后的 fastq/input 绝对路径。Match 应识别 Demo_A、Demo_B 两个样本的 2 对 FASTQ。'},
      pig_igblast: {input:'猪配对 FASTQ；扫描目录或使用 Submission', output:'使用猪参考库生成的 IgBLAST 注释', folder:'fastq/input', expected:'使用 pig_igblast.xlsx，保持 Species=Pig。将 Note 改为 fastq/input 的服务器绝对路径；Match 应识别 2 对 FASTQ。'}
    };
    const guide=teaching[key] || {input:info.description,output:'分析结果',folder:'',expected:''};
    const isMapping=key==='mapping';
    let steps=isMapping ? ['下载并解压示例包，将 remapping/input 放到服务器允许的输入目录。','新建 reMapping 任务，选择该目录，点击“扫描输入目录”；选择样本与链。','检查配置后创建任务，在结果页查看样本状态、条数及 UMI count 文件。'] : ['下载并解压示例包，将 fastq/input 和 submission 放到服务器允许的目录。',`选择 ${info.label}；填写输入目录，选择 submission/${key}.xlsx；通过“查看 / 编辑”替换 Note 路径。${info.requires_barcode ? '在可选设置中选择包内 barcode.csv。' : ''}`,'检查配置并创建任务；Match 完成后核对 2 个样本的匹配记录。教学 FASTQ 建议练习到审核步骤后停止任务。'];
    if(info.supports_directory_input){
      steps=['下载并解压示例包，将 fastq/input 放到服务器允许的输入目录。',`新建 ${info.label}，样本信息来源选择“扫描目录，无需 Submission”；扫描后全选有效配对，统一设置 Chain，也可逐样本修改。`,'检查配置后创建任务；核对 Match 的样本、Chain 和配对路径。教学 FASTQ 建议练习到审核步骤后停止任务。'];
      guide.expected='应识别 2 个有效配对，默认候选名为 Demo_A_A01_L01 和 Demo_B_A02_L01；可改成 Demo_A、Demo_B。指定 IGH 后应提交 2 个样本、2 对 FASTQ，无需工作簿。';
    }
    $('#pipeline-help-content').innerHTML = `<div class="guide-layout"><div class="guide-main"><section class="guide-intro"><div class="guide-title-line"><h3>${esc(info.label)}</h3><span>${isMapping?'CSV 数据':key==='10x_split'?'单细胞数据':key==='ir_split'?'Bulk IR 数据':key==='pig_igblast'?'猪 FASTQ':'通用 FASTQ'}</span></div><p>${esc(pipelineDescriptions[key] || info.description)}</p><div class="guide-facts"><div><span>输入数据</span><strong>${esc(guide.input)}</strong></div><div><span>分析结果</span><strong>${esc(guide.output)}</strong></div></div></section>
      <section class="guide-section"><h4>分析流程 <span>${(info.stages || []).length} 个阶段</span></h4><ol class="guide-flow">${(info.stages || []).map((stage,i)=>`<li><span>${String(i+1).padStart(2,'0')}</span><div><strong>${esc(info.stage_labels?.[stage] || stage)}</strong></div>${stage==='01.match'?'<em>需审核</em>':''}</li>`).join('')}</ol><p class="guide-review">${isMapping?'扫描目录后选择样本与链，无需 Match 审核。':'样本匹配完成后需审核，再继续后续步骤。'}</p></section>
      <details class="guide-technical"><summary>处理逻辑与结果字段</summary><p>${esc(details[key] || info.description)}</p>${finalSection}${representative}${isMapping ? '<p>CSV 文件名：样本__链.csv；必需列：CDR3(pep)、copy、joinedSeq。umi_count 从序列标识末尾还原原始 copy；结果条数不等于 copy 总和。</p>' : ''}</details></div>
      <aside class="guide-demo" aria-labelledby="guide-demo-title"><div class="guide-demo-heading"><h4 id="guide-demo-title">用示例数据练习</h4><span>合成数据 · v1</span></div><p>2 个样本，包含 CSV、配对 FASTQ、Submission 模板与 Barcode CSV。</p><a class="button button-ghost" href="/static/demos/scigblast-teaching-v1.zip" download>下载示例包 <span>ZIP ↓</span></a><h5>如何开始</h5><ol class="guide-instructions">${steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><details class="guide-expected"><summary>练习检查点</summary><p>${esc(guide.expected)}</p></details><p class="guide-demo-limit">合成数据仅用于操作练习。FASTQ 未模拟 IR / 10X 建库结构，继续分析可能为空或失败；reMapping 注释也可能为空，输入条数不代表最终结果。</p><a class="guide-readme" href="/static/demos/README.txt" download>详细教程与文件清单 TXT ↓</a></aside></div>`;
    $('#help-pipeline-nav').innerHTML=Object.entries(pipelineInfo).map(([id,pipeline])=>`<button type="button" data-help-pipeline="${esc(id)}" aria-pressed="${id===key}">${esc(pipeline.label)}</button>`).join('');
    $('#guide-current-pipeline').textContent=`当前流程：${info.label}`;
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
    const focused = document.activeElement?.closest('#jobs a, #jobs [data-action]');
    const focusedKey = focused ? {id:focused.closest('[data-job-id]').dataset.jobId,action:focused.dataset.action,link:focused.dataset.focusKey} : null;
    const operatorSelect = $('#operator-filter'), selectedOperator = operatorSelect.value;
    const operators=[...new Set([...(data.operators || []), selectedOperator].filter(Boolean))],operatorSignature=JSON.stringify(operators);
    if(operatorSelect._operatorSignature!==operatorSignature){operatorSelect.innerHTML='<option value="">全部操作者</option>'+operators.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');operatorSelect._operatorSignature=operatorSignature;}
    operatorSelect.value = selectedOperator;
    // Filtering and pagination are both server-side; filtering this page again
    // would hide valid rows and make the page count disagree with the API.
    const visible = jobs;
    const existing=new Map([...$('#jobs').children].map(row=>[row.dataset.jobId,row]));
    visible.forEach((job,position)=>{
      const candidate=document.createElement('tr');candidate.dataset.jobId=job.id;
      candidate.innerHTML=`
      <td><a class="job-link" data-focus-key="job" href="/jobs/${job.id}"><strong>${esc(job.dataset)}</strong><span class="job-id">${esc(job.id)}</span></a></td>
      <td><span class="pipeline-chip ${job.pipeline}"><i></i>${esc(job.pipeline_label)}</span></td>
      <td><span class="stage-name">${esc(job.status === 'ARCHIVING' ? '结果压缩与校验' : job.execution_phase === 'waiting_resource' ? '等待运行资源' : job.stage_labels?.[job.current_stage] || job.current_stage || '等待启动')}</span>${job.status === 'ARCHIVING' ? '<small>分析已完成，归档后可下载压缩包</small>' : ''}</td>
      <td><div class="progress-cell"><div class="progress-track"><span style="width:${Math.max(0, Math.min(100, job.progress))}%"></span></div><b>${job.progress}%</b></div></td>
      <td><span class="operator-name">${esc(job.operator_display_name||job.operator)}</span><small class="time-text">${formatTime(job.started_at || job.created_at)}</small></td>
      <td><span class="status-badge ${job.status.toLowerCase()}"><i></i>${esc(statusText(job.status))}</span>${job.last_error ? `<small class="error-text" title="${esc(job.last_error)}">${esc(job.last_error)}</small>` : ''}</td>
      <td class="job-actions-cell"><div class="row-actions">${job.status === 'WAITING_REVIEW' ? `<button class="button button-review button-small" data-action="confirm" data-id="${job.id}">核对样本</button>` : ''}${stoppableStates.has(job.status) ? `<button class="button button-danger-ghost button-small" data-action="stop" data-id="${job.id}">停止</button>` : ''}<a class="button button-ghost button-small" data-focus-key="detail" href="/jobs/${job.id}">${['SUCCEEDED','ARCHIVED'].includes(job.status) ? '查看结果' : ['FAILED','INTERRUPTED','COMPLETED_WITHOUT_MARKER'].includes(job.status) ? '查看原因 / 续跑' : '详情'}</a>${!isActive(job)?`<button class="button button-danger-ghost button-small" data-action="delete" data-id="${job.id}">删除</button>`:''}</div></td>`;
      const row=existing.get(job.id)||candidate;
      [...candidate.children].forEach((cell,i)=>{
        const markup=cell.innerHTML,target=row.children[i];
        if(target._renderedMarkup!==markup){if(row!==candidate)target.innerHTML=markup;target._renderedMarkup=markup;}
      });
      if($('#jobs').children[position]!==row)$('#jobs').insertBefore(row,$('#jobs').children[position]||null);
      existing.delete(job.id);
    });
    existing.forEach(row=>row.remove());
    $('#empty-state').classList.toggle('hidden', visible.length !== 0);
    const counts = data.counts || {}; $('#stat-active').textContent = counts.active ?? data.active_jobs ?? jobs.filter(isActive).length; $('#stat-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#stat-done').textContent = counts.done ?? jobs.filter(isDone).length; $('#stat-total').textContent = counts.total ?? data.total ?? jobs.length;
    $('#count-all').textContent = counts.total ?? data.total ?? jobs.length; $('#count-active').textContent = counts.active ?? jobs.filter(isActive).length; $('#count-review').textContent = counts.review ?? jobs.filter((job) => job.status === 'WAITING_REVIEW').length; $('#count-done').textContent = counts.done ?? jobs.filter(isDone).length;
    const start = data.total ? data.offset + 1 : 0; const end = Math.min((data.offset || 0) + jobs.length, data.total || 0); $('#page-summary').textContent = data.total ? `当前筛选 ${start}–${end} / 共 ${data.total} 条任务` : '当前筛选无任务'; $('#page-prev').disabled = page === 0; $('#page-next').disabled = end >= (data.total || 0);
    visible.forEach(job => renderActionState(job.id));
    if(focusedKey&&(!focused.isConnected||document.activeElement===document.body)&&!document.querySelector('dialog[open]')){
      const row=[...$('#jobs').children].find(row=>row.dataset.jobId===focusedKey.id);
      (focusedKey.action?actionButton(focusedKey)||row?.querySelector('[data-focus-key="detail"]'):row?.querySelector(`[data-focus-key="${focusedKey.link}"]`))?.focus({preventScroll:true});
      if(document.activeElement===document.body)$('#refresh-jobs').focus({preventScroll:true});
    }
  }
  $('#jobs').addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(button&&!button.disabled)runAction(button.dataset.id,button.dataset.action);});

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
  function syncJobFilterTabs() {
    document.querySelectorAll('.filter-tab').forEach(tab => {
      const active=tab.dataset.filter===filter;
      tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));
    });
  }
  function renderJobFilters() {
    const entries=[];
    if(filter!=='all')entries.push(['status',`状态：${{active:'进行中',review:'待审核',done:'已结束'}[filter]}`]);
    for(const [name,selector,title] of [['pipeline','#pipeline-filter','流程'],['operator','#operator-filter','操作者']]){
      const select=$(selector);if(select.value)entries.push([name,`${title}：${select.selectedOptions[0]?.textContent||select.value}`]);
    }
    if($('#job-search').value.trim())entries.push(['search',`搜索：${$('#job-search').value.trim()}`]);
    $('#job-filter-summary').classList.toggle('hidden',!entries.length);
    const markup=entries.map(([name,label])=>`<button type="button" class="job-filter-chip" data-clear-filter="${name}" aria-label="移除${esc(label)}筛选"><span>${esc(label)}</span><b aria-hidden="true">×</b></button>`).join('');
    if($('#job-filter-chips')._markup!==markup){$('#job-filter-chips').innerHTML=markup;$('#job-filter-chips')._markup=markup;}
  }
  function resetJobFilters() {
    clearTimeout(searchTimer);filter='all';page=0;
    $('#pipeline-filter').value='';$('#operator-filter').value='';$('#job-search').value='';
    syncJobFilterTabs();refreshJobs();$('.filter-tab[data-filter="all"]').focus({preventScroll:true});
  }
  $('#job-filter-reset').addEventListener('click',resetJobFilters);
  $('#job-filter-chips').addEventListener('click',event=>{
    const button=event.target.closest('[data-clear-filter]');if(!button)return;
    const name=button.dataset.clearFilter;
    const input=name==='status'?$('.filter-tab[data-filter="all"]'):$(name==='pipeline'?'#pipeline-filter':name==='operator'?'#operator-filter':'#job-search');
    if(name==='status'){filter='all';syncJobFilterTabs();}else input.value='';
    clearTimeout(searchTimer);page=0;refreshJobs();input.focus({preventScroll:true});
  });
  async function refreshJobs() {
    clearTimeout(pollTimer);
    renderJobFilters();
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
    const button=$('#validate-button'), generation=validationGeneration; button.disabled=true; $('#task-check-status').textContent='正在检查配置…';
    try {
      const body=taskBody($('#new-job-form'));
      const result=await Submission.api('/api/validate',body);
      if(generation!==validationGeneration)return;
      ['input_path','submission_path','barcode_csv'].forEach(name=>fieldFeedback(name, name==='barcode_csv'&&!body.barcode_csv ? `使用默认 Barcode：${result.barcode_csv || formDefaults.barcode_csv || '服务器默认值'}` : '✓ 路径检查通过'));
      $('#task-check-status').textContent=result.valid===false?'检查发现问题':result.stage==='match'?'匹配检查通过':result.stage==='analysis'?'分析检查通过':'配置检查通过';
      const issues=[...(result.errors||[]),...(result.warnings||[])];
      $('#validation-summary').textContent=result.valid===false?'检查发现问题，请按下方提示调整。':`${result.dataset} · ${body.pipeline==='mapping'?'可创建并开始分析':'可创建任务，下一步核对样本匹配结果'}。${body.pipeline==='ir_split'?' IR 输入类型会在 Match 时自动识别。':''}`;
      $('#validation-details').classList.toggle('hidden', !issues.length);
      $('#validation-details div').textContent=issues.map(i=>i.message||i.code||String(i)).join('；');
      (result.errors||[]).forEach(i=>fieldFeedback(i.field,i.message||i.code,true));
      if(result.valid===false){$('#validation-details').open=true;focusInvalidField();}
    }catch(error){if(generation===validationGeneration)validationError(error);}finally{button.disabled=false;}
  }

  $('#new-job-form').addEventListener('submit', async event=>{
    event.preventDefault();if(taskSubmitting)return;
    const button=event.target.querySelector('button[type="submit"]');let locked=[];
    try{
      const body=taskBody(event.target),url=DirectoryInput.editingJob()?`/api/jobs/${DirectoryInput.editingJob()}/input-rematch`:'/api/jobs';
      taskSubmitting=true;
      locked=[...$('#drawer-shell').querySelectorAll('input,select,button')].map(node=>[node,node.disabled]);
      locked.forEach(([node])=>node.disabled=true);button.classList.add('loading');syncMappingSubmit();
      const result=await Submission.api(url,body);
      taskSubmitting=false;resetForm();closeDrawer();location.href=`/jobs/${result.id}`;
    }catch(error){taskSubmitting=false;locked.forEach(([node,disabled])=>node.disabled=disabled);locked=[];validationError(error);}
    finally{taskSubmitting=false;locked.forEach(([node,disabled])=>node.disabled=disabled);button.classList.remove('loading');syncMappingSubmit();}
  });
  $('[name="input_path"]').addEventListener('input', clearMappingScan);
  UI.bindChainPicker($('#mapping-chains'));
  $('#mapping-chains').addEventListener('change', syncMappingSubmit);
  DirectoryInput.init(() => {syncMappingSubmit();invalidateValidation();});
  document.querySelectorAll('[name=sample_source]').forEach(n=>n.addEventListener('change',updatePipelineFields));
  $('[name=input_path]').addEventListener('input',()=>DirectoryInput.setContext());
  $('#mapping-samples').addEventListener('change',event=>{if(!event.target.matches('input[type=checkbox]'))return;event.target.checked?mappingSelected.add(event.target.value):mappingSelected.delete(event.target.value);syncMappingSubmit();});
  $('#mapping-sample-search').oninput=()=>{mappingPage=0;renderMappingSamples();};
  $('#mapping-sample-prev').onclick=()=>{mappingPage--;renderMappingSamples();};
  $('#mapping-sample-next').onclick=()=>{mappingPage++;renderMappingSamples();};
  function formChanged(event){if(event.target.matches('#directory-search,#mapping-sample-search'))return;invalidateValidation();syncMappingSubmit();}
  $('#new-job-form').addEventListener('input', formChanged);
  $('#new-job-form').addEventListener('change', formChanged);
  $('#jobs-retry').onclick=refreshJobs;
  $('#jobs-action-retry').onclick=() => { if (failedAction) runAction(failedAction.id, failedAction.action); };
  $('#jobs-action-dismiss').onclick=() => { if (failedAction) { const {id}=failedAction; jobActionErrors.delete(id); renderActionState(id); } failedAction=null; $('#jobs-action-message').classList.add('hidden'); };
  $('#mapping-samples-all').onclick = () => { mappingRows.forEach(row=>mappingSelected.add(row.sample_key));renderMappingSamples(); invalidateValidation(); };
  $('#mapping-samples-none').onclick = () => { mappingSelected.clear();renderMappingSamples(); invalidateValidation(); };
  $('#mapping-all').onclick = () => { document.querySelectorAll('#mapping-chains input:not(:disabled)').forEach(n => n.checked = true); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-none').onclick = () => { document.querySelectorAll('#mapping-chains input').forEach(n => n.checked = false); syncMappingSubmit(); invalidateValidation(); };
  $('#mapping-scan').onclick = async () => {
    if(mappingScanning)return;
    const generation = ++scanGeneration, path = $('[name="input_path"]').value.trim(),restoring=scannedInput===path;
    const priorRows=new Map(mappingRows.map(row=>[row.sample_key,row])),priorSelected=new Set(mappingSelected),priorChains=mappingSelection();
    const locked=[...$('#mapping-chains').querySelectorAll('input,button')].map(node=>[node,node.disabled]);locked.forEach(([node])=>node.disabled=true);
    mappingScanning=true;mappingCurrent=false;invalidateValidation();syncMappingSubmit();
    $('#mapping-scan').disabled = true; $('#mapping-scan-status').classList.remove('error'); $('#mapping-scan-status').textContent = '正在识别 CSV 文件中的样本与链…';
    try {
      const result = await Submission.api('/api/mapping/scan', {input_path:path});
      if (generation !== scanGeneration) return;
      scannedInput = path;
      $('#mapping-chains').innerHTML=UI.chainPicker({counts:Object.fromEntries(result.chains.map(chain=>[chain.chain,chain.file_count])),selected:restoring?priorChains:['IGH']});
      mappingRows=result.samples||[];mappingSelected=new Set(mappingRows.filter(row=>!restoring||!priorRows.has(row.sample_key)||priorSelected.has(row.sample_key)&&Boolean(row.source_version)&&row.source_version===priorRows.get(row.sample_key).source_version).map(row=>row.sample_key));mappingCurrent=true;renderMappingSamples();
      $('#mapping-scan-status').textContent = result.chains.length ? `已识别 ${mappingRows.length} 个样本 · ${result.chains.length} 种链。${restoring?'已保留未变化样本的选择；变化样本请重新选择。':result.chains.some(c=>c.chain==='IGH')?'默认选择 IGH 和全部样本。':'未发现 IGH，请选择处理链。'}${result.unrecognized_files?` ${result.unrecognized_files} 个 CSV 未识别，详见下方。`:''}` : '未找到可处理的 CSV，请检查目录及文件名（样本__链.csv）。';
      $('#mapping-ignored').classList.toggle('hidden', !result.unrecognized_files);
      $('#mapping-ignored ul').innerHTML=(result.ignored_files||[]).map(f=>`<li>${esc(f)}：文件名或链不符合 样本__链.csv 约定。</li>`).join('');
    } catch (error) { if (generation === scanGeneration) { $('#mapping-scan-status').classList.add('error'); $('#mapping-scan-status').textContent = `扫描失败：${error.message}${restoring?'；原选择已保留，请重新扫描后提交。':''}`; } }
    finally { if (generation === scanGeneration) { mappingScanning=false;locked.forEach(([node,disabled])=>node.disabled=disabled);$('#mapping-scan').disabled = false; syncMappingSubmit(); } }
  };
  let searchTimer; $('#operator-filter').addEventListener('change', () => { page = 0; refreshJobs(); });
  $('#cancel-new-job').addEventListener('click', closeDrawer);
  $('#open-drawer').addEventListener('click', openDrawer); $('#close-drawer').addEventListener('click', closeDrawer); $('#drawer-backdrop').addEventListener('click', closeDrawer); $('#validate-button').addEventListener('click', validatePaths); $('#pipeline').addEventListener('change', updatePipelineFields); $('#refresh-jobs').addEventListener('click', refreshJobs); $('#job-search').addEventListener('input', () => { page = 0; clearTimeout(searchTimer); searchTimer = setTimeout(refreshJobs, 300); }); $('#job-search').addEventListener('keydown', (event) => { if (event.key === 'Enter') { clearTimeout(searchTimer); page = 0; refreshJobs(); } }); $('#pipeline-filter').addEventListener('change', () => { page = 0; refreshJobs(); }); $('#page-prev').addEventListener('click', () => { if (page > 0) { page -= 1; refreshJobs(); } }); $('#page-next').addEventListener('click', () => { page += 1; refreshJobs(); }); document.querySelectorAll('.field input').forEach((input) => input.addEventListener('input', () => { const state = document.querySelector(`.path-state[data-for="${input.name}"]`); if (state) state.classList.remove('ok', 'bad'); }));
  document.querySelectorAll('.browse-button').forEach((button) => button.addEventListener('click', () => openPicker(button.dataset.browseKind, button.dataset.browseTarget)));
  $('#close-file-picker').addEventListener('click', closePicker); $('#file-picker-cancel').addEventListener('click', closePicker); $('#file-picker-backdrop').addEventListener('click', closePicker); $('#file-picker-up').addEventListener('click', () => loadPicker($('#file-picker-up').disabled ? '' : $('#file-picker-up').dataset.path)); $('#file-picker-select').addEventListener('click', () => { if (pickerPath) choosePickerPath(pickerPath); });
  $('#file-picker-entries').addEventListener('click', (event) => { const entry = event.target.closest('.file-tree-entry'); if (!entry) return; const path = entry.dataset.path; if (entry.dataset.entryType === 'directory') { loadPicker(path); } else { choosePickerPath(path); } });
  document.querySelectorAll('.filter-tab').forEach(tab=>tab.addEventListener('click',()=>{if(tab.dataset.filter==='all'){resetJobFilters();return;}filter=tab.dataset.filter;page=0;syncJobFilterTabs();refreshJobs();}));
  document.addEventListener('keydown', event => {
    if(event.key!=='Tab'||document.querySelector('dialog[open]'))return;
    const shell=$('#file-picker-shell.open')||$('#drawer-shell.open');if(!shell)return;
    const nodes=[...shell.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],summary,[tabindex="0"]')].filter(n=>n.getClientRects().length&&!n.closest('[inert]')&&(!n.closest('details:not([open])')||n===n.closest('details:not([open])').querySelector('summary')));
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
    if(editJob){Submission.api(`/api/jobs/${encodeURIComponent(editJob)}/input-assignments`).then(async saved=>{openDrawer();$('#pipeline').value=saved.pipeline;$('[name=input_path]').value=saved.input_path;$('[name=input_path]').readOnly=true;$('[name=dataset_label]').value=saved.dataset_label;$('[name=generate_treemap]').checked=Boolean(saved.generate_treemap??saved.options?.generate_treemap);$('[name=sample_source][value=directory]').checked=true;DirectoryInput.edit({...saved,job_id:editJob});$('#drawer-title').textContent='修改目录样本 / 重新 Match';updatePipelineFields();await DirectoryInput.scan();}).catch(e=>showMessage(e.message,true));} }).catch(connectionError);
  window.addEventListener('pageshow', event => { if(event.persisted) { resetForm(); closeDrawer(); refreshJobs(); } });
  document.addEventListener('visibilitychange', () => { clearTimeout(pollTimer); if (!document.hidden) refreshJobs(); });
})();

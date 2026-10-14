(() => {
  const $=s=>document.querySelector(s),id=window.SCIGBLAST_JOB_ID,{api,esc}=Submission;
  let job,tab='overview',revision='',attempt=-1,matchSummarySignature='',matchPage=0,offset=0,source='',paused=false,busy=false,logBusy=false;
  let refreshGeneration=0,refreshPendingCount=0,lastUpdate='',artifactsRequest=0;
  const fmt=v=>v?new Date(v).toLocaleString('zh-CN'):'—';
  function errorText(error){let data;try{data=JSON.parse(error.message);}catch{}return data?.errors?.map(i=>i.message||i.code).join('；')||data?.message||error.message||String(error);}
  const fail=e=>{const node=$('#page-error');node.textContent=errorText(e);node.classList.remove('hidden');};
  function connectionError(error){$('#job-connection span').textContent=`连接暂时中断，${lastUpdate?`当前显示为 ${lastUpdate} 更新的数据`:'尚未获取任务信息'}。${errorText(error)}`;$('#job-connection').classList.remove('hidden');}
  function nextStep(){return job.status_detail||({QUEUED:'任务已排队，轮到后自动启动。',WAITING_REVIEW:'核对样本并完成全部标注，然后点击“审核并继续”。',SUCCEEDED:'分析已完成，可按样本查看和下载结果。',ARCHIVING:'正在归档，完成后可下载压缩包。',ARCHIVED:'结果已归档，可下载完整压缩包。',FAILED:'查看失败原因和日志，修正后可断点续跑。',STOPPED:'中间结果已保留，可断点续跑。',INTERRUPTED:'执行已中断，请查看当前执行状态后续跑。'})[job.status]||'分析中，已完成的阶段会保留。';}
  const statusLabels={QUEUED:'排队中',RUNNING:'运行中',MATCHING:'匹配中',WAITING_REVIEW:'待审核',SUCCEEDED:'已完成',ARCHIVING:'归档中',ARCHIVED:'已归档',FAILED:'失败',STOPPED:'已停止',STOPPING:'停止中',INTERRUPTED:'已中断',COMPLETED_WITHOUT_MARKER:'需检查'};
  const actionLabels = {create:'创建任务', 'confirm-match':'确认 Match 并继续', review:'审核标注', rematch:'更新资料并重新匹配', resume:'断点续跑', stop:'停止任务'};
  function actionDetail(a) {
    if(a.action==='create' && job?.pipeline==='mapping') return '按所选链执行 FASTA、IgBLAST 和 UMI count 整理。';
    if(a.action==='confirm-match') { const attempt = String(a.details||'').split(':')[0]; return /^\d+$/.test(attempt) ? `已确认第 ${attempt} 次匹配结果，仅匹配成功的记录进入下游。` : '已确认匹配结果。'; }
    if(a.action==='review') { try { const r=JSON.parse(a.details); return `${r.row_keys ? new Set(r.row_keys).size : 1} 条记录 · ${r.label || '清除标注'}`; } catch { return '已保存审核标注'; } }
    return {create:'已提交配置，首先执行样本匹配。', rematch:'已更新 Submission 工作副本。', resume:'保留已有结果，继续执行。',stop:'已请求停止，保留中间结果。'}[a.action] || '';
  }
  function visibleOptions(options) {
    const items = [['数据集',job.dataset],['输出目录',job.output_root]];
    if(job.pipeline==='mapping') items.push(['物种','human'],['处理链',(job.mapping_chains||[]).join(', ')],['处理样本',job.mapping_samples?.join(', ')||'全部样本']);
    if(job.pipeline==='ir_split') items.push(['输入模式',({'auto':'自动识别','presplit':'已拆分（presplit）','raw':'未拆分（raw）'})[options.ir_input_mode]||'未拆分（raw）'],['代表序列',options.ir_variant==='merged'?'不提取，使用全部合并序列':'提取代表序列'],['结果整理',({'auto':'自动','1':'启用','0':'关闭'})[options.run_preprocessing]||'自动']);
    return items;
  }
  $('#tab-match .match-filter').insertAdjacentHTML('beforeend',`<input id="match-search" placeholder="搜索样本 / 原因" aria-label="搜索 Match"><label><input id="unmarked-only" type="checkbox">只看未标注</label><button id="next-unmarked" class="button button-ghost">下一条未标注</button><button id="match-refresh" class="button button-ghost">刷新</button><a href="/api/jobs/${id}/download?kind=match">下载清单</a>`);
  $('#tab-match').insertAdjacentHTML('beforeend','<div class="pagination"><button id="match-prev" class="button button-ghost">上一页</button><button id="match-next" class="button button-ghost">下一页</button></div><p class="review-semantics">是否进入分析由 OK / ERROR 决定。“待补资料”是审核注释，标注为待补资料的 OK 记录仍会进入分析；ERROR 记录不会进入。修正 Submission 后请重新匹配。</p><p id="review-save" role="status" aria-live="polite"></p><button id="review-retry" class="button button-ghost button-small hidden">重试保存</button>');
  $('#tab-match').insertAdjacentHTML('beforeend','<section class="metadata-review-section"><div class="tab-toolbar"><h3>登记但未识别的样本</h3><span id="metadata-count" class="muted"></span></div><p class="muted">以下登记样本未匹配到输入文件，不会进入下游。</p><p id="match-changes" class="muted"></p><div id="metadata-report" class="table-wrap" aria-live="polite">正在读取登记信息…</div></section>');
  async function loadMetadata(token){
    $('#metadata-report').textContent='正在读取登记信息…';$('#metadata-count').textContent='';
    try{
      const metadata=await api(`/api/jobs/${id}/metadata-review`);
      if(token!==matchRequest)return;
      const query=$('#match-search').value.trim().toLocaleLowerCase();
      const rows=metadata.rows.filter(r=>!query||Object.values(r).join(' ').toLocaleLowerCase().includes(query));
      $('#metadata-count').textContent=`未识别 ${metadata.rows.length} 条登记 · 当前显示 ${rows.length} 条`;
      $('#metadata-report').innerHTML=rows.length?`<table><thead><tr><th>状态</th><th>样本</th><th>Dual Index</th><th>Note</th><th>来源</th><th>说明</th></tr></thead><tbody>${rows.map(r=>`<tr class="error-row"><td>未识别</td><td>${esc(r.sample_id)}</td><td>${esc(r.dual_index)}</td><td>${esc(r.note)}</td><td>${esc(r.file)} / ${esc(r.sheet)}:${r.row}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table>`:'<p class="muted">当前筛选下没有未识别的登记样本。</p>';
    }catch(e){if(token===matchRequest)$('#metadata-report').textContent=`登记信息读取失败：${e.message}。请刷新重试。`;}
  }
  function setTab(name){if(tab==='artifacts')saveSummaryState();if(name==='match'&&job?.requires_match_review===false)name='overview';tab=name;document.querySelectorAll('.detail-tab').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));b.tabIndex=b.dataset.tab===name?0:-1;});document.querySelectorAll('.tab-content').forEach(p=>p.classList.toggle('active',p.id===`tab-${name}`));if(name==='match')loadMatch().catch(fail);if(name==='log')loadLog().catch(fail);if(name==='artifacts')loadArtifacts().catch(fail);syncSelection();persistResultView();}
  async function refresh(force=false){if(document.hidden||refreshPendingCount&&!force)return;const generation=++refreshGeneration,previousStatus=job?.status,previousAttempt=attempt;let data;refreshPendingCount++;try{data=await api(`/api/jobs/${id}`);}catch(error){if(generation===refreshGeneration)connectionError(error);return;}finally{refreshPendingCount--;}if(generation!==refreshGeneration)return;job=data.job;lastUpdate=new Date().toLocaleTimeString('zh-CN');$('#job-updated').textContent=`最近更新 ${lastUpdate}`;$('#job-connection').classList.add('hidden');const nextSummarySignature=JSON.stringify(data.match_summary_signature||[]);const summaryChanged=matchSummarySignature!==''&&matchSummarySignature!==nextSummarySignature;matchSummarySignature=nextSummarySignature;if(attempt!==job.attempt_no){if(attempt>=0)resetResultView();attempt=job.attempt_no;revision='';offset=0;source='';$('#log-view').textContent='';if(tab==='match')loadMatch().catch(fail);}else if(tab==='match'&&job.requires_match_review&&job.status==='WAITING_REVIEW'&&(summaryChanged||previousStatus!==job.status)){loadMatch().catch(fail);}
    $('#job-id').textContent=id;$('#dataset-title').textContent=job.dataset;$('#job-subtitle').textContent=`${job.pipeline_label} · ${nextStep()}`;
    $('[data-tab="match"]').classList.toggle('hidden', job.requires_match_review===false);
    $('#hero-status').innerHTML=`<span class="status-badge ${job.status.toLowerCase()}">${esc(statusLabels[job.status]||job.status)}</span><strong>${job.progress}%</strong><small>阶段完成比例</small>`;
    const actions=[];if(job.status==='WAITING_REVIEW')actions.push(['confirm-match','审核并继续']);if(['QUEUED','RUNNING','MATCHING'].includes(job.status))actions.push(['stop','停止']);if(['FAILED','STOPPED','INTERRUPTED','COMPLETED_WITHOUT_MARKER'].includes(job.status))actions.push(['resume','断点续跑']);if(!['QUEUED','RUNNING','MATCHING','STOPPING','ARCHIVING','ARCHIVED'].includes(job.status)&&(job.submission_revision||job.sample_source==='directory'))actions.push(['edit','编辑资料 / 重新 Match']);$('#head-actions').innerHTML=actions.map(([a,t])=>`<button class="button ${a==='stop'?'button-danger':a==='edit'?'button-ghost':'button-primary'}" data-action="${a}">${t}</button>`).join('');
    $('#meta-cards').innerHTML=[['操作者',job.operator_display_name||job.operator],['创建时间',fmt(job.created_at)],['当前状态',job.execution_phase==='waiting_resource'?'等待运行资源':job.execution_phase==='starting'?'正在启动':statusLabels[job.status]||job.status],['已完成阶段',`${job.completed_stages.length} / ${job.stages.length}`]].map(([k,v])=>`<div class="meta-card"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('');
    $('#progress-total').textContent=`${job.completed_stages.length} / ${job.stages.length} 已完成`;$('#stages').innerHTML=job.stages.map(s=>{const done=job.completed_stages.includes(s),current=s===job.current_stage;return `<div class="stage-row ${done?'done':''} ${current?'current':''}"><span class="stage-check">${done?'✓':current?'●':'○'}</span><span><strong>${esc(job.stage_labels[s]||s)}</strong><small>${s}</small></span><span class="stage-state">${done?'已完成':current?(statusLabels[job.status]||job.status):'未开始'}</span></div>`;}).join('');
    const advancedOpen=$('#runtime-details')?.open;
    $('#overview-content').innerHTML=`<p class="next-step">${esc(nextStep())}</p>${job.last_error?`<p class="page-error">${esc(job.last_error)}</p>`:''}${job.pipeline==='mapping'?`<dl><dt>处理链</dt><dd>${esc((job.mapping_chains||[]).join(', '))}</dd><dt>处理样本</dt><dd>${job.mapping_samples?.length||'全部'} 个</dd><dt>主要结果</dt><dd>各样本 UMI count（原 CSV 的 copy）</dd></dl>`:''}<details id="runtime-details" class="advanced-details" ${advancedOpen?'open':''}><summary>高级详情 · 路径与执行信息</summary><dl>${[['输入',job.input_path],...(job.pipeline==='mapping'?[['物种','human'],['样本列表',job.mapping_samples?.join(', ')||'全部样本']]:job.sample_source==='directory'?[['样本信息来源','目录识别'],['清单版本',job.directory_manifest_hash],['样本与链',job.input_assignments?.map(r=>`${r.sample_id}: ${r.chains.join(', ')}`).join('；')]]:[['Submission 副本',job.submission_path],['Barcode',job.barcode_csv||'不需要']]),['输出',job.output_root],['开始',fmt(job.started_at)],['结束',fmt(job.ended_at)],['尝试次数',job.attempt_no],['阶段内进度',`${job.stage_progress??0}%（日志报告）`],['退出码',job.exit_code??'—']].map(([k,v])=>`<dt>${k}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl></details>`;
    if(job.last_error) $('#overview-content').insertAdjacentHTML('beforeend','<button type="button" class="button button-ghost button-small" data-open-log>查看失败日志</button>');
    if(!['QUEUED','RUNNING','MATCHING','STOPPING','ARCHIVING'].includes(job.status)) $('#head-actions').insertAdjacentHTML('beforeend','<button class="button button-danger-ghost" data-action="delete">删除任务</button>');
    $('#options-list').innerHTML=visibleOptions(data.options||{}).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');$('#audit-timeline').innerHTML=(data.actions||[]).map(a=>`<div class="audit-item"><div><strong>${esc(actionLabels[a.action]||'操作记录')}</strong><small>${esc(a.operator.replace(/ \([A-Za-z0-9_.-]+\)$/, ''))} · ${fmt(a.created_at)}</small><p>${esc(actionDetail(a))}</p></div></div>`).join('');
    if(job.resume_blocked)$('[data-action="resume"]')?.setAttribute('disabled','');
    if(job.can_stop_recovered&&!$('[data-action="stop"]'))$('#head-actions').insertAdjacentHTML('afterbegin','<button class="button button-danger" data-action="stop">停止仍在执行的任务</button>');
    syncSelection();
    if(tab==='artifacts'&&previousStatus&&(previousStatus!==job.status||previousAttempt!==attempt))loadArtifacts().catch(fail);
    nextDetailPoll=Date.now()+detailPollDelay();
    if(!resultViewReady){const restored=restoreResultView();if(location.hash==='#match')setTab('match');else if(location.hash==='#artifacts')setTab('artifacts');else if(restored)setTab(restored);else if(['SUCCEEDED','ARCHIVED'].includes(job.status))setTab('artifacts');}
  }
  let matchRequest=0, matchLoading=false, batchBusy=false, pageRows=[], selected=new Set(), reviewCounts=null, lastUnmarked=-1, matchSearchTimer, failedReview=null;
  $('#tab-match .match-table-wrap').before(Object.assign(document.createElement('div'), {className:'batch-review-bar', innerHTML:
    '<div><strong id="selection-count">已选 0 条</strong><small>跨页保留选择；更改筛选或清单版本后清空</small></div><div class="batch-review-actions"><select id="batch-label" aria-label="批量审核标注"><option value="已核对">已核对</option><option value="待补资料">待补资料</option><option value="">清除标注</option></select><button id="batch-apply" class="button button-primary" disabled>应用到所选</button><button id="selection-clear" class="button button-ghost" disabled>取消选择</button></div><span id="batch-message" role="status" aria-live="polite"></span>'}));
  $('#tab-match .match-filter').insertAdjacentHTML('afterend','<p id="review-progress" class="review-progress" role="status" aria-live="polite"></p>');
  function syncSelection(){
    const confirmButton=$('[data-action="confirm-match"]');
    if(confirmButton){confirmButton.disabled=busy||tab==='match'&&(matchLoading||batchBusy||!reviewCounts||reviewCounts.unmarked>0);confirmButton.title=tab==='match'&&reviewCounts?.unmarked?`还有 ${reviewCounts.unmarked} 条未标注`:'';}
    const editable=job?.status==='WAITING_REVIEW'&&!batchBusy&&!matchLoading;
    $('#selection-count').textContent=`已选 ${selected.size} 条`;
    $('#batch-apply').disabled=!editable||!selected.size;$('#selection-clear').disabled=batchBusy||!selected.size;$('#batch-label').disabled=!editable;
    const all=$('#select-page'),checked=pageRows.filter(r=>selected.has(r._row_key)).length;
    if(all){all.checked=pageRows.length>0&&checked===pageRows.length;all.indeterminate=checked>0&&checked<pageRows.length;all.disabled=!editable||!pageRows.length;}
    document.querySelectorAll('[data-select-row]').forEach(c=>{c.checked=selected.has(c.dataset.selectRow);c.disabled=!editable;c.closest('tr').classList.toggle('selected-row',c.checked);});
    document.querySelectorAll('[data-review]').forEach(c=>c.disabled=!editable);
  }
  async function loadMatch(){
    if(job?.requires_match_review===false)return;
    const token=++matchRequest;matchLoading=true;syncSelection();
    loadMetadata(token);
    try{
      const data=await api(`/api/jobs/${id}/match-preview?${new URLSearchParams({offset:matchPage*50,limit:50,query:$('#match-search').value,errors_only:$('#errors-only').checked,unmarked_only:$('#unmarked-only').checked})}`);
      if(token!==matchRequest)return;
      $('#match-updated').textContent=`最近刷新 ${new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;
      if(revision!==(data.revision||'')){selected.clear();$('#batch-message').textContent='';failedReview=null;$('#review-retry').classList.add('hidden');$('#review-save').textContent='';}
      revision=data.revision||'';pageRows=data.rows;reviewCounts=data.review_counts||null;
      $('#review-progress').textContent=reviewCounts?`将进入分析 ${data.counts?.ok??0} 条 OK 记录（${data.counts?.matched_samples??0} 个匹配样本），排除 ${data.counts?.error??0} 条 ERROR，标注待补资料 ${data.pending_count??0} 条。已标注 ${reviewCounts.marked} / ${reviewCounts.total} 条${reviewCounts.unmarked?` · 还有 ${reviewCounts.unmarked} 条未标注`:' · 标注已完成'}。`:'尚未生成可审核清单';
      $('#match-changes').textContent=`相比上次确认：新增 OK 记录 ${data.changes?.new_ok_records??0}；消失或改变的 OK 记录 ${data.changes?.removed_or_changed_ok_records??0}。`;
      $('#match-path').textContent=data.path?'核对样本匹配结果并完成标注。':'尚未生成清单';
      const names={total:'记录',ok:'匹配成功',error:'匹配失败',file_pairs:'文件对',matched_samples:'匹配样本'};
      $('#match-counts').innerHTML=Object.entries(data.counts||{}).map(([k,v])=>`<span class="count-pill ${k}"><b>${v}</b>${esc(names[k]||k)}</span>`).join('');
      $('#match-row-count').textContent=`筛选后 ${data.total||0} 行 · 第 ${matchPage+1} 页`;
      $('#match-prev').disabled=!matchPage;$('#match-next').disabled=(matchPage+1)*50>=(data.total||0);
      $('#match-head').innerHTML=`<tr><th class="selection-cell"><input type="checkbox" id="select-page" aria-label="全选当前页" title="全选当前页"></th><th>审阅标注</th>${data.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr>`;
      $('#match-body').innerHTML=data.rows.map(r=>`<tr class="${r.status==='OK'?'':'error-row'}"><td class="selection-cell"><input type="checkbox" data-select-row="${esc(r._row_key)}" aria-label="选择记录 ${esc(r.sample_id||r._row_key)}"></td><td><select data-review="${esc(r._row_key)}" aria-label="审核标注 ${esc(r.sample_id||r._row_key)}">${['','已核对','待补资料'].map(v=>`<option value="${v}" ${data.reviews?.[r._row_key]?.label===v?'selected':''}>${v||'未标注'}</option>`).join('')}</select></td>${data.columns.map(c=>`<td title="${esc(r[c])}">${esc(r[c])}</td>`).join('')}</tr>`).join('');
      $('#match-empty').classList.toggle('hidden',Boolean(data.rows.length));
      $('#select-page').onchange=e=>{pageRows.forEach(r=>e.target.checked?selected.add(r._row_key):selected.delete(r._row_key));syncSelection();};
    }finally{if(token===matchRequest){matchLoading=false;syncSelection();}}
  }
  $('#next-unmarked').onclick=async()=>{
    const button=$('#next-unmarked');button.disabled=true;
    try{const data=await api(`/api/jobs/${id}/match-preview?after_key=${lastUnmarked}`);const target=data.next_unmarked;
      if(!target){$('#batch-message').textContent='全部记录已标注';return;}
      $('#unmarked-only').checked=false;$('#errors-only').checked=false;$('#match-search').value='';selected.clear();matchPage=Math.floor(target.offset/50);await loadMatch();
      lastUnmarked=Number(target.row_key);const field=document.querySelector(`[data-review="${target.row_key}"]`);field?.scrollIntoView({block:'center'});field?.focus();
    }catch(error){fail(error);}finally{button.disabled=false;}
  };
  $('#selection-clear').onclick=()=>{selected.clear();syncSelection();};
  $('#batch-apply').onclick=async()=>{
    if(batchBusy||matchLoading||!selected.size)return;
    batchBusy=true;const keys=[...selected],currentRevision=revision;syncSelection();$('#batch-message').textContent='正在保存…';
    try{
      const result=await api(`/api/jobs/${id}/review`,{revision:currentRevision,row_keys:keys,label:$('#batch-label').value});
      if(revision===currentRevision)selected.clear();
      await loadMatch();$('#batch-message').textContent=`已更新 ${result.updated} 条审核标注`;
    }catch(e){$('#batch-message').textContent=`保存失败：${errorText(e)}。所选记录已保留，可再次点击“应用到所选”重试。`;}
    finally{batchBusy=false;syncSelection();}
  };
  async function loadLog(){if(document.hidden||paused||logBusy)return;logBusy=true;const requestedAttempt=attempt;try{const data=await api(`/api/jobs/${id}/log?${new URLSearchParams({offset,source})}`);if(requestedAttempt!==attempt||(data.attempt_no!==undefined&&data.attempt_no!==attempt))return;if(data.reset)$('#log-view').textContent='';source=data.source||'';offset=data.next_offset;$('#log-view').textContent=($('#log-view').textContent+data.content).slice(-500000);$('#log-source').textContent=`第 ${attempt} 次执行 · ${data.source_label||'本次执行日志'}`;$('#log-error').classList.add('hidden');if($('#auto-scroll').checked)$('#log-view').scrollTop=$('#log-view').scrollHeight;}catch(error){$('#log-error').textContent=`日志刷新失败，保留已读取内容。${errorText(error)}`;$('#log-error').classList.remove('hidden');}finally{logBusy=false;}}
  let summaryKind='', summaryPage=0, summaryRequest=0, summaryLimit=5, summaryData=null, summaryCursors=[''], summaryShown=[0], summaryQuery='', summarySearchTimer;
  let artifactsData=null, artifactsSignature='', resultQuery='', resultChain='', resultState='', resultPage=0, downloadBusy=false, resultScroll=0;
  let mappingData=null, mappingLoading=false, mappingError='', mappingRequest=0, resultSearchTimer;
  const resultPageSize=50, resultStateLabels={success:'处理完成',empty:'结果为空',failed:'处理失败',pending:'尚未完成',archived:'已归档'};
  const summaryStates=new Map(), selectedColumns=new Map(), selectedResults=new Set(), mappingCatalog=new Map(), mappingSources=new Map();
  const resultViewKey=window.SCIGBLAST_USER_ID?`scigblast:result-view:v1:${JSON.stringify([window.SCIGBLAST_USER_ID,id])}`:null;
  let pendingPreview=null, resultViewReady=false;
  // Store view preferences only. Rows, cursors, download selections and URLs stay in memory.
  function persistResultView(){
    if(!resultViewReady||!resultViewKey||!job)return;
    const query=$('#mapping-result-search')?.value??resultQuery,state=summaryStates.get(summaryKind),file=resultEntry(summaryKind),previewVisible=$('#stage-summary-panel')&&!$('#stage-summary-panel').classList.contains('hidden');
    const preview=summaryKind?{kind:summaryKind,source_file:file?.source_file||pendingPreview?.source_file||'',
      query:previewVisible?$('#summary-query').value:state?.query??summaryQuery,limit:summaryLimit,
      columns:selectedColumns.has(summaryKind)?[...selectedColumns.get(summaryKind)]:null}:null;
    try{sessionStorage.setItem(resultViewKey,JSON.stringify({version:1,attempt,tab,
      mapping:{query,chain:resultChain,state:resultState,page:query===resultQuery?resultPage:0},preview}));}catch{}
  }
  function restoreResultView(){
    resultViewReady=true;
    if(!resultViewKey)return '';
    try{
      const saved=JSON.parse(sessionStorage.getItem(resultViewKey));
      if(!saved||saved.version!==1||saved.attempt!==attempt){sessionStorage.removeItem(resultViewKey);return '';}
      const text=value=>typeof value==='string'?value.slice(0,4096):'',mapping=saved.mapping||{};
      resultQuery=text(mapping.query);resultChain=text(mapping.chain);resultState=Object.hasOwn(resultStateLabels,mapping.state)?mapping.state:'';
      resultPage=Number.isSafeInteger(mapping.page)&&mapping.page>=0?mapping.page:0;
      const preview=saved.preview;
      if(preview&&text(preview.kind)){
        summaryKind=text(preview.kind);summaryLimit=[5,10,20,50].includes(preview.limit)?preview.limit:5;
        pendingPreview={kind:summaryKind,source_file:text(preview.source_file)};
        summaryStates.set(summaryKind,{query:text(preview.query),page:0,limit:summaryLimit,cursors:[''],shown:[0],data:null,
          notice:'已恢复浏览条件，当前表格从第一页重新读取。'});
        if(Array.isArray(preview.columns)&&preview.columns.every(column=>typeof column==='string'))selectedColumns.set(summaryKind,new Set(preview.columns.slice(0,256).map(text)));
      }
      return ['overview','match','log','artifacts','audit'].includes(saved.tab)?saved.tab:'';
    }catch{return '';}
  }
  function resetResultView(){
    clearTimeout(resultSearchTimer);clearTimeout(summarySearchTimer);artifactsRequest++;mappingRequest++;summaryRequest++;
    artifactsData=null;artifactsSignature='';mappingData=null;mappingLoading=false;mappingError='';pendingPreview=null;
    resultQuery='';resultChain='';resultState='';resultPage=0;resultScroll=0;summaryKind='';summaryQuery='';summaryLimit=5;resetSummaryPage();
    for(const values of [summaryStates,selectedColumns,selectedResults,mappingCatalog,mappingSources])values.clear();
    $('#artifacts-list').textContent='';
    try{if(resultViewKey)sessionStorage.removeItem(resultViewKey);}catch{}
  }
  function resetSummaryPage(){summaryPage=0;summaryCursors=[''];summaryShown=[0];summaryData=null;summaryRequest++;}
  function saveSummaryState(){
    clearTimeout(summarySearchTimer);
    resultScroll=$('#mapping-results-wrap')?.scrollTop??resultScroll;
    if(!summaryKind||!$('#summary-query')||$('#stage-summary-panel').classList.contains('hidden')){persistResultView();return;}
    const query=$('#summary-query').value;
    if(query!==summaryQuery){summaryQuery=query;resetSummaryPage();}
    const table=$('.summary-table');
    summaryStates.set(summaryKind,{query:summaryQuery,page:summaryPage,limit:summaryLimit,cursors:[...summaryCursors],shown:[...summaryShown],data:summaryData,
      left:table?.scrollLeft||0,top:table?.scrollTop||0,windowTop:tab==='artifacts'?window.scrollY:summaryStates.get(summaryKind)?.windowTop,
      version:resultEntry(summaryKind)?.file_version,notice:$('#summary-notice')?.textContent||''});
    persistResultView();
  }
  function resultEntry(kind){return mappingCatalog.get(kind)||(artifactsData?.reports||[]).find(file=>file.kind===kind);}
  function fileIdentity(file){const label=file.label||'';const index=label.lastIndexOf('__');return {sample:file.sample_key||file.sample_id||(index<0?label:label.slice(0,index)),chain:file.chain||(index<0?'—':label.slice(index+2))};}
  function resultNotice(message){$('#summary-notice').textContent=message;$('#summary-notice').classList.toggle('hidden',!message);}
  function restoreSummaryScroll(state){requestAnimationFrame(()=>{if(tab!=='artifacts')return;const table=$('.summary-table');if(table&&state){table.scrollLeft=state.left||0;table.scrollTop=state.top||0;if(state.windowTop!==undefined)window.scrollTo(0,state.windowTop);}});}
  $('#artifacts-refresh').onclick=()=>loadArtifacts(true).catch(fail);
  const columnLabels = {sample:'样本',sample_id:'样本',sample_key:'样本',chain:'链',sequence_id:'序列编号',umi_count:'UMI count',umi_counts:'UMI 支持数',v_call:'V 基因',d_call:'D 基因',j_call:'J 基因',junction:'CDR3 核苷酸',junction_aa:'CDR3 氨基酸',productive:'有效重排',total_reads:'输入 reads',matched_reads:'匹配 reads',matched_pct:'匹配比例 %',mismatch_reads:'未匹配 reads',mismatch_pct:'未匹配比例 %',discarded_pct:'丢弃比例 %',short_reads:'过短 reads',processed_reads:'处理 reads',Total_Reads:'输入 reads',OK_Reads:'合并成功',Merged_Percent:'合并比例 %',Merged_FASTA_Records:'合并序列数',Unaligned_FASTA_Records:'未合并序列数',before_reads:'过滤前 reads',after_reads:'过滤后 reads',reads_retained_pct:'保留比例 %',status:'状态',error:'原因'};
  function reportCards(reports){return `<div class="report-list" aria-label="阶段报告">${reports.map(r=>`<section class="report-item ${r.exists?'':'pending'}"><button class="report-open" data-summary="${esc(r.kind)}" ${r.exists?'':'disabled'} aria-pressed="false"><span>${esc(r.label)}</span><small>${r.exists?'查看统计':'尚未生成'}</small></button>${r.exists?`<a class="report-download" href="/api/jobs/${id}/download?kind=${encodeURIComponent(r.kind)}" aria-label="下载 ${esc(r.label)}">下载</a>`:''}</section>`).join('')}</div>`;}
  function previewPanel(){return `<section id="stage-summary-panel" class="hidden" aria-label="文件预览"><div class="tab-toolbar preview-toolbar"><div class="result-preview-heading"><span class="result-section-label">数据预览</span><h3 id="stage-summary-title"></h3></div><div class="preview-actions"><button id="summary-refresh" class="button button-ghost button-small">刷新</button><a id="summary-download" class="button button-primary button-small">下载当前文件</a></div></div><p id="stage-summary-description" class="muted"></p><div id="result-metrics" class="result-metrics"></div><p id="summary-notice" class="result-notice hidden" role="status"></p><div class="summary-filter"><input id="summary-query" type="search" placeholder="搜索当前文件的内容" aria-label="搜索当前结果"><label>每页 <select id="summary-limit" aria-label="每页记录数"><option value="5">5 条</option><option value="10">10 条</option><option value="20">20 条</option><option value="50">50 条</option></select></label><details class="column-picker"><summary>选择字段</summary><div class="column-picker-menu"><input id="column-search" type="search" placeholder="搜索字段" aria-label="搜索字段"><div class="column-picker-actions" role="group" aria-label="批量选择全部字段"><button type="button" data-columns="all" class="button button-ghost button-small">全选</button><button type="button" data-columns="none" class="button button-ghost button-small">全不选</button><button type="button" data-columns="invert" class="button button-ghost button-small">反选</button></div><small class="muted">对全部字段生效，搜索仅筛选列表</small><div id="column-options"></div></div></details></div><p id="stage-summary-info" class="muted" role="status"></p><div class="table-wrap summary-table"><table><thead id="stage-summary-head"></thead><tbody id="stage-summary-body"></tbody></table></div><dialog id="record-detail" class="record-detail" aria-labelledby="record-title"><div class="tab-toolbar"><h3 id="record-title">记录详情</h3><button id="record-close" class="button button-ghost">关闭</button></div><dl id="record-fields"></dl></dialog><div class="pagination"><button id="summary-prev" class="button button-ghost">上一页</button><button id="summary-next" class="button button-ghost">下一页</button></div></section>`;}
  function previewWorkspace(){return `<div class="result-preview-column">${previewPanel()}<div class="result-preview-empty"><svg viewBox="0 0 40 48" width="40" height="48" fill="none" aria-hidden="true"><path d="M8 2h17l11 11v33H4V2h4Z" stroke="currentColor" stroke-width="2"/><path d="M25 2v12h11M12 24h16M12 31h16M12 38h10" stroke="currentColor" stroke-width="2"/></svg><strong>选择文件，查看数据</strong><p>从文件列表中选择可用结果，<br>在这里搜索记录、选择字段或下载。</p></div></div>`;}
  function mappingOverview(){return `<section class="result-overview" aria-label="结果概览"><div class="result-overview-title"><div><span class="result-section-label">最终结果</span><h3>各样本 UMI count</h3></div><p>每行对应一个样本的一条链。umi_count 保留原 CSV 的 copy。</p></div><div id="mapping-result-counts" class="result-counts" aria-label="按处理状态筛选"></div><p id="mapping-inventory-note" class="result-inventory-note"></p></section>`;}
  function mappingWorkspace(){
    return `<section class="result-workspace" aria-labelledby="mapping-results-title"><div class="result-workspace-header"><div class="result-list-heading"><h3 id="mapping-results-title">样本文件</h3><span class="muted">选择预览 · 勾选下载</span></div><div class="result-workspace-tools"><input id="mapping-result-search" type="search" aria-label="搜索结果样本" placeholder="搜索样本名称 / 相对目录" value="${esc(resultQuery)}"><div class="result-filter-row"><select id="mapping-result-chain" aria-label="筛选结果链"><option value="">全部链</option></select><select id="mapping-result-state" aria-label="筛选处理状态"><option value="">全部状态</option>${Object.entries(resultStateLabels).map(([state,label])=>`<option value="${state}" ${state===resultState?'selected':''}>${label}</option>`).join('')}</select><button id="mapping-filter-clear" class="button button-ghost button-small">清除筛选</button></div></div></div><div class="result-list-status"><span id="mapping-list-status" class="muted" role="status" aria-live="polite"></span><button id="mapping-list-retry" class="button button-ghost button-small hidden">重试读取</button></div><div id="mapping-results-wrap" class="table-wrap" aria-busy="true" tabindex="0" aria-label="样本结果列表"><table><thead><tr><th><input id="mapping-select-visible" type="checkbox" aria-label="选择当前页可下载结果" title="选择当前页可下载结果"></th><th>样本 / 链</th><th>状态</th><th>序列条数</th><th>操作</th></tr></thead><tbody id="mapping-results-body"></tbody></table></div><div class="result-workspace-footer"><div class="result-selection-bar"><div><span id="mapping-selection-info" class="muted" role="status"></span><button id="mapping-selection-clear" class="button button-ghost button-small">清空选择</button></div><button id="mapping-download-selected" class="button button-primary" disabled>下载所选结果</button></div><nav class="result-pagination" aria-label="样本结果分页"><button id="mapping-results-prev" class="button button-ghost button-small">上一页</button><span id="mapping-page-info" class="muted"></span><button id="mapping-results-next" class="button button-ghost button-small">下一页</button></nav></div><p id="mapping-download-status" class="muted result-download-status" role="status"></p></section>`;
  }
  function visibleMappingFiles(){return mappingLoading||mappingError?[]:(mappingData?.rows||[]);}
  function downloadableResult(file){return Boolean(file.kind&&file.available&&file.availability==='available');}
  function numberLabel(value){return value===null||value===undefined||value===''?'—':Number(value).toLocaleString('zh-CN');}
  function retainedLabel(file){return file.retained_percent===null||file.retained_percent===undefined?'—':`${Number(file.retained_percent).toFixed(1)}%`;}
  function renderMappingResults(){
    if(!$('#mapping-results-body'))return;
    const files=visibleMappingFiles();
    $('#mapping-results-wrap').setAttribute('aria-busy',String(mappingLoading));
    $('#mapping-list-status').textContent=mappingLoading?'正在读取样本结果…':mappingError?`读取失败：${mappingError}。筛选和已选结果已保留。`:`筛选后 ${numberLabel(mappingData?.total||0)} 条样本链记录 · 每页 ${resultPageSize} 条`;
    $('#mapping-list-retry').classList.toggle('hidden',!mappingError);
    if(mappingData){
      const focusedState=document.activeElement?.closest('[data-result-state]')?.dataset.resultState;
      $('#mapping-result-counts').innerHTML=Object.entries(resultStateLabels).map(([state,label])=>`<button type="button" class="result-count result-state-${state}" data-result-state="${state}" aria-pressed="${state===resultState}" aria-label="${label} ${numberLabel(mappingData.counts?.[state]??0)} 个，点击筛选"><span>${label}</span><strong>${numberLabel(mappingData.counts?.[state]??0)}</strong></button>`).join('');
      if(focusedState)$('#mapping-result-counts').querySelector(`[data-result-state="${focusedState}"]`)?.focus({preventScroll:true});
      $('#mapping-inventory-note').textContent=`${mappingData.inventory_complete?'以上统计覆盖创建任务时选中的输入文件。':'此历史任务未保存完整输入清单，以下仅展示现有摘要记录，不能据此判断是否包含全部样本。'} 状态来自处理摘要；当前页另行检查文件是否可用。`;
      $('#mapping-result-chain').innerHTML=`<option value="">全部链</option>${[...new Set([...(mappingData.chains||[]),...(resultChain?[resultChain]:[])])].map(chain=>`<option value="${esc(chain)}" ${chain===resultChain?'selected':''}>${esc(chain)}</option>`).join('')}`;
    }
    $('#mapping-results-body').innerHTML=files.length?files.map(file=>{
      const identity=fileIdentity(file),available=downloadableResult(file),intermediate=file.intermediate;
      const stateLabel=`${file.state_label||resultStateLabels[file.state]||'尚未完成'}${file.availability==='missing'?' · 文件缺失':''}`;
      return `<tr class="${file.kind&&file.kind===summaryKind?'result-selected':''}" data-result-kind="${esc(file.kind||'')}" data-source-file="${esc(file.source_file||'')}"><td><input type="checkbox" data-select-result="${esc(file.kind||'')}" aria-label="选择 ${esc(identity.sample)} ${esc(identity.chain)}" ${available?'':'disabled'} ${available&&selectedResults.has(file.kind)?'checked':''}></td><td class="result-sample"><strong>${esc(identity.sample)}</strong><span class="result-chain">${esc(identity.chain)}</span></td><td class="result-state-cell"><span class="result-state result-state-${esc(file.state)}">${esc(stateLabel)}</span>${file.error?`<small class="result-reason">${esc(file.error)}</small>`:''}</td><td class="result-number"><strong>${numberLabel(file.output_rows)}</strong><small>比对 ${numberLabel(file.input_sequences)}</small><small>保留 ${retainedLabel(file)}</small></td><td><div class="result-actions"><button class="button button-ghost button-small" data-preview-result="${esc(file.kind||'')}" aria-pressed="${Boolean(file.kind&&file.kind===summaryKind)}" ${available?'':'disabled'}>预览</button>${available?`<a class="button button-ghost button-small" href="/api/jobs/${id}/download?kind=${encodeURIComponent(file.kind)}" aria-label="下载 ${esc(identity.sample)} ${esc(identity.chain)}">下载</a>`:'<span class="muted result-unavailable">暂不可下载</span>'}</div>${intermediate?.kind?`<details class="result-row-secondary"><summary>IgBLAST 中间结果</summary><div class="result-actions"><button class="button button-ghost button-small" data-preview-result="${esc(intermediate.kind)}">预览 02</button><a class="button button-ghost button-small" href="/api/jobs/${id}/download?kind=${encodeURIComponent(intermediate.kind)}">下载 02</a></div></details>`:''}</td></tr>`;
    }).join(''):`<tr><td colspan="5" class="result-list-empty">${mappingLoading?'正在读取样本结果…':mappingError?'暂时无法读取，请点击“重试读取”。':mappingData?.counts?.total?'没有符合当前搜索和筛选条件的记录。':'尚未读取到样本处理记录，可查看阶段统计和运行日志。'}</td></tr>`;
    const pages=Math.max(1,Math.ceil((mappingData?.total||0)/resultPageSize));
    $('#mapping-page-info').textContent=`第 ${resultPage+1} / ${pages} 页`;
    $('#mapping-results-prev').disabled=mappingLoading||Boolean(mappingError)||resultPage===0;
    $('#mapping-results-next').disabled=mappingLoading||Boolean(mappingError)||resultPage+1>=pages;
    $('#mapping-filter-clear').disabled=!(resultQuery||resultChain||resultState);
    syncResultSelection();
  }
  function syncResultSelection(){
    const all=$('#mapping-select-visible');if(!all)return;
    const visible=visibleMappingFiles().filter(downloadableResult),count=visible.filter(file=>selectedResults.has(file.kind)).length;
    all.checked=Boolean(visible.length)&&count===visible.length;all.indeterminate=count>0&&count<visible.length;all.disabled=!visible.length;
    $('#mapping-selection-info').textContent=`当前页可下载 ${visible.length} 个 · 已选择 ${selectedResults.size} 个${selectedResults.size>count?`（其中 ${selectedResults.size-count} 个不在当前页）`:''}`;
    $('#mapping-selection-clear').disabled=!selectedResults.size;
    $('#mapping-download-selected').disabled=downloadBusy||!selectedResults.size;
  }
  async function downloadSelectedResults(){
    if(downloadBusy||!selectedResults.size)return;
    const button=$('#mapping-download-selected'),kinds=[...selectedResults];downloadBusy=true;UI.setBusy(button,true,'正在准备下载…');
    $('#mapping-download-status').textContent='正在准备所选最终结果…';
    try{const result=await api(`/api/jobs/${id}/mapping-download`,{kinds});const link=document.createElement('a');link.href=result.url;link.download='';document.body.append(link);link.click();link.remove();$('#mapping-download-status').textContent=`已准备 ${result.file_count} 个最终结果，浏览器将开始下载。`;}
    catch(error){$('#mapping-download-status').textContent=`下载准备失败：${errorText(error)}。选择已保留，可再次点击下载。`;}
    finally{downloadBusy=false;UI.setBusy(button,false);syncResultSelection();}
  }
  function bindMappingWorkspace(){
    if(!$('#mapping-results-body'))return;
    const applyFilters=()=>{clearTimeout(resultSearchTimer);resultQuery=$('#mapping-result-search').value;resultChain=$('#mapping-result-chain').value;resultState=$('#mapping-result-state').value;resultPage=0;resultScroll=0;loadMappingResults();};
    $('#mapping-result-search').oninput=()=>{clearTimeout(resultSearchTimer);mappingRequest++;mappingLoading=true;mappingError='';renderMappingResults();resultSearchTimer=setTimeout(applyFilters,300);};
    $('#mapping-result-search').onkeydown=event=>{if(event.key==='Enter')applyFilters();};
    $('#mapping-result-chain').onchange=$('#mapping-result-state').onchange=applyFilters;
    $('#mapping-result-counts').onclick=event=>{const button=event.target.closest('[data-result-state]');if(!button)return;$('#mapping-result-state').value=resultState===button.dataset.resultState?'':button.dataset.resultState;applyFilters();};
    $('#mapping-filter-clear').onclick=()=>{$('#mapping-result-search').value='';$('#mapping-result-chain').value='';$('#mapping-result-state').value='';applyFilters();};
    $('#mapping-select-visible').onchange=event=>{visibleMappingFiles().filter(downloadableResult).forEach(file=>event.target.checked?selectedResults.add(file.kind):selectedResults.delete(file.kind));renderMappingResults();};
    $('#mapping-results-body').onchange=event=>{const kind=event.target.dataset.selectResult;if(!kind)return;event.target.checked?selectedResults.add(kind):selectedResults.delete(kind);syncResultSelection();};
    $('#mapping-results-body').onclick=event=>{const button=event.target.closest('[data-preview-result]');if(!button)return;chooseSummary(button.dataset.previewResult);if(!window.matchMedia('(min-width:1280px)').matches)$('#stage-summary-panel').scrollIntoView({block:'start'});};
    $('#mapping-selection-clear').onclick=()=>{selectedResults.clear();renderMappingResults();};
    $('#mapping-download-selected').onclick=downloadSelectedResults;
    $('#mapping-results-prev').onclick=()=>{resultPage--;resultScroll=0;loadMappingResults();};
    $('#mapping-results-next').onclick=()=>{resultPage++;resultScroll=0;loadMappingResults();};
    $('#mapping-list-retry').onclick=()=>loadMappingResults();
    renderMappingResults();$('#mapping-results-wrap').scrollTop=resultScroll;
  }
  function rememberMappingFile(file){
    if(!file?.kind)return;
    mappingCatalog.set(file.kind,file);
    const state=summaryStates.get(file.kind);
    if(state?.version&&file.file_version&&state.version!==file.file_version){state.page=0;state.cursors=[''];state.shown=[0];state.data=null;state.left=0;state.top=0;state.windowTop=undefined;state.version=file.file_version;state.notice='结果文件已更新，已从第一页重新读取；搜索条件和字段选择已保留。';if(summaryKind===file.kind){summaryData=null;}}
  }
  async function restoreMappingPreview(token){
    const saved=pendingPreview;
    if(!saved)return;
    if(!resultEntry(saved.kind)&&saved.source_file){
      const data=await api(`/api/jobs/${id}/mapping-results?${new URLSearchParams({query:saved.source_file,limit:200})}`);
      if(token!==mappingRequest)return;
      const row=data.rows?.find(row=>row.source_file===saved.source_file);
      if(row){rememberMappingFile(row);rememberMappingFile(row.intermediate);}
    }
    if(token!==mappingRequest)return;
    pendingPreview=null;
    const file=resultEntry(saved.kind);
    if(!file||file.availability&&file.availability!=='available')summaryKind='';
  }
  async function loadMappingResults({force=false}={}){
    if(job?.pipeline!=='mapping'||!$('#mapping-results-body'))return;
    const token=++mappingRequest;mappingLoading=true;mappingError='';renderMappingResults();
    try{
      const data=await api(`/api/jobs/${id}/mapping-results?${new URLSearchParams({query:resultQuery,chain:resultChain,state:resultState,offset:resultPage*resultPageSize,limit:resultPageSize})}`);
      if(token!==mappingRequest)return;
      if(resultPage>0&&resultPage*resultPageSize>=data.total){resultPage=Math.max(0,Math.floor((data.total-1)/resultPageSize));return await loadMappingResults({force});}
      mappingData=data;mappingLoading=false;
      for(const row of data.rows||[]){
        const previous=mappingSources.get(row.source_file)||{},priorKind=previous.kind;
        if(priorKind&&(!downloadableResult(row)||priorKind!==row.kind)){selectedResults.delete(priorKind);const prior=mappingCatalog.get(priorKind);if(prior)mappingCatalog.set(priorKind,{...prior,available:false,preview:false,availability:row.availability,error:row.error});}
        if(previous.intermediateKind&&previous.intermediateKind!==row.intermediate?.kind){const prior=mappingCatalog.get(previous.intermediateKind);if(prior)mappingCatalog.set(previous.intermediateKind,{...prior,available:false,preview:false,availability:'not_ready'});}
        mappingSources.set(row.source_file,{kind:row.kind||priorKind,intermediateKind:row.intermediate?.kind||previous.intermediateKind});
        if(row.kind){rememberMappingFile(row);if(!downloadableResult(row))selectedResults.delete(row.kind);}
        rememberMappingFile(row.intermediate);
      }
      if(job.status==='ARCHIVED'){selectedResults.clear();for(const [kind,file] of mappingCatalog)mappingCatalog.set(kind,{...file,available:false,preview:false,availability:'archived'});}
      await restoreMappingPreview(token);if(token!==mappingRequest)return;
      renderMappingResults();$('#mapping-results-wrap').scrollTop=resultScroll;
      const active=resultEntry(summaryKind);
      if(active?.availability&&active.availability!=='available'){
        summaryRequest++;summaryData=null;$('#stage-summary-panel').classList.add('hidden');
      }else if(summaryKind&&active&&(!summaryData||force))chooseSummary(summaryKind,{force});
      else if(!summaryKind){const first=data.rows?.find(downloadableResult);if(first)chooseSummary(first.kind);}
      persistResultView();
    }catch(error){if(token!==mappingRequest)return;mappingLoading=false;mappingError=errorText(error);renderMappingResults();}
  }
  async function loadArtifacts(force=false){
    if(document.hidden)return;
    const token=++artifactsRequest;
    const data=await api(`/api/jobs/${id}/artifacts${job.pipeline==='mapping'?'?include_mapping_files=false':''}`);
    if(token!==artifactsRequest)return;
    const signature=JSON.stringify([data.archive_available,data.reports,data.mapping_files]);
    if(artifactsData&&signature===artifactsSignature&&!force){if(job.pipeline==='mapping')await loadMappingResults();if(token!==artifactsRequest)return;if(summaryKind&&!summaryData&&resultEntry(summaryKind)?.availability!=='missing')chooseSummary(summaryKind);else restoreSummaryScroll(summaryStates.get(summaryKind));return;}
    saveSummaryState();summaryRequest++;
    const previousKind=summaryKind;artifactsData=data;artifactsSignature=signature;
    for(const [kind,state] of summaryStates){const entry=resultEntry(kind);if(entry?.file_version&&state.version&&entry.file_version!==state.version){state.page=0;state.cursors=[''];state.shown=[0];state.data=null;state.left=0;state.top=0;state.windowTop=undefined;state.version=entry.file_version;state.notice='结果文件已更新，已从第一页重新读取；搜索条件和字段选择已保留。';}}
    const reports=data.reports||[],mapping=job.pipeline==='mapping';
    $('#artifacts-list').innerHTML=`${data.archive_available?`<div class="result-archive"><div><strong>结果已归档</strong><p>下载完整压缩包，查看保存的分析文件。</p></div><a class="button button-primary" href="/api/jobs/${id}/download?kind=archive">下载完整压缩包</a></div>`:''}${mapping?mappingOverview():''}<div class="results-layout ${mapping?'results-layout-mapping':'results-layout-reports'}">${mapping?mappingWorkspace():`<aside class="result-report-navigation" aria-label="选择输出文件"><div class="result-list-heading"><h3>输出文件</h3><span class="muted">选择文件预览</span></div>${reportCards(reports)}</aside>`}${previewWorkspace()}</div>${mapping?`<details class="result-secondary"><summary>阶段统计与处理报告<span>查看各阶段汇总</span></summary>${reportCards(reports)}<p class="muted">各样本已筛选的 IgBLAST 中间结果位于对应样本行的“中间结果”入口。</p></details>`:''}`;
    bindMappingWorkspace();
    document.querySelectorAll('[data-summary]').forEach(button=>button.onclick=()=>{chooseSummary(button.dataset.summary);if(button.closest('.result-secondary')||!window.matchMedia('(min-width:1280px)').matches)$('#stage-summary-panel').scrollIntoView({block:'start'});});
    const applySearch=()=>{const query=$('#summary-query').value;if(query===summaryQuery)return;summaryQuery=query;resetSummaryPage();loadStageSummary().catch(fail);};
    $('#summary-query').oninput=()=>{clearTimeout(summarySearchTimer);summarySearchTimer=setTimeout(applySearch,300);};
    $('#summary-query').onkeydown=event=>{if(event.key==='Enter'){clearTimeout(summarySearchTimer);applySearch();}};
    $('#summary-limit').onchange=()=>{clearTimeout(summarySearchTimer);summaryQuery=$('#summary-query').value;summaryLimit=Number($('#summary-limit').value);resetSummaryPage();loadStageSummary().catch(fail);};
    $('#summary-refresh').onclick=()=>loadArtifacts(true).catch(fail);
    $('#column-search').oninput=()=>{const query=$('#column-search').value.toLocaleLowerCase();document.querySelectorAll('#column-options label').forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase().includes(query));};
    document.querySelectorAll('[data-columns]').forEach(button=>button.onclick=()=>{if(!summaryData)return;const chosen=selectedColumns.get(summaryKind)||new Set(summaryData.columns),mode=button.dataset.columns;selectedColumns.set(summaryKind,new Set(summaryData.columns.filter(column=>mode==='all'||(mode==='invert'&&!chosen.has(column)))));renderSummary();});
    $('#column-options').onchange=event=>{if(!event.target.matches('input[type=checkbox]'))return;const chosen=selectedColumns.get(summaryKind);if(event.target.checked)chosen.add(event.target.value);else chosen.delete(event.target.value);renderSummary();};
    $('#record-close').onclick=()=>$('#record-detail').close();
    $('#stage-summary-body').onclick=event=>{const button=event.target.closest('[data-record]');if(!button||!summaryData)return;const row=summaryData.rows[Number(button.dataset.record)];$('#record-fields').innerHTML=summaryData.columns.map(column=>`<dt>${esc(columnLabels[column]||column)}${columnLabels[column]?`<small>${esc(column)}</small>`:''}</dt><dd>${esc(row[column]||'—')}</dd>`).join('');$('#record-detail').showModal();};
    $('#summary-prev').onclick=()=>{if(summaryPage>0){summaryPage--;loadStageSummary().catch(fail);}};
    $('#summary-next').onclick=()=>{if(!summaryData)return;summaryCursors[summaryPage+1]=summaryData.next_cursor||'';summaryShown[summaryPage+1]=(summaryShown[summaryPage]||0)+summaryData.rows.length;summaryPage++;loadStageSummary().catch(fail);};
    summaryKind='';
    if(mapping){summaryKind=previousKind;summaryData=null;await loadMappingResults({force});if(token!==artifactsRequest)return;}
    if(!mapping||!summaryKind){
      const chosen=resultEntry(previousKind);
      const preferred=chosen&&(chosen.preview||chosen.exists)?chosen.kind:['results',...[...reports].reverse().map(report=>report.kind)].find(kind=>reports.some(report=>report.kind===kind&&report.exists));
      if(preferred)chooseSummary(preferred,{force});
    }
  }
  function chooseSummary(kind,{force=false}={}){
    const file=resultEntry(kind);
    if(!file||file.availability&&file.availability!=='available')return;
    if(summaryKind&&summaryData)saveSummaryState();
    summaryRequest++;summaryKind=kind;pendingPreview=null;
    const state=summaryStates.get(kind);
    summaryQuery=state?.query||'';summaryPage=state?.page||0;summaryLimit=state?.limit||5;summaryCursors=state?.cursors||[''];summaryShown=state?.shown||[0];summaryData=force?null:state?.data||null;
    $('#summary-query').value=summaryQuery;$('#summary-limit').value=String(summaryLimit);$('#stage-summary-title').textContent=file.label||'阶段统计';
    $('#stage-summary-description').textContent=file.stage==='03.umi_count'?'已按当前规则筛选的最终结果。umi_count 为原 CSV 的 copy；下方统计的是序列条数，不是 copy 总和。保留比例 = 最终序列条数 / 进入比对的序列条数。':file.stage==='02.igblastn_out'?'已筛选的 IgBLAST 中间结果，尚未添加 umi_count。':file.description||'';
    $('#summary-download').href=`/api/jobs/${id}/download?kind=${encodeURIComponent(kind)}`;
    $('#result-metrics').innerHTML=file.stage==='03.umi_count'?[['CSV 输入行数',numberLabel(file.input_rows)],['进入比对的序列条数',numberLabel(file.input_sequences)],['最终序列条数',numberLabel(file.output_rows)],['序列保留比例',retainedLabel(file)]].map(([label,value])=>`<div class="result-metric"><span>${label}</span><strong>${value}</strong></div>`).join(''):'';
    resultNotice(state?.notice||'');$('#stage-summary-panel').classList.remove('hidden');
    document.querySelectorAll('[data-summary]').forEach(button=>{const active=button.dataset.summary===kind;button.closest('.report-item').classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));});
    renderMappingResults();
    if(summaryData){renderSummary();restoreSummaryScroll(state);}else{$('#stage-summary-head').textContent='';$('#stage-summary-body').textContent='';loadStageSummary({restore:state}).catch(fail);}
  }
  async function loadStageSummary({restore=null,retryChanged=true}={}){
    if(document.hidden)return;
    const token=++summaryRequest,kind=summaryKind;
    $('#stage-summary-info').textContent='正在读取结果…';$('.summary-table').setAttribute('aria-busy','true');
    $('#summary-prev').disabled=true;$('#summary-next').disabled=true;
    try{
      const params=new URLSearchParams({kind,limit:summaryLimit,query:summaryQuery});
      if(summaryCursors[summaryPage])params.set('cursor',summaryCursors[summaryPage]);else if(summaryPage)params.set('offset',summaryPage*summaryLimit);
      const data=await api(`/api/jobs/${id}/stage-summary?${params}`);
      if(token!==summaryRequest||kind!==summaryKind)return;
      summaryData=data;renderSummary();saveSummaryState();restoreSummaryScroll(restore);
    }catch(error){
      if(token!==summaryRequest||kind!==summaryKind)return;
      if(error.status===409&&retryChanged){resetSummaryPage();resultNotice('结果文件或筛选已变化，已从第一页重新读取；搜索条件和字段选择已保留。');await loadStageSummary({retryChanged:false});return;}
      $('#stage-summary-info').textContent=`读取失败：${errorText(error)}。当前浏览条件已保留，请点击“刷新当前结果”重试。`;
      $('#summary-prev').disabled=summaryPage===0;
    }finally{if(token===summaryRequest)$('.summary-table').setAttribute('aria-busy','false');}
  }
  function renderSummary(){
    if(!summaryData)return;
    const data=summaryData;
    if(!selectedColumns.has(summaryKind)){const preferred=['sample_id','sample_key','chain','sequence_id','umi_count','umi_counts','v_call','d_call','j_call','junction_aa','productive'];const defaults=data.columns.includes('sequence_id')?preferred.filter(column=>data.columns.includes(column)):data.columns.slice(0,8);selectedColumns.set(summaryKind,new Set(defaults.length?defaults:data.columns.slice(0,8)));}
    const chosen=selectedColumns.get(summaryKind),columns=data.columns.filter(column=>chosen.has(column));
    $('#column-options').innerHTML=data.columns.map(column=>`<label><input type="checkbox" value="${esc(column)}" ${chosen.has(column)?'checked':''}>${esc(columnLabels[column]||column)}${columnLabels[column]?` · ${esc(column)}`:''}</label>`).join('');$('#column-search').oninput();
    const shown=(summaryShown[summaryPage]||0)+data.rows.length;
    const countText=data.total==null?`已展示 ${shown} 条匹配记录${data.scan_complete===false?' · 尚未扫描完整文件':''}`:`共 ${data.total} 条记录 · 当前第 ${summaryPage+1} 批`;
    const scanned=data.scanned_rows!=null?` · 已扫描 ${Number(data.scanned_rows).toLocaleString('zh-CN')} 条`:'';
    $('#stage-summary-info').textContent=data.path?`${countText}${scanned} · 显示 ${columns.length} / ${data.columns.length} 个字段`:(job.status==='ARCHIVED'?'结果已归档，请下载压缩包。':'尚未生成此结果');
    $('#stage-summary-head').innerHTML=`<tr>${columns.map(column=>`<th>${esc(columnLabels[column]||column)}${columnLabels[column]?`<small class="column-code">${esc(column)}</small>`:''}</th>`).join('')}<th>操作</th></tr>`;
    const emptyText=!data.path?'结果尚未生成':data.has_more?'本段尚未找到匹配记录，请继续查找。':summaryQuery.trim()?'搜索完成，没有更多符合条件的记录。':'此结果没有符合筛选条件的记录。';
    $('#stage-summary-body').innerHTML=data.rows.length?data.rows.map((row,index)=>`<tr class="${row.status==='ERROR'||(Number(row.total_reads)>0&&row.matched_reads!==undefined&&Number(row.matched_reads)===0)?'error-row':''}">${columns.map(column=>`<td title="${esc(row[column])}">${esc(row[column])}</td>`).join('')}<td><button class="button button-ghost button-small" data-record="${index}">详情</button></td></tr>`).join(''):`<tr><td colspan="${columns.length+1}">${emptyText}</td></tr>`;
    $('#summary-prev').disabled=summaryPage===0;$('#summary-next').disabled=data.has_more!==undefined?!data.has_more:(summaryPage+1)*summaryLimit>=data.total;
    $('#summary-next').textContent=data.rows.length===0&&data.has_more?'继续查找':'下一页';$('.summary-table').setAttribute('aria-busy','false');
  }
  async function action(name,button){
    if(busy)return;const restoreFocus=document.activeElement===button;busy=true;refreshGeneration++;UI.setBusy(button,true,'处理中…');$('#page-error').classList.add('hidden');
    try{
      if(name==='edit'){
        if(job.sample_source==='directory'){location.href=`/?edit_input_job=${encodeURIComponent(id)}`;return;}
        const data=await api(`/api/submissions?revision=${encodeURIComponent(job.submission_revision)}`);
        Submission.open(data,async updated=>{refreshGeneration++;await api(`/api/jobs/${id}/rematch`,{revision:updated.revision});revision='';await refresh(true);});return;
      }
      if(name==='confirm-match'){
        if(!revision||tab!=='match'){setTab('match');return;}await loadMatch();
        if(failedReview)throw new Error('还有未保存的审核标注，请先重试保存。');
        if(!reviewCounts||reviewCounts.unmarked)throw new Error(`还有 ${reviewCounts?.unmarked??'未知数量的'} 条 Match 记录未标注，请完成全部审阅标注。`);
        if(!await UI.confirm({title:'审核并继续分析',message:'全部记录已标注，确认本版清单并继续？仅 OK 记录进入下游；标注“待补资料”的 OK 记录也会进入分析。',confirmLabel:'确认并继续'}))return;
      }
      if(name==='stop'&&!await UI.confirm({title:'停止当前任务',message:`停止“${job.dataset}”的执行？已生成的中间产物会保留，可稍后断点续跑。`,confirmLabel:'停止任务',danger:true}))return;
      await api(`/api/jobs/${id}/${name}`,name==='confirm-match'?{revision}:{});await refresh(true);
    }catch(e){fail(e);}finally{busy=false;UI.setBusy(button,false);syncSelection();if(restoreFocus&&button?.isConnected&&!button.disabled)button.focus();}
  }
  $('#head-actions').onclick=async e=>{const b=e.target.closest('[data-action]');if(!b)return;if(b.dataset.action==='delete'){if(busy)return;busy=true;try{if(await Submission.deleteJob(job))location.href='/';}catch(error){fail(error);}finally{busy=false;}}else action(b.dataset.action,b);};document.querySelectorAll('.detail-tab').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
  async function saveReview(payload){
    if(batchBusy)return;batchBusy=true;syncSelection();$('#review-save').textContent='保存中…';$('#review-retry').classList.add('hidden');
    try{await api(`/api/jobs/${id}/review`,payload);failedReview=null;await loadMatch();$('#review-save').textContent='✓ 审核标注已保存';$('#review-save').classList.remove('error');}
    catch(error){failedReview=payload;$('#review-save').textContent=`保存失败，当前选择尚未保存：${errorText(error)}`;$('#review-save').classList.add('error');$('#review-retry').classList.remove('hidden');}
    finally{batchBusy=false;syncSelection();}
  }
  $('#review-retry').onclick=()=>{if(failedReview)saveReview(failedReview);};
  $('#match-body').onchange=e=>{if(e.target.dataset.selectRow!==undefined){e.target.checked?selected.add(e.target.dataset.selectRow):selected.delete(e.target.dataset.selectRow);syncSelection();return;}if(e.target.dataset.review===undefined)return;saveReview({revision,row_key:e.target.dataset.review,label:e.target.value});};
  $('#unmarked-only').onchange=$('#errors-only').onchange=()=>{selected.clear();syncSelection();matchPage=0;loadMatch().catch(fail);};
  $('#match-search').oninput=()=>{clearTimeout(matchSearchTimer);matchSearchTimer=setTimeout(()=>{selected.clear();syncSelection();matchPage=0;loadMatch().catch(fail);},300);};
  $('#match-search').onkeydown=event=>{if(event.key==='Enter'){clearTimeout(matchSearchTimer);selected.clear();syncSelection();matchPage=0;loadMatch().catch(fail);}};
  $('#match-refresh').onclick=()=>loadMatch().catch(fail);$('#match-prev').onclick=()=>{matchPage--;loadMatch().catch(fail);};$('#match-next').onclick=()=>{matchPage++;loadMatch().catch(fail);};
  const tabs=[...document.querySelectorAll('.detail-tab')];
  tabs.forEach(button=>{button.setAttribute('aria-controls','tab-'+button.dataset.tab);button.onkeydown=e=>{const visible=tabs.filter(b=>!b.classList.contains('hidden')),index=visible.indexOf(button);let next;if(e.key==='ArrowRight')next=(index+1)%visible.length;else if(e.key==='ArrowLeft')next=(index+visible.length-1)%visible.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=visible.length-1;else return;e.preventDefault();visible[next].focus();setTab(visible[next].dataset.tab);};});
  $('#pause-log').onclick=()=>{paused=!paused;$('#pause-log').textContent=paused?'继续刷新':'暂停刷新';};$('#copy-log').onclick=()=>navigator.clipboard.writeText($('#log-view').textContent).catch(fail);
  document.addEventListener('click',event=>{if(event.target.closest('[data-open-log]'))setTab('log');});
  $('#job-retry').onclick=()=>refresh(true);
  const activeStatuses=new Set(['QUEUED','RUNNING','MATCHING','STOPPING','ARCHIVING']);
  let nextDetailPoll=0,nextLogPoll=0;
  const detailPollDelay=()=>activeStatuses.has(job?.status)?5000:30000;
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(summarySearchTimer);saveSummaryState();return;}nextDetailPoll=Date.now()+detailPollDelay();refresh(true).catch(connectionError);if(tab==='log')loadLog().catch(fail);if(tab==='artifacts')loadArtifacts().catch(fail);});
  window.addEventListener('pagehide',saveSummaryState);
  refresh().catch(fail);
  setInterval(()=>{if(document.hidden||busy)return;const now=Date.now();if(now>=nextDetailPoll){nextDetailPoll=now+detailPollDelay();refresh().catch(connectionError);}if(tab==='log'&&now>=nextLogPoll){nextLogPoll=now+(activeStatuses.has(job?.status)?2000:30000);loadLog().catch(fail);}},1000);
})();

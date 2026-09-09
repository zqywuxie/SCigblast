(() => {
  const $=s=>document.querySelector(s),id=window.SCIGBLAST_JOB_ID,{api,esc}=Submission;
  let job,tab='overview',revision='',attempt=-1,matchPage=0,offset=0,source='',paused=false,busy=false,logBusy=false;
  const fmt=v=>v?new Date(v).toLocaleString('zh-CN'):'—',fail=e=>alert(e.message||e);
  const actionLabels = {create:'创建任务', 'confirm-match':'确认 Match 并继续', review:'审核标注', rematch:'更新资料并重新匹配', resume:'断点续跑', stop:'停止任务'};
  function actionDetail(a) {
    if(a.action==='confirm-match') { const attempt = String(a.details||'').split(':')[0]; return /^\d+$/.test(attempt) ? `已确认第 ${attempt} 次匹配结果，仅匹配成功的记录进入下游。` : '已确认匹配结果。'; }
    if(a.action==='review') { try { const r=JSON.parse(a.details); return `${r.row_keys ? new Set(r.row_keys).size : 1} 条记录 · ${r.label || '清除标注'}`; } catch { return '已保存审核标注'; } }
    return {create:'已提交配置，首先执行样本匹配。', rematch:'已更新 Submission 工作副本。', resume:'保留已有结果，继续执行。',stop:'已请求停止，保留中间结果。'}[a.action] || '';
  }
  function visibleOptions(options) {
    const items = [['数据集',job.dataset],['输出目录',job.output_root]];
    if(job.pipeline==='ir_split') items.push(['输入模式',options.ir_input_mode==='presplit'?'已拆分（presplit）':'未拆分（raw）'],['代表序列',options.ir_variant==='merged'?'不提取，使用全部合并序列':'提取代表序列'],['结果整理',({'auto':'自动','1':'启用','0':'关闭'})[options.run_preprocessing]||'自动']);
    return items;
  }
  $('#tab-match .match-filter').insertAdjacentHTML('beforeend',`<input id="match-search" placeholder="搜索样本 / 原因" aria-label="搜索 Match"><button id="match-refresh" class="button button-ghost">刷新</button><a href="/api/jobs/${id}/download?kind=match">下载清单</a>`);
  $('#tab-match').insertAdjacentHTML('beforeend','<div class="pagination"><button id="match-prev" class="button button-ghost">上一页</button><button id="match-next" class="button button-ghost">下一页</button></div><p>标注不改变匹配状态。补齐资料后请重新 Match。</p>');
  $('#tab-match').insertAdjacentHTML('beforeend','<button id="metadata-review" class="button button-ghost">检查登记样本 / 本次变化</button><div id="metadata-report" class="table-wrap"></div>');
  $('#metadata-review').onclick=async()=>{try{const [metadata,match]=await Promise.all([api(`/api/jobs/${id}/metadata-review`),api(`/api/jobs/${id}/match-preview`)]);$('#metadata-report').innerHTML=`<p>相比上次确认：新增 OK 记录 ${match.changes?.new_ok_records??0}；消失或改变的 OK 记录 ${match.changes?.removed_or_changed_ok_records??0}。以下 ${metadata.rows.length} 条登记未在清单识别（不等同于物理文件缺失）：</p><table><thead><tr><th>样本</th><th>Note</th><th>来源</th><th>说明</th></tr></thead><tbody>${metadata.rows.map(r=>`<tr><td>${esc(r.sample_id)}</td><td>${esc(r.note)}</td><td>${esc(r.file)} / ${esc(r.sheet)}:${r.row}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table>`;}catch(e){fail(e);}};
  function setTab(name){tab=name;document.querySelectorAll('.detail-tab').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));});document.querySelectorAll('.tab-content').forEach(p=>p.classList.toggle('active',p.id===`tab-${name}`));if(name==='match')loadMatch().catch(fail);if(name==='log')loadLog().catch(fail);if(name==='artifacts')loadArtifacts().catch(fail);}
  async function refresh(){const data=await api(`/api/jobs/${id}`);job=data.job;if(attempt!==job.attempt_no){attempt=job.attempt_no;revision='';offset=0;source='';$('#log-view').textContent='';if(tab==='match')loadMatch().catch(fail);}
    $('#job-id').textContent=id;$('#dataset-title').textContent=job.dataset;$('#job-subtitle').textContent=`${job.pipeline_label} · ${job.input_path}`;
    $('#hero-status').innerHTML=`<span class="status-badge ${job.status.toLowerCase()}">${esc(job.status)}</span><strong>${job.progress}%</strong><small>阶段完成比例</small>`;
    const actions=[];if(job.status==='WAITING_REVIEW')actions.push(['confirm-match','审核并继续']);if(['QUEUED','RUNNING','MATCHING'].includes(job.status))actions.push(['stop','停止']);if(['FAILED','STOPPED','INTERRUPTED','COMPLETED_WITHOUT_MARKER'].includes(job.status))actions.push(['resume','断点续跑']);if(!['QUEUED','RUNNING','MATCHING','STOPPING'].includes(job.status)&&job.submission_revision)actions.push(['edit','编辑资料 / 重新 Match']);$('#head-actions').innerHTML=actions.map(([a,t])=>`<button class="button ${a==='stop'?'button-danger':a==='edit'?'button-ghost':'button-primary'}" data-action="${a}">${t}</button>`).join('');
    $('#meta-cards').innerHTML=[['操作者',job.operator],['创建时间',fmt(job.created_at)],['阶段内进度',`${job.stage_progress}%（日志报告）`],['尝试次数',job.attempt_no]].map(([k,v])=>`<div class="meta-card"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('');
    $('#progress-total').textContent=`${job.completed_stages.length} / ${job.stages.length} 已完成`;$('#stages').innerHTML=job.stages.map(s=>{const done=job.completed_stages.includes(s),current=s===job.current_stage;return `<div class="stage-row ${done?'done':''} ${current?'current':''}"><span class="stage-check">${done?'✓':current?'●':'○'}</span><span><strong>${esc(job.stage_labels[s]||s)}</strong><small>${s}</small></span><span class="stage-state">${done?'DONE':current?job.status:'PENDING'}</span></div>`;}).join('');
    $('#overview-content').innerHTML=`<dl>${[['输入',job.input_path],['Submission 副本',job.submission_path],['Barcode',job.barcode_csv||'不需要'],['输出',job.output_root],['开始',fmt(job.started_at)],['结束',fmt(job.ended_at)],['退出码',job.exit_code??'—'],['错误',job.last_error||'无']].map(([k,v])=>`<dt>${k}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl>`;
    if(!['QUEUED','RUNNING','MATCHING','STOPPING'].includes(job.status)) $('#head-actions').insertAdjacentHTML('beforeend','<button class="button button-danger-ghost" data-action="delete">删除任务</button>');
    $('#options-list').innerHTML=visibleOptions(data.options||{}).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');$('#audit-timeline').innerHTML=(data.actions||[]).map(a=>`<div class="audit-item"><div><strong>${esc(actionLabels[a.action]||'操作记录')}</strong><small>${esc(a.operator)} · ${fmt(a.created_at)}</small><p>${esc(actionDetail(a))}</p></div></div>`).join('');
    syncSelection();
  }
  let matchRequest=0, matchLoading=false, batchBusy=false, pageRows=[], selected=new Set();
  $('#tab-match .match-table-wrap').before(Object.assign(document.createElement('div'), {className:'batch-review-bar', innerHTML:
    '<div><strong id="selection-count">已选 0 条</strong><small>跨页保留选择；更改筛选或清单版本后清空</small></div><div class="batch-review-actions"><select id="batch-label" aria-label="批量审核标注"><option value="已核对">已核对</option><option value="待补资料">待补资料</option><option value="">清除标注</option></select><button id="batch-apply" class="button button-primary" disabled>应用到所选</button><button id="selection-clear" class="button button-ghost" disabled>取消选择</button></div><span id="batch-message" role="status" aria-live="polite"></span>'}));
  function syncSelection(){
    const editable=job?.status==='WAITING_REVIEW'&&!batchBusy&&!matchLoading;
    $('#selection-count').textContent=`已选 ${selected.size} 条`;
    $('#batch-apply').disabled=!editable||!selected.size;$('#selection-clear').disabled=batchBusy||!selected.size;$('#batch-label').disabled=!editable;
    const all=$('#select-page'),checked=pageRows.filter(r=>selected.has(r._row_key)).length;
    if(all){all.checked=pageRows.length>0&&checked===pageRows.length;all.indeterminate=checked>0&&checked<pageRows.length;all.disabled=!editable||!pageRows.length;}
    document.querySelectorAll('[data-select-row]').forEach(c=>{c.checked=selected.has(c.dataset.selectRow);c.disabled=!editable;c.closest('tr').classList.toggle('selected-row',c.checked);});
    document.querySelectorAll('[data-review]').forEach(c=>c.disabled=!editable);
  }
  async function loadMatch(){
    const token=++matchRequest;matchLoading=true;syncSelection();
    try{
      const data=await api(`/api/jobs/${id}/match-preview?${new URLSearchParams({offset:matchPage*50,limit:50,query:$('#match-search').value,errors_only:$('#errors-only').checked})}`);
      if(token!==matchRequest)return;
      if(revision!==(data.revision||'')){selected.clear();$('#batch-message').textContent='';}
      revision=data.revision||'';pageRows=data.rows;
      $('#match-path').textContent=data.path||'尚未生成清单';
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
  $('#selection-clear').onclick=()=>{selected.clear();syncSelection();};
  $('#batch-apply').onclick=async()=>{
    if(batchBusy||matchLoading||!selected.size)return;
    batchBusy=true;const keys=[...selected],currentRevision=revision;syncSelection();$('#batch-message').textContent='正在保存…';
    try{
      const result=await api(`/api/jobs/${id}/review`,{revision:currentRevision,row_keys:keys,label:$('#batch-label').value});
      if(revision===currentRevision)selected.clear();
      await loadMatch();$('#batch-message').textContent=`已更新 ${result.updated} 条审核标注`;
    }catch(e){$('#batch-message').textContent=e.message;await loadMatch().catch(console.error);}
    finally{batchBusy=false;syncSelection();}
  };
  async function loadLog(){if(paused||logBusy)return;logBusy=true;try{const data=await api(`/api/jobs/${id}/log?${new URLSearchParams({offset,source})}`);if(data.reset)$('#log-view').textContent='';source=data.source||'';offset=data.next_offset;$('#log-view').textContent=($('#log-view').textContent+data.content).slice(-500000);if($('#auto-scroll').checked)$('#log-view').scrollTop=$('#log-view').scrollHeight;}finally{logBusy=false;}}
  let summaryKind='', summaryPage=0, summaryRequest=0;
  $('#artifacts-refresh').onclick=()=>loadArtifacts().catch(fail);
  const columnLabels = {sample:'样本',sample_id:'样本',sample_key:'样本',total_reads:'输入 reads',matched_reads:'匹配 reads',matched_pct:'匹配比例 %',mismatch_reads:'未匹配 reads',mismatch_pct:'未匹配比例 %',discarded_pct:'丢弃比例 %',short_reads:'过短 reads',processed_reads:'处理 reads',Total_Reads:'输入 reads',OK_Reads:'合并成功',Merged_Percent:'合并比例 %',Merged_FASTA_Records:'合并序列数',Unaligned_FASTA_Records:'未合并序列数',before_reads:'过滤前 reads',after_reads:'过滤后 reads',reads_retained_pct:'保留比例 %',status:'状态',error:'原因'};
  async function loadArtifacts(){
    const data=await api(`/api/jobs/${id}/artifacts`);
    $('#artifacts-list').innerHTML=`<div class="report-cards">${(data.reports||[]).map(r=>`<section class="report-card ${r.exists?'':'pending'}"><span class="report-status">${r.exists?'可查看':'尚未生成'}</span><h4>${esc(r.label)}</h4><p>${esc(r.description)}</p><div><button class="button button-ghost button-small" data-summary="${r.kind}" ${r.exists?'':'disabled'}>查看统计</button>${r.exists?`<a class="button button-ghost button-small" href="/api/jobs/${id}/download?kind=${r.kind}">下载 CSV / TSV</a>`:''}</div></section>`).join('')}</div><section id="stage-summary-panel" class="hidden"><div class="tab-toolbar"><h3 id="stage-summary-title"></h3><button id="summary-refresh" class="button button-ghost button-small">刷新统计</button></div><div class="summary-filter"><input id="summary-query" placeholder="搜索样本 / 状态" aria-label="搜索阶段统计"><label><input id="summary-all-columns" type="checkbox">显示路径等全部字段</label></div><p id="stage-summary-info" class="muted"></p><div class="table-wrap"><table><thead id="stage-summary-head"></thead><tbody id="stage-summary-body"></tbody></table></div><div class="pagination"><button id="summary-prev" class="button button-ghost">上一页</button><button id="summary-next" class="button button-ghost">下一页</button></div></section><details class="technical-paths"><summary>查看关键文件路径</summary>${data.files.map(f=>`<div class="artifact-row"><code>${esc(f.path)}</code><b>${f.exists?'存在':'未生成'}</b></div>`).join('')}</details>`;
    const reports=data.reports||[];
    document.querySelectorAll('.report-card').forEach((card,index)=>{
      const number=document.createElement('span'); number.className='report-number'; number.textContent=`报告 ${index+1}`; card.prepend(number);
    });
    const originalChoose = kind => document.querySelectorAll('[data-summary]').forEach(b=>{
      const active=b.dataset.summary===kind; b.closest('.report-card').classList.toggle('selected',active); b.setAttribute('aria-pressed',String(active));
    });
    const choose = kind => {summaryKind=kind;summaryPage=0;$('#stage-summary-title').textContent=reports.find(r=>r.kind===kind)?.label||'阶段统计';$('#stage-summary-panel').classList.remove('hidden');loadStageSummary().catch(fail);};
    document.querySelectorAll('[data-summary]').forEach(b=>b.onclick=()=>{originalChoose(b.dataset.summary);choose(b.dataset.summary);});
    $('#summary-query').onchange=()=>{summaryPage=0;loadStageSummary().catch(fail);};
    $('#summary-all-columns').onchange=$('#summary-refresh').onclick=()=>loadStageSummary().catch(fail);
    $('#summary-prev').onclick=()=>{summaryPage--;loadStageSummary().catch(fail);};$('#summary-next').onclick=()=>{summaryPage++;loadStageSummary().catch(fail);};
    const preferred=[summaryKind,'split','prefilter','pandaseq','fastp','results'].find(k=>reports.some(r=>r.kind===k&&r.exists));
    if(preferred){originalChoose(preferred);choose(preferred);}
  }
  async function loadStageSummary(){
    const token=++summaryRequest;
    $('#stage-summary-info').textContent='正在读取统计…';
    $('#stage-summary-head').textContent='';$('#stage-summary-body').textContent='';
    const data=await api(`/api/jobs/${id}/stage-summary?${new URLSearchParams({kind:summaryKind,offset:summaryPage*50,limit:50,query:$('#summary-query').value})}`);
    if(token!==summaryRequest)return;
    const hidden=/^(r1|r2|output_r1|output_r2|umi_sidecar|json_path|note)$|_path$/;
    const columns=data.columns.filter(c=>$('#summary-all-columns').checked||!hidden.test(c));
    $('#stage-summary-info').textContent=data.path?`${data.total} 条记录 · 第 ${summaryPage+1} 页 · 按脚本原始计数展示；不会重新计算。`:'尚未生成统计';
    $('#stage-summary-head').innerHTML=`<tr>${columns.map(c=>`<th>${esc(columnLabels[c]||c)}${columnLabels[c]?`<small class="column-code">${esc(c)}</small>`:''}</th>`).join('')}</tr>`;
    $('#stage-summary-body').innerHTML=data.rows.map(r=>`<tr class="${r.status==='ERROR'||(Number(r.total_reads)>0&&r.matched_reads!==undefined&&Number(r.matched_reads)===0)?'error-row':''}">${columns.map(c=>`<td title="${esc(r[c])}">${esc(r[c])}</td>`).join('')}</tr>`).join('');
    $('#summary-prev').disabled=summaryPage===0;$('#summary-next').disabled=(summaryPage+1)*50>=data.total;
  }
  async function action(name){if(busy)return;busy=true;try{if(name==='edit'){const data=await api(`/api/submissions?revision=${encodeURIComponent(job.submission_revision)}`);Submission.open(data,async updated=>{await api(`/api/jobs/${id}/rematch`,{revision:updated.revision});revision='';await refresh();});return;}if(name==='confirm-match'){if(!revision||tab!=='match'){setTab('match');return;}if(!confirm('确认本版清单？仅 OK 记录进入下游，ERROR 保留待补。'))return;}if(name==='stop'&&!confirm('停止任务并保留中间产物？'))return;await api(`/api/jobs/${id}/${name}`,name==='confirm-match'?{revision}:{});await refresh();}catch(e){fail(e);}finally{busy=false;}}
  $('#head-actions').onclick=async e=>{const b=e.target.closest('[data-action]');if(!b)return;if(b.dataset.action==='delete'){if(busy)return;busy=true;try{if(await Submission.deleteJob(job))location.href='/';}catch(error){fail(error);}finally{busy=false;}}else action(b.dataset.action);};document.querySelectorAll('.detail-tab').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
  $('#match-body').onchange=async e=>{if(e.target.dataset.selectRow!==undefined){e.target.checked?selected.add(e.target.dataset.selectRow):selected.delete(e.target.dataset.selectRow);syncSelection();return;}if(!e.target.dataset.review)return;e.target.disabled=true;try{await api(`/api/jobs/${id}/review`,{revision,row_key:e.target.dataset.review,label:e.target.value});}catch(error){fail(error);await loadMatch();}finally{syncSelection();}};
  $('#errors-only').onchange=$('#match-search').onchange=()=>{selected.clear();syncSelection();matchPage=0;loadMatch().catch(fail);};$('#match-refresh').onclick=()=>loadMatch().catch(fail);$('#match-prev').onclick=()=>{matchPage--;loadMatch().catch(fail);};$('#match-next').onclick=()=>{matchPage++;loadMatch().catch(fail);};
  $('#pause-log').onclick=()=>{paused=!paused;$('#pause-log').textContent=paused?'继续刷新':'暂停刷新';};$('#copy-log').onclick=()=>navigator.clipboard.writeText($('#log-view').textContent).catch(fail);
  refresh().then(()=>{if(location.hash==='#match')setTab('match');}).catch(fail);setInterval(()=>refresh().catch(console.error),5000);setInterval(()=>{if(tab==='log')loadLog().catch(console.error);},2000);
})();

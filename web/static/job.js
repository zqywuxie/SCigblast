(() => {
  const $=s=>document.querySelector(s),id=window.SCIGBLAST_JOB_ID,{api,esc}=Submission;
  let job,tab='overview',revision='',attempt=-1,matchPage=0,resultPage=0,offset=0,source='',paused=false,busy=false,logBusy=false;
  const fmt=v=>v?new Date(v).toLocaleString('zh-CN'):'—',fail=e=>alert(e.message||e);
  document.querySelector('.detail-tabs').insertAdjacentHTML('beforeend','<button class="detail-tab" data-tab="results" role="tab">IgBLAST 统计</button>');
  $('#tab-match .match-filter').insertAdjacentHTML('beforeend',`<input id="match-search" placeholder="搜索样本 / 原因" aria-label="搜索 Match"><button id="match-refresh" class="button button-ghost">刷新</button><a href="/api/jobs/${id}/download?kind=match">下载清单</a>`);
  $('#tab-match').insertAdjacentHTML('beforeend','<div class="pagination"><button id="match-prev" class="button button-ghost">上一页</button><button id="match-next" class="button button-ghost">下一页</button></div><p>标注不改变匹配状态。补齐资料后请重新 Match。</p>');
  $('#tab-match').insertAdjacentHTML('beforeend','<button id="metadata-review" class="button button-ghost">检查登记样本 / 本次变化</button><div id="metadata-report" class="table-wrap"></div>');
  $('#metadata-review').onclick=async()=>{try{const [metadata,match]=await Promise.all([api(`/api/jobs/${id}/metadata-review`),api(`/api/jobs/${id}/match-preview`)]);$('#metadata-report').innerHTML=`<p>相比上次确认：新增 OK 记录 ${match.changes?.new_ok_records??0}；消失或改变的 OK 记录 ${match.changes?.removed_or_changed_ok_records??0}。以下 ${metadata.rows.length} 条登记未在清单识别（不等同于物理文件缺失）：</p><table><thead><tr><th>样本</th><th>Note</th><th>来源</th><th>说明</th></tr></thead><tbody>${metadata.rows.map(r=>`<tr><td>${esc(r.sample_id)}</td><td>${esc(r.note)}</td><td>${esc(r.file)} / ${esc(r.sheet)}:${r.row}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table>`;}catch(e){fail(e);}};
  document.querySelector('.tab-panel').insertAdjacentHTML('beforeend',`<div id="tab-results" class="tab-content"><div class="tab-toolbar"><h3>IgBLAST mapping / 筛选统计</h3><a href="/api/jobs/${id}/download?kind=results">下载 Summary</a></div><p>mapped / mapping_percent 为匹配统计；filtered / filtered_percent 为筛选后统计。按脚本原始列展示。</p><input id="result-search" placeholder="搜索样本或链" aria-label="搜索结果"><button id="result-refresh" class="button button-ghost">刷新</button><p id="result-info"></p><div class="table-wrap"><table><thead id="result-head"></thead><tbody id="result-body"></tbody></table></div><div class="pagination"><button id="result-prev" class="button button-ghost">上一页</button><button id="result-next" class="button button-ghost">下一页</button></div></div>`);
  function setTab(name){tab=name;document.querySelectorAll('.detail-tab').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));});document.querySelectorAll('.tab-content').forEach(p=>p.classList.toggle('active',p.id===`tab-${name}`));if(name==='match')loadMatch().catch(fail);if(name==='results')loadResults().catch(fail);if(name==='log')loadLog().catch(fail);if(name==='artifacts')loadArtifacts().catch(fail);}
  async function refresh(){const data=await api(`/api/jobs/${id}`);job=data.job;if(attempt!==job.attempt_no){attempt=job.attempt_no;revision='';offset=0;source='';$('#log-view').textContent='';if(tab==='match')loadMatch().catch(fail);}
    $('#job-id').textContent=id;$('#dataset-title').textContent=job.dataset;$('#job-subtitle').textContent=`${job.pipeline_label} · ${job.input_path}`;
    $('#hero-status').innerHTML=`<span class="status-badge ${job.status.toLowerCase()}">${esc(job.status)}</span><strong>${job.progress}%</strong><small>阶段完成比例</small>`;
    const actions=[];if(job.status==='WAITING_REVIEW')actions.push(['confirm-match','审核并继续']);if(['QUEUED','RUNNING','MATCHING'].includes(job.status))actions.push(['stop','停止']);if(['FAILED','STOPPED','INTERRUPTED','COMPLETED_WITHOUT_MARKER'].includes(job.status))actions.push(['resume','断点续跑']);if(!['QUEUED','RUNNING','MATCHING','STOPPING'].includes(job.status)&&job.submission_revision)actions.push(['edit','编辑资料 / 重新 Match']);$('#head-actions').innerHTML=actions.map(([a,t])=>`<button class="button ${a==='stop'?'button-danger':a==='edit'?'button-ghost':'button-primary'}" data-action="${a}">${t}</button>`).join('');
    $('#meta-cards').innerHTML=[['操作者',job.operator],['创建时间',fmt(job.created_at)],['阶段内进度',`${job.stage_progress}%（日志报告）`],['尝试次数',job.attempt_no]].map(([k,v])=>`<div class="meta-card"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('');
    $('#progress-total').textContent=`${job.completed_stages.length} / ${job.stages.length} 已完成`;$('#stages').innerHTML=job.stages.map(s=>{const done=job.completed_stages.includes(s),current=s===job.current_stage;return `<div class="stage-row ${done?'done':''} ${current?'current':''}"><span class="stage-check">${done?'✓':current?'●':'○'}</span><span><strong>${esc(job.stage_labels[s]||s)}</strong><small>${s}</small></span><span class="stage-state">${done?'DONE':current?job.status:'PENDING'}</span></div>`;}).join('');
    $('#overview-content').innerHTML=`<dl>${[['输入',job.input_path],['Submission 副本',job.submission_path],['Barcode',job.barcode_csv||'不需要'],['输出',job.output_root],['开始',fmt(job.started_at)],['结束',fmt(job.ended_at)],['退出码',job.exit_code??'—'],['错误',job.last_error||'无']].map(([k,v])=>`<dt>${k}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl>`;
    $('#options-list').innerHTML=Object.entries(data.options||{}).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');$('#audit-timeline').innerHTML=(data.actions||[]).map(a=>`<div class="audit-item"><div><strong>${esc(a.action)}</strong><small>${esc(a.operator)} · ${fmt(a.created_at)}</small><p>${esc(a.details)}</p></div></div>`).join('');
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
  async function loadResults(){const data=await api(`/api/jobs/${id}/results?${new URLSearchParams({offset:resultPage*50,limit:50,query:$('#result-search').value})}`);$('#result-info').textContent=data.path?`${data.path} · ${data.total} 行 · 第 ${resultPage+1} 页`:'尚未生成 Summary';$('#result-prev').disabled=!resultPage;$('#result-next').disabled=(resultPage+1)*50>=data.total;$('#result-head').innerHTML=`<tr>${data.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr>`;$('#result-body').innerHTML=data.rows.map(r=>`<tr class="${Number(r.input_sequences)===0&&Number(r.mapped_seqs)>0?'error-row':''}">${data.columns.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('');}
  async function loadLog(){if(paused||logBusy)return;logBusy=true;try{const data=await api(`/api/jobs/${id}/log?${new URLSearchParams({offset,source})}`);if(data.reset)$('#log-view').textContent='';source=data.source||'';offset=data.next_offset;$('#log-view').textContent=($('#log-view').textContent+data.content).slice(-500000);if($('#auto-scroll').checked)$('#log-view').scrollTop=$('#log-view').scrollHeight;}finally{logBusy=false;}}
  async function loadArtifacts(){const data=await api(`/api/jobs/${id}/artifacts`);$('#artifacts-list').innerHTML=data.files.map(f=>`<div class="artifact-row"><code>${esc(f.path)}</code><b>${f.exists?'存在':'未生成'}</b></div>`).join('');}
  async function action(name){if(busy)return;busy=true;try{if(name==='edit'){const data=await api(`/api/submissions?revision=${encodeURIComponent(job.submission_revision)}`);Submission.open(data,async updated=>{await api(`/api/jobs/${id}/rematch`,{revision:updated.revision});revision='';await refresh();});return;}if(name==='confirm-match'){if(!revision||tab!=='match'){setTab('match');return;}if(!confirm('确认本版清单？仅 OK 记录进入下游，ERROR 保留待补。'))return;}if(name==='stop'&&!confirm('停止任务并保留中间产物？'))return;await api(`/api/jobs/${id}/${name}`,name==='confirm-match'?{revision}:{});await refresh();}catch(e){fail(e);}finally{busy=false;}}
  $('#head-actions').onclick=e=>{const b=e.target.closest('[data-action]');if(b)action(b.dataset.action);};document.querySelectorAll('.detail-tab').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
  $('#match-body').onchange=async e=>{if(e.target.dataset.selectRow!==undefined){e.target.checked?selected.add(e.target.dataset.selectRow):selected.delete(e.target.dataset.selectRow);syncSelection();return;}if(!e.target.dataset.review)return;e.target.disabled=true;try{await api(`/api/jobs/${id}/review`,{revision,row_key:e.target.dataset.review,label:e.target.value});}catch(error){fail(error);await loadMatch();}finally{syncSelection();}};
  $('#errors-only').onchange=$('#match-search').onchange=()=>{selected.clear();syncSelection();matchPage=0;loadMatch().catch(fail);};$('#match-refresh').onclick=()=>loadMatch().catch(fail);$('#match-prev').onclick=()=>{matchPage--;loadMatch().catch(fail);};$('#match-next').onclick=()=>{matchPage++;loadMatch().catch(fail);};
  $('#result-search').onchange=()=>{resultPage=0;loadResults().catch(fail);};$('#result-refresh').onclick=()=>loadResults().catch(fail);$('#result-prev').onclick=()=>{resultPage--;loadResults().catch(fail);};$('#result-next').onclick=()=>{resultPage++;loadResults().catch(fail);};$('#pause-log').onclick=()=>{paused=!paused;$('#pause-log').textContent=paused?'继续刷新':'暂停刷新';};$('#copy-log').onclick=()=>navigator.clipboard.writeText($('#log-view').textContent).catch(fail);
  refresh().then(()=>{if(location.hash==='#match')setTab('match');}).catch(fail);setInterval(()=>refresh().catch(console.error),5000);setInterval(()=>{if(tab==='log')loadLog().catch(console.error);},2000);
})();

(() => {
  'use strict';
  const $=selector=>document.querySelector(selector),api=Submission.api,{esc,status,date,active}=TreemapView;
  const params=new URLSearchParams(location.search),pageSize=20;
  let scan=null,catalog=new Map(),selected=new Set(),page=0,pageTotal=0,scanBusy=false,creating=false,scanGeneration=0,pageGeneration=0,queryTimer;
  let runs=[],runPage=0,currentId=params.get('run')||'',detail=null,viewer=null,listBusy=false,detailBusy=false,detailGeneration=0,runListSignature='';
  function message(text='',error=false){$('#tm-message').textContent=text;$('#tm-message').classList.toggle('error',error);}
  function showError(error){message(error.message||String(error),true);}
  const source=()=>document.querySelector('[name=source]:checked').value;
  function lockSource(locked){$('#tm-source-form').querySelectorAll('input,select,button').forEach(node=>{node.disabled=locked;});if(!locked){$('#tm-task').disabled=source()!=='task';$('#tm-path').disabled=$('#tm-browse').disabled=source()!=='directory';}}
  function syncSelection(){
    $('#tm-selected').textContent=`已选 ${selected.size} 项样本 / 链`;
    $('#tm-generate').disabled=!scan||!selected.size||scanBusy||creating;
    $('#tm-all').disabled=$('#tm-none').disabled=$('#tm-search').disabled=!scan||scanBusy||creating;
    $('#tm-name').disabled=creating;
    $('#tm-selection-note').textContent=scan?`${catalog.size} 项输入 · ${[...catalog.values()].filter(row=>row.valid).length} 项可绘制。选择跨搜索与分页保留。`:'先扫描一个结果来源。';
    $('#tm-prev').disabled=!scan||page===0||creating;
    $('#tm-next').disabled=!scan||(page+1)*pageSize>=pageTotal||creating;
  }
  function resetScan(){
    scanGeneration++;pageGeneration++;clearTimeout(queryTimer);scan=null;catalog.clear();selected.clear();page=0;pageTotal=0;$('#tm-search').value='';$('#tm-page').textContent='尚未扫描';
    $('#tm-rows').innerHTML='<tr><td colspan="4"><div class="tm-empty"><strong>选择数据后开始</strong><span>扫描会列出可绘制的样本与链，你可以自由调整范围。</span></div></td></tr>';syncSelection();
  }
  function renderRows(rows){
    $('#tm-rows').innerHTML=rows.map(row=>`<tr><td><input type="checkbox" data-row="${esc(row.id)}" aria-label="选择 ${esc(row.sample_label||row.sample_key)} ${esc(row.chain)}" ${selected.has(row.id)?'checked':''} ${!row.valid||creating?'disabled':''}></td><td><strong>${esc(row.sample_label||row.sample_key||'未命名样本')}</strong>${row.sample_key&&row.sample_key!==row.sample_label?`<small>${esc(row.sample_key)}</small>`:''}</td><td><span class="tm-chain">${esc(row.chain||'—')}</span></td><td><span class="${row.valid?'tm-valid':'tm-invalid'}">${esc(row.valid?'可绘制':row.error||'无法绘制')}</span></td></tr>`).join('')||'<tr><td colspan="4"><div class="tm-empty"><strong>没有匹配的样本与链</strong><span>尝试更换搜索条件或输入来源。</span></div></td></tr>';
    $('#tm-page').textContent=`${pageTotal} 项 · 第 ${page+1} / ${Math.max(1,Math.ceil(pageTotal/pageSize))} 页`;
    syncSelection();
  }
  async function loadPage(){
    if(!scan)return;const token=++pageGeneration,scanId=scan.id,requestedPage=page;
    $('#tm-prev').disabled=$('#tm-next').disabled=true;$('#tm-rows').setAttribute('aria-busy','true');
    try{
      const result=await api(`/api/treemap/scans/${encodeURIComponent(scanId)}?${new URLSearchParams({query:$('#tm-search').value.trim(),offset:String(page*pageSize),limit:String(pageSize)})}`);
      if(token!==pageGeneration||scan?.id!==scanId)return;
      pageTotal=result.total??result.rows?.length??0;page=requestedPage;renderRows(result.rows||[]);
    }catch(error){if(token===pageGeneration)showError(error);}finally{if(token===pageGeneration){$('#tm-rows').setAttribute('aria-busy','false');syncSelection();}}
  }
  document.querySelectorAll('[name=source]').forEach(input=>input.onchange=()=>{resetScan();const task=source()==='task';$('#tm-task-field').hidden=!task;$('#tm-path-field').hidden=task;$('#tm-task').disabled=!task;$('#tm-task').required=task;$('#tm-path').disabled=$('#tm-browse').disabled=task;$('#tm-path').required=!task;});
  $('#tm-task').onchange=resetScan;$('#tm-path').oninput=resetScan;
  const directoryDialog=$('#tm-directory-dialog');
  let directoryGeneration=0,directoryBusy=false,directoryData=null,directoryReturnFocus=null;
  async function browseDirectory(path=''){
    if(directoryBusy||!directoryDialog.open)return;
    directoryBusy=true;directoryData=null;const token=++directoryGeneration;
    $('#tm-directory-message').textContent='正在读取目录…';$('#tm-directory-entries').setAttribute('aria-busy','true');
    $('#tm-directory-roots').disabled=$('#tm-directory-up').disabled=$('#tm-directory-select').disabled=true;
    $('#tm-directory-entries').querySelectorAll('button').forEach(button=>button.disabled=true);
    try{
      const data=await api(`/api/browse?${new URLSearchParams({kind:'input',path})}`);
      if(token!==directoryGeneration||!directoryDialog.open)return;
      directoryData=data;$('#tm-directory-current').textContent=data.path||'允许的根目录';
      const entries=(data.entries||[]).filter(entry=>entry.type==='directory');
      $('#tm-directory-entries').innerHTML=entries.map(entry=>`<button class="file-tree-entry" type="button" data-tm-directory="${esc(entry.path)}"><span class="file-tree-icon" aria-hidden="true">▱</span><span class="file-tree-copy"><strong>${esc(entry.name)}</strong><small>目录</small></span><span class="file-tree-action" aria-hidden="true">打开 ›</span></button>`).join('')||`<div class="file-tree-empty">${data.path?'当前目录没有子目录，可以直接选用当前目录。':'没有可浏览的根目录。'}</div>`;
      $('#tm-directory-message').textContent=data.path?'打开子目录继续浏览，或选用当前目录。':'请选择一个允许的根目录。';
      $('#tm-directory-up').disabled=!data.parent;$('#tm-directory-select').disabled=!data.path||!data.can_select_current;
      $('#tm-directory-entries').scrollTop=0;
    }catch(error){
      if(token!==directoryGeneration||!directoryDialog.open)return;
      $('#tm-directory-current').textContent=path||'允许的根目录';
      $('#tm-directory-message').textContent=`目录读取失败：${error.message}`;
      $('#tm-directory-entries').innerHTML='<div class="file-tree-empty">返回根目录重新选择，或关闭后手动填写路径。</div>';
    }finally{if(token===directoryGeneration){directoryBusy=false;$('#tm-directory-roots').disabled=false;$('#tm-directory-entries').setAttribute('aria-busy','false');}}
  }
  $('#tm-browse').onclick=()=>{if(scanBusy||creating||source()!=='directory')return;directoryReturnFocus=document.activeElement;directoryDialog.showModal();browseDirectory($('#tm-path').value.trim());};
  $('#tm-directory-close').onclick=$('#tm-directory-cancel').onclick=()=>directoryDialog.close();
  directoryDialog.addEventListener('close',()=>{directoryGeneration++;directoryBusy=false;directoryData=null;directoryReturnFocus?.focus({preventScroll:true});});
  $('#tm-directory-roots').onclick=()=>browseDirectory();
  $('#tm-directory-up').onclick=()=>{if(directoryData?.parent)browseDirectory(directoryData.parent);};
  $('#tm-directory-entries').onclick=event=>{const button=event.target.closest('[data-tm-directory]');if(button)browseDirectory(button.dataset.tmDirectory);};
  $('#tm-directory-select').onclick=()=>{if(directoryBusy||scanBusy||creating||!directoryData?.path||!directoryData.can_select_current)return;$('#tm-path').value=directoryData.path;resetScan();directoryDialog.close();$('#tm-path').focus();};
  $('#tm-source-form').onsubmit=async event=>{
    event.preventDefault();if(scanBusy||creating)return;
    const body=source()==='task'?{job_id:$('#tm-task').value}:{input_path:$('#tm-path').value.trim()};
    if(!body.job_id&&!body.input_path){message('请选择一个任务，或填写结果目录。',true);return;}
    resetScan();scanBusy=true;const token=scanGeneration;lockSource(true);$('#tm-scan').textContent='正在扫描…';message('正在识别可绘制的样本与链…');
    try{
      const result=await api('/api/treemap/scan',body);if(token!==scanGeneration)return;
      scan=result;catalog=new Map((result.rows||[]).map(row=>[row.id,row]));selected=new Set([...catalog.values()].filter(row=>row.valid).map(row=>row.id));
      pageTotal=result.total??catalog.size;renderRows([...catalog.values()].slice(0,pageSize));
      message(selected.size?`扫描完成，已默认选择 ${selected.size} 项有效输入。`:'未发现可绘制输入，请检查来源目录和检查结果。',!selected.size);
      if(pageTotal>catalog.size){message('扫描清单不完整，请缩小输入范围后重新扫描。',true);scan=null;selected.clear();}
    }catch(error){showError(error);}finally{scanBusy=false;lockSource(false);$('#tm-scan').innerHTML='扫描样本与链 <span aria-hidden="true">→</span>';syncSelection();}
  };
  $('#tm-rows').onchange=event=>{const node=event.target.closest('[data-row]');if(!node||creating)return;const row=catalog.get(node.dataset.row);if(!row?.valid)return;node.checked?selected.add(row.id):selected.delete(row.id);syncSelection();};
  $('#tm-all').onclick=()=>{selected=new Set([...catalog.values()].filter(row=>row.valid).map(row=>row.id));$('#tm-rows').querySelectorAll('[data-row]:not(:disabled)').forEach(node=>node.checked=true);syncSelection();};
  $('#tm-none').onclick=()=>{selected.clear();$('#tm-rows').querySelectorAll('[data-row]').forEach(node=>node.checked=false);syncSelection();};
  $('#tm-search').oninput=()=>{clearTimeout(queryTimer);queryTimer=setTimeout(()=>{page=0;loadPage();},250);};
  $('#tm-prev').onclick=()=>{if(page>0){page--;loadPage();}};$('#tm-next').onclick=()=>{page++;loadPage();};
  $('#tm-generate-form').onsubmit=async event=>{
    event.preventDefault();if(creating||!scan||!selected.size)return;
    const body={scan_id:scan.id,selected_ids:[...selected],name:$('#tm-name').value.trim()||undefined};
    creating=true;lockSource(true);syncSelection();$('#tm-rows').querySelectorAll('input').forEach(node=>node.disabled=true);$('#tm-generate').textContent='正在提交…';
    try{
      const result=await api('/api/treemap/runs',body),run=result.run||result;currentId=run.id;
      message('已提交树图生成任务，可在下方查看进度。');
      await refreshRuns();await selectRun(run.id);$('#tm-history-title').scrollIntoView({block:'start',behavior:'smooth'});
    }catch(error){showError(error);}finally{creating=false;lockSource(false);syncSelection();$('#tm-rows').querySelectorAll('[data-row]').forEach(node=>node.disabled=!catalog.get(node.dataset.row)?.valid);$('#tm-generate').textContent='生成树图';}
  };
  function renderRuns(){
    runPage=Math.max(0,Math.min(runPage,Math.ceil(runs.length/8)-1));const visible=runs.slice(runPage*8,runPage*8+8);
    const signature=JSON.stringify([visible,currentId]);
    if(signature!==runListSignature){runListSignature=signature;$('#tm-runs').innerHTML=visible.map(run=>`<button class="tm-run-choice" data-run="${esc(run.id)}" aria-current="${run.id===currentId}"><strong>${esc(run.name||'克隆树图')}</strong>${status(run)}<small>${esc(date(run.created_at))}</small></button>`).join('')||'<div class="tm-empty"><strong>暂无生成记录</strong><span>完成上方两步后，结果将显示在这里。</span></div>';}
    $('#tm-runs-page').textContent=`${runPage+1} / ${Math.max(1,Math.ceil(runs.length/8))}`;$('#tm-runs-prev').disabled=runPage===0;$('#tm-runs-next').disabled=(runPage+1)*8>=runs.length;
  }
  async function refreshRuns(){
    if(listBusy)return;listBusy=true;
    try{const result=await api('/api/treemap/runs');runs=result.runs||[];if(!currentId&&runs.length)currentId=runs[0].id;renderRuns();}finally{listBusy=false;}
  }
  async function refreshDetail(){
    if(!currentId||detailBusy)return;detailBusy=true;const token=++detailGeneration,id=currentId;
    try{const result=await api(`/api/treemap/runs/${encodeURIComponent(id)}`);if(token!==detailGeneration||id!==currentId)return;detail=result.run||result;if(!viewer)viewer=TreemapView.mount($('#tm-run-detail'),runAction);viewer.update(detail);}finally{detailBusy=false;}
  }
  async function selectRun(id){
    if(detail?.id!==id){viewer=null;$('#tm-run-detail').innerHTML='<div class="tm-empty" role="status"><strong>正在读取生成记录…</strong></div>';}
    currentId=id;detailGeneration++;detail=null;
    const url=new URL(location.href);url.searchParams.set('run',id);history.replaceState(null,'',url);renderRuns();
    if(detailBusy){setTimeout(()=>{if(currentId===id)selectRun(id).catch(showError);},150);return;}
    await refreshDetail();
  }
  async function runAction(action,run){
    try{
      if(action==='cancel'&&window.UI?.confirm){const accepted=await UI.confirm({title:'取消树图生成',message:'已生成的图片会保留，尚未完成的项目将停止处理。',confirmLabel:'取消生成',danger:true});if(!accepted)return;}
      const result=await api(`/api/treemap/runs/${encodeURIComponent(run.id)}/${action}`,{}),next=result.run||result;
      if(action==='retry'&&next.id)currentId=next.id;
      message(action==='cancel'?'已请求取消生成。':'已提交重试。');await refreshRuns();await selectRun(currentId);
    }catch(error){showError(error);}
  }
  $('#tm-runs').onclick=event=>{const button=event.target.closest('[data-run]');if(button)selectRun(button.dataset.run).catch(showError);};
  $('#tm-runs-prev').onclick=()=>{runPage--;renderRuns();};$('#tm-runs-next').onclick=()=>{runPage++;renderRuns();};
  async function refresh(){await refreshRuns();await refreshDetail();}
  $('#tm-refresh').onclick=async()=>{$('#tm-refresh').disabled=true;try{await refresh();message();}catch(error){showError(error);}finally{$('#tm-refresh').disabled=false;}};
  async function loadTasks(){
    try{const result=await api('/api/treemap/tasks');const tasks=result.tasks||[];$('#tm-task').innerHTML='<option value="">选择一个成功任务</option>'+tasks.map(task=>`<option value="${esc(task.id)}">${esc(task.dataset||task.label)} · ${esc(task.label||task.pipeline)}</option>`).join('');if(params.get('job'))$('#tm-task').value=params.get('job');if(!tasks.length)$('#tm-task').innerHTML='<option value="">暂无可用的成功任务</option>';}catch(error){$('#tm-task').innerHTML='<option value="">任务加载失败，请刷新页面</option>';showError(error);}
  }
  loadTasks();refresh().catch(showError);
  let nextPoll=0;
  setInterval(()=>{if(document.hidden||Date.now()<nextPoll)return;nextPoll=Date.now()+(detail&&active(detail)||runs.some(active)?5000:30000);refresh().catch(showError);},1000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){nextPoll=Date.now()+5000;refresh().catch(showError);}});
})();

/* Shared, bounded treemap output viewer. No raw source paths are accepted. */
window.TreemapView = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {PENDING:'等待生成',QUEUED:'等待生成',RUNNING:'正在生成',SUCCEEDED:'已完成',PARTIAL:'部分完成',FAILED:'生成失败',INTERRUPTED:'已中断',CANCELLED:'已取消',CANCELED:'已取消',CANCELLING:'正在取消',CANCELING:'正在取消',EMPTY:'无有效记录',STALE:'来源已更新',ARCHIVED:'已归档',SKIPPED:'已跳过'};
  const state = run => String(run.status||'').toUpperCase();
  const active = run => ['PENDING','QUEUED','RUNNING','CANCELLING','CANCELING'].includes(state(run));
  const status = run => `<span class="tm-status" data-state="${esc(state(run))}">${esc(labels[state(run)] || run.status || '未知状态')}</span>`;
  const date = value => { if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const url = (run, file, download=false) => `/api/treemap/runs/${encodeURIComponent(run.id)}/files/${encodeURIComponent(file.id)}${download?'?download=1':''}`;
  function mount(root, onAction) {
    root.innerHTML = `<div class="tm-run-head"><div><h3 data-title></h3><div class="tm-run-meta" data-meta></div></div><div class="tm-run-actions" data-actions></div></div><div class="tm-progress" data-progress role="status" aria-live="polite"><progress></progress><span></span></div><p class="tm-run-note" data-note></p><p class="tm-run-error" data-error role="status"></p><div class="tm-result-tools"><input data-search type="search" placeholder="筛选样本" aria-label="筛选树图样本"><select data-kind aria-label="筛选产物类型"><option value="">全部产物</option><option value="combined">样本合图</option><option value="chain">单链树图</option><option value="stats">统计文件</option></select></div><div class="tm-file-grid" data-files></div><div class="tm-result-pager"><span data-page></span><div><button class="button button-ghost button-small" data-prev>上一页</button><button class="button button-ghost button-small" data-next>下一页</button></div></div><details class="tm-item-errors" data-issues hidden><summary></summary><ul></ul></details>`;
    const $ = selector => root.querySelector(selector);
    let run = null, page = 0, busy = false, signature = '', actionSignature = '', searchTimer;
    const replace = (node, markup) => {if(node.innerHTML!==markup)node.innerHTML=markup;};
    function renderFiles(force=false) {
      if(!run)return;
      const query=$('[data-search]').value.trim().toLocaleLowerCase(),kind=$('[data-kind]').value;
      const availableFiles=['ARCHIVED','STALE'].includes(run.status)?[]:run.files||[];
      const files=availableFiles.filter(file=>(!kind||file.kind===kind)&&(!query||`${file.sample_key||''} ${file.label||''} ${file.chain||''}`.toLocaleLowerCase().includes(query)));
      page=Math.max(0,Math.min(page,Math.ceil(files.length/9)-1));
      const slice=files.slice(page*9,page*9+9),next=JSON.stringify([run.id,slice,page,query,kind,run.status]);
      if(force||next!==signature){
        signature=next;
        $('[data-files]').innerHTML=slice.map(file=>`<article class="tm-file-card">${file.kind==='stats'?'<div class="tm-stats-icon" aria-hidden="true">CSV / JSON</div>':`<a class="tm-file-preview" href="${url(run,file)}" target="_blank" rel="noopener" aria-label="查看 ${esc(file.label||file.sample_key||'树图')} 原图"><img src="${url(run,file)}" loading="lazy" alt="${esc(file.label||file.sample_key||'克隆树图')}"></a>`}<div class="tm-file-caption"><strong>${esc(file.label||file.sample_key||'结果文件')}</strong><small>${esc(file.kind==='combined'?'样本合图 · 固定链区排版':file.kind==='chain'?`${file.chain||''} · 按记录数`:'计数与生成统计')}</small><a href="${url(run,file,true)}">下载${file.kind==='stats'?'统计文件':' PNG'} ↓</a></div></article>`).join('') || `<div class="tm-empty"><strong>${availableFiles.length?'没有匹配的产物':active(run)?'图片生成后将在这里显示':run.status==='ARCHIVED'?'图片已归档':run.status==='STALE'?'来源任务已更新':'暂无可用图片'}</strong><span>${availableFiles.length?'调整样本或类型筛选。':active(run)?'可离开此页面，稍后查看结果。':run.status==='ARCHIVED'?'请从来源任务下载完整归档。':run.status==='STALE'?'此记录属于之前的分析结果，请基于当前结果重新生成。':'请查看生成状态与处理说明。'}</span></div>`;
      }
      $('[data-page]').textContent=`${files.length} 个产物 · 第 ${page+1} / ${Math.max(1,Math.ceil(files.length/9))} 页`;
      $('[data-prev]').disabled=page===0;$('[data-next]').disabled=(page+1)*9>=files.length;
    }
    function update(next) {
      if(!next)return;
      if(run?.id!==next.id){page=0;$('[data-search]').value='';$('[data-kind]').value='';signature='';actionSignature='';}
      run={...next,status:state(next),items:(next.items||[]).map(item=>({...item,status:state(item)}))};
      $('[data-title]').textContent=run.name||'克隆树图';
      replace($('[data-meta]'),`${status(run)}<span>创建于 ${esc(date(run.created_at))}</span>${run.parent_job_id?`<a href="/jobs/${encodeURIComponent(run.parent_job_id)}#artifacts">来源任务 ↗</a>`:''}`);
      const total=Math.max(0,Number(run.progress?.total)||0),done=Math.max(0,Number(run.progress?.done)||0);
      $('[data-progress]').hidden=!total;
      $('[data-progress] progress').max=Math.max(total,1);$('[data-progress] progress').value=Math.min(done,total);
      $('[data-progress] span').textContent=`已处理 ${done} / ${total} 项`;
      $('[data-note]').textContent=active(run)?'图片将按样本与链依次生成。按有效记录数计数，不按 copy 或 UMI count 加权。':'单链内按有效记录数展示克隆占比。合图的链区采用固定排版，不表示链间丰度。';
      $('[data-error]').textContent=run.error||'';
      const actions=active(run)&&!['CANCELLING','CANCELING'].includes(run.status)?['cancel']:['FAILED','PARTIAL','INTERRUPTED','CANCELLED','CANCELED'].includes(run.status)?['retry']:[];
      const nextActions=JSON.stringify([run.id,actions,busy]);
      if(actionSignature!==nextActions){actionSignature=nextActions;$('[data-actions]').innerHTML=actions.map(action=>`<button class="button ${action==='cancel'?'button-danger-ghost':'button-primary'} button-small" data-action="${action}" ${busy?'disabled':''}>${busy?'处理中…':action==='cancel'?'取消生成':'重试生成'}</button>`).join('');}
      const issues=(run.items||[]).filter(item=>item.error||['EMPTY','SKIPPED','FAILED','INTERRUPTED'].includes(item.status));
      $('[data-issues]').hidden=!issues.length;
      $('[data-issues] summary').textContent=`处理说明 · ${issues.length} 项`;
      replace($('[data-issues] ul'),issues.slice(0,50).map(item=>`<li>${esc(item.sample_label||item.sample_key)} / ${esc(item.chain)}：${esc(item.error||labels[item.status]||item.status)}</li>`).join('')+(issues.length>50?'<li>仅展示前 50 项处理说明。</li>':''));
      renderFiles();
    }
    $('[data-actions]').onclick=async event=>{const button=event.target.closest('[data-action]');if(!button||busy)return;busy=true;update(run);try{await onAction(button.dataset.action,run);}finally{busy=false;update(run);}};
    $('[data-search]').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=0;renderFiles();},200);};
    $('[data-kind]').onchange=()=>{page=0;renderFiles();};
    $('[data-prev]').onclick=()=>{page--;renderFiles();};$('[data-next]').onclick=()=>{page++;renderFiles();};
    return {update};
  }
  function jobPanel(root, jobId) {
    const $=selector=>root.querySelector(selector);
    let context=null,inflight=false,generation=0,lastFetch=0,working=false,lastSignature='',previewSignature='',preview=null;
    const notice = text => {$('[data-tm-job-message]').textContent=text;};
    async function load(force=false) {
      if(!context||document.hidden||inflight||!force&&Date.now()-lastFetch<8000)return;
      inflight=true;const token=++generation,attempt=context.attempt_no;
      try{
        const data=await Submission.api(`/api/jobs/${encodeURIComponent(jobId)}/treemap`);
        if(token!==generation||attempt!==context.attempt_no)return;
        lastFetch=Date.now();
        const runs=data.runs||[],signature=JSON.stringify(runs.map(run=>[run.id,run.name,run.status,run.progress]));
        if(signature!==lastSignature){lastSignature=signature;$('[data-tm-job-runs]').innerHTML=runs.slice(0,6).map(run=>`<a href="/treemap?run=${encodeURIComponent(run.id)}"><strong>${esc(run.name||'克隆树图')}</strong>${status(run)}${run.progress?.total?`<span>${esc(run.progress.done)} / ${esc(run.progress.total)}</span>`:''}</a>`).join('');}
        const archived=['ARCHIVED','ARCHIVING'].includes(context.status),running=runs.some(active);
        $('[data-tm-generate]').hidden=archived||context.status!=='SUCCEEDED'||running;
        $('[data-tm-generate]').disabled=working;
        notice(archived?'结果已归档，树图请从完整压缩包中查看。':running?'树图正在排队或生成，可打开树图工作台查看进度。':runs.length?'打开生成记录，预览单链图与样本合图。':context.status==='SUCCEEDED'?'尚未生成树图，可基于本次结果生成。':data.enabled?'已勾选生成树图，主分析成功后自动生成。':'主分析成功后可生成树图。');
        const current=runs.find(run=>run.attempt_no===context.attempt_no&&!['stale','archived'].includes(String(run.status).toLowerCase()));
        const container=$('[data-tm-job-preview]');
        if(container){
          container.hidden=!current||archived;
          const nextPreview=JSON.stringify(current?[current.id,current.status,current.progress]:null);
          if(current&&!archived&&(force||nextPreview!==previewSignature)){
            const detail=await Submission.api(`/api/treemap/runs/${encodeURIComponent(current.id)}`);
            if(token!==generation||attempt!==context.attempt_no)return;
            if(!preview)preview=mount(container,async(action,run)=>{
              if(action==='cancel'&&!await UI.confirm({title:'取消树图生成',message:'已生成的图片会保留，尚未完成的项目将停止处理。',confirmLabel:'取消生成',danger:true}))return;
              await Submission.api(`/api/treemap/runs/${encodeURIComponent(run.id)}/${action}`,{});await load(true);
            });
            preview.update(detail);previewSignature=nextPreview;
          }
        }
      }catch(error){notice(`树图状态读取失败：${error.message}`);}finally{inflight=false;}
    }
    $('[data-tm-generate]').onclick=async()=>{if(working)return;working=true;$('[data-tm-generate]').disabled=true;notice('正在创建树图生成记录…');try{await Submission.api(`/api/jobs/${encodeURIComponent(jobId)}/treemap`,{});lastFetch=0;await load(true);}catch(error){notice(error.message);}finally{working=false;$('[data-tm-generate]').disabled=false;}};
    $('[data-tm-workspace]').href=`/treemap?job=${encodeURIComponent(jobId)}`;
    setInterval(()=>{if(root.getClientRects().length)load();},8000);
    return {load,update(job){if(context?.attempt_no!==job.attempt_no){generation++;lastFetch=0;lastSignature='';previewSignature='';$('[data-tm-job-runs]').textContent='';}context=job;load();}};
  }
  return {esc,labels,status,active,date,mount,jobPanel};
})();

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
    root.innerHTML = `<div class="tm-run-head"><div><h3 data-title></h3><div class="tm-run-meta" data-meta></div></div><div class="tm-run-actions" data-actions></div></div><div class="tm-progress" data-progress role="status" aria-live="polite"><progress></progress><span></span></div><p class="tm-run-note" data-note></p><p class="tm-run-error" data-error role="status"></p><div class="tm-result-tools"><label>样本<select data-sample aria-label="选择树图样本"><option value="">请选择样本</option></select></label><label>链 / 图类型<select data-chain aria-label="选择链或多链合图" disabled><option value="">请先选择样本</option></select></label><button class="button button-primary button-small" data-view disabled>查看图片</button></div><div class="tm-downloads" data-downloads></div><div class="tm-single-result" data-files aria-live="polite"></div><details class="tm-item-errors" data-issues hidden><summary></summary><ul></ul></details>`;
    const $ = selector => root.querySelector(selector);
    let run = null, busy = false, signature = '', actionSignature = '', viewedId = '';
    const replace = (node, markup) => {if(node.innerHTML!==markup)node.innerHTML=markup;};
    function renderFiles() {
      if(!run)return;
      const availableFiles=['ARCHIVED','STALE'].includes(run.status)?[]:run.files||[];
      const images=availableFiles.filter(file=>['chain','combined'].includes(file.kind));
      const labelsBySample=new Map((run.items||[]).map(item=>[item.sample_key,item.sample_label]));
      const samples=new Map(images.map(file=>[file.sample_key,labelsBySample.get(file.sample_key)||file.sample_key]));
      const sampleSelect=$('[data-sample]'),chainSelect=$('[data-chain]');
      const selectedSample=sampleSelect.value,selectedChain=chainSelect.value;
      const labelCounts=new Map();for(const label of samples.values())labelCounts.set(label,(labelCounts.get(label)||0)+1);
      replace(sampleSelect,'<option value="">请选择样本</option>'+[...samples].map(([key,label])=>`<option value="${esc(key)}">${esc(labelCounts.get(label)>1?key:label)}</option>`).join(''));
      sampleSelect.value=samples.has(selectedSample)?selectedSample:'';
      sampleSelect.disabled=!samples.size;
      const choices=images.filter(file=>file.sample_key===sampleSelect.value);
      replace(chainSelect,`<option value="">${sampleSelect.value?'请选择链或合图':'请先选择样本'}</option>`+choices.map(file=>`<option value="${esc(file.id)}">${esc(file.kind==='combined'?'多链合并图':file.chain)}</option>`).join(''));
      chainSelect.value=choices.some(file=>file.id===selectedChain)?selectedChain:'';
      chainSelect.disabled=!choices.length;
      $('[data-view]').disabled=!chainSelect.value;
      if(viewedId!==chainSelect.value)viewedId='';
      const zip=`/api/treemap/runs/${encodeURIComponent(run.id)}/download`;
      replace($('[data-downloads]'),(images.length?`<a class="button button-ghost button-small" href="${zip}">下载全部结果 ZIP</a>`:'')+(choices.length?`<a class="button button-ghost button-small" href="${zip}?sample_key=${encodeURIComponent(sampleSelect.value)}">下载当前样本 ZIP</a>`:'')+availableFiles.filter(file=>['stats','clone_top'].includes(file.kind)).map(file=>`<a class="button button-ghost button-small" href="${url(run,file,true)}">${file.kind==='clone_top'?'下载 Clone Top CSV':'下载统计 CSV'}</a>`).join(''));
      const file=images.find(file=>file.id===viewedId),next=JSON.stringify([run.id,file||null,images.length,run.status]);
      if(next!==signature){
        signature=next;
        $('[data-files]').innerHTML=file?`<article class="tm-file-card"><div class="tm-file-caption"><strong>${esc(samples.get(file.sample_key))} · ${esc(file.kind==='combined'?'多链合并图':file.chain)}</strong><a href="${url(run,file,true)}">下载 PNG ↓</a><a href="${url(run,file)}" target="_blank" rel="noopener">查看原图 ↗</a></div><a class="tm-file-preview" href="${url(run,file)}" target="_blank" rel="noopener"><img src="${url(run,file)}" alt="${esc(file.label||'克隆树图')}"></a></article>`:`<div class="tm-empty"><strong>${images.length?'选择样本和链后查看图片':active(run)?'等待图片生成':run.status==='ARCHIVED'?'图片已归档':run.status==='STALE'?'来源任务已更新':'暂无可用图片'}</strong><span>${images.length?`${samples.size} 个样本 · ${images.length} 张图片，可直接下载压缩包。`:active(run)?'可离开此页面，稍后选择查看。':run.status==='ARCHIVED'?'请从来源任务下载完整归档。':run.status==='STALE'?'请基于当前结果重新生成。':'请查看生成状态与处理说明。'}</span></div>`;
      }
    }
    function update(next) {
      if(!next)return;
      if(run?.id!==next.id){$('[data-sample]').value='';$('[data-chain]').value='';viewedId='';signature='';actionSignature='';}
      run={...next,status:state(next),items:(next.items||[]).map(item=>({...item,status:state(item)}))};
      $('[data-title]').textContent=run.name||'克隆树图';
      replace($('[data-meta]'),`${status(run)}<span>创建于 ${esc(date(run.created_at))}</span>${run.parent_job_id?`<a href="/jobs/${encodeURIComponent(run.parent_job_id)}#treemap">来源任务 ↗</a>`:''}`);
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
    $('[data-sample]').onchange=()=>{$('[data-chain]').value='';viewedId='';renderFiles();};
    $('[data-chain]').onchange=()=>{viewedId='';renderFiles();};
    $('[data-view]').onclick=()=>{viewedId=$('[data-chain]').value;renderFiles();};
    return {update};
  }
  function jobPanel(root, jobId) {
    const $=selector=>root.querySelector(selector);
    let context=null,inflight=false,generation=0,lastFetch=0,working=false,lastSignature='',previewSignature='',preview=null;
    const notice = text => {$('[data-tm-job-message]').textContent=text;};
    async function load(force=false) {
      if(!context||document.hidden||!root.getClientRects().length||inflight||!force&&Date.now()-lastFetch<8000)return;
      inflight=true;const token=++generation,attempt=context.attempt_no;
      const refreshButton=$('[data-tm-refresh]');if(refreshButton)refreshButton.disabled=true;
      try{
        const data=await Submission.api(`/api/jobs/${encodeURIComponent(jobId)}/treemap`);
        if(token!==generation||attempt!==context.attempt_no)return;
        lastFetch=Date.now();
        const runs=data.runs||[],signature=JSON.stringify(runs.map(run=>[run.id,run.name,run.status,run.progress]));
        if(signature!==lastSignature){lastSignature=signature;$('[data-tm-job-runs]').innerHTML=runs.slice(0,6).map(run=>`<a href="/treemap?run=${encodeURIComponent(run.id)}"><strong>${esc(run.name||'克隆树图')}</strong>${status(run)}${run.progress?.total?`<span>${esc(run.progress.done)} / ${esc(run.progress.total)}</span>`:''}</a>`).join('');}
        const archived=['ARCHIVED','ARCHIVING'].includes(context.status),running=runs.some(active);
        $('[data-tm-generate]').hidden=archived||context.status!=='SUCCEEDED'||running;
        $('[data-tm-generate]').disabled=working;
        notice(archived?'结果已归档，树图请从完整压缩包中查看。':running?'树图正在排队或生成，可打开树图工作台查看进度。':runs.length?'选择样本和链查看图片，或下载结果压缩包。':context.status==='SUCCEEDED'?'尚未生成树图，可基于本次结果生成。':data.enabled?'已勾选生成树图，主分析成功后自动生成。':'主分析成功后可生成树图。');
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
      }catch(error){notice(`树图状态读取失败：${error.message}`);}finally{inflight=false;if(refreshButton)refreshButton.disabled=false;}
    }
    $('[data-tm-generate]').onclick=async()=>{if(working)return;working=true;$('[data-tm-generate]').disabled=true;notice('正在创建树图生成记录…');try{await Submission.api(`/api/jobs/${encodeURIComponent(jobId)}/treemap`,{});lastFetch=0;await load(true);}catch(error){notice(error.message);}finally{working=false;$('[data-tm-generate]').disabled=false;}};
    const refreshButton=$('[data-tm-refresh]');if(refreshButton)refreshButton.onclick=()=>load(true);
    $('[data-tm-workspace]').href=`/treemap?job=${encodeURIComponent(jobId)}`;
    setInterval(()=>{if(root.getClientRects().length)load();},8000);
    return {load,update(job){if(context?.attempt_no!==job.attempt_no){generation++;lastFetch=0;lastSignature='';previewSignature='';$('[data-tm-job-runs]').textContent='';const container=$('[data-tm-job-preview]');if(container){container.hidden=true;container.textContent='';preview=null;}}context=job;load();}};
  }
  return {esc,labels,status,active,date,mount,jobPanel};
})();

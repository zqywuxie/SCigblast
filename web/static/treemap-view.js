/* Shared, bounded treemap output viewer. No raw source paths are accepted. */
window.TreemapView = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {PENDING:'等待生成',QUEUED:'等待生成',RUNNING:'正在生成',SUCCEEDED:'已完成',PARTIAL:'部分完成',FAILED:'生成失败',INTERRUPTED:'已中断',CANCELLED:'已取消',CANCELED:'已取消',CANCELLING:'正在取消',CANCELING:'正在取消',EMPTY:'无有效记录',STALE:'来源已更新',ARCHIVED:'已归档',SKIPPED:'已跳过'};
  const state = run => String(run.status||'').toUpperCase();
  const active = run => ['PENDING','QUEUED','RUNNING','CANCELLING','CANCELING'].includes(state(run));
  const status = run => `<span class="tm-status" data-state="${esc(state(run))}">${esc(labels[state(run)] || run.status || '未知状态')}</span>`;
  const date = value => { if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const url = (run, file, download=false) => `/api/treemap/runs/${encodeURIComponent(run.id)}/files/${encodeURIComponent(file.id)}${download?'?download=1':''}`;
  const IMAGE_PAGE_SIZE = 7;
  function mount(root, onAction, {embedded=false,parentLabel='',metaTarget=null,actionsTarget=null}={}) {
    const pager = bottom => `<nav class="tm-result-pager" data-image-pager ${bottom?'data-bottom-pager':''} aria-label="树图${bottom?'底部':''}分页" hidden><span data-page-summary ${bottom?'':'role="status" aria-live="polite"'}></span><div><button type="button" class="button button-ghost button-small" data-page-prev>上一页</button><button type="button" class="button button-ghost button-small" data-page-next>下一页</button></div></nav>`;
    root.classList.toggle('tm-view-embedded',embedded);
    const header=embedded?'':'<div class="tm-run-head"><div><h3 data-title></h3><div class="tm-run-meta" data-meta></div></div><div class="tm-run-actions" data-actions></div></div>';
    root.innerHTML = `${header}<div class="tm-progress" data-progress role="status" aria-live="polite"><progress></progress><span></span></div><p class="tm-run-error" data-error role="status"></p><div class="tm-result-tools"><label>样本<select data-sample aria-label="选择树图样本"><option value="">全部样本</option></select></label><label>链 / 图类型<select data-chain aria-label="选择链或多链合图"><option value="">全部链 / 图类型</option></select></label><button type="button" class="button button-ghost button-small" data-show-all>查看全部</button></div><div class="tm-output-toolbar"><div class="tm-downloads" data-downloads></div><details class="tm-count-details"><summary>计数说明</summary><p class="tm-run-note" data-note></p></details></div>${pager(false)}<div class="tm-file-grid tm-image-gallery" data-files></div>${pager(true)}<details class="tm-item-errors" data-issues hidden><summary></summary><ul></ul></details>`;
    const $ = selector => root.querySelector(selector);
    const metaNode=metaTarget||$('[data-meta]'),actionsNode=actionsTarget||$('[data-actions]');
    let run = null, busy = false, signature = '', actionSignature = '', showGallery = false, imagePage = 0;
    const replace = (node, markup) => {if(node.innerHTML!==markup)node.innerHTML=markup;};
    function renderFiles() {
      if(!run)return;
      const availableFiles=['ARCHIVED','STALE'].includes(run.status)?[]:run.files||[];
      const images=availableFiles.filter(file=>['chain','combined'].includes(file.kind));
      const chainKey=file=>file.kind==='combined'?'combined':file.chain;
      const chainLabel=chain=>chain==='combined'?'多链合并图':chain;
      const labelsBySample=new Map((run.items||[]).map(item=>[item.sample_key,item.sample_label]));
      const samples=new Map(images.map(file=>[file.sample_key,labelsBySample.get(file.sample_key)||file.sample_key]));
      const sampleSelect=$('[data-sample]'),chainSelect=$('[data-chain]');
      const selectedSample=sampleSelect.value,selectedChain=chainSelect.value;
      // Keep a chosen filter if its last image disappears during a refresh;
      // silently resetting it would broaden the gallery to unrelated samples.
      if(selectedSample&&!samples.has(selectedSample))samples.set(selectedSample,labelsBySample.get(selectedSample)||selectedSample);
      const chains=new Set(images.map(chainKey));if(selectedChain)chains.add(selectedChain);
      const labelCounts=new Map();for(const label of samples.values())labelCounts.set(label,(labelCounts.get(label)||0)+1);
      const sampleLabel=key=>{const label=samples.get(key)||key;return labelCounts.get(label)>1?key:label;};
      replace(sampleSelect,'<option value="">全部样本</option>'+[...samples].map(([key])=>`<option value="${esc(key)}">${esc(sampleLabel(key))}</option>`).join(''));
      sampleSelect.value=selectedSample;sampleSelect.disabled=!images.length&&!selectedSample;
      replace(chainSelect,'<option value="">全部链 / 图类型</option>'+[...chains].map(chain=>`<option value="${esc(chain)}">${esc(chainLabel(chain))}</option>`).join(''));
      chainSelect.value=selectedChain;chainSelect.disabled=!images.length&&!selectedChain;
      $('[data-show-all]').disabled=!images.length;
      const choices=images.filter(file=>(!selectedSample||file.sample_key===selectedSample)&&(!selectedChain||chainKey(file)===selectedChain));
      const pages=Math.max(1,Math.ceil(choices.length/IMAGE_PAGE_SIZE));
      imagePage=Math.min(Math.max(0,imagePage),pages-1);
      const pageImages=showGallery?choices.slice(imagePage*IMAGE_PAGE_SIZE,(imagePage+1)*IMAGE_PAGE_SIZE):[];
      root.querySelectorAll('[data-image-pager]').forEach(pager=>{
        pager.hidden=!showGallery||!choices.length||pager.hasAttribute('data-bottom-pager')&&pages===1;
        const text=`${imagePage*IMAGE_PAGE_SIZE+1}–${Math.min((imagePage+1)*IMAGE_PAGE_SIZE,choices.length)} / ${choices.length} 张 · 第 ${imagePage+1} / ${pages} 页`;
        if(pager.querySelector('[data-page-summary]').textContent!==text)pager.querySelector('[data-page-summary]').textContent=text;
        pager.querySelector('[data-page-prev]').disabled=imagePage===0;
        pager.querySelector('[data-page-next]').disabled=imagePage>=pages-1;
      });
      const zip=`/api/treemap/runs/${encodeURIComponent(run.id)}/download`;
      replace($('[data-downloads]'),(images.length?`<a class="button button-ghost button-small" href="${zip}">下载全部结果 ZIP</a>`:'')+(selectedSample&&images.some(file=>file.sample_key===selectedSample)?`<a class="button button-ghost button-small" href="${zip}?sample_key=${encodeURIComponent(selectedSample)}">下载当前样本 ZIP</a>`:'')+availableFiles.filter(file=>['stats','clone_top'].includes(file.kind)).map(file=>`<a class="button button-ghost button-small" href="${url(run,file,true)}">${file.kind==='clone_top'?'下载 Clone Top CSV':'下载统计 CSV'}</a>`).join(''));
      const next=JSON.stringify([run.id,pageImages.map(file=>[file,sampleLabel(file.sample_key)]),pageImages.length?null:[showGallery,images.length,run.status]]);
      if(next!==signature){
        signature=next;
        $('[data-files]').innerHTML=pageImages.length?pageImages.map(file=>`<article class="tm-file-card"><div class="tm-file-caption"><strong>${esc(sampleLabel(file.sample_key))}</strong><small>${esc(chainLabel(chainKey(file)))}</small><div><a href="${url(run,file,true)}">下载 PNG ↓</a><a href="${url(run,file)}" target="_blank" rel="noopener">查看原图 ↗</a></div></div><a class="tm-file-preview" href="${url(run,file)}" target="_blank" rel="noopener"><img src="${url(run,file)}" width="3000" height="3000" loading="lazy" decoding="async" alt="${esc(sampleLabel(file.sample_key))} · ${esc(chainLabel(chainKey(file)))}"></a></article>`).join(''):`<div class="tm-empty" role="status"><strong>${images.length?showGallery?'当前筛选暂无图片':'按样本或链查看图片':active(run)?'等待图片生成':run.status==='ARCHIVED'?'图片已归档':run.status==='STALE'?'来源任务已更新':'暂无可用图片'}</strong><span>${images.length?showGallery?'调整样本或链，或点击“查看全部”。':`${samples.size} 个样本 · ${images.length} 张图片。选择任一筛选条件即可展示，每页最多 ${IMAGE_PAGE_SIZE} 张。`:active(run)?'生成后按样本或链选择查看。':run.status==='ARCHIVED'?'请从来源任务下载完整归档。':run.status==='STALE'?'请基于当前结果重新生成。':'请查看生成状态与处理说明。'}</span></div>`;
      }
    }
    function update(next) {
      if(!next)return;
      if(run?.id!==next.id){$('[data-sample]').value='';$('[data-chain]').value='';showGallery=false;imagePage=0;signature='';actionSignature='';}
      run={...next,status:state(next),items:(next.items||[]).map(item=>({...item,status:state(item)}))};
      if(!embedded)$('[data-title]').textContent=run.name||'克隆树图';
      const total=Math.max(0,Number(run.progress?.total)||0),done=Math.max(0,Number(run.progress?.done)||0);
      replace(metaNode,`${embedded&&run.name&&run.name!==parentLabel?`<span>${esc(run.name)}</span>`:''}${status(run)}${total&&!active(run)?`<span>已处理 ${done} / ${total} 项</span>`:''}<span>创建于 ${esc(date(run.created_at))}</span>${run.parent_job_id&&!embedded?`<a href="/jobs/${encodeURIComponent(run.parent_job_id)}#treemap">来源任务 ↗</a>`:''}`);
      $('[data-progress]').hidden=!total||!active(run);
      $('[data-progress] progress').max=Math.max(total,1);$('[data-progress] progress').value=Math.min(done,total);
      $('[data-progress] span').textContent=`已处理 ${done} / ${total} 项`;
      $('[data-note]').textContent='按有效记录数展示克隆占比，不按 copy 或 UMI count 加权。合图的链区采用固定排版，不表示链间丰度。';
      $('[data-error]').textContent=run.error||'';
      const actions=active(run)&&!['CANCELLING','CANCELING'].includes(run.status)?['cancel']:['FAILED','PARTIAL','EMPTY','INTERRUPTED','CANCELLED','CANCELED'].includes(run.status)?['retry']:[];
      const nextActions=JSON.stringify([run.id,actions,busy]);
      if(actionSignature!==nextActions){actionSignature=nextActions;actionsNode.innerHTML=actions.map(action=>`<button type="button" class="button ${action==='cancel'?'button-danger-ghost':'button-primary'} button-small" data-action="${action}" ${busy?'disabled':''}>${busy?'处理中…':action==='cancel'?'取消生成':'重试生成'}</button>`).join('');}
      const issues=(run.items||[]).filter(item=>item.error||['EMPTY','SKIPPED','FAILED','INTERRUPTED'].includes(item.status));
      $('[data-issues]').hidden=!issues.length;
      $('[data-issues] summary').textContent=`处理说明 · ${issues.length} 项`;
      replace($('[data-issues] ul'),issues.slice(0,50).map(item=>`<li>${esc(item.sample_label||item.sample_key)} / ${esc(item.chain)}：${esc(item.error||labels[item.status]||item.status)}</li>`).join('')+(issues.length>50?'<li>仅展示前 50 项处理说明。</li>':''));
      renderFiles();
    }
    actionsNode.onclick=async event=>{const button=event.target.closest('[data-action]');if(!button||busy)return;busy=true;update(run);try{await onAction(button.dataset.action,run);}finally{busy=false;update(run);}};
    $('[data-sample]').onchange=$('[data-chain]').onchange=()=>{showGallery=true;imagePage=0;renderFiles();};
    $('[data-show-all]').onclick=()=>{$('[data-sample]').value='';$('[data-chain]').value='';showGallery=true;imagePage=0;renderFiles();};
    root.addEventListener('click',event=>{
      const button=event.target.closest('[data-page-prev],[data-page-next]');if(!button||button.disabled)return;
      imagePage+=button.hasAttribute('data-page-next')?1:-1;renderFiles();
      $('[data-image-pager]').scrollIntoView({block:'nearest'});
    });
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
        if(token!==generation||attempt!==context.attempt_no||document.hidden||!root.getClientRects().length)return;
        lastFetch=Date.now();
        const runs=data.runs||[];
        const archived=['ARCHIVED','ARCHIVING'].includes(context.status),running=runs.some(active);
        const current=runs.find(run=>run.attempt_no===context.attempt_no&&!['stale','archived'].includes(String(run.status).toLowerCase()));
        const history=runs.filter(run=>run.id!==current?.id),signature=JSON.stringify(history.map(run=>[run.id,run.name,run.status,run.created_at]));
        if(signature!==lastSignature){lastSignature=signature;$('[data-tm-job-runs]').innerHTML=history.slice(0,6).map(run=>`<a href="/treemap?run=${encodeURIComponent(run.id)}"><strong>${esc(run.name||'克隆树图')}</strong>${status(run)}<span>${esc(date(run.created_at))}</span><span aria-hidden="true">↗</span></a>`).join('');}
        $('[data-tm-history]').hidden=archived||runs.length<2;
        $('[data-tm-history] summary').textContent=`其他生成记录 · ${history.length}`;
        $('[data-tm-current-meta]').hidden=$('[data-tm-run-actions]').hidden=!current||archived;
        $('[data-tm-generate]').hidden=archived||context.status!=='SUCCEEDED'||running||Boolean(current);
        $('[data-tm-generate]').disabled=working;
        notice(archived?'结果已归档，树图请从完整压缩包中查看。':current?'':running?'树图正在排队或生成，可在工作台查看进度。':context.status==='SUCCEEDED'?'尚未生成树图，可基于本次结果生成。':data.enabled?'主分析成功后自动生成树图。':'主分析成功后可生成树图。');
        const container=$('[data-tm-job-preview]');
        if(container){
          container.hidden=!current||archived;
          const nextPreview=JSON.stringify(current?[current.id,current.status,current.progress]:null);
          if(current&&!archived&&(force||nextPreview!==previewSignature)){
            const detail=await Submission.api(`/api/treemap/runs/${encodeURIComponent(current.id)}`);
            if(token!==generation||attempt!==context.attempt_no||document.hidden||!root.getClientRects().length)return;
            if(!preview)preview=mount(container,async(action,run)=>{
              if(action==='cancel'&&!await UI.confirm({title:'取消树图生成',message:'已生成的图片会保留，尚未完成的项目将停止处理。',confirmLabel:'取消生成',danger:true}))return;
              await Submission.api(`/api/treemap/runs/${encodeURIComponent(run.id)}/${action}`,{});await load(true);
            },{embedded:true,parentLabel:context.dataset,metaTarget:$('[data-tm-current-meta]'),actionsTarget:$('[data-tm-run-actions]')});
            preview.update(detail);previewSignature=nextPreview;
          }
        }
      }catch(error){notice(`树图状态读取失败：${error.message}`);}finally{inflight=false;if(refreshButton)refreshButton.disabled=false;}
    }
    $('[data-tm-generate]').onclick=async()=>{if(working)return;working=true;$('[data-tm-generate]').disabled=true;notice('正在创建树图生成记录…');try{await Submission.api(`/api/jobs/${encodeURIComponent(jobId)}/treemap`,{});lastFetch=0;await load(true);}catch(error){notice(error.message);}finally{working=false;$('[data-tm-generate]').disabled=false;}};
    const refreshButton=$('[data-tm-refresh]');if(refreshButton)refreshButton.onclick=()=>load(true);
    $('[data-tm-workspace]').href=`/treemap?job=${encodeURIComponent(jobId)}`;
    setInterval(()=>{if(root.getClientRects().length)load();},8000);
    return {load,update(job){if(context?.attempt_no!==job.attempt_no){generation++;lastFetch=0;lastSignature='';previewSignature='';$('[data-tm-job-runs]').textContent='';$('[data-tm-current-meta]').textContent='';$('[data-tm-run-actions]').textContent='';const container=$('[data-tm-job-preview]');if(container){container.hidden=true;container.textContent='';preview=null;}}context=job;load();}};
  }
  return {esc,labels,status,active,date,mount,jobPanel};
})();

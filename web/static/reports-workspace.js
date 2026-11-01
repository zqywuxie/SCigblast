/* Four scoped analysis views. Charts and exports share the published counts. */
window.ReportWorkspace = (() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=value=>value==null?'—':Number(value).toLocaleString('zh-CN');
  const status={observed:'已观察',zero_observed:'零观察',outside_reference:'清单外注释'};
  function mount(root,{fileURL,onScope,onRedraw,onError,view='all'}) {
    const host=root.querySelector('[data-rp-workspace]');
    const state={tab:view==='stages'?'sample':'overview',group:'',primer:'',level:'main',segment:'all',referenceScope:'sample',search:'',pages:{},galleryPage:0};
    let data,run,revision=0,controller=null;
    const controls=()=>{
      const groups=data.usage.groups;
      if(!groups.some(g=>g.group_id===state.group))state.group=groups[0]?.group_id||'';
      const group=groups.find(g=>g.group_id===state.group);
      const primers=(group?.primers||[]).filter(p=>!state.search||p.toLowerCase().includes(state.search.toLowerCase()));
      if(!primers.includes(state.primer))state.primer=primers[0]||'';
      return group;
    };
    function photo(figure,{wide=false,heading=true}={}) {
      if(!figure)return '';
      return `<figure class="ws-figure${wide?' ws-wide':''}">${heading?`<figcaption><strong>${esc(figure.title)}</strong><a class="button button-ghost button-small" href="${esc(fileURL(figure.png,true))}">PNG ↓</a></figcaption>`:''}<a target="_blank" rel="noopener" href="${esc(fileURL(figure.png))}"><img loading="lazy" src="${esc(fileURL(figure.png))}" alt="${esc(figure.title)}"></a></figure>`;
    }
    function notice(text){return `<p class="ws-notice">${esc(text)}</p>`;}
    function groupSelect() {
      const repeats=new Map();for(const g of data.usage.groups)repeats.set(g.sample+' / '+g.chain,(repeats.get(g.sample+' / '+g.chain)||0)+1);
      return `<label class="ws-group-select">样本 / 链<select data-ws-group>${data.usage.groups.map(g=>`<option value="${esc(g.group_id)}"${g.group_id===state.group?' selected':''}>${esc(g.sample+' / '+g.chain+(repeats.get(g.sample+' / '+g.chain)>1?' · 参考 '+g.reference_id.slice(0,8):''))}</option>`).join('')}</select></label>`;
    }
    function scope(group) {
      if(view==='stages')group=null;
      onScope?.({sample:group?.sample||'',chain:group?.chain||'',primer:state.tab==='primer'||(state.tab==='reference'&&state.referenceScope==='primer')?state.primer:'',group_id:group?.group_id||''});
    }
    function primerSelect(group) {
      return `<label class="ws-primer-search">查找引物<input type="search" value="${esc(state.search)}" placeholder="搜索完整名称" data-ws-search></label><label class="ws-primer-select">引物<select data-ws-primer>${(group?.primers||[]).filter(p=>!state.search||p.toLowerCase().includes(state.search.toLowerCase())).map(p=>`<option${p===state.primer?' selected':''}>${esc(p)}</option>`).join('')}</select></label>`;
    }
    function filters(group) {
      if(view==='stages')return `<div class="ws-filters">${groupSelect()}</div>`;
      const reference=state.tab==='reference';
      return `<div class="ws-filters">${groupSelect()}${reference?`<label>统计对象<select data-ws-reference-scope><option value="sample"${state.referenceScope==='sample'?' selected':''}>整个样本</option><option value="primer"${state.referenceScope==='primer'?' selected':''}>单条引物</option></select></label>`:''}${state.tab==='primer'||(reference&&state.referenceScope==='primer')?primerSelect(group):''}<div class="ws-filter-actions"><label>基因区<select data-ws-segment>${['all','V','J','C'].map(s=>`<option value="${s}"${s===state.segment?' selected':''}>${s==='all'?'V / J / C':s}</option>`).join('')}</select></label></div></div>`;
    }
    function kpis(group) {
      const volumes=(data.stage_volumes||[]).filter(r=>r.sample===group?.sample&&!r.chain);
      const get=stage=>volumes.find(r=>r.stage===stage);
      return `<div class="ws-kpis">${[['Raw reads',get('raw')?.count],['Clean reads',get('fastp')?.count],['Merged reads',get('merged')?.count],['Retained reads',group?.record_count]].map(([label,value])=>`<div><span>${label}</span><strong>${number(value)}</strong></div>`).join('')}</div>`;
    }
    function overview() {
      const figures=data.figures||[];
      const stages=figures.filter(f=>f.kind==='stage_overview');
      const matching=figures.find(f=>f.kind==='matching'&&f.id==='primer_matching_total');
      const iso=figures.filter(f=>f.kind==='isotype'&&f.level===state.level);
      const totals=data.matching_total_summary||[];
      const unassessed=totals.filter(r=>r.match_class==='unique').reduce((n,r)=>n+(r.unassessed_count||0),0);
      const coverage=data.usage.groups.filter(g=>g.chain==='IGH').map(g=>`<tr><td>${esc(g.sample)}</td><td>${number(g.record_count)}</td><td>${number(g.isotype_states?.classified||0)}</td><td>${(g.aa_coverage||[]).map(r=>esc(r.field||'字段缺失')+'：'+number(r.isotype_valid_records??r.valid_records)).join('<br>')||'字段缺失'}</td></tr>`).join('');
      return `<div class="ws-section-head"><div><span class="ws-eyebrow">OVERVIEW</span><h4>全部样本，总体比较</h4></div><span class="ws-pill">${new Set(data.samples.map(s=>s.sample)).size} 个样本</span></div><div class="ws-overview-grid">${stages.map(f=>photo(f,{wide:true})).join('')}${matching?photo(matching,{wide:true}):notice('引物尚未配置或没有可评估记录；补充引物表后可生成匹配总览。')}${unassessed?notice('未评估记录 '+number(unassessed)+' 条，未计入唯一 / 多重 / 未匹配比例。'):''}<section class="ws-isotype ws-wide"><div class="ws-section-head"><div><h4>IGH Isotype</h4><p>% Expression / % uCDR3</p></div><div class="ws-switch" role="group" aria-label="Isotype 粒度"><button data-ws-level="main" aria-pressed="${state.level==='main'}">主类</button><button data-ws-level="subclass" aria-pressed="${state.level==='subclass'}">亚类</button></div></div>${iso.length?iso.map(f=>photo(f,{heading:false})).join(''):notice('当前结果没有可用的 IGH isotype 组成。其他链的 C 基因分布请查看样本分析。')}${iso.length?'<p class="ws-footnote">% Expression 按本分析的 reads 权重统计；% uCDR3 按各 Isotype 内去重，主类合并后重新去重。</p>':''}<div class="rp-actions">${iso.map(f=>`<a class="button button-ghost button-small" href="${esc(fileURL(f.png,true))}">下载 Isotype PNG${f.aa_field?' · '+esc(f.aa_field):''}</a>`).join('')}</div>${coverage?`<details class="ws-rules"><summary>注释与 CDR3 可计算范围</summary><div class="rp-table-wrap"><table class="rp-data-table"><thead><tr><th>样本</th><th>Retained reads</th><th>Isotype-assigned reads</th><th>Reads with valid AA</th></tr></thead><tbody>${coverage}</tbody></table></div></details>`:''}</section></div>`;
    }
    function usageCards(group) {
      if(!group)return notice('当前分析没有样本结果。');
      if((state.tab==='primer'||(state.tab==='reference'&&state.referenceScope==='primer'))&&!state.primer)return notice(state.search?'没有符合搜索条件的引物，请修改搜索内容。':'该样本未配置引物，仍可在样本分析中查看 V/J/C 分布。');
      const segments=state.segment==='all'?['V','J','C']:[state.segment];
      return `<div class="ws-gene-grid">${segments.map(segment=>`<section class="ws-gene-card${segment==='C'&&segments.length===3?' ws-wide':''}" data-ws-card="${segment}"><div class="ws-card-head"><div><h4>${segment} 基因使用</h4><span data-ws-card-meta>正在读取统计…</span></div><div class="rp-actions" data-ws-card-download></div></div><div data-ws-bars aria-live="polite"></div><div class="ws-card-pager" data-ws-card-pager></div></section>`).join('')}</div>`;
    }
    function sample(group) {
      if(view==='stages'){
        const all=data.figures.filter(f=>f.kind==='stage_overview');
        return `<div class="ws-section-head"><div><span class="ws-eyebrow">READ COUNTS</span><h4>Read counts across samples</h4></div></div><div class="ws-overview-grid">${all.length?all.map(f=>photo(f,{wide:true})).join(''):notice('当前分析尚无全样本图，请重新生成图片。')}</div>`;
      }
      return `<div class="ws-section-head"><div><span class="ws-eyebrow">SAMPLE</span><h4>样本 Reads 与基因使用</h4></div></div>${filters(group)}${kpis(group)}${usageCards(group)}`;
    }
    function primer(group) {
      const rank=data.figures.find(f=>f.kind==='primers'&&f.sample===group?.sample&&f.chain===group?.chain);
      const matched=group?.primer_observations?.[state.primer]||0,total=group?.matching_records||0;
      return `<div class="ws-section-head"><div><span class="ws-eyebrow">PRIMER</span><h4>引物扩增数据的 V / J / C 组成</h4></div><button class="button button-ghost button-small" data-ws-redraw${state.primer?'':' disabled'}>重绘所选引物 PNG</button></div>${filters(group)}${rank?`<details class="ws-stage"><summary>查看引物唯一匹配率</summary>${photo(rank,{heading:false})}</details>`:''}${state.primer?`<p class="ws-selection-title">${esc(state.primer)}<br><span class="ws-footnote">唯一匹配 ${number(matched)} / ${number(total)} reads · ${total?(matched*100/total).toFixed(4)+'%':'比例不可计算'}</span></p>`:''}${usageCards(group)}`;
    }
    function reference(group) {
      return `<div class="ws-section-head"><div><span class="ws-eyebrow">REFERENCE</span><h4>未观察参考基因</h4><p>与已有比对基因的分布图分开列出。</p></div></div>${filters(group)}${usageCards(group)}`;
    }
    function downloads(group) {
      const allowed=['matching','primers','isotype','sample_genes','primer_genes','composition','stage_overview'];
      const allFigures=data.figures.filter(f=>allowed.includes(f.kind));
      const figures=view==='stages'?allFigures.filter(f=>f.kind==='stage_overview'):allFigures;
      const pages=Math.max(1,Math.ceil(figures.length/7));state.galleryPage=Math.max(0,Math.min(state.galleryPage,pages-1));
      return `<div class="ws-section-head"><div><span class="ws-eyebrow">EXPORT</span><h4>数据与汇报图片</h4></div></div><div class="ws-download-grid"><a class="ws-download" href="/api/reports/${encodeURIComponent(run.id)}/download?images_only=true"><strong>全部 PNG 图片</strong><span>${allFigures.length} 张 · 按分析类别整理</span></a><a class="ws-download" href="${esc(fileURL('report.xlsx',true))}"><strong>实验核查工作簿</strong><span>分 Sheet 核对匹配、未观察基因、引物及样本指标</span></a><a class="ws-download" href="/api/reports/${encodeURIComponent(run.id)}/download"><strong>完整分析包 ZIP</strong><span>PNG、CSV、Excel 与版本记录</span></a></div><details class="ws-rules"><summary>统计口径与 CSV</summary><p>基因图使用最终有效记录的唯一基因归属；未观察参考基因另表列出。Unique CDR3 在各类型内去重，同一 AA 跨类型分别计入，主类按合并后的 AA 重新去重。</p><div class="rp-actions">${Object.entries(data.exports||{}).map(([key,path])=>`<a class="button button-ghost button-small" href="${esc(fileURL(path,true))}">${esc(({sample_gene_usage:'样本 V/J/C',primer_gene_usage:'引物 V/J/C',reference_gene_observation:'参考基因观察',unobserved_reference_genes:'样本未观察基因',primer_unobserved_reference_genes:'引物未观察基因',isotype_summary:'Isotype',matching_total_summary:'引物匹配总览',stage_volume_summary:'阶段数据量'})[key]||key)} CSV</a>`).join('')}</div></details><div class="ws-section-head"><h4>全部汇报图片</h4><span class="ws-footnote">${figures.length} 张 · 第 ${state.galleryPage+1} / ${pages} 页</span></div><div class="rp-gallery">${figures.slice(state.galleryPage*7,state.galleryPage*7+7).map(f=>photo(f)).join('')}</div><div class="ws-pagination"><button class="button button-ghost button-small" data-ws-gallery="-1"${state.galleryPage?'':' disabled'}>上一页</button><button class="button button-ghost button-small" data-ws-gallery="1"${state.galleryPage+1<pages?'':' disabled'}>下一页</button></div>`;
    }
    async function loadCard(group,segment,epoch,signal) {
      const reference=state.tab==='reference';
      const primer=state.tab==='primer'||(reference&&state.referenceScope==='primer')?state.primer:'';
      const offset=state.pages[segment]||0;
      const params=new URLSearchParams({group_id:group.group_id,primer,segment,observed_only:String(!reference),offset:String(offset),limit:'25'});
      if(reference)params.set('observation_state','zero_observed');
      const url=`/api/reports/${encodeURIComponent(run.id)}/usage?${params}`;
      const response=await fetch(url,{signal,headers:{'X-SCIGBLAST-Request':'1'}});
      const result=await response.json();if(!response.ok)throw Error(result.detail||'读取统计失败');
      if(epoch!==revision)return;
      const card=host.querySelector(`[data-ws-card="${segment}"]`);if(!card)return;
      const figures=reference?[]:data.figures.filter(f=>f.group_id===group.group_id&&f.primer===primer&&f.segment===segment&&f.kind===(primer?'primer_genes':'sample_genes'));
      const ref=group.reference[segment]?.available;
      card.querySelector('[data-ws-card-meta]').textContent=reference?`${number(result.total)} 个基因`:`Assigned reads ${number(result.summary.unique||0)} · 未注释 ${number(result.summary.unannotated||0)}`;
      const csv=new URLSearchParams(params);csv.set('format','csv');csv.delete('offset');csv.delete('limit');
      card.querySelector('[data-ws-card-download]').innerHTML=`<a class="button button-ghost button-small" href="/api/reports/${encodeURIComponent(run.id)}/usage?${esc(csv.toString())}">当前 CSV ↓</a>${figures.length?`<a class="button button-ghost button-small" href="${esc(fileURL((figures.find(f=>f.page===Math.floor(offset/25)+1)||figures[0]).png,true))}">PNG ↓</a>`:''}`;
      const maximum=Math.max(5,5*Math.ceil(Math.max(...result.rows.map(r=>r.percent||0),1)/5));
      card.querySelector('[data-ws-bars]').innerHTML=(ref?'':notice('缺少当次参考清单，仅展示已有注释，不能判断未观察的参考基因。'))+(reference?(result.rows.length?`<div class="rp-table-wrap"><table class="rp-data-table"><thead><tr><th>参考基因</th></tr></thead><tbody>${result.rows.map(row=>`<tr><td>${esc(row.gene)}</td></tr>`).join('')}</tbody></table></div>`:notice(ref?'当前范围没有未观察的参考基因。':'缺少当次参考清单，无法判断未观察基因。')):result.rows.length?`<div class="ws-gene-bars"><div class="ws-bar-scale" aria-label="% Expression"><span>% Expression · 0%</span><span>${maximum}%</span></div>${result.rows.map(row=>`<div class="ws-gene-row" data-status="${esc(row.status)}" data-gene-state="observed"><span class="ws-gene-label" title="${esc(row.gene)}">${esc(row.gene)}</span><span class="ws-gene-track"><i style="width:${(row.percent||0)*100/maximum}%"></i></span><span class="ws-gene-value">${row.percent==null?'—':row.percent.toFixed(2)+'%'}<small>${number(row.unique_count)} reads</small></span><span class="ws-gene-status">${esc(status[row.status]||'已观察')}</span></div>`).join('')}</div>`:notice('当前范围没有可明确归属的基因；未观察基因请查看参考观察清单。'));
      card.querySelector('[data-ws-card-pager]').innerHTML=`<span>${result.total} 个基因${reference?' · 未观察':''}${ref?' · '+(group.reference_scope==='comparison_current'?'当前参考比较（非历史库） ':'当次参考 ')+group.reference_id.slice(0,8):''}</span><button class="button button-ghost button-small" data-ws-page="${segment}" data-delta="-25"${offset?'':' disabled'}>上一页</button><button class="button button-ghost button-small" data-ws-page="${segment}" data-delta="25"${result.has_more?'':' disabled'}>下一页</button>`;
      card.querySelectorAll('[data-ws-page]').forEach(button=>button.onclick=()=>{state.pages[segment]=Math.max(0,offset+Number(button.dataset.delta));paint();});
    }
    function paint() {
      if(!data)return;
      controller?.abort();controller=new AbortController();const epoch=++revision;
      const group=controls();scope(group);
      host.hidden=false;
      const tabs=view==='stages'?[['sample','Reads 变化'],['downloads','数据下载']]:[['overview','总体概览'],['sample','样本分析'],['primer','引物分析'],['reference','参考观察清单'],['downloads','数据下载']];
      host.innerHTML=`<div class="ws-tabs" role="tablist" aria-label="后续分析视图">${tabs.map(([key,label])=>`<button role="tab" data-ws-tab="${key}" aria-selected="${state.tab===key}" tabindex="${state.tab===key?0:-1}">${label}</button>`).join('')}</div><div class="ws-content" role="tabpanel" aria-label="${tabs.find(t=>t[0]===state.tab)?.[1]||''}">${state.tab==='overview'?overview():state.tab==='sample'?sample(group):state.tab==='primer'?primer(group):state.tab==='reference'?reference(group):downloads(group)}</div>`;
      root.querySelectorAll('.rp-filter-card,[data-rp-coverage],[data-rp-charts],.rp-gallery-section').forEach(n=>n.hidden=true);
      root.querySelector('[data-rp-tables]').closest('details').hidden=true;
      root.querySelector('[data-rp-records]').closest('details').hidden=state.tab!=='downloads'||view==='stages';
      root.querySelector('[data-rp-result-summary]').innerHTML=`<span><strong>${new Set(data.samples.map(s=>s.sample)).size}</strong> 个样本</span><span><strong>${data.usage.groups.length}</strong> 项样本链结果</span>`;
      root.querySelector('[data-rp-primer-png]').hidden=true;
      host.querySelectorAll('[data-ws-tab]').forEach(button=>{
        button.onclick=()=>{state.tab=button.dataset.wsTab;state.pages={};paint();host.querySelector(`[data-ws-tab="${state.tab}"]`)?.focus();};
        button.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const all=[...host.querySelectorAll('[data-ws-tab]')],i=all.indexOf(button);all[e.key==='Home'?0:e.key==='End'?all.length-1:(i+(e.key==='ArrowRight'?1:all.length-1))%all.length].click();};
      });
      const bind=(selector,event,handler)=>host.querySelector(selector)?.addEventListener(event,handler);
      bind('[data-ws-group]','change',e=>{state.group=e.target.value;state.search='';state.pages={};paint();});
      bind('[data-ws-primer]','change',e=>{state.primer=e.target.value;state.pages={};paint();});
      bind('[data-ws-segment]','change',e=>{state.segment=e.target.value;state.pages={};paint();});
      bind('[data-ws-reference-scope]','change',e=>{state.referenceScope=e.target.value;state.pages={};paint();});
      bind('[data-ws-search]','input',e=>{
        state.search=e.target.value;state.pages={};paint();
        const input=host.querySelector('[data-ws-search]');input.focus();input.setSelectionRange(state.search.length,state.search.length);
      });
      host.querySelectorAll('[data-ws-level]').forEach(b=>b.onclick=()=>{state.level=b.dataset.wsLevel;paint();});
      host.querySelectorAll('[data-ws-gallery]').forEach(b=>b.onclick=()=>{state.galleryPage+=Number(b.dataset.wsGallery);paint();});
      bind('[data-ws-redraw]','click',async e=>{e.target.disabled=true;try{await onRedraw?.({primer:state.primer,sample:group.sample,chain:group.chain});}catch(error){onError(error.message);}finally{if(e.target.isConnected)e.target.disabled=false;}});
      if(['sample','primer','reference'].includes(state.tab)&&view!=='stages'&&group&&(!(state.tab==='primer'||(state.tab==='reference'&&state.referenceScope==='primer'))||state.primer)){
        Promise.all((state.segment==='all'?['V','J','C']:[state.segment]).map(s=>loadCard(group,s,epoch,controller.signal))).catch(error=>{if(error.name!=='AbortError'&&epoch===revision){host.querySelectorAll('[data-ws-bars]').forEach(n=>{if(!n.children.length)n.innerHTML=notice(error.message);});onError(error.message);}});
      }
    }
    return {render(value,currentRun){const changed=run?.id!==currentRun.id;data=value;run=currentRun;if(changed){state.group='';state.primer='';state.pages={};state.galleryPage=0;}paint();},scope:()=>({group_id:view==='stages'?'':state.group}),destroy(){controller?.abort();}};
  }
  return {mount};
})();

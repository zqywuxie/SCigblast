
(()=>{
 const data=JSON.parse(document.getElementById('report-data').textContent);
 const $=s=>document.querySelector(s),pendingCharts=[],scopeCache=new Map(),figureFrames=new Map(),state={main:'overview',view:'overview',metricsCategory:'diversity',category:'matching',page:0,referencePages:{},referencePageSize:10,contexts:[]};
 let currentFrames=new Set();
 let overviewIsotypeSample='';
  const labels={overview:'总体概览',outputs:'样本产出比较',metrics:'多样性与 B cell 指标',stages:'数据变化',sample:'样本基因',primer:'引物分析',reference:'基因匹配'};
 const categories={matching:['引物匹配','比较 Raw 与 Mapping 阶段的引物识别情况。'],mapping:['Mapping 效率','比较当前样本全部引物的 Mapping rate 与保留情况。'],genes:['逐引物基因组成','选择引物，左右对照 V/J/C 的两种组成指标。'],ccompare:['引物间 C 基因对比','比较同一样本内不同引物的 C 基因组成。']};
 const rawPrimerMatching=data.primer_matching_source==='raw_R1';
 if(rawPrimerMatching){categories.matching[1]='仅使用最初始 Raw R1（_j 文件）统计引物匹配，MIX 总览按共享来源去重。';$('#stage').replaceChildren(new Option('Raw R1','raw_R1'));}
 $('.filters').append($('#analysis-tools'));
  function compositionNotice(){
    let host=$('#composition-note');if(!host){host=document.createElement('p');host.id='composition-note';host.className='caption';$('#module-rules').after(host);}
    const applies=['overview','sample','metrics','reference'].includes(state.view)||(state.view==='primer'&&['genes','ccompare'].includes(state.category));
    const rows=(data.composition_sources||[]).filter(r=>state.contexts.some(g=>g.sample===r.sample&&g.chain===r.chain)&&matchesSample(r)&&(!$('#chain').value||r.chain===$('#chain').value));
    const unavailable=rows.filter(r=>r.status!=='complete'),unresolved=rows.reduce((n,r)=>{const p=(data.primer_scenarios||[]).find(p=>p.scenario_id===scenarioFor(r.sample)&&p.chain===r.chain);return n+(p?.unresolved_copies??r.unresolved_copies??0);},0);
    host.textContent=!applies?'':unavailable.length?'部分样本的 06 后处理结果缺失或来源未验证，相关组成及指标不可用。':state.view==='primer'&&unresolved?unresolved.toLocaleString()+' copy 未能唯一归属到引物，保留在样本统计，不分配到具体引物。':'';
    host.hidden=!host.textContent;
  }
 const option=(text,value)=>new Option(text,value),values=(rows,key)=>[...new Set(rows.map(r=>r[key]).filter(Boolean))].sort();
 let sampleMode='all',selectedSamples=[],eligibleSamples=[],samplePanelOpen=false,sampleQuery='';
 const selection=()=>sampleMode==='all'?null:sampleMode==='multi'?selectedSamples:$('#sample').value?[$('#sample').value]:[];
 const matchesSample=row=>!row.sample||selection()===null||selection().includes(row.sample);
 const singleSample=()=>selection()?.length===1?selection()[0]:'';
 const singleSampleOnly=()=>state.view==='primer'&&state.category==='genes';
 function scenarioFor(sample){const p=(data.primer_scenarios||[]).find(p=>p.scenario_id===$('#scenario')?.value);return p?.sample===sample?p.scenario_id:'';}
 const matchesScenario=row=>(row.scenario_id||'')===scenarioFor(row.sample);
 const scenarioTable=id=>ReportCharts.table(id).concat(ReportCharts.table('scenario_'+id));
 const moduleFilters=new Map(),lastSections=new Map(),filterIds=['sample','chain','primer','segment','stage','scenario','reference-scope','reference-query','reference-state','output-metric','gene-compare'];
 const moduleKey=()=>[state.view,state.view==='metrics'?state.metricsCategory:state.view==='primer'?state.category:''].join(':');
 function defaultModule(){return {values:Object.fromEntries(filterIds.map(id=>[id,id==='sample'?samples[0]||'':id==='stage'?(rawPrimerMatching?'raw_R1':'mapping'):id==='reference-scope'?'sample':''])),sampleMode:['overview','outputs'].includes(state.view)?'all':'single',selectedSamples:[],sampleQuery:'',page:0,referencePages:{},referencePageSize:10};}
 function rememberModule(){moduleFilters.set(moduleKey(),{values:Object.fromEntries(filterIds.map(id=>[id,$('#'+id)?.value||''])),sampleMode,selectedSamples:[...selectedSamples],sampleQuery,primerQuery:state.primerQuery||'',page:state.page,referencePages:{...state.referencePages},referencePageSize:state.referencePageSize});}
 function restoreModule(saved){
   samplePanelOpen=false;sampleMode=saved.sampleMode;selectedSamples=[...saved.selectedSamples];sampleQuery=saved.sampleQuery||'';state.primerQuery=saved.primerQuery||'';state.page=saved.page;state.referencePages={...saved.referencePages};state.referencePageSize=saved.referencePageSize||10;$('#reference-page-size').value=state.referencePageSize;
   for(const id of filterIds){const input=$('#'+id);if(!input)continue;const value=saved.values[id]||'';if(input.tagName==='SELECT'&&value&&!Array.from(input.options).some(o=>o.value===value))input.add(option(value,value));input.value=value;}
 }
 function locate(node){const top=node.getBoundingClientRect().top+window.scrollY-($('.report-nav')?.offsetHeight||0)-20;window.scrollTo({top:Math.max(0,top),behavior:'instant'});}
 function openModule(change){
   rememberModule();lastSections.set(state.main,{view:state.view,metricsCategory:state.metricsCategory,category:state.category});change();restoreModule(moduleFilters.get(moduleKey())||defaultModule());render();locate($('#view-title'));
 }
 function scenarioOptions(){
   const select=$('#scenario');if(!select)return;
   const old=select.value,profiles=(data.primer_scenarios||[]).filter(matchesSample).filter(p=>!$('#chain').value||p.chain===$('#chain').value);
   const active=state.view==='primer'||state.view==='reference'&&$('#reference-scope').value==='primer';
   select.replaceChildren(option('原引物组',''),...profiles.map(p=>option(p.sample+' / '+p.primer_group+'（存疑）',p.scenario_id)));
   if(profiles.some(p=>p.scenario_id===old))select.value=old;
   $('#scenario-filter').hidden=!active||!profiles.length;
   if(!active)select.value='';
   let note=$('#scenario-note');if(!note){note=document.createElement('p');note.id='scenario-note';note.className='scenario-note';$('#module-rules').after(note);}
   const chosen=profiles.find(p=>p.scenario_id===select.value);
   note.hidden=!chosen;note.textContent=chosen?chosen.sample+' / '+chosen.primer_group+'（存疑）':'';
 }
 function samplePicker(){
   const select=$('#sample');let host=$('#sample-picker');
   select.setAttribute('aria-label','样本');
   const changed=()=>{state.page=0;state.referencePages={};render();};
   if(!host){
     host=document.createElement('div');host.id='sample-picker';host.className='sample-picker';select.closest('label').replaceWith(host);
     const head=document.createElement('div'),title=document.createElement('span'),modes=document.createElement('div');head.className='sample-picker-head';title.textContent='样本范围';modes.className='sample-modes';modes.setAttribute('role','group');modes.setAttribute('aria-label','样本选择方式');
     for(const [mode,label] of [['all','全部'],['single','单选'],['multi','多选']]){const button=document.createElement('button');button.type='button';button.textContent=label;button.dataset.mode=mode;button.onclick=()=>{if(mode===sampleMode)return;const prior=selection();if(mode==='multi')selectedSamples=prior||[...eligibleSamples];if(mode==='single')select.value=prior?.find(s=>eligibleSamples.includes(s))||select.value||eligibleSamples[0]||'';sampleMode=mode;samplePanelOpen=false;changed();};modes.append(button);}head.append(title,modes);
     const all=document.createElement('div');all.className='sample-all-summary';
     const search=document.createElement('input'),singleEmpty=document.createElement('span');search.id='sample-single-search';search.type='search';search.placeholder='搜索样本';search.setAttribute('aria-label','搜索单个样本');search.oninput=()=>{sampleQuery=search.value;samplePicker();};singleEmpty.id='sample-single-empty';singleEmpty.className='caption';singleEmpty.textContent='没有匹配样本，可清除搜索后重选。';
     const trigger=document.createElement('button');trigger.type='button';trigger.className='sample-multi-trigger';trigger.setAttribute('aria-controls','sample-options');trigger.onclick=()=>{samplePanelOpen=!samplePanelOpen;samplePicker();if(samplePanelOpen)$('#sample-query').focus({preventScroll:true});};
     const panel=document.createElement('div'),query=document.createElement('input'),actions=document.createElement('div'),list=document.createElement('div'),empty=document.createElement('div');panel.id='sample-options';panel.className='sample-options';query.id='sample-query';query.type='search';query.placeholder='搜索样本';query.setAttribute('aria-label','搜索多个样本');query.oninput=()=>{sampleQuery=query.value;samplePicker();};actions.className='sample-options-actions';list.className='sample-options-list';empty.className='sample-search-empty';
     const choose=document.createElement('button'),clear=document.createElement('button');choose.type=clear.type='button';choose.id='sample-choose-results';choose.onclick=()=>{const found=eligibleSamples.filter(sampleMatchesQuery);selectedSamples=eligibleSamples.filter(s=>selectedSamples.includes(s)||found.includes(s));changed();};clear.textContent='清空';clear.onclick=()=>{selectedSamples=[];changed();};actions.append(choose,clear);
     const message=document.createElement('span'),reset=document.createElement('button');message.textContent='没有符合条件的样本';reset.type='button';reset.textContent='清除搜索';reset.onclick=()=>{sampleQuery='';samplePicker();query.focus({preventScroll:true});};empty.append(message,reset);panel.append(query,actions,list,empty);host.append(head,all,search,singleEmpty,select,trigger,panel);
   }
   const modes=host.querySelector('.sample-modes');modes.hidden=singleSampleOnly();for(const button of modes.children){button.setAttribute('aria-pressed',String(button.dataset.mode===sampleMode));button.disabled=!eligibleSamples.length;}
   select.hidden=sampleMode!=='single';const search=$('#sample-single-search');search.hidden=sampleMode!=='single'||eligibleSamples.length<=12;if(search.value!==sampleQuery)search.value=sampleQuery;
   for(const item of select.options)item.hidden=sampleMode==='single'&&!sampleMatchesQuery(item.value);
   $('#sample-single-empty').hidden=sampleMode!=='single'||!sampleQuery||eligibleSamples.some(sampleMatchesQuery);
   const all=host.querySelector('.sample-all-summary');all.hidden=sampleMode!=='all';all.textContent=(eligibleSamples.length<samples.length?'全部适用样本':'全部样本')+' · '+eligibleSamples.length+' 个';
   const trigger=host.querySelector('.sample-multi-trigger');trigger.hidden=sampleMode!=='multi';trigger.textContent=selectedSamples.length===1?selectedSamples[0]:selectedSamples.length?'已选 '+selectedSamples.length+' 个样本':'选择要比较的样本';trigger.setAttribute('aria-expanded',String(samplePanelOpen&&sampleMode==='multi'));
   const panel=$('#sample-options');panel.hidden=!samplePanelOpen||sampleMode!=='multi';const query=$('#sample-query');if(query.value!==sampleQuery)query.value=sampleQuery;
   const list=panel.querySelector('.sample-options-list'),signature=JSON.stringify([moduleKey(),eligibleSamples]);
   if(list.dataset.signature!==signature){list.replaceChildren();list.dataset.signature=signature;for(const name of eligibleSamples){const row=document.createElement('label'),check=document.createElement('input'),text=document.createElement('span'),badge=document.createElement('small');check.type='checkbox';check.value=name;check.onchange=()=>{selectedSamples=eligibleSamples.filter(s=>s===name?check.checked:selectedSamples.includes(s));changed();};text.textContent=name;badge.textContent=values(state.contexts.filter(r=>r.sample===name),'chain').join(' / ');row.append(check,text,badge);list.append(row);}}
   let found=0;for(const row of list.children){const check=row.querySelector('input');check.checked=selectedSamples.includes(check.value);row.hidden=!sampleMatchesQuery(check.value);if(!row.hidden)found++;}
   panel.querySelector('.sample-search-empty').hidden=found>0;$('#sample-choose-results').textContent=sampleQuery.trim()?'选择搜索结果':'全选';$('#sample-choose-results').disabled=!found;
 }
 function sampleMatchesQuery(name){return (name+' '+values(state.contexts.filter(r=>r.sample===name),'chain').join(' ')).toLowerCase().includes(sampleQuery.trim().toLowerCase());}
 document.addEventListener('click',e=>{if(samplePanelOpen&&!e.composedPath().includes($('#sample-picker'))){samplePanelOpen=false;samplePicker();}});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&samplePanelOpen){samplePanelOpen=false;samplePicker();$('#sample-picker .sample-multi-trigger')?.focus();}});
 function options(select,items,all){select.replaceChildren(...(all?[option(all,'')]:[]),...items.map(v=>option(v,v)));}
 function searchable(select){
   let input=document.querySelector('[data-search-for="'+select.id+'"]');
   if(select.options.length<=12){if(input)input.remove();return;}
   if(!input){input=document.createElement('input');input.type='search';input.dataset.searchFor=select.id;input.setAttribute('aria-label',select.id==='sample'?'搜索样本':'搜索引物');input.placeholder=select.id==='sample'?'搜索样本':'搜索引物';select.before(input);input.oninput=()=>{if(select.id==='primer')state.primerQuery=input.value;const query=input.value.toLowerCase();for(const option of select.options)option.hidden=Boolean(option.value)&&!option.text.toLowerCase().includes(query);};}
   input.oninput();
 }
 const samples=[...new Set(data.samples.map(r=>r.sample).filter(Boolean))],chains=values(data.samples,'chain');
 options($('#sample'),samples);options($('#chain'),chains,'全部链');
 function moduleContexts(){
   const stage=state.view==='primer'&&state.category==='matching'?$('#stage').value:'',key=[state.view,state.metricsCategory,state.category,stage,$('#reference-scope').value].join(':');
   if(scopeCache.has(key))return scopeCache.get(key);
   let ids;
   if(state.view==='metrics')ids=[state.metricsCategory==='bcell'?'bcell_metrics':'diversity_summary'];
   else if(state.view==='sample')ids=['sample_gene_usage'];
   else if(state.view==='outputs')ids=['sample_output_summary'];
   else if(state.view==='stages')ids=['stage_volume_summary'];
   else if(state.view==='reference')ids=$('#reference-scope').value==='primer'?['primer_reference_gene_observation','scenario_primer_reference_gene_observation','primer_gene_usage','scenario_primer_gene_usage','primer_unobserved_reference_genes']:['reference_gene_observation','sample_gene_usage','unobserved_reference_genes'];
   else if(state.view==='primer')ids=state.category==='genes'||state.category==='ccompare'?['primer_gene_usage','scenario_primer_gene_usage']:['primer_mapping_summary','scenario_primer_mapping_summary'];
   else ids=['dual_matching_summary','matching_total_summary','isotype_summary','isotype_distribution'];
   const available=ids.filter(id=>document.getElementById('table-'+id));
   let contexts;
   if(available.length){
     const groupIds=new Set(),samplesWithAllChains=new Set(),pairs=new Set();
     for(const id of available)for(const row of ReportCharts.table(id)){
       if(stage&&row.stage&&row.stage!==stage)continue;
       if(state.view==='primer'&&state.category==='mapping'&&row.stage!=='mapping')continue;
       if(state.view==='primer'&&state.category==='ccompare'&&row.segment!=='C')continue;
       if(row.group_id)groupIds.add(row.base_group_id||row.group_id);
       else if(row.sample&&row.chain)pairs.add(JSON.stringify([row.sample,row.chain]));
       else if(row.sample)samplesWithAllChains.add(row.sample);
     }
     contexts=data.groups.filter(g=>groupIds.has(g.group_id)||pairs.has(JSON.stringify([g.sample,g.chain]))||samplesWithAllChains.has(g.sample));
   }else{
     const figures=data.figures.filter(f=>f.view===state.view&&primerFigure(f)&&(state.view!=='metrics'||(state.metricsCategory==='bcell'?['bcell_metrics','bcell_metrics_comparison']:['diversity','diversity_comparison']).includes(f.kind)));
     contexts=data.groups.filter(g=>figures.some(f=>(!f.sample||f.sample===g.sample)&&(!f.chain||f.chain===g.chain))&&(state.view!=='metrics'||state.metricsCategory!=='bcell'||g.chain==='IGH'));
   }
   scopeCache.set(key,contexts);return contexts;
 }
 function scopeFilters(){
   state.contexts=moduleContexts();const eligible=new Set(state.contexts.map(g=>g.sample)),items=samples.filter(sample=>eligible.has(sample)),select=$('#sample');
   if(singleSampleOnly()){const chosen=selection()?.find(sample=>items.includes(sample));if(chosen)select.value=chosen;sampleMode='single';selectedSamples=[];samplePanelOpen=false;}
   const previous=select.value;
   eligibleSamples=items;if(sampleMode==='multi')selectedSamples=selectedSamples.filter(s=>items.includes(s));
   options(select,items);select.value=items.includes(previous)?previous:items[0]||'';select.disabled=!items.length;
   const chain=$('#chain'),old=chain.value,available=values(state.contexts.filter(matchesSample),'chain');
   options(chain,available,'全部链');if(available.includes(old))chain.value=old;
   if(available.length===1&&['sample','primer','reference'].includes(state.view))chain.value=available[0];
   chain.disabled=!available.length;return available;
 }
 function primers(){
   const chain=$('#chain').value,previous=$('#primer').value;
   const rows=(data.primer_catalog||[]).filter(r=>matchesSample(r)&&matchesScenario(r)&&(!chain||r.chain===chain)),items=values(rows,'primer');
   const specific=state.view==='reference'&&$('#reference-scope').value==='primer'||state.view==='primer'&&state.category==='genes';
   options($('#primer'),items,specific&&selection()?.length===1?null:'全部引物');if(items.includes(previous))$('#primer').value=previous;const query=state.primerQuery||'';searchable($('#primer'));const search=$('[data-search-for="primer"]');if(search){search.value=query;search.oninput();}
 }

 function include(row){return ['sample','chain','primer','segment','stage'].every(key=>{
   if(key==='sample')return matchesSample(row);
   if(row.scenario_id&&!matchesScenario(row))return false;
   if(state.view==='primer'&&row.sample&&!matchesScenario(row))return false;
   if(key==='chain'&&!row.chain&&row.interactive)return true;
   if(key==='stage'){
     if(state.view==='primer'&&state.category==='mapping')return row.stage==='mapping';
     if(!(state.view==='primer'&&state.category==='matching'))return true;
   }
   if(key==='primer'&&(state.view==='primer'&&state.category!=='genes'||['primers','primer_mapping'].includes(row.kind)))return true;
   return !$('#'+key).value||!Object.hasOwn(row,key)||row[key]===$('#'+key).value;
 });}
 function metricFigure(row){if(state.view!=='metrics')return true;const kinds=state.metricsCategory==='bcell'?['bcell_metrics','bcell_metrics_comparison']:['diversity','diversity_comparison'];return kinds.includes(row.kind)&&(singleSample()?row.sample===singleSample():!row.sample);}
 function primerFigure(row){return state.view!=='primer'||({primers:'matching',primer_mapping:'mapping',primer_genes:'genes',genes:'genes',composition:'ccompare',heatmap:'ccompare'})[row.kind]===state.category;}
 function outputFigure(row){return state.view!=='outputs'||($('#chain').value?row.chain===$('#chain').value:!row.chain);}
  function overviewFigure(row){if(state.view==='overview'&&row.kind==='isotype_distribution')return false;return !['overview','stages'].includes(state.view)||(singleSample()?row.sample===singleSample():!row.sample);}
  function overviewIsotype(){
    if(state.view!=='overview')return null;
    const available=data.figures.filter(f=>f.view==='overview'&&f.kind==='isotype_distribution'&&matchesSample(f));
    if(!available.length)return null;
    const options=samples.filter(sample=>available.some(f=>f.sample===sample));
    if(!options.includes(overviewIsotypeSample))overviewIsotypeSample=options[0];
    const figure=available.find(f=>f.sample===overviewIsotypeSample);
    return {...figure,samples:[figure.sample],moduleKey:'overview:isotype-focus',overviewFocus:true,focusOptions:options};
  }
 let focusedFigure=null;
 function closeFigure(){
   if(!focusedFigure)return;const {frame,placeholder,opener,scroll}=focusedFigure;focusedFigure=null;placeholder.replaceWith(frame);frame.classList.remove('expanded-figure');if($('#figure-dialog').open)$('#figure-dialog').close();document.documentElement.classList.remove('figure-open');window.scrollTo({top:scroll,behavior:'instant'});if(opener.isConnected)opener.focus({preventScroll:true});
 }
 function enlargeFigure(frame,opener){
   if(focusedFigure)return;const placeholder=document.createComment('figure-position');frame.before(placeholder);focusedFigure={frame,placeholder,opener,scroll:window.scrollY};$('#figure-dialog-content').append(frame);frame.classList.add('expanded-figure');document.documentElement.classList.add('figure-open');$('#figure-dialog').showModal();$('#figure-dialog-close').focus({preventScroll:true});
 }
 $('#figure-dialog-close').onclick=closeFigure;$('#figure-dialog').addEventListener('cancel',event=>{event.preventDefault();closeFigure();});$('#figure-dialog').addEventListener('close',()=>{if(!$('#figure-dialog').open)closeFigure();});
 function figureNode(figure,heading){
   const frameKey=JSON.stringify([figure,heading||'',document.documentElement.classList.contains('dark')]);currentFrames.add(frameKey);if(figureFrames.has(frameKey))return figureFrames.get(frameKey);
   const frame=document.createElement('figure'),caption=document.createElement('figcaption'),title=document.createElement('span'),button=document.createElement('button'),actions=document.createElement('div'),status=document.createElement('p'),expand=document.createElement('button');actions.className='figure-actions';status.className='figure-status';status.setAttribute('role','status');status.hidden=true;
   const context=[figure.sample,figure.chain,figure.primer,figure.primer_group&&figure.primer_group+'（存疑）'].filter(Boolean).join(' / ');title.textContent=heading||context||figure.title;if(heading||!context)title.className='sr-only';expand.type='button';expand.textContent='放大';expand.setAttribute('aria-label','放大查看图表');expand.onclick=()=>enlargeFigure(frame,expand);button.type='button';button.dataset.export='png';button.textContent=figure.interactive?'导出 PNG':'下载 PNG';actions.append(expand,button);caption.append(title,actions);frame.append(caption,status);
   async function runExport(control,label,exporter){
     control.disabled=true;status.hidden=false;status.classList.remove('error');status.textContent='正在生成 '+label+'…';control.textContent='正在生成…';let failed=false;
     try{const result=await exporter();status.textContent=result===false?'当前没有可导出的显示数据，可恢复图例或调整筛选。':'已发起 '+label+' 下载。';}
     catch(error){failed=true;status.classList.add('error');status.textContent=label+' 生成失败，请重试。';}
     finally{control.disabled=false;control.textContent=failed?'重试 '+label:(label==='CSV'?'数据 CSV':figure.interactive?'导出 PNG':'下载 PNG');}
   }
   if(figure.interactive){
    const specs=ReportCharts.specs(figure),charts=[],content=document.createElement('div');content.className=specs.length===2?'metric-pair':figure.kind==='sample_outputs'&&specs.length===3?'output-panels':'chart-stack';
    const coverage=coverageNode(figure);if(coverage){figure.coverage=Array.from(coverage.children,n=>n.textContent);frame.append(coverage);}frame.append(content);
     if(!specs.length){const empty=document.createElement('p');empty.className='empty';empty.textContent='当前范围无可用统计数据。';content.append(empty);button.disabled=true;}
     for(const spec of specs){const host=document.createElement('div');host.className='chart-host';content.append(host);pendingCharts.push(()=>charts.push(ReportCharts.mount(host,spec,figure)));}
     button.onclick=()=>runExport(button,'PNG',()=>ReportCharts.exportPNG(charts,figure));
     const csv=document.createElement('button');csv.type='button';csv.dataset.export='csv';csv.textContent='数据 CSV';csv.setAttribute('aria-label','导出当前图数据 CSV');csv.disabled=!specs.length;csv.onclick=()=>runExport(csv,'CSV',()=>ReportCharts.exportCSV(charts,figure));actions.append(csv);
   }else{
     const img=document.getElementById(figure.id).content.firstElementChild.cloneNode(true);img.loading='lazy';frame.append(img);
     button.onclick=()=>runExport(button,'PNG',()=>{const a=document.createElement('a');a.href=img.src;a.download=figure.filename;a.click();return true;});
   }
   if(figure.sample&&['isotype','isotype_distribution','primer_genes','composition','primers','primer_mapping'].includes(figure.kind)){
     const inspect=document.createElement('button');inspect.type='button';inspect.className='inspect-context';inspect.textContent=['isotype','isotype_distribution'].includes(figure.kind)?'查看该样本引物分析 →':figure.kind==='primer_mapping'?'查看该样本基因组成 →':'查看该样本 Mapping 效率 →';
     inspect.onclick=()=>openContext(figure,['isotype','isotype_distribution'].includes(figure.kind)?'matching':figure.kind==='primer_mapping'?'sample':'mapping');frame.append(inspect);
   }
   figureFrames.set(frameKey,frame);return frame;
 }
 function openContext(figure,target){
   openModule(()=>{state.main=target==='sample'?'sample':'primer';state.view=state.main;if(target!=='sample')state.category=target;});
   sampleMode='single';selectedSamples=[];$('#sample').value=figure.sample;scopeFilters();$('#chain').value=figure.chain||'';scenarioOptions();
   if(figure.scenario_id)$('#scenario').value=figure.scenario_id;else $('#scenario').value='';
   state.page=0;render();locate($('#view-title'));
 }
 function coverageNode(figure){
   if(figure.kind==='sample_outputs'){
     const host=document.createElement('div');host.className='coverage-strip';const keys=figure.outputMetric?[figure.outputMetric]:['total_reads','total_receptor_rna','total_unique_cdr3s'];
     for(const row of ReportCharts.table('sample_output_summary').filter(r=>(!figure.sample||r.sample===figure.sample)&&(!figure.samples||figure.samples.includes(r.sample))&&(!figure.chain||r.chain===figure.chain))){
       const missing=keys.filter(key=>(key==='total_reads'&&!figure.chain?(data.sample_inputs||[]).find(r=>r.sample===row.sample)?.total_reads:row[key])==null);
       if(missing.length){const note=document.createElement('span');note.className='coverage-badge';note.textContent=row.sample+' / '+row.chain+' · N/A: '+missing.map(k=>({total_reads:'Total Reads',total_receptor_rna:'Total Receptor RNA',total_unique_cdr3s:'Total Unique CDR3s'})[k]).join(', ');host.append(note);}
     }
     return host.children.length?host:null;
   }
   const isotype=['isotype','isotype_distribution'].includes(figure.kind),genes=['sample_genes','primer_genes','composition'].includes(figure.kind);
   if(!isotype&&!genes)return null;
   const host=document.createElement('div');host.className='coverage-strip';
   const groups=data.groups.filter(g=>(!figure.group_id||g.group_id===figure.group_id)&&(!figure.sample||g.sample===figure.sample)&&(!figure.chain||g.chain===figure.chain)&&(!figure.samples||figure.samples.includes(g.sample))&&(!isotype||g.chain==='IGH'));
   for(const group of groups){
     const segment=isotype||figure.kind==='composition'?'C':figure.segment;
     let total,classified,unit='copy';
     if(isotype){const row=ReportCharts.table('isotype_summary').find(r=>r.group_id===group.group_id&&r.metric==='abundance'&&r.level==='main');total=row?.total_records;classified=row?.annotated_records;unit=row?.unit||data.composition_weight||'reads';}
     else if(!figure.scenario_id){const coverage=ReportCharts.table('annotation_coverage').find(r=>r.group_id===group.group_id&&(r.primer||'')===(figure.primer||'')&&r.segment===segment);total=coverage?.total_count;classified=coverage?.classified_count;
       if(!figure.primer&&figure.kind!=='composition'&&total==null){const source=(data.composition_sources||[]).find(r=>r.group_id===group.group_id&&r.status==='complete');const row=ReportCharts.table('reference_gene_observation').find(r=>r.group_id===group.group_id&&r.segment===segment&&r.metric==='expression');total=source?.copies;classified=row?.denominator;}
     }
     if(total==null||classified==null||total<0||classified<0||classified>total)continue;
     const badge=document.createElement('span');badge.className='coverage-badge';badge.dataset.sample=group.sample;
     badge.textContent=group.sample+' / '+group.chain+' · '+segment+' classified '+(total?((classified/total)*100).toFixed(1)+'%':'N/A')+' ('+Number(classified).toLocaleString('en-US')+' / '+Number(total).toLocaleString('en-US')+' '+unit+')';host.append(badge);
   }
   return host.children.length?host:null;
 }

 function comparisonUnits(rows){const units=[],groups=new Map(),paired=new Set(['sample_genes','primer_genes','genes','composition','heatmap']);for(const figure of rows){if(figure.kind==='matching'&&['raw_R1','mapping'].includes(figure.stage)){const key=JSON.stringify(['matching',figure.sample,figure.chain,figure.group_id,figure.page]);if(!groups.has(key)){const group={matching:true,figures:[]};groups.set(key,group);units.push(group);}groups.get(key).figures.push(figure);continue;}if(!paired.has(figure.kind)||!['expression','unique_cdr3'].includes(figure.metric)){units.push({single:figure,cost:figure.kind==='sample_outputs'&&figure.interactive?Math.max(1,ReportCharts.specs(figure).length):['isotype','isotype_distribution','bcell_metrics_comparison'].includes(figure.kind)&&figure.interactive?2:1});continue;}const key=JSON.stringify([figure.view,figure.kind,figure.group_id,figure.sample,figure.chain,figure.primer,figure.segment,figure.preparation,figure.page]);if(!groups.has(key)){const group={figures:[]};groups.set(key,group);units.push(group);}groups.get(key).figures.push(figure);}return units.flatMap(unit=>{if(unit.single)return [unit];if(unit.matching){const left=unit.figures.find(f=>f.stage==='raw_R1'),right=unit.figures.find(f=>f.stage==='mapping');return [{left,right,matching:true,cost:Number(Boolean(left))+Number(Boolean(right))}];}const expressions=unit.figures.filter(f=>f.metric==='expression'),unique=unit.figures.filter(f=>f.metric==='unique_cdr3');if(expressions.length>1)return unit.figures.map(f=>({left:f.metric==='expression'?f:null,right:f.metric==='unique_cdr3'?f:null,cost:1}));return (unique.length?unique:[null]).map(right=>({left:expressions[0]||null,right,cost:Number(Boolean(expressions[0]))+Number(Boolean(right))}));});}
 function comparisonNode(unit){if(unit.single){if(!unit.single.overviewFocus)return figureNode(unit.single);const section=document.createElement('section'),head=document.createElement('div'),title=document.createElement('h3'),choices=document.createElement('div');section.className='overview-isotype';section.id='overview-isotype';head.className='overview-isotype-head';title.textContent='单样本 Isotype';choices.className='isotype-samples';choices.setAttribute('role','group');choices.setAttribute('aria-label','选择 Isotype 样本');for(const sample of unit.single.focusOptions){const button=document.createElement('button');button.type='button';button.textContent=sample;button.dataset.isotypeSample=sample;button.setAttribute('aria-pressed',String(sample===unit.single.sample));button.onclick=()=>{const top=section.getBoundingClientRect().top,left=window.scrollX;overviewIsotypeSample=sample;figures();const active=document.querySelector('[data-isotype-sample="'+CSS.escape(sample)+'"]');active?.focus({preventScroll:true});const restore=()=>{const card=$('#overview-isotype');if(card)window.scrollTo({left,top:window.scrollY+card.getBoundingClientRect().top-top,behavior:'instant'});};restore();requestAnimationFrame(restore);};choices.append(button);}head.append(title,choices);section.append(head,figureNode(unit.single,'Isotype'));return section;}const source=unit.left||unit.right,section=document.createElement('section'),heading=document.createElement('h3'),pair=document.createElement('div');section.className='comparison';pair.className='metric-pair';heading.textContent=[source.sample,source.chain,source.primer_group?source.primer_group+'（存疑）':'',source.primer,unit.matching?'引物匹配构成':source.segment+(source.kind==='composition'?' · 引物间对比':' 基因组成'),source.page>1?'第 '+source.page+' 页':''].filter(Boolean).join(' / ');section.append(heading);for(const [figure,label] of [[unit.left,unit.matching?'Raw':'% Expression'],[unit.right,unit.matching?'Mapping reads':'% Unique CDR3']]){if(figure)pair.append(figureNode(figure,label));else{const empty=document.createElement('div');empty.className='metric-missing';const title=document.createElement('strong'),text=document.createElement('p');title.textContent=label;text.textContent='当前范围没有可用的对应指标数据。';empty.append(title,text);pair.append(empty);}}section.append(pair);return section;}
 function geneFamily(gene,segment){const name=String(gene||'').split('*')[0].toUpperCase();let m;if(segment==='C')m=name.match(/^(IGH[AMDEG]|IGKC|IGLC|TR[ABDG]C)/);else if(segment==='V')m=name.match(/^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)V\d+)/);else if(segment==='J')m=name.match(/^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)J\d+)-/)||name.match(/^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)J)/);return m?m[1]:name;}
 function geneState(row){return Number(row.unique_count)>0?['matched','已匹配']:Number(row.ambiguous_count)>0?['unresolved','未唯一归属']:row.status==='zero_observed'?['unmatched','未匹配']:['unknown','未评估'];}
 function referenceEmpty(message,reset=false){const box=document.createElement('div'),text=document.createElement('p');box.className='reference-empty';text.textContent=message;box.append(text);if(reset){const button=document.createElement('button');button.type='button';button.textContent='清除基因筛选';button.onclick=()=>{$('#reference-query').value='';$('#reference-state').value='';state.referencePages={};references();};box.append(button);}return box;}
 function referenceRows(){
  const primer=$('#reference-scope').value==='primer',id=primer?'primer_reference_gene_observation':'reference_gene_observation';
  const source=document.getElementById('table-'+id),fallback=[primer?'primer_gene_usage':'sample_gene_usage',primer?'primer_unobserved_reference_genes':'unobserved_reference_genes'];
  const records=source?(primer?scenarioTable(id):ReportCharts.table(id)):fallback.flatMap(id=>ReportCharts.table(id));
  const query=$('#reference-query').value.trim().toLowerCase(),status=$('#reference-state').value;
  const scoped=records.filter(r=>Number(r.reference_catalog)===1&&include(r)&&(!primer||matchesScenario(r))),seen=new Set();
  const rows=scoped.filter(r=>{if(!['matched','unmatched'].includes(geneState(r)[0])||query&&!r.gene.toLowerCase().includes(query)||status&&geneState(r)[0]!==status)return false;
    const key=JSON.stringify([r.sample,r.chain,r.group_id||r.reference_id,r.primer||'',r.scenario_id||'',r.segment,r.gene]);if(seen.has(key))return false;seen.add(key);return true;});
  const natural=(a,b)=>String(a||'').localeCompare(String(b||''),'en',{numeric:true});
  rows.sort((a,b)=>samples.indexOf(a.sample)-samples.indexOf(b.sample)||natural(a.chain,b.chain)||natural(a.group_id||a.reference_id,b.group_id||b.reference_id)||natural(a.primer,b.primer)||['V','J','C'].indexOf(a.segment)-['V','J','C'].indexOf(b.segment)||natural(geneFamily(a.gene,a.segment),geneFamily(b.gene,b.segment))||natural(a.gene,b.gene));
  return {primer,source,records,scoped,rows};
 }
 let geneMatchingTotal=null;
 function totalGeneMatching(){
  if(geneMatchingTotal)return geneMatchingTotal;
  const load=(id,fallback)=>document.getElementById('table-'+id)?ReportCharts.table(id):fallback.flatMap(id=>ReportCharts.table(id));
  const primers=load('primer_reference_gene_observation',['primer_gene_usage','primer_unobserved_reference_genes']).concat(load('scenario_primer_reference_gene_observation',['scenario_primer_gene_usage']));
  const eligible=r=>Number(r.reference_catalog)===1&&['matched','unmatched'].includes(geneState(r)[0])&&(!r.metric||r.metric==='expression');
  const covered=new Set(primers.filter(eligible).map(r=>JSON.stringify([r.sample,r.chain,r.group_id||r.reference_id])));
  const fallbackSamples=load('reference_gene_observation',['sample_gene_usage','unobserved_reference_genes']).filter(r=>!covered.has(JSON.stringify([r.sample,r.chain,r.group_id||r.reference_id])));
  const seen=new Set(),rows=primers.concat(fallbackSamples).filter(r=>{if(!eligible(r))return false;const key=JSON.stringify([r.sample,r.chain,r.group_id||r.reference_id,r.primer||'',r.scenario_id||'',r.segment,r.gene]);if(seen.has(key))return false;seen.add(key);return true;});
  const natural=(a,b)=>String(a||'').localeCompare(String(b||''),'en',{numeric:true});
  rows.sort((a,b)=>samplesOrder(a.sample)-samplesOrder(b.sample)||natural(a.chain,b.chain)||natural(a.scenario_id,b.scenario_id)||natural(a.primer,b.primer)||['V','J','C'].indexOf(a.segment)-['V','J','C'].indexOf(b.segment)||natural(geneFamily(a.gene,a.segment),geneFamily(b.gene,b.segment))||natural(a.gene,b.gene)||natural(a.group_id||a.reference_id,b.group_id||b.reference_id));
  const variants=new Map();for(const r of rows){const key=JSON.stringify([r.sample,r.chain,r.primer||'',r.scenario_id||'']);if(!variants.has(key))variants.set(key,new Set());variants.get(key).add(r.group_id||r.reference_id||'');}
  const scenarios=rows.some(r=>r.scenario_id),multiple=[...variants.values()].some(set=>set.size>1);
  geneMatchingTotal=rows.map(r=>({'样本':r.sample,'链':r.chain,'引物':r.primer||'全部引物','基因':r.gene,'状态':geneState(r)[1],...(scenarios?{'分析组':r.scenario_id?'存疑 · '+(r.primer_group||r.scenario_id):'原组'}:{}),...(multiple?{'结果标识':r.group_id||r.reference_id||''}:{})}));
  return geneMatchingTotal;
 }
 function samplesOrder(sample){const index=samples.indexOf(sample);return index<0?samples.length:index;}
 function references(){
  const host=$('#reference-list');host.replaceChildren();host.hidden=state.view!=='reference';$('#reference-tools').hidden=host.hidden;if(host.hidden)return;
  const {primer,source,records,scoped,rows}=referenceRows();
  const pageSize=state.referencePageSize;
  const query=$('#reference-query').value.trim().toLowerCase(),status=$('#reference-state').value;
  const groups=new Map(),download=$('#reference-download'),total=totalGeneMatching();download.disabled=!total.length;
  $('#reference-export-status').textContent=total.length?'全部样本与引物 · '+total.length+' 条记录':'';
  download.onclick=()=>{
    const filename=ReportCharts.exportName({kind:'gene_matching_total',samples:null},'csv').replace(/__p1\.csv$/,'.csv');
    try{ReportCharts.exportRows(total,filename);$('#reference-export-status').textContent='已导出基因匹配总表 · '+total.length+' 条记录';}catch{$('#reference-export-status').textContent='下载失败，请重试';}
  };
  if(!source&&records.length){const note=document.createElement('p');note.className='caption';note.textContent='当前为历史报告的可用基因清单；重新生成分析可补全数据库匹配状态。';host.append(note);}
  for(const row of rows){const key=JSON.stringify([row.sample,row.chain,row.group_id||row.reference_id,row.primer||'',row.scenario_id||'']);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  if(!rows.length){host.append(referenceEmpty(selection()?.length===0?'请选择要查看的样本':scoped.length?'暂无符合条件的基因':'暂无可用的参考基因清单',Boolean(query||status)));return;}
  const contexts=new Map();for(const records of groups.values()){const key=JSON.stringify([records[0].sample,records[0].chain]);contexts.set(key,(contexts.get(key)||0)+1);}const positions=new Map();
  for(const records of groups.values()){
   const context=JSON.stringify([records[0].sample,records[0].chain]),position=(positions.get(context)||0)+1;positions.set(context,position);
   const section=document.createElement('section'),heading=document.createElement('h3'),grid=document.createElement('div');section.className='reference-group';grid.className='reference-grid'+($('#segment').value?' single-segment':'');heading.textContent=[records[0].sample,records[0].chain,primer?records[0].primer:'',records[0].primer_group?records[0].primer_group+'（存疑）':'',contexts.get(context)>1?'结果 '+position:''].filter(Boolean).join(' / ');section.append(heading);
   for(const segment of ['V','J','C']){
    if($('#segment').value&&$('#segment').value!==segment)continue;
    const genes=new Map();for(const row of records.filter(r=>r.segment===segment)){if(!genes.has(row.gene))genes.set(row.gene,row);}
    const ordered=[...genes.values()].sort((a,b)=>geneFamily(a.gene,segment).localeCompare(geneFamily(b.gene,segment),'en',{numeric:true})||a.gene.localeCompare(b.gene,'en',{numeric:true}));
    const key=JSON.stringify([records[0].sample,records[0].chain,records[0].group_id||records[0].reference_id,records[0].primer||'',records[0].scenario_id||'',segment]);
    const page=Math.max(0,Math.min(state.referencePages[key]||0,Math.ceil(ordered.length/pageSize)-1));state.referencePages[key]=page;
    const card=document.createElement('section'),title=document.createElement('h4');card.className='reference-card';title.textContent=({V:'V · 可变区',J:'J · 连接区',C:'C · 恒定区'})[segment];
    if(!ordered.length){const known=scoped.some(r=>r.sample===records[0].sample&&r.chain===records[0].chain&&(r.group_id||r.reference_id)===(records[0].group_id||records[0].reference_id)&&r.segment===segment);card.append(title,referenceEmpty(known?'暂无符合条件的 '+segment+' 基因':'暂无 '+segment+' 区参考基因',known&&Boolean(query||status)));grid.append(card);continue;}
    const columns=document.createElement('div');columns.className='reference-columns';columns.innerHTML='<span>基因</span><span>状态</span>';card.append(title,columns);
    const families=new Map();for(const row of ordered.slice(page*pageSize,page*pageSize+pageSize)){const family=geneFamily(row.gene,segment);if(!families.has(family))families.set(family,[]);families.get(family).push(row);}
    for(const [family,rows] of families){const block=document.createElement('section'),header=document.createElement('div'),label=document.createElement('h5'),count=document.createElement('span'),table=document.createElement('table'),body=document.createElement('tbody');block.className='gene-family-block';block.dataset.geneFamily=family;header.className='gene-family-heading';label.textContent=family;const total=ordered.filter(r=>geneFamily(r.gene,segment)===family).length;count.textContent=rows.length===total?total+' 个基因':rows.length+' / '+total;count.title='本页 '+rows.length+' 个，家族共 '+total+' 个基因';header.append(label,count);table.className='reference-table';table.setAttribute('aria-label',family+' 家族基因');
     for(const row of rows){const [status,label]=geneState(row),tr=document.createElement('tr'),name=document.createElement('td'),cell=document.createElement('td'),badge=document.createElement('span');tr.dataset.gene=row.gene;name.textContent=row.gene;badge.className='gene-status';badge.dataset.state=status;badge.textContent=label;cell.append(badge);tr.append(name,cell);body.append(tr);}table.append(body);block.append(header,table);card.append(block);
    }
    const pager=document.createElement('div'),count=document.createElement('span');pager.className='reference-pager';count.textContent=ordered.length?`${page*pageSize+1}–${Math.min(ordered.length,page*pageSize+pageSize)} / ${ordered.length} 个基因`:'0 个基因';pager.append(count);
    for(const [label,delta] of [['上一页',-1],['下一页',1]]){const button=document.createElement('button');button.textContent=label;button.disabled=delta<0?page===0:(page+1)*pageSize>=ordered.length;button.setAttribute('aria-label',segment+' 基因：'+label);button.onclick=()=>{state.referencePages[key]=page+delta;references();};pager.append(button);}card.append(pager);grid.append(card);
   }section.append(grid);host.append(section);
  }
 }

 function rules(){const host=$('#module-rules'),kind=state.view==='primer'?(state.category==='ccompare'?'genes':state.category):state.view==='sample'?'genes':state.view==='metrics'?state.metricsCategory:state.view,rows=data.statistic_rules[kind]||(state.view==='metrics'?data.statistic_rules.metrics:[])||[];host.replaceChildren();host.hidden=!rows.length||state.view==='reference';if(host.hidden)return;const summary=document.createElement('summary'),list=document.createElement('ol');summary.textContent='统计口径';for(const [title,text] of rows){const item=document.createElement('li'),label=document.createElement('strong');label.textContent=title+': ';item.append(label,document.createTextNode(text));list.append(item);}host.append(summary,list);}
 function analysisTools(){
   const outputs=state.view==='outputs',genes=state.view==='sample';$('#analysis-tools').hidden=!outputs&&!genes;$('#output-metric-filter').hidden=!outputs;$('#gene-compare-filter').hidden=!genes;state.alignment=null;
   const note=$('#comparison-note');note.textContent='';if(!genes)return;
   const aligned=ReportCharts.alignGenes({samples:selection(),chain:$('#chain').value,segment:$('#segment').value}),select=$('#gene-compare');select.options[1].disabled=!aligned.ok;
   if(select.value==='aligned'&&aligned.ok){state.alignment=aligned;note.textContent='同一基因使用统一顺序和坐标。';}
   else if(!aligned.ok){select.value='';note.textContent=aligned.reason;}
 }
 function alignedFigures(rows){
   if(!state.alignment)return rows;
   const seen=new Set(),result=[];
   for(const row of rows){if(row.kind!=='sample_genes'){result.push(row);continue;}const key=JSON.stringify([row.group_id,row.segment,row.metric]),alignment=state.alignment.segments[row.segment];if(seen.has(key)||!alignment)continue;seen.add(key);
     for(let page=1;page<=Math.ceil(alignment.names.length/25);page++)result.push({...row,page,alignment:state.alignment});
   }return result;
 }
 function figures(){
   closeFigure();pendingCharts.length=0;currentFrames=new Set();$('#overview-isotype-shortcut').hidden=true;
   const reference=state.view==='reference';references();$('#gallery').hidden=reference;$('#fig-count').parentElement.hidden=reference;$('#gallery-bottom').hidden=true;$('#fig-empty').hidden=true;if(reference){ReportCharts.dispose();figureFrames.clear();$('#gallery').replaceChildren();return;}
   const rows=selection()?.length===0?[]:alignedFigures(data.figures.filter(r=>r.view===state.view&&include(r)&&primerFigure(r)&&metricFigure(r)&&outputFigure(r)&&overviewFigure(r)).map(r=>({...r,...($('#chain').value&&!r.chain&&r.interactive?{chain:$('#chain').value}:{}),samples:selection(),moduleKey:moduleKey(),outputMetric:state.view==='outputs'?$('#output-metric').value:''})));
   const isotype=overviewIsotype();if(isotype){if(singleSample())for(let i=rows.length-1;i>=0;i--)if(rows[i].kind==='isotype')rows.splice(i,1);rows.push(isotype);}
   const units=comparisonUnits(rows),pages=[[]];let used=0;
   for(const unit of units){if(used+(unit.cost||1)>7&&pages[pages.length-1].length){pages.push([]);used=0;}pages[pages.length-1].push(unit);used+=unit.cost||1;}
   const shortcut=$('#overview-isotype-shortcut');shortcut.hidden=!isotype;shortcut.onclick=()=>{state.page=Math.max(0,pages.findIndex(page=>page.some(unit=>unit.single?.overviewFocus)));figures();const card=$('#overview-isotype');if(card){locate(card);card.querySelector('[aria-pressed=true]')?.focus({preventScroll:true});}};
   state.page=Math.max(0,Math.min(state.page,pages.length-1));
   $('#fig-count').textContent=`${units.reduce((n,u)=>n+(u.cost||1),0)} 个图表 · 第 ${state.page+1} / ${pages.length} 页`;
   $('#fig-count').parentElement.hidden=!units.length;$('#fig-prev').hidden=$('#fig-next').hidden=pages.length<=1;$('#fig-prev').disabled=state.page===0;$('#fig-next').disabled=state.page+1>=pages.length;
   $('#gallery-bottom').hidden=pages.length<=1;$('#bottom-count').textContent=`第 ${state.page+1} / ${pages.length} 页`;$('#bottom-prev').disabled=state.page===0;$('#bottom-next').disabled=state.page+1>=pages.length;
   const pageBy=delta=>{state.page+=delta;figures();locate($('#gallery'));$('#gallery').focus({preventScroll:true});};$('#fig-prev').onclick=$('#bottom-prev').onclick=()=>pageBy(-1);$('#fig-next').onclick=$('#bottom-next').onclick=()=>pageBy(1);
   $('#gallery').replaceChildren(...pages[state.page].map(comparisonNode));$('#fig-empty').hidden=rows.length>0;
   $('#fig-empty').textContent='当前范围暂无可用数据。';
   if(!rows.length&&state.view==='metrics'){
     const selectedGroups=data.groups.filter(g=>matchesSample(g)&&(!$('#chain').value||g.chain===$('#chain').value));
     $('#fig-empty').textContent=state.metricsCategory==='bcell'&&selectedGroups.length&&!selectedGroups.some(g=>g.chain==='IGH')?'B cell 指标适用于 IGH，请选择 IGH 样本。':'这份报告尚未生成'+(state.metricsCategory==='bcell'?' B cell 指标':'多样性指标')+'，需要基于已完成且来源校验通过的 06 后处理结果补充生成。';
   }
   if(!rows.length&&selection()?.length===0&&eligibleSamples.length){$('#fig-empty').textContent='请选择要查看的样本。';const choose=document.createElement('button');choose.type='button';choose.textContent='选择样本';choose.onclick=()=>{samplePanelOpen=true;samplePicker();$('#sample-query').focus({preventScroll:true});};$('#fig-empty').append(' ',choose);}
   for(const key of figureFrames.keys())if(!currentFrames.has(key))figureFrames.delete(key);
   ReportCharts.dispose(new Set($('#gallery').querySelectorAll('.interactive-chart')));
   for(const mount of pendingCharts)mount();pendingCharts.length=0;
 }
 function render(){
   const subviews=state.main==='outputs'?[['outputs','数据产出'],['stages','数据变化']]:state.main==='sample'?[['sample','基因组成'],['metrics-diversity','多样性指标'],['metrics-bcell','B cell 指标']]:[];
   const activeView=state.view==='metrics'?'metrics-'+state.metricsCategory:state.view;
   const tabs=$('#section-tabs');tabs.hidden=!subviews.length;const signature=subviews.map(([key])=>key).join(':');if(tabs.dataset.signature!==signature){tabs.dataset.signature=signature;tabs.replaceChildren(...subviews.map(([key,label])=>{const button=document.createElement('button');button.textContent=label;button.dataset.section=key;button.setAttribute('role','tab');button.setAttribute('aria-controls','gallery');button.onclick=()=>openModule(()=>{if(key.startsWith('metrics-')){state.view='metrics';state.metricsCategory=key.slice(8);}else state.view=key;});return button;}));}for(const button of tabs.children){const active=button.dataset.section===activeView;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
   for(const button of document.querySelectorAll('[data-main-view]')){const active=button.dataset.mainView===state.main;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}$('#report-workspace').setAttribute('aria-labelledby','tab-'+state.main);
   $('#sample-info').hidden=state.main!=='overview';
   const chain=$('#chain'),primerView=state.view==='primer',reference=state.view==='reference',available=scopeFilters();
   $('#primer-categories').hidden=!primerView;$('#category-note').hidden=true;
   if(rawPrimerMatching&&state.category==='mapping')state.category='matching';
   for(const button of document.querySelectorAll('[data-primer-category]')){button.hidden=rawPrimerMatching&&button.dataset.primerCategory==='mapping';const active=button.dataset.primerCategory===state.category;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;button.setAttribute('aria-controls','gallery');}
   $('#view-title').textContent=primerView?categories[state.category][0]:reference?'基因匹配':state.view==='metrics'?(state.metricsCategory==='bcell'?'B cell 指标':'多样性指标'):labels[state.view];
   samplePicker();scenarioOptions();chain.closest('label').hidden=['overview','stages'].includes(state.view)||available.length<2;
   primers();$('#primer').closest('label').hidden=!(primerView&&state.category==='genes'||reference&&$('#reference-scope').value==='primer');
   $('#segment').closest('label').hidden=!(state.view==='sample'||primerView&&state.category==='genes'||reference);
   $('#reference-scope-filter').hidden=!reference;$('#stage-filter').hidden=rawPrimerMatching||!(primerView&&state.category==='matching');
   if(primerView&&state.category==='mapping')$('#stage').value='mapping';
   $('#filter-summary').textContent=[sampleMode==='multi'&&selectedSamples.length?selectedSamples.slice(0,3).join('、')+(selectedSamples.length>3?' 等':''):'',chain.closest('label').hidden&&available.length===1?'适用链：'+available[0]:''].filter(Boolean).join(' · ');$('#filter-summary').hidden=!$('#filter-summary').textContent;
   analysisTools();rules();compositionNotice();figures();
 }

 for(const button of document.querySelectorAll('[data-main-view]'))button.onclick=()=>{if(state.main===button.dataset.mainView){locate($('#view-title'));return;}openModule(()=>{state.main=button.dataset.mainView;Object.assign(state,lastSections.get(state.main)||{view:state.main});});};
 for(const button of document.querySelectorAll('[data-primer-category]'))button.onclick=()=>openModule(()=>{state.category=button.dataset.primerCategory;});
 $('#reference-page-size').onchange=e=>{if(!e.target.reportValidity())return;state.referencePageSize=Number(e.target.value);state.referencePages={};references();};
 let referenceSearchTimer;$('#reference-query').oninput=()=>{clearTimeout(referenceSearchTimer);referenceSearchTimer=setTimeout(()=>{state.referencePages={};if(state.view==='reference')references();},180);};$('#reference-state').onchange=()=>{clearTimeout(referenceSearchTimer);state.referencePages={};references();};
 for(const select of document.querySelectorAll('.filters select'))select.onchange=()=>{if(select.id==='sample'||select.id==='chain'||select.id==='reference-scope')primers();if(select.id==='reference-scope'&&select.value==='sample')$('#primer').value='';state.referencePages={};state.page=0;render();};
 for(const id of ['output-metric','gene-compare'])$('#'+id).onchange=()=>{state.page=0;render();};
 $('#reset-filters').onclick=()=>{ReportCharts.resetSelections(moduleKey());if(state.view==='overview'){ReportCharts.resetSelections('overview:isotype-focus');overviewIsotypeSample='';}restoreModule(defaultModule());render();};
 $('#return-filters').onclick=()=>{locate($('#view-title'));$('#view-title').focus({preventScroll:true});};
 document.addEventListener('keydown',event=>{const list=event.target.closest('[role=tablist]');if(!list||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const buttons=Array.from(list.querySelectorAll('[role=tab]')).filter(b=>!b.disabled),index=buttons.indexOf(event.target);if(index<0)return;event.preventDefault();const next=buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length];next.focus({preventScroll:true});next.click();const box=next.getBoundingClientRect(),area=list.getBoundingClientRect();if(box.left<area.left)list.scrollLeft-=area.left-box.left;if(box.right>area.right)list.scrollLeft+=box.right-area.right;});
 $('#theme').onclick=()=>{document.documentElement.classList.toggle('dark');figures();};
 $('#share-html').onclick=()=>{
   const copy=document.documentElement.cloneNode(true),picker=copy.querySelector('#sample-picker');
   if(picker){const label=document.createElement('label'),select=picker.querySelector('#sample');label.textContent='样本';select.hidden=false;select.disabled=false;label.append(select);picker.replaceWith(label);}
   copy.querySelector('#gallery').replaceChildren();copy.querySelector('#figure-dialog-content').replaceChildren();copy.querySelector('#figure-dialog').removeAttribute('open');copy.classList.remove('figure-open');
   copy.querySelectorAll('[data-search-for],.interactive-chart,.chart-legend,.overflow-hint').forEach(node=>node.remove());
   const name=String(data.task_name||'SCIGBLAST').replace(/[\\/:*?"<>|]+/g,'_')+'__'+(data.report_id||'report')+'.html';
   ReportCharts.download(new Blob(['<!doctype html>\n'+copy.outerHTML],{type:'text/html;charset=utf-8'}),name);
 };
 const sampleScroll=$('#sample-info .table-wrap');let drag=null;
 sampleScroll.addEventListener('pointerdown',event=>{if(event.pointerType!=='mouse'||event.button!==0||event.altKey||event.target.closest('button,a,input,select'))return;const box=sampleScroll.getBoundingClientRect();if(event.clientX>=box.left+sampleScroll.clientWidth||event.clientY>=box.top+sampleScroll.clientHeight)return;drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:sampleScroll.scrollLeft,top:sampleScroll.scrollTop,active:false};sampleScroll.setPointerCapture(event.pointerId);});
 sampleScroll.addEventListener('pointermove',event=>{if(!drag||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(!drag.active&&Math.hypot(dx,dy)<5)return;if(!drag.active){drag.active=true;sampleScroll.classList.add('is-dragging');const selection=window.getSelection();if(selection?.anchorNode&&sampleScroll.contains(selection.anchorNode))selection.removeAllRanges();}event.preventDefault();sampleScroll.scrollLeft=drag.left-dx;sampleScroll.scrollTop=drag.top-dy;});
 const stopDrag=()=>{drag=null;sampleScroll.classList.remove('is-dragging');};sampleScroll.addEventListener('pointerup',stopDrag);sampleScroll.addEventListener('pointercancel',stopDrag);sampleScroll.addEventListener('lostpointercapture',stopDrag);
 window.addEventListener('pagehide',()=>ReportCharts.dispose());
 $('#sample-info details').open=true;
 primers();render();
})();

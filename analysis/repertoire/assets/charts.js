/* Public aggregates only. Counts and denominators are never recalculated here. */
window.ReportCharts=(()=>{
  const data=JSON.parse(document.getElementById('report-data').textContent),cache=new Map(),live=new Map(),selected=new Map(),views=new Map();
  const number=v=>v==null?'N/A':Number(v).toLocaleString('en-US',{maximumFractionDigits:4});
  function table(id){
    if(cache.has(id))return cache.get(id);
    const node=document.getElementById('table-'+id);if(!node)return [];
    const packed=JSON.parse(node.textContent),rows=packed.rows.map(values=>Object.fromEntries(packed.columns.map((key,i)=>[key,values[i]])));
    cache.set(id,rows);return rows;
  }
  const scopeIndexes=new WeakMap();
  function scope(rows,f,{primer=false,metric=false}={}){
    let candidates=rows;
    if(rows.length>=500){let indexes=scopeIndexes.get(rows);if(!indexes){indexes=new Map();scopeIndexes.set(rows,indexes);}
      for(const [key,value] of [['sample',f.sample],...(primer?[['primer',f.primer||'']]:[])]){if(value===undefined||key==='sample'&&!value)continue;
        if(!indexes.has(key)){const index=new Map();for(const row of rows){const v=key==='primer'?row.primer||'':row[key];if(!index.has(v))index.set(v,[]);index.get(v).push(row);}indexes.set(key,index);}
        const bucket=indexes.get(key).get(value)||[];if(bucket.length<candidates.length)candidates=bucket;
      }
    }
    const samples=Array.isArray(f.samples)?new Set(f.samples):null;
    return candidates.filter(r=>(!f.sample||r.sample===f.sample)&&(!samples||samples.has(r.sample))&&(!f.chain||!r.chain||r.chain===f.chain)&&(!f.group_id||!r.group_id||r.group_id===f.group_id)&&(!primer||(r.primer||'')===(f.primer||''))&&(!f.segment||r.segment===f.segment)&&(!metric||r.metric===f.metric&&(f.metric!=='unique_cdr3'||r.aa_field==='cdr3_aa')));
  }
  const unique=xs=>[...new Set(xs)],natural=(a,b)=>String(a).localeCompare(String(b),'en',{numeric:true});
  const point=(value,row={})=>({value:value==null?null:Number(value),count:row.count??row.unique_count??row.matched_reads??null,denominator:row.denominator??null,unit:row.unit||'',denominatorMeaning:row.denominatorMeaning||''});
  const metricName=metric=>metric==='unique_cdr3'?'% Unique CDR3':'% Expression';
  const compositionUnit=metric=>metric==='unique_cdr3'?'distinct_cdr3':data.composition_weight||'reads';
  const geneMeaning=metric=>metric==='unique_cdr3'?'Sum of distinct CDR3 counts within classifiable genes in this segment':'Classifiable '+(data.composition_weight||'reads')+' in this segment';
  const units={copy:'copy (AIRR umi_counts)',reads:'reads',distinct_cdr3:'distinct CDR3s'};
  function alignGenes(f){
    const rows=scope(table('sample_gene_usage'),f).filter(r=>r.metric==='expression'),ids=unique(rows.map(r=>r.group_id)),groups=ids.map(id=>data.groups.find(g=>g.group_id===id));
    if(groups.length<2)return {ok:false,reason:'选择至少两个同链样本后可对齐比较。'};
    if(groups.some(g=>!g)||unique(groups.map(g=>g.chain)).length!==1)return {ok:false,reason:'请先选择同一条链，再进行基因对齐。'};
    if(unique(groups.map(g=>g.sample)).length!==groups.length)return {ok:false,reason:'存在同一样本的多个结果组，请先限定结果来源。'};
    const segments={};
    for(const segment of unique(rows.map(r=>r.segment))){
      const references=groups.map(g=>g.reference_segments?.[segment]);
      const verified=references.every(r=>r?.available&&r.sha256)&&unique(references.map(r=>r.sha256)).length===1&&unique(groups.map(g=>g.reference_scope)).length===1;
      if(!verified)return {ok:false,reason:segment+' 区参考来源缺失或不一致，暂不能对齐比较。'};
      const observed=rows.filter(r=>r.segment===segment&&r.unique_count>0),names=unique(observed.map(r=>r.gene)),ranks=new Map();
      for(const row of observed)ranks.set(row.gene,Math.max(ranks.get(row.gene)||0,row.percent||0));
      names.sort((a,b)=>ranks.get(b)-ranks.get(a)||natural(a,b));
      const maximum=metric=>Math.max(1,...scope(table('sample_gene_usage'),{...f,segment,metric},{metric:true}).map(r=>r.percent||0));
      segments[segment]={names,maxima:Object.fromEntries(['expression','unique_cdr3'].map(m=>[m,Math.min(100,Math.max(5,5*Math.ceil(maximum(m)/5)))]))};
    }
    return {ok:true,segments,groupIds:ids};
  }
  function bars(labels,series,title,{horizontal=false,percent=false,stack=false}={}){
    return {type:'bars',labels,series,title,horizontal,percent,stack};
  }
  function gene(f){
    const source=table(f.scenario_id?'scenario_primer_gene_usage':f.kind==='primer_genes'?'primer_gene_usage':'sample_gene_usage'),rows=scope(source,f,{primer:true,metric:true}).filter(r=>r.unique_count>0);
    // Distinct input/reference groups must never be silently pooled.
    if(unique(rows.map(r=>r.group_id)).length>1)return [];
    const order=scope(source,{...f,metric:'expression'},{primer:true,metric:true}).filter(r=>r.unique_count>0).sort((a,b)=>b.unique_count-a.unique_count||natural(a.gene,b.gene));
    const aligned=f.alignment?.segments[f.segment],names=(aligned?.names||(order.length?order:rows).map(r=>r.gene)).slice((f.page-1)*25,f.page*25);
    const catalog=scope(table('reference_gene_observation'),f),denominator=scope(source,f,{primer:true,metric:true}).find(r=>r.denominator>0)?.denominator;
    return names.length?[{...bars(names,[{name:metricName(f.metric),color:data.colors.segment[f.segment],data:names.map(name=>{let r=rows.find(r=>r.gene===name);if(!r&&aligned&&denominator>0&&catalog.some(r=>r.gene===name&&Number(r.reference_catalog)===1))r={percent:0,unique_count:0,denominator};return point(r?.percent,{...r,unit:compositionUnit(f.metric),denominatorMeaning:geneMeaning(f.metric)});})}],f.segment+' gene usage · '+metricName(f.metric),{horizontal:true,percent:true}),axisMax:aligned?.maxima[f.metric],aligned:!!aligned}]:[];
  }
  function matching(f){
    const dual=table('dual_matching_summary'),source=dual.length&&['sample','stage','match_class','count','percent','denominator'].every(k=>Object.hasOwn(dual[0],k))?dual:table('matching_total_summary');
    const groups=new Map();for(const r of source.filter(r=>(!f.sample||r.sample===f.sample)&&(!Array.isArray(f.samples)||f.samples.includes(r.sample))&&(!r.stage||r.stage===f.stage))){const key=JSON.stringify([r.sample,r.unit]);if(!groups.has(key))groups.set(key,{sample:r.sample,unit:r.unit,values:{}});groups.get(key).values[r.match_class]=r;}
    const columns=[];
    const sampleOrder=new Map(data.samples.map((r,i)=>[r.sample,i]));
    for(const group of [...groups.values()].sort((a,b)=>(sampleOrder.get(a.sample)??999)-(sampleOrder.get(b.sample)??999)||natural(a.sample,b.sample)||natural(a.unit,b.unit))){
      const assignment=data.samples.find(r=>r.sample===group.sample)?.primer_groups;
      const original={label:assignment||'Assigned',sample:group.sample,group:assignment||'Assigned',values:group.values};
      const others=table('primer_group_comparisons').filter(r=>r.sample===group.sample&&r.status==='suspected'&&(r.stage||'mapping')===(f.stage||'mapping')&&(r.unit===group.unit||(r.unit==='queries'&&group.unit==='reads')));
      columns.push(original,...others.map(r=>({label:r.tested_group+'*',sample:group.sample,group:r.tested_group,suspected:true,values:Object.fromEntries(['unique','multiple','unmatched'].map(c=>[c,{percent:r[c+'_percent'],count:r[c+'_count'],denominator:r.denominator}]))})));
    }
    const batch=columns.slice((f.page-1)*12,f.page*12),categories=['unique','multiple','unmatched'];
    if(!batch.length)return [];
    const sampleGroups=[];batch.forEach((r,i)=>{const last=sampleGroups.at(-1);if(last?.sample===r.sample)last.end=i;else sampleGroups.push({sample:r.sample,start:i,end:i});});
    const spec=bars(batch.map(r=>r.label),categories.map((category,i)=>({name:['Unique','Multiple','Unmatched'][i],color:data.colors.matching[category],data:batch.map(r=>({...point(r.values[category]?.percent,r.values[category]),sample:r.sample,primerGroup:r.group,suspected:!!r.suspected,itemStyle:r.suspected?{borderColor:'#B84A48',borderWidth:1.2}:undefined}))})),'Primer matching composition · '+(f.stage==='raw_R1'?'Raw':'Mapping reads'),{percent:true,stack:true});
    return [{...spec,matching:true,sampleGroups,suspected:batch.map(r=>!!r.suspected)}];
  }
  function rates(f){
    const rows=scope(table(f.scenario_id?'scenario_primer_mapping_summary':'primer_mapping_summary'),f).filter(r=>r.stage===f.stage&&!r.primer.startsWith('[')).sort((a,b)=>(b.matched_reads||0)-(a.matched_reads||0)||natural(a.primer,b.primer)).slice((f.page-1)*20,f.page*20);
    const mapping=f.kind==='primer_mapping',name=mapping?'Mapping rate':'Unique primer matching';
    return rows.length?[bars(rows.map(r=>r.primer),[{name: name+' (%)',color:mapping?'#3979B8':'#419A8B',data:rows.map(r=>({value:r[mapping?'mapping_percent':'match_percent'],count:r[mapping?'mapped_reads':'matched_reads'],denominator:r[mapping?'matched_reads':'total_reads'],unit:'reads',denominatorMeaning:mapping?'Reads uniquely assigned to this primer at Mapping input':'All assessable reads at this stage'}))}],name+' · '+(f.stage==='raw_R1'?'Raw':'Mapping reads'),{horizontal:true,percent:true})]:[];
  }
  function isotypes(f){
    return ['abundance','unique_cdr3'].map(metric=>{
      const rows=scope(table('isotype_summary'),f).filter(r=>r.level==='main'&&r.metric===metric&&(metric==='abundance'||r.aa_field==='cdr3_aa'));
      const groups=unique(rows.map(r=>r.group_id)),labels=groups.map(gid=>{const r=rows.find(r=>r.group_id===gid);return r.sample+(groups.filter(g=>rows.find(r=>r.group_id===g).sample===r.sample).length>1?' / '+gid.slice(0,6):'');});
      const title=metric==='abundance'?'% Expression':'% Unique CDR3';
      const series=['IgM','IgD','IgA','IgG','IgE'].map(iso=>({name:iso,color:data.colors.isotype[iso],data:groups.map(gid=>{const r=rows.find(r=>r.group_id===gid&&r.isotype===iso);return point(r?.percent,{...r,denominatorMeaning:metric==='abundance'?'Copy or read count with classifiable C annotation':'Sum of distinct CDR3 counts within classifiable Isotypes'});})}));
      return rows.length?bars(labels,series,title,{percent:true,stack:true}):null;
    }).filter(Boolean);
  }
  function composition(f){
    const source=table(f.scenario_id?'scenario_primer_gene_usage':'primer_gene_usage'),rows=scope(source,{...f,segment:'C'},{metric:true}).filter(r=>r.primer&&r.unique_count>0),expression=scope(source,{...f,segment:'C',metric:'expression'},{metric:true}).filter(r=>r.primer&&r.unique_count>0);
    if(unique(rows.map(r=>r.group_id)).length>1)return [];
    const primers=unique(expression.map(r=>r.primer)).sort(natural).slice((f.page-1)*12,f.page*12),genes=unique(expression.map(r=>r.gene)).sort(natural);
    return primers.length?[{...bars(primers,genes.map(gene=>({name:gene,color:data.colors.constant[gene]||'#7C6C96',data:primers.map(primer=>{const r=rows.find(r=>r.primer===primer&&r.gene===gene),denominator=rows.find(r=>r.primer===primer)?.denominator;return point(r?r.percent:denominator>0?0:null,{...(r||{count:denominator>0?0:null,denominator}),unit:compositionUnit(f.metric),denominatorMeaning:geneMeaning(f.metric)});})})),metricName(f.metric),{percent:true,stack:true}),primerComposition:true}]:[];
  }
  function outputs(f){
    const rows=scope(table('sample_output_summary'),f),metrics=[['total_reads','Total Reads','#567FB5','reads'],['total_receptor_rna','Total Receptor RNA','#419A8B','copy'],['total_unique_cdr3s','Total Unique CDR3s','#CE8351','distinct_cdr3']].filter(([key])=>!f.outputMetric||f.outputMetric===key);
    const order=new Map(data.samples.map((r,i)=>[r.sample+' / '+r.chain,i]));rows.sort((a,b)=>(order.get(a.sample+' / '+a.chain)??999)-(order.get(b.sample+' / '+b.chain)??999)||natural(a.sample,b.sample)||natural(a.chain,b.chain));
    const batch=f.chain?rows:rows.slice((f.page-1)*12,f.page*12);
    return batch.length?[{...bars(batch.map(r=>r.sample+(f.chain?'':'\n'+r.chain)),metrics.map(([key,name,color,unit])=>({name,color,data:batch.map(r=>point(r[key],{unit}))})),(f.outputMetric?metrics[0][1]:'Sample output comparison')+' · '+(f.chain|| (f.samples?'Selected samples':'All samples'))),outputComparison:true}]:[];
  }
  function stages(f){
    const all=table('stage_volume_summary'),rows=scope(all,f),samples=unique(rows.map(r=>r.sample)).sort(natural).slice((f.page-1)*12,f.page*12);
    const get=(sample,stage,chain='')=>{const values=unique(rows.filter(r=>r.sample===sample&&r.stage===stage&&(r.chain||'')===chain).map(r=>r.count));return values.length===1?values[0]:null;};
    const cols=[['raw_r1','','Raw · R1','raw'],['raw_r2','','Raw · R2','raw'],['fastp_r1','','Clean (Fastp) · R1','fastp'],['fastp_r2','','Clean (Fastp) · R2','fastp'],['merged','','Merged','merged']];
    const chains=unique(rows.filter(r=>r.chain&&['mapping_input','mapping_output'].includes(r.stage)).map(r=>r.chain)).sort(natural);
    for(const chain of chains){const selected=unique(rows.filter(r=>r.chain===chain).map(r=>r.sample));if(!selected.every(s=>get(s,'mapping_input',chain)!=null&&get(s,'mapping_input',chain)===get(s,'merged')))cols.push(['mapping_input',chain,'Mapping input · '+chain,'mapping_input']);}
    cols.push(...chains.map(c=>['mapping_output',c,'Retained · '+c,'mapping_output']));
    const maxima=Object.fromEntries(unique(cols.map(c=>c[3])).map(family=>[family,Math.max(1,...all.filter(r=>cols.some(c=>c[0]===r.stage&&c[1]===(r.chain||'')&&c[3]===family)).map(r=>r.count||0))]));
    const points=[];
    samples.forEach((sample,y)=>cols.forEach(([stage,chain,label,family],x)=>{
      const count=get(sample,stage,chain);let change='';
      const before=stage.startsWith('fastp_')?get(sample,stage.replace('fastp_','raw_')):stage==='mapping_output'?get(sample,'mapping_input',chain):stage==='merged'?get(sample,'merge_input'):null;
      if(count!=null&&before>0)change=stage.startsWith('fastp_')?((count-before)*100/before).toFixed(1)+'%':'Retained '+(count*100/before).toFixed(1)+'%';
      points.push({value:[x,y,count==null?0:100*count/maxima[family]],count,change,itemStyle:{color:mix(data.colors.stage[family],count==null ? .96 : .94*(1-count/maxima[family]))}});
    }));
    return samples.length?[{type:'matrix',title:'Read counts across samples',labels:cols.map(c=>c[2]),rows:samples,points,max:100}]:[];
  }
  function mix(hex,white){return '#'+[1,3,5].map(i=>Math.round(parseInt(hex.slice(i,i+2),16)*(1-white)+255*white).toString(16).padStart(2,'0')).join('');}
  function textOn(hex){const luminance=color=>[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);const light=luminance(hex);return (light+.05)/(luminance('#233343')+.05)>=4.5?'#233343':1.05/(light+.05)>=4.5?'#FFFFFF':'#111111';}
  function metrics(f){
    if(f.kind==='isotype_distribution'){
      let fixedHeight;
      if(f.overviewFocus){const counts=new Map();for(const row of table('isotype_distribution')){if(!f.focusOptions.includes(row.sample)||f.chain&&row.chain!==f.chain)continue;if(!counts.has(row.sample))counts.set(row.sample,new Set());counts.get(row.sample).add(row.isotype);}fixedHeight=Math.max(310,...[...counts.values()].map(names=>names.size*40+130));}
      return ['expression','unique_cdr3'].map(metric=>{const rows=scope(table('isotype_distribution'),f).filter(r=>r.metric===metric);return {...bars(rows.map(r=>r.isotype),[{name:metricName(metric),color:'#DD8452',data:rows.map(r=>({...point(r.percent,{...r,unit:compositionUnit(metric),denominatorMeaning:metric==='expression'?'Copy count with classifiable C annotation':'Sum of distinct CDR3 counts within classifiable Isotypes'}),itemStyle:{color:['IgM','IgD'].includes(r.isotype)?'#4C72B0':'#DD8452'}}))}],metricName(metric),{horizontal:true,percent:true}),fixedHeight};}).filter(s=>s.labels.length);
    }
    if(f.kind.startsWith('diversity')){
      let rows=scope(table('diversity_summary'),f);
      if(f.kind==='diversity_comparison'){
        const ids=orderedGroups(rows).slice((f.page-1)*12,f.page*12);rows=rows.filter(r=>ids.includes(r.group_id)&&(/simpson_evenness/.test(f.filename)?r.metric==='Simpson evenness':r.metric!=='Simpson evenness'));
      }
      return rows.length?[{type:'dashboard',title:'Diversity indexes',panels:unique(rows.map(r=>r.metric)).map(metric=>{const batch=rows.filter(r=>r.metric===metric);return bars(batch.map(r=>r.sample+' / '+r.chain),[{name:metric,color:'#315D8B',data:batch.map(r=>point(r.value))}],metric,{horizontal:true});})}]:[];
    }
    const rows=scope(table('bcell_metrics'),f);if(!rows.length)return [];
    if(f.kind==='bcell_metrics_comparison'){
      const ids=orderedGroups(rows).slice((f.page-1)*10,f.page*10),isotypes=unique(rows.map(r=>r.isotype)),labels=ids.map(id=>rows.find(r=>r.group_id===id).sample);
      return [['mean_shm_percent','Mean SHM','#315D8B'],['shared_cdr3_percent','CDR3s observed as other isotype','#4F8F91']].map(([key,title,color])=>{
        const step=key==='mean_shm_percent'?2.5:10,max=Math.max(step,Math.min(100,step*Math.ceil(Math.max(1,...rows.map(r=>r[key]||0))/step))),points=[];
        isotypes.forEach((iso,y)=>ids.forEach((id,x)=>{const value=rows.find(r=>r.group_id===id&&r.isotype===iso)?.[key];points.push({value:[x,y,value??0],count:value??null,itemStyle:value==null?{color:'#E9EDF0'}:undefined});}));
        return {type:'matrix',title,labels,rows:isotypes,points,max,unit:'%',rangeColors:[mix(color,.94),color]};
      });
    }
    return [{type:'dashboard',title:'B cell Metrics',panels:[['mean_shm_percent','Mean SHM','#315D8B'],['shared_cdr3_percent','CDR3s observed as other isotype','#4F8F91']].map(([key,title,color])=>bars(rows.map(r=>r.isotype+(f.sample?'':' / '+r.sample)),[{name:title+' (%)',color,data:rows.map(r=>point(r[key]))}],title,{horizontal:true,percent:true}))}];
  }
  function orderedGroups(rows){const order=['IGH','IGK','IGL','TRA','TRB','TRD','TRG'];return unique(rows.map(r=>r.group_id)).sort((a,b)=>{const x=rows.find(r=>r.group_id===a),y=rows.find(r=>r.group_id===b);return order.indexOf(x.chain)-order.indexOf(y.chain)||natural(x.sample,y.sample)||natural(a,b);});}
  function specs(f){if(!f.interactive)return [];switch(f.kind){case 'sample_genes':case 'primer_genes':return gene(f);case 'matching':return matching(f);case 'primers':case 'primer_mapping':return rates(f);case 'isotype':return isotypes(f);case 'composition':return composition(f);case 'sample_outputs':return outputs(f);case 'stage_overview':return stages(f);default:return metrics(f);}}
  function tooltip(params){return (Array.isArray(params)?params:[params]).map(p=>{
    const r=p.data||{},value=Array.isArray(r.value)?r.count:r.value,unit=units[r.unit]||r.unit;return (r.sample?r.sample+' / '+r.primerGroup+(r.suspected?' · Suspected':''):p.name)+'\n'+(p.seriesName?p.seriesName+': ':'')+number(value)+(r.count!=null&&(r.denominator!=null||r.count!==value)?'\nCount: '+number(r.count)+(unit?' '+unit:''):'')+(r.denominator!=null?'\nDenominator: '+number(r.denominator)+(unit?' '+unit:'')+(r.denominatorMeaning?'\n'+r.denominatorMeaning:''):'')+(r.change?'\n'+r.change:'');
  }).join('\n\n');}
  const wrap=(s,width=22)=>String(s).split('\n').map(line=>line.match(new RegExp('.{1,'+width+'}','g'))?.join('\n')||'').join('\n');
  const dashboardColumns=spec=>spec.panels.length>2&&window.innerWidth>1200?2:1;
  const dashboardCell=spec=>Math.max(210,Math.min(600,Math.max(...spec.panels.map(p=>p.labels.length),1)*24+90));
  function barOption(spec,{small=false}={}){
    const max=Math.max(1,...spec.series.flatMap(s=>s.data.map(r=>r.value||0))),labels=spec.labels,wide=spec.horizontal;
    const category={type:'category',data:labels.map((label,i)=>({value:label,textStyle:spec.suspected?.[i]||String(label).includes('\nSuspected')?{color:'#B84A48'}:undefined})),inverse:wide,axisTick:{show:false},axisLine:{show:false},axisLabel:{color:'#506273',fontSize:spec.matching?10:11,interval:0,formatter:v=>wrap(v,spec.matching?9:wide?28:18)}};
    const value={type:'value',max:spec.axisMax??(spec.percent?(spec.stack?100:Math.min(100,Math.max(5,5*Math.ceil(max/5)))):undefined),axisLabel:{color:'#647586',formatter:v=>number(v)+(spec.percent?'%':'')},splitLine:{lineStyle:{color:'#E8EDF2'}}};
    const sampleAxis=spec.matching?{...category,position:'bottom',offset:32,data:labels.map((_,i)=>{const group=spec.sampleGroups.find(g=>Math.floor((g.start+g.end)/2)===i);return group?group.sample:'';}),axisLabel:{color:'#506273',fontSize:10,interval:0,margin:8,formatter:(v,i)=>{const g=spec.sampleGroups.find(g=>Math.floor((g.start+g.end)/2)===i);return wrap(v,g?Math.max(7,Math.min(22,(g.end-g.start+1)*7)):7);}}}:null;
    return {title:{text:spec.title,left:'center',top:8,textStyle:{fontSize:small?12:15,color:'#233343',fontWeight:600}},
      legend:{show:false,type:'scroll',top:42,left:'center',textStyle:{fontSize:11,color:'#455564'}},
      grid:{left:wide?'3%':'4%',right:wide?'13%':'4%',top:58,bottom:spec.matching?100:wide?24:Math.min(180,Math.max(...labels.map(s=>wrap(s,18).split('\n').length),1)*15+25),containLabel:true},
      xAxis:sampleAxis?[category,sampleAxis]:wide?value:category,yAxis:wide?category:value,
      dataZoom:!wide&&labels.length>12?[{type:'slider',xAxisIndex:0,startValue:0,endValue:11,bottom:4,height:15},{type:'inside',xAxisIndex:0,startValue:0,endValue:11,zoomOnMouseWheel:false}]:[],
      series:spec.series.map(series=>({name:series.name,type:'bar',stack:spec.stack?'composition':undefined,barMaxWidth:wide?20:48,itemStyle:{color:series.color},emphasis:{focus:'series'},label:{show:true,position:spec.stack?'inside':wide?'right':'top',color:spec.stack?textOn(series.color):'#233343',fontSize:10,formatter:p=>p.value==null?'N/A':spec.stack?(p.value>=7?Number(p.value).toFixed(1)+'%':''):spec.percent?Number(p.value).toFixed(2)+'%':number(p.value)},data:series.data}))};
  }
  function option(spec){
    const shared={animation:false,backgroundColor:'#FFFFFF',aria:{enabled:true},tooltip:{trigger:'item',renderMode:'richText',confine:true,formatter:tooltip,textStyle:{fontSize:12}},textStyle:{fontFamily:'Segoe UI, Microsoft YaHei, sans-serif'}};
    if(spec.type==='bars')return {...shared,...barOption(spec)};
    if(spec.type==='matrix')return {...shared,title:{text:spec.title,left:'center',top:8,textStyle:{fontSize:15}},grid:{left:'2%',right:'3%',top:90,bottom:65,containLabel:true},xAxis:{type:'category',position:'top',data:spec.labels,axisLabel:{interval:0,formatter:v=>v.replace(' · ','\n'),fontSize:11},axisLine:{show:false},axisTick:{show:false}},yAxis:{type:'category',inverse:true,data:spec.rows,axisLabel:{fontSize:11},axisTick:{show:false},axisLine:{show:false}},visualMap:{type:'continuous',min:0,max:spec.max,dimension:2,calculable:true,orient:'horizontal',left:'center',bottom:0,seriesIndex:0,inRange:{opacity:[.55,1],...(spec.rangeColors?{color:spec.rangeColors}:{})},outOfRange:{opacity:.12},text:spec.unit?['High','Low']:['Relative high','Relative low']},series:[{type:'heatmap',data:spec.points,label:{show:true,formatter:p=>p.data.count==null?'N/A':number(p.data.count)+(spec.unit||'')+(p.data.change?'\n'+p.data.change:''),fontSize:11,color:'#233343'},itemStyle:{borderColor:'#FFF',borderWidth:3}}]};
    const n=spec.panels.length,cols=dashboardColumns(spec),cell=dashboardCell(spec),titles=[{text:spec.title,left:'center',top:6,textStyle:{fontSize:15}}],grids=[],xs=[],ys=[],series=[];
    spec.panels.forEach((p,i)=>{const col=i%cols,row=Math.floor(i/cols),left=col*100/cols,top=44+row*cell;
      titles.push({text:p.title,left:(left+50/cols)+'%',top,textAlign:'center',textStyle:{fontSize:11,color:'#233343'}});grids.push({left:(left+2)+'%',right:(100-left-100/cols+5)+'%',top:top+34,height:cell-70,containLabel:true});
      xs.push({type:'value',gridIndex:i,axisLabel:{fontSize:10},splitLine:{lineStyle:{color:'#E8EDF2'}}});ys.push({type:'category',gridIndex:i,inverse:true,data:p.labels,axisLabel:{fontSize:10,formatter:v=>wrap(v,20)},axisTick:{show:false},axisLine:{show:false}});
      series.push(...p.series.map(s=>({name:s.name,type:'bar',xAxisIndex:i,yAxisIndex:i,barMaxWidth:20,itemStyle:{color:s.color},data:s.data,label:{show:true,position:'right',fontSize:10,formatter:q=>number(q.value)}})));
    });return {...shared,title:titles,grid:grids,xAxis:xs,yAxis:ys,series};
  }
  function saveView(item){const opts=item.chart.getOption();views.set(item.viewKey,{dataZoom:opts.dataZoom,range:opts.visualMap?.[0]?.range});}
  function restoreView(opts,key){const saved=views.get(key);if(saved?.dataZoom?.length)opts.dataZoom=saved.dataZoom;if(opts.visualMap&&saved?.range)opts.visualMap.range=saved.range;return opts;}
  function dispose(keep=new Set()){for(const [node,item] of live){if(keep.has(node))continue;saveView(item);item.observer.disconnect();item.host.removeEventListener('scroll',item.scroll);item.chart.dispose();live.delete(node);}}
  function themed(spec){
    const opts=option(spec);if(!document.documentElement.classList.contains('dark'))return opts;
    const colors={'#FFFFFF':'#192733','#233343':'#d8e4ec','#506273':'#afbfcb','#455564':'#b3c4d0','#647586':'#9aafbd','#E8EDF2':'#334452','#FFF':'#334452'};
    const visit=value=>{if(Array.isArray(value))return value.map(visit);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,visit(v)]));return typeof value==='string'&&colors[value]?colors[value]:value;};const themed=visit(opts);if(spec.type==='bars'&&spec.stack)themed.series.forEach((s,i)=>s.label.color=textOn(spec.series[i].color));return themed;
  }
  function mount(host,spec,f){
    const key=JSON.stringify([f.moduleKey||f.view||'',f.kind,f.group_id,f.sample,f.chain,f.primer,f.segment,f.stage,f.samples===null?'all':f.samples,!!f.alignment]),viewKey=JSON.stringify([f.moduleKey||f.view||'',key,f.metric||spec.title,f.page||1,f.outputMetric||'']);
    const node=document.createElement('div');node.className='interactive-chart';node.setAttribute('role','img');node.setAttribute('aria-label',spec.title);
    const height=spec.fixedHeight??(spec.type==='dashboard'?Math.ceil(spec.panels.length/dashboardColumns(spec))*dashboardCell(spec)+60:spec.type==='matrix'?Math.max(350,spec.rows.length*65+200):spec.horizontal?Math.max(310,spec.labels.length*40+130):440);
    node.style.height=height+'px';
    if(spec.matching){host.classList.add('matching-chart');node.style.minWidth=Math.max(280,spec.labels.length*58+68)+'px';}
    if(spec.outputComparison){host.classList.add('chart-scroll');node.style.minWidth=Math.max(280,spec.labels.length*100+100)+'px';}
    if(spec.primerComposition){host.classList.add('chart-scroll');node.style.minWidth=Math.max(280,spec.labels.length*82+100)+'px';}
    const legend=document.createElement('div');legend.className='chart-legend';legend.setAttribute('aria-label','选择显示的类别');
    if(spec.type==='bars'&&spec.series.length>1){for(const series of spec.series){const button=document.createElement('button'),color=document.createElement('i'),label=document.createTextNode(series.name);color.style.background=series.color;button.append(color,label);button.dataset.series=series.name;button.type='button';button.setAttribute('aria-pressed',String((selected.get(key)||{})[series.name]!==false));button.onclick=()=>chart.dispatchAction({type:'legendToggleSelect',name:series.name});legend.append(button);}host.append(legend);}
    if(spec.matching&&spec.suspected.some(Boolean)){const note=document.createElement('span');note.className='suspected-key';note.textContent='* Suspected group';legend.append(note);}
    const hint=document.createElement('div');hint.className='overflow-hint';hint.hidden=true;host.append(hint,node);
    const scroll=()=>{const overflow=host.scrollWidth>host.clientWidth+2;hint.hidden=!overflow;hint.textContent=host.scrollLeft<2?'向右滑动查看 →':host.scrollLeft+host.clientWidth>=host.scrollWidth-2?'← 向左滑动查看':'← 左右滑动查看 →';};host.addEventListener('scroll',scroll,{passive:true});
    const chart=window.ReportECharts.init(node,null,{renderer:'canvas',devicePixelRatio:1}),opts=restoreView(themed(spec),viewKey);
    if(opts.legend)opts.legend.selected=selected.get(key)||{};chart.setOption(opts);
    chart.on('legendselectchanged',event=>{selected.set(key,event.selected);for(const item of live.values())if(item.key===key){if(item.chart!==chart)for(const [name,show] of Object.entries(event.selected))item.chart.dispatchAction({type:show?'legendSelect':'legendUnSelect',name});for(const button of item.legend.querySelectorAll('button'))button.setAttribute('aria-pressed',String(event.selected[button.dataset.series]!==false));}});
    const observer=new ResizeObserver(()=>{if(spec.type==='dashboard'){node.style.height=Math.ceil(spec.panels.length/dashboardColumns(spec))*dashboardCell(spec)+60+'px';chart.setOption(themed(spec),{notMerge:true});}chart.resize();scroll();});observer.observe(host);live.set(node,{chart,observer,key,viewKey,spec,legend,figure:f,host,scroll});scroll();
    return chart;
  }
  function shortHash(value){let hash=2166136261;for(const char of value){hash^=char.codePointAt(0);hash=Math.imul(hash,16777619);}return (hash>>>0).toString(16).padStart(8,'0');}
  function exportName(f,extension){
    const samples=f.sample?[f.sample]:f.samples,scope=Array.isArray(samples)?samples.length===1?samples[0]:'selected-'+samples.length+'-'+shortHash(JSON.stringify([...samples].sort())):'all-samples';
    const identity=[f.kind,scope,f.chain,f.primer,f.segment,f.metric,f.outputMetric,f.stage,f.primer_group?'Suspected-'+f.primer_group:'',f.group_id?.slice(0,8),f.alignment?'aligned-'+shortHash(JSON.stringify([...(f.samples||[])].sort())):'','p'+(f.page||1)];
    const sanitize=s=>String(s).normalize('NFKC').replace(/[^\p{L}\p{N}_.-]+/gu,'-').replace(/^-+|-+$/g,'');
    const full=identity.filter(Boolean).map(sanitize).join('__'),stem=full.length>170?full.slice(0,150)+'__'+shortHash(full):full;
    return stem+'.'+extension;
  }
  function contextLines(f){
    const samples=f.sample?[f.sample]:f.samples;
    const group=data.groups.find(g=>g.group_id===f.group_id),duplicate=group&&data.groups.filter(g=>g.sample===group.sample&&g.chain===group.chain).length>1;
    return [data.task_name||'SCIGBLAST',Array.isArray(samples)?'Sample'+(samples.length===1?'':'s')+': '+(samples.length?samples.join(', '):'None'):'All applicable samples',
      [f.chain&&'Chain: '+f.chain,f.primer&&'Primer: '+f.primer,f.primer_group&&'Suspected group: '+f.primer_group,f.stage&&'Stage: '+(f.stage==='raw_R1'?'Raw':f.stage),duplicate&&'Result: '+f.group_id.slice(0,8), 'Page: '+(f.page||1)].filter(Boolean).join(' | '),f.alignment&&'Aligned with: '+(f.samples||[]).filter(s=>s!==f.sample).join(', ')].filter(Boolean);
  }
  function download(blob,filename){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function exportPNG(charts,figure){
    if(!charts.length)return false;
    const canvases=charts.map(chart=>{const current=[...live.values()].find(item=>item.chart===chart),original=chart.getOption(),legend=original.legend?.[0]?.selected;
      const preserve=opts=>{if(original.dataZoom?.length)opts.dataZoom=original.dataZoom;if(opts.visualMap&&original.visualMap?.[0]?.range)opts.visualMap.range=original.visualMap[0].range;return opts;};
      try{if(current){const light=preserve(option(current.spec));if(light.legend){light.legend.selected=legend;light.legend.show=current.spec.series?.length>1;if(light.legend.show)light.grid.top=90;}if(current.spec.matching&&current.spec.suspected.some(Boolean)){light.title.subtext='* Suspected group';light.title.subtextStyle={color:'#B84A48',fontSize:10};light.title.itemGap=4;light.legend.top=54;light.grid.top=100;}chart.setOption(light,{notMerge:true});}
        const image=new Image();image.src=chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#FFFFFF'});return image;
      }finally{if(current){const restore=preserve(themed(current.spec));if(restore.legend)restore.legend.selected=legend;chart.setOption(restore,{notMerge:true});}}});
    await Promise.all(canvases.map(img=>img.decode()));
    const canvas=document.createElement('canvas'),paired=canvases.length===2;canvas.width=Math.max(640,paired?canvases.reduce((n,i)=>n+i.width,0):Math.max(...canvases.map(i=>i.width)));
    const context=canvas.getContext('2d'),font=24,lineHeight=34,padding=32,lines=[];context.font=font+'px Segoe UI, sans-serif';
    for(const text of contextLines(figure)){let line='';for(const char of text){if(context.measureText(line+char).width>canvas.width-padding*2&&line){lines.push(line);line='';}line+=char;}if(line)lines.push(line);}
    const header=padding*2+lines.length*lineHeight;canvas.height=header+(paired?Math.max(...canvases.map(i=>i.height)):canvases.reduce((n,i)=>n+i.height,0));
    context.fillStyle='#FFF';context.fillRect(0,0,canvas.width,canvas.height);context.font=font+'px Segoe UI, sans-serif';context.fillStyle=figure.scenario_id?'#983F3D':'#455564';
    lines.forEach((line,i)=>context.fillText(line,padding,padding+font+i*lineHeight));let offset=0;
    for(const image of canvases){context.drawImage(image,paired?offset:(canvas.width-image.width)/2,header+(paired?0:offset));offset+=paired?image.width:image.height;}
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('PNG encoding failed');download(blob,exportName(figure,'png'));return true;
  }
  function exportCSV(charts,figure){
    const records=[],selectedSamples=figure.sample||figure.samples?.join('; ')||'All applicable samples';
    for(const chart of charts){const item=[...live.values()].find(item=>item.chart===chart);if(!item)continue;const spec=item.spec,options=chart.getOption(),legend=options.legend?.[0]?.selected||{};
      const zoom=options.dataZoom?.[0],indexes=labels=>labels.map((_,i)=>i).filter(i=>!zoom||(i>=(zoom.startValue??Math.floor((zoom.start||0)*(labels.length-1)/100))&&i<=(zoom.endValue??Math.ceil((zoom.end??100)*(labels.length-1)/100))));
      const add=(panel,label,series,p,extra={})=>records.push({task:data.task_name||'',sample:p.sample||((spec.outputComparison||figure.kind==='isotype')?String(label).split('\n')[0]:selectedSamples),chain:figure.chain||'',primer:figure.primer||'',primer_group:p.primerGroup||figure.primer_group||'',analysis:figure.scenario_id||p.suspected?'Suspected':'Original',segment:figure.segment||'',stage:figure.stage||'',chart:panel.title,label,series,value:p.value,unit:panel.percent?'percent':units[p.unit]||p.unit||'',count:p.count,denominator:p.denominator,count_unit:units[p.unit]||p.unit||'',denominator_definition:p.denominatorMeaning||'',...extra});
      for(const panel of spec.type==='dashboard'?spec.panels:spec.type==='bars'?[spec]:[]){for(const series of panel.series){if(legend[series.name]===false)continue;for(const i of indexes(panel.labels))add(panel,panel.labels[i],series.name,series.data[i]);}}
      if(spec.type==='matrix')for(const p of spec.points){const [x,y,value]=p.value,range=options.visualMap?.[0]?.range;if(range&&(value<range[0]||value>range[1]))continue;add(spec,spec.labels[x],spec.rows[y],{...p,value:p.count,unit:spec.unit||'reads'},{sample:figure.kind==='stage_overview'?spec.rows[y]:spec.labels[x],change:p.change||'',value:p.count});}
    }
    if(!records.length)return false;
    const columns=unique(records.flatMap(Object.keys)),cell=value=>'"'+(typeof value==='string'?value.replace(/^[\s\uFEFF]*[=+@-]/u,match=>"'"+match):String(value??'')).replace(/"/g,'""')+'"';
    const csv='\uFEFF'+[columns.map(cell).join(','),...records.map(row=>columns.map(key=>cell(row[key])).join(','))].join('\r\n');download(new Blob([csv],{type:'text/csv;charset=utf-8'}),exportName(figure,'csv'));return true;
  }
  function resetSelections(module){
    const matches=key=>module===undefined||JSON.parse(key)[0]===module;
    for(const key of selected.keys())if(matches(key))selected.delete(key);for(const key of views.keys())if(matches(key))views.delete(key);
    for(const item of live.values())if(matches(item.key)){item.chart.setOption(themed(item.spec),{notMerge:true});for(const button of item.legend.querySelectorAll('button'))button.setAttribute('aria-pressed','true');}
  }
  return {table,specs,mount,dispose,exportPNG,exportCSV,alignGenes,exportName,activeCount:()=>live.size,resetSelections};
})();

/* Post-pipeline statistics. All charts and PNGs use the published aggregate. */
window.RepertoireReports = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(url, body, method) {
    const response = await fetch(url, {method:method || (body ? 'POST':'GET'),headers:{'X-SCIGBLAST-Request':'1',...(body ? {'Content-Type':'application/json'}:{})},...(body ? {body:JSON.stringify(body)}:{})});
    if (response.status === 401) {location.href='/login';throw new Error('请重新登录');}
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未完成');
    return data;
  }
  const configs = new WeakMap(), instances=[];
  let configIndex=0;
  const timeLabel=value=>{const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});};
  function configPanel(root) {
    if (!root) return {value:()=>({}),reset:()=>{}};
    if (configs.has(root)) return configs.get(root);
    const uploads={},books={},revisions={submission:0,primer:0},previewId='rp-column-preview-'+(++configIndex);let pending=0,generation=0,previewKey='submission';
    const $=q=>root.querySelector(q), error=$('[data-rp-config-error]');
    const field=name=>$(`[data-rp-field="${name}"]`);
    const columnNumber=text=>{text=text.trim().toUpperCase();if(!/^[A-Z]{1,3}$/.test(text))throw Error('列须用 Excel 字母，例如 B、I、AA');return [...text].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0)-1;};
    const columnName=index=>{let name='';for(let n=index+1;n>0;n=Math.floor((n-1)/26))name=String.fromCharCode(65+(n-1)%26)+name;return name;};
    const headerRow=key=>{const row=Number(field(key+'_header_row').value);if(!Number.isInteger(row)||row<1||row>500000)throw Error('表头行须为 1–500000 的整数');return row;};
    function fail(e){error.textContent=e.message;error.classList.remove('hidden');}
    function uploadState(key,state){
      const card=$(`[data-rp-file-card="${key}"]`);card.dataset.state=state;
      $(`[data-rp-upload-state="${key}"]`).textContent={empty:'未选择',loading:'读取中',ready:'已载入',error:'未完成'}[state];
      $(`[data-rp-upload-action="${key}"]`).textContent=state==='ready'?'更换文件':'选择文件';
    }
    function resetSheet(key){const select=field(key+'_sheet');select.disabled=false;select.innerHTML='<option value="">上传后选择</option>';select.closest('label').hidden=true;const note=$(`[data-rp-upload-note="${key}"]`);note.textContent=key==='submission'?'样本与引物组对应表':'引物名称与序列表';note.removeAttribute('title');uploadState(key,'empty');}
    function syncPreviewTabs(){
      if(!books[previewKey])previewKey=Object.keys(books)[0]||'submission';
      root.querySelectorAll('[data-rp-preview-tab]').forEach(button=>{const key=button.dataset.rpPreviewTab,active=key===previewKey;button.hidden=!books[key];button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
      root.querySelectorAll('[data-rp-preview-section]').forEach(section=>section.hidden=section.dataset.rpPreviewSection!==previewKey);
    }
    for(const button of root.querySelectorAll('[data-rp-preview-tab]')){
      const key=button.dataset.rpPreviewTab;button.id=previewId+'-'+key+'-tab';button.setAttribute('aria-controls',previewId+'-'+key);
      button.onclick=()=>{previewKey=key;syncPreviewTabs();};
      button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...root.querySelectorAll('[data-rp-preview-tab]')].filter(b=>!b.hidden);const index=buttons.indexOf(button);const next=event.key==='Home'?buttons[0]:event.key==='End'?buttons.at(-1):buttons[(index+(event.key==='ArrowRight'?1:buttons.length-1))%buttons.length];next?.click();next?.focus();};
    }
    function previewTable(book,header,columns,rows){
      if(!rows.length)return '<p class="rp-preview-message">当前表头后暂无可预览的数据。</p>';
      return `<div class="rp-table-wrap"><table class="rp-preview-table"><thead><tr><th scope="col" class="rp-excel-row">行</th>${columns.map(col=>`<th scope="col"><strong><span class="rp-col-letter">${esc(col.letter)}</span>${esc(col.role)}</strong><small>${esc(cell(book,header,col.index).value||'空表头')}</small></th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr><th scope="row" class="rp-excel-row">${row+1}</th>${columns.map(col=>{const data=cell(book,row,col.index,col.inherit);return `<td${col.role==='序列'?' class="rp-preview-sequence"':''}>${data.value?esc(data.value)+(data.merged?'<span class="rp-cell-merged">（合并）</span>':''):'<span class="rp-cell-empty">—</span>'}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    function cell(book,row,col,inherit=false){
      if(inherit)for(const merged of book.merged||[])if(row>=merged.rows[0]&&row<merged.rows[1]&&col>=merged.columns[0]&&col<merged.columns[1])return {value:String(merged.value??''),merged:row!==merged.rows[0]||col!==merged.columns[0]};
      return {value:String(book.preview[row-(book.preview_start||0)]?.[col]??''),merged:false};
    }
    function selected(key){
      const columns=key==='submission'?[['sample_column','样本',false],['submission_join_column','引物组',true]]:[['primer_join_column','引物组',true]];
      const result=columns.map(([name,role,inherit])=>({name,role,inherit,letter:field(name).value.trim().toUpperCase(),index:columnNumber(field(name).value)}));
      if(key==='primer'){
        const pairs=field('primer_columns').value.split(',').map(p=>p.trim()).filter(Boolean);
        if(!pairs.length||pairs.length>12)throw Error('请选择 1–12 对名称:序列列，例如 C:D,E:F');
        for(const pair of pairs){const parts=pair.split(':');if(parts.length!==2)throw Error('名称:序列列对格式为 C:D,E:F');parts.forEach((text,i)=>result.push({name:'primer_columns',role:i?'序列':'名称',inherit:false,letter:text.trim().toUpperCase(),index:columnNumber(text)}));}
      }
      return result;
    }
    function showPreview(){
      const sections=[];
      for(const key of ['submission','primer']){
        const book=books[key],names=key==='submission'?['sample_column','submission_join_column']:['primer_join_column','primer_columns'];
        names.forEach(name=>{const note=$(`[data-rp-column-note="${name}"]`);note.textContent=book?'':'上传后显示列内容';note.classList.remove('rp-column-warning');});
        if(!book)continue;
        const title=(key==='submission'?'Submission':'引物表')+' · '+book.sheet;
        try{
          const header=headerRow(key)-1;
          if(book.sheet!==field(key+'_sheet').value||header!==(book.preview_start||0))throw Error('工作表或表头已更改，正在更新预览');
          const columns=selected(key),sampleRows=Array.from({length:Math.max(0,Math.min(5,book.row_count-header-1))},(_,i)=>header+i+1);
          for(const name of names){
            const note=$(`[data-rp-column-note="${name}"]`),chosen=columns.filter(c=>c.name===name);
            if(name==='primer_columns')note.textContent=chosen.map(c=>c.letter+'：'+(cell(book,header,c.index).value||'空列')).join('；');
            else{
              const col=chosen[0],values=[...new Set(sampleRows.map(r=>cell(book,r,col.index,col.inherit).value).filter(Boolean))].slice(0,3);
              note.textContent=(cell(book,header,col.index).value||col.letter+' 列')+'：'+(values.join('、')||'预览行内无内容，请核对');
              if(!values.length)note.classList.add('rp-column-warning');
            }
          }
          sections.push(`<section id="${previewId}-${key}" role="tabpanel" aria-labelledby="${previewId}-${key}-tab" data-rp-preview-section="${key}"><div class="rp-preview-heading"><strong>${key==='submission'?'Submission':'引物表'}</strong><span class="rp-sheet-name">${esc(book.sheet)}</span><span class="rp-row-count">${sampleRows.length} 条预览</span></div>${previewTable(book,header,columns,sampleRows)}</section>`);
        }catch(e){names.forEach(name=>{const note=$(`[data-rp-column-note="${name}"]`);note.textContent=e.message;note.classList.add('rp-column-warning');});sections.push(`<section id="${previewId}-${key}" role="tabpanel" aria-labelledby="${previewId}-${key}-tab" data-rp-preview-section="${key}"><p class="rp-preview-message">${esc(title)}：${esc(e.message)}</p></section>`);}
      }
      $('[data-rp-selected-columns]').hidden=!sections.length;
      $('[data-rp-selected-preview]').innerHTML=sections.join('');syncPreviewTabs();
      $('[data-rp-workbook-preview]').innerHTML=Object.entries(books).map(([key,book])=>`<h4>${key==='submission'?'Submission':'引物表'} · ${esc(book.sheet)}</h4>${table(book.preview.slice(0,10).map((row,index)=>Object.fromEntries([['行',(book.preview_start||0)+index+1],...row.map((value,col)=>[columnName(col),value])])) )}`).join('');
    }
    async function refreshBook(key){
      if(!uploads[key]){showPreview();return;}
      let header;try{header=headerRow(key);}catch(e){showPreview();fail(e);return;}
      const token=uploads[key],revision=generation,epoch=++revisions[key],sheet=field(key+'_sheet').value;
      pending++;showPreview();
      try{
        const book=await api(`/api/reports/workbook/${token}?sheet=${encodeURIComponent(sheet)}&header_row=${header}`);
        if(revision!==generation||epoch!==revisions[key]||uploads[key]!==token)return;
        books[key]=book;showPreview();error.classList.add('hidden');
      }catch(e){if(revision===generation&&epoch===revisions[key]){delete books[key];showPreview();fail(e);}}
      finally{pending--;}
    }
    root.querySelectorAll('[data-rp-upload]').forEach(input=>input.addEventListener('change',async()=>{
      const file=input.files[0],key=input.dataset.rpUpload,revision=generation,epoch=++revisions[key];
      delete uploads[key];delete books[key];showPreview();if(!file){resetSheet(key);return;}
      let header;try{header=headerRow(key);}catch(e){fail(e);return;}
      pending++;error.classList.add('hidden');uploadState(key,'loading');field(key+'_sheet').disabled=true;$(`[data-rp-upload-note="${key}"]`).textContent='上传中…';
      try{
        const response=await fetch('/api/reports/upload?filename='+encodeURIComponent(file.name)+'&header_row='+header,{method:'POST',headers:{'X-SCIGBLAST-Request':'1'},body:file});
        const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'上传失败');
        if(revision!==generation||epoch!==revisions[key])return;
        uploads[key]=data.id;books[key]=data;
        const sheetSelect=field(key+'_sheet');sheetSelect.innerHTML=data.sheets.map(name=>`<option>${esc(name)}</option>`).join('');sheetSelect.value=data.sheet;sheetSelect.closest('label').hidden=data.sheets.length<=1;
        $(`[data-rp-upload-note="${key}"]`).textContent=file.name;$(`[data-rp-upload-note="${key}"]`).title=file.name;uploadState(key,'ready');previewKey=key;showPreview();
        if(headerRow(key)-1!==data.preview_start)await refreshBook(key);
      }catch(e){if(revision===generation&&epoch===revisions[key]){uploadState(key,'error');fail(e);$(`[data-rp-upload-note="${key}"]`).textContent='上传失败，请重新选择';}}
      finally{pending--;if(revision===generation)field(key+'_sheet').disabled=false;}
    }));
    for(const key of ['submission','primer']){
      field(key+'_sheet').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
      field(key+'_header_row').addEventListener('input',showPreview);
      field(key+'_header_row').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
    }
    for(const name of ['sample_column','submission_join_column','primer_join_column','primer_columns'])field(name).addEventListener('input',()=>{previewKey=name.startsWith('primer')?'primer':'submission';showPreview();$('[data-rp-selected-columns]').open=true;});
    const result={value(){
      if(pending)throw Error('请等待工作簿预览加载完成');
      if(!uploads.submission&&!uploads.primer)return {};
      if(!uploads.submission||!uploads.primer)throw Error('请同时上传 Submission 和引物表');
      const columns={};root.querySelectorAll('[data-rp-field]').forEach(input=>columns[input.dataset.rpField]=input.type==='checkbox'?input.checked:input.value.trim());
      columns.primer_columns=columns.primer_columns.split(',').filter(Boolean).map(pair=>{const parts=pair.split(':').map(s=>s.trim());if(parts.length!==2)throw Error('名称:序列列对格式为 C:D,E:F');return parts;});
      ['submission_header_row','primer_header_row','umi_length','barcode_length'].forEach(k=>columns[k]=Number(columns[k]));
      return {submission_id:uploads.submission,primer_id:uploads.primer,columns};
    },reset(){generation++;Object.keys(uploads).forEach(k=>delete uploads[k]);Object.keys(books).forEach(k=>delete books[k]);root.querySelectorAll('input[type=file]').forEach(i=>i.value='');for(const key of ['submission','primer'])resetSheet(key);previewKey='submission';showPreview();$('[data-rp-selected-columns]').open=false;error.classList.add('hidden');}};
    configs.set(root,result);return result;
  }
  function table(rows,columns){
    if(!rows.length)return '<p class="muted">暂无数据</p>';
    columns=columns||Object.keys(rows[0]);
    const cell=v=>v===null||v===undefined?'—':typeof v==='object'?JSON.stringify(v):typeof v==='number'?Number.isInteger(v)?v.toLocaleString():v.toFixed(3):String(v);
    const labels={sample:'样本',sample_key:'样本标识',pair:'配对',chain:'链',scope:'数据范围',subset:'统计范围',primer:'引物',segment:'基因区',gene:'基因',count:'数量',denominator:'分母',percent:'比例 (%)',stage:'阶段',metric:'指标',unit:'单位',note:'说明',match_class:'匹配类别',reason:'注释/过滤状态',unique_count:'唯一基因注释',ambiguous_count:'歧义候选记录',reference_catalog:'当次参考库收录',status:'状态',sequence_id:'序列 ID',primers:'本次统计的引物集合',mapping_primers:'mapping 前记录的引物归属',trim_state:'mapping 前引物处理',trim_length:'去除碱基数',filter_reason:'注释/过滤状态'};
    Object.assign(labels,{state_denominator:'该引物同处理状态的分母',state_percent:'同处理状态内比例 (%)',label:'阶段',pair_count:'配对数',pair_scope:'配对范围',previous_count:'上一步数量',previous_unit:'上一步单位',reduction:'较上一步减少',reduction_unit:'减少量单位',retention_percent:'保留 / 合并率 (%)',rate_basis:'比例口径'});
    return `<div class="rp-table-wrap"><table class="rp-data-table"><thead><tr>${columns.map(c=>`<th scope="col">${esc(labels[c]||c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,300).map(row=>`<tr>${columns.map(c=>`<td${typeof row[c]==='number'?' class="rp-number"':''}>${esc(cell(row[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${rows.length>300?'<p class="muted">页面显示前 300 行；完整统计请下载表格。</p>':''}`;
  }
  function bars(title,rows,note,scaleMax){
    const max=scaleMax||Math.max(...rows.map(r=>r.value),1);
    return `<section class="rp-chart"><div class="rp-chart-heading"><h4>${esc(title)}</h4><span>${rows.length} 类</span></div><div class="rp-chart-bars">${rows.slice(0,20).map(r=>`<div class="rp-bar-row"><span class="rp-bar-label">${esc(r.label)}</span><span class="rp-bar-track"><i style="width:${Math.max(0,Math.min(100,r.value*100/max))}%"></i></span><span class="rp-bar-value">${esc(r.display??r.value.toLocaleString())}</span></div>`).join('')||'<p class="rp-inline-empty">暂无可用统计</p>'}</div><small>${esc(note||'')}</small></section>`;
  }
  function sampleMatching(rows){
    const groups=new Map();
    for(const row of rows){
      const key=JSON.stringify([row.sample,row.unit]);
      if(!groups.has(key))groups.set(key,{sample:row.sample,unit:row.unit,unique:0,multiple:0,unmatched:0,unassessed:0});
      const group=groups.get(key);if(['unique','multiple','unmatched'].includes(row.match_class))group[row.match_class]+=row.count;else group.unassessed+=row.count;
    }
    return [...groups.values()].sort((a,b)=>a.sample.localeCompare(b.sample)||a.unit.localeCompare(b.unit));
  }
  function matchingComposition(groups){
    const names={unique:'唯一匹配',multiple:'多重匹配',unmatched:'未匹配'};
    const bars=groups.map(group=>{
      const total=group.unique+group.multiple+group.unmatched;
      const label=group.sample+(groups.filter(other=>other.sample===group.sample).length>1?' / '+group.unit:'');
      const describe=Object.entries(names).map(([key,label])=>`${label} ${group[key].toLocaleString()} (${total?(group[key]*100/total).toFixed(2):'—'}%)`).join('，');
      const unique=total?group.unique*100/total:0;
      return `<div class="rp-match-column"><div class="rp-match-stack${total?'':' rp-match-empty'}" role="img" aria-label="${esc(label+'：'+describe)}" title="${esc(describe)}">${['unmatched','multiple','unique'].map(key=>`<span class="rp-match-${key}" style="height:${total?group[key]*100/total:0}%">${key==='unique'&&unique>=12?unique.toFixed(1)+'%':''}</span>`).join('')}${unique<12?`<span class="rp-match-small-value">${total?unique.toFixed(unique>0&&unique<.1?4:1)+'%':'—'}</span>`:''}</div><span class="rp-match-sample">${esc(label)}</span></div>`;
    }).join('');
    return `<section class="rp-chart rp-matching-overview"><div class="rp-chart-heading"><h4>引物匹配构成 · 全部样本</h4><span>${groups.length} 项</span></div><div class="rp-match-legend">${Object.entries(names).map(([key,label])=>`<span><i class="rp-match-${key}"></i>${label}</span>`).join('')}</div><div class="rp-match-scroll"><div class="rp-match-axis"><span>100%</span><span>50%</span><span>0%</span></div><div class="rp-match-columns">${bars||'<p class="muted">暂无可用统计</p>'}</div></div><small>总览展示当前链范围的全部样本；样本筛选用于其余图表和明细。${groups.some(group=>group.unassessed)?'未评估记录不计入三类比例。':''}${groups.some(group=>group.unit==='AIRR rows')?'历史结果按链结果行统计，不等同于输入 reads。':''}</small></section>`;
  }
  const figureCategories={'04_data_changes':'数据变化','02_primer_matching':'引物匹配','04_primer_c_distribution':'C 区分布'};
  const figureCategory=figure=>figure.category||({stage_overview:'04_data_changes',stages:'04_data_changes',matching:'02_primer_matching',primers:'02_primer_matching',genes:'04_primer_c_distribution',heatmap:'04_primer_c_distribution',composition:'04_primer_c_distribution'}[figure.kind]||'');
  const visibleFigure=figure=>['stage_overview','matching','primers','genes','heatmap','composition'].includes(figure.kind)&&(figure.kind!=='matching'||figure.id==='primer_matching_total');
  const statusName={queued:'排队中',running:'生成中',succeeded:'已完成',failed:'失败',cancelled:'已取消',cancelling:'正在取消',interrupted:'已中断'};
  function mount(root,{jobId='',view='all'}={}){
    const $=q=>root.querySelector(q),cfg=configPanel($('.rp-config'));
    let job=null,scan=null,runs=[],run=null,data=null,page=0,recordsAfter=0,busy=false,loading=false,timer=null,selection=new Set(),loadedId='',setupTouched=false;
    $('[data-rp-records]').insertAdjacentHTML('beforebegin','<div data-rp-record-downloads class="rp-actions"></div>');
    $('[data-rp-downloads]').insertAdjacentHTML('beforebegin','<button type="button" class="button button-ghost button-small" data-rp-primer-png>导出该引物 PNG</button>');
    $('[data-rp-table-kind]').insertAdjacentHTML('beforeend','<option value="matching_total">引物匹配总统计</option><option value="trim">mapping 前引物处理</option>');
    const trimLabel=document.createElement('label');trimLabel.innerHTML='引物处理<select data-rp-trim><option value="">全部</option><option value="trimmed_unique">唯一匹配，已去引物</option><option value="trimmed_multi_same_length">同长多重匹配，已去引物</option><option value="unmatched_untrimmed">未匹配，保留原序列</option><option value="ambiguous_length_untrimmed">长度不确定，保留原序列</option><option value="structure_untrimmed">结构不符，保留原序列</option><option value="primer_only_untrimmed">无剩余片段，保留原序列</option></select>';
    $('[data-rp-record-load]').before(trimLabel);
    const groupLabel=document.createElement('label');groupLabel.innerHTML='C 区处理分组<select data-rp-preparation><option value="">全部</option><option value="trimmed">已去引物</option><option value="untrimmed">保留原序列</option><option value="not_recorded">无剪切记录</option></select>';$('.rp-main-filters').append(groupLabel);
    if(view==='stages'){['primer','subset','preparation'].forEach(name=>$(`[data-rp-${name}]`).closest('label').hidden=true);const choices=$('[data-rp-table-kind]');[...choices.options].forEach(option=>{if(!['volumes','stages','trim'].includes(option.value))option.remove();});choices.value='volumes';choices.closest('.rp-filters').append($('[data-rp-chain]').closest('label'));for(const name of ['gene-segment','gene'])$(`[data-rp-${name}]`).closest('label').hidden=true;$('[data-rp-gene-apply]').hidden=true;$('[data-rp-records]').closest('details').hidden=true;$('.rp-gallery-section').hidden=true;}
    const message=(text,bad=false)=>{const node=$('[data-rp-message]');node.textContent=text;node.classList.toggle('page-error',bad);node.dataset.tone=bad?'error':'info';};
    const setSetup=open=>{$('[data-rp-setup]').hidden=!open;$('[data-rp-open]').setAttribute('aria-expanded',String(open));$('[data-rp-open]').textContent=open?'收起配置':'新建后续分析';};
    const fileURL=(name,download=false)=>`/api/reports/${encodeURIComponent(run.id)}/file?name=${encodeURIComponent(name)}${download?'&download=true':''}`;
    const workspace=window.ReportWorkspace?.mount(root,{fileURL,view,
      onError:text=>message(text,true),
      onScope:scope=>{for(const key of ['sample','chain','primer'])$(`[data-rp-${key}]`).value=scope[key]||'';},
      onRedraw:async selection=>{const created=await api(`/api/reports/${run.id}/figures`,selection);message('已加入绘图队列，将复用已有统计生成 V/J/C PNG。');await load();$('[data-rp-runs]').value=created.id;loadedId='';await display();}});
    function body(){if(!scan)throw new Error('请先扫描已有结果');if(!selection.size)throw new Error('请至少选择一项样本链结果');return {scan_id:scan.scan_id,selected_ids:[...selection],config:cfg.value(),comparison_species:$('[data-rp-comparison-species]').value};}
    async function action(button,fn){if(busy)return;busy=true;const old=button.textContent;button.disabled=true;button.dataset.loading='true';button.setAttribute('aria-busy','true');try{await fn();}catch(e){message(e.message,true);}finally{busy=false;button.disabled=false;button.textContent=old;delete button.dataset.loading;button.removeAttribute('aria-busy');}}
    function applyFilters(){
      if(!data)return;
      if(data.usage&&workspace){workspace.render(data,run);return;}
      $('[data-rp-workspace]').hidden=true;
      root.querySelectorAll('.rp-filter-card,[data-rp-coverage],[data-rp-charts]').forEach(node=>node.hidden=false);
      const sample=$('[data-rp-sample]').value,chain=$('[data-rp-chain]').value,primer=$('[data-rp-primer]').value,subset=$('[data-rp-subset]').value;
      const preparation=$('[data-rp-preparation]').value;
      const preparationInclude=r=>!preparation||(preparation==='trimmed'?(r.trim_state||'').startsWith('trimmed_'):preparation==='untrimmed'?(r.trim_state||'').endsWith('_untrimmed'):!r.trim_state||r.trim_state==='not_recorded');
      $('[data-rp-primer-png]').disabled=!primer||primer.startsWith('[');$('[data-rp-primer-png]').hidden=view==='stages'||!primer||primer.startsWith('[');
      const include=r=>(!sample||(r.sample||'')===sample)&&(!chain||r.chain===chain||!r.chain);
      const summaries=data.samples.filter(r=>(!sample||r.sample===sample)&&(view==='stages'||!chain||r.chain===chain||!r.chain));
      const scopeRow=r=>`<div class="rp-scope-row"><span class="rp-scope-sample">${esc(r.sample)}${r.pair&&r.pair!==r.sample?`<small>${esc(r.pair)}</small>`:''}</span><span class="rp-chain-tag">${esc(r.chain)}</span><span class="rp-scope-count"><strong>${r.observations.toLocaleString()}</strong> ${r.unit==='read pairs'?'read pairs':'reads'}</span></div>`;
      const notes=[...new Set(summaries.map(r=>r.note).filter(Boolean))];
      if(summaries.length&&summaries.every(r=>r.mapping_preparation))notes.unshift('已记录比对前引物归属；未确定的序列保留原样。');
      $('[data-rp-coverage]').innerHTML=summaries.length?`<div class="rp-scope-heading">当前数据范围<span>各链任务独立计数</span></div>${summaries.slice(0,4).map(scopeRow).join('')}${summaries.length>4?`<details class="rp-scope-more"><summary>展开其余 ${summaries.length-4} 项</summary>${summaries.slice(4).map(scopeRow).join('')}</details>`:''}${notes.map(note=>`<p class="rp-scope-note">${esc(note)}</p>`).join('')}`:'<p class="rp-inline-empty">当前筛选下暂无结果，试试清除筛选。</p>';
      const volumes=(data.stage_volumes||[]).filter(r=>r.sample===sample);
      if(view==='stages'&&volumes.length){
        const get=stage=>volumes.find(r=>r.stage===stage&&!r.chain);
        const show=value=>value===null||value===undefined?'—':value.toLocaleString();
        const cards=[['Raw reads',get('raw'),'reads（R1 + R2）','本次选择的配对'],['Clean reads',get('fastp'),'reads',get('fastp')?.retention_percent==null?'保留率 —':'质控保留率 '+get('fastp').retention_percent.toFixed(1)+'%'],['Merged reads',get('merged'),'reads',get('merged')?.retention_percent==null?'合并率 —':'合并率 '+get('merged').retention_percent.toFixed(1)+'%']];
        $('[data-rp-coverage]').innerHTML='<div class="rp-volume-kpis">'+cards.map(([title,row,unit,note])=>`<div><span>${title}</span><strong>${show(row?.count)}</strong><small>${unit}</small><p>${note}</p></div>`).join('')+'</div>';
      }
      $('[data-rp-result-summary]').innerHTML=`<span><strong>${new Set(summaries.map(r=>r.sample)).size}</strong> 个样本</span><span><strong>${new Set(summaries.map(r=>r.chain)).size}</strong> 条链</span><span>${summaries.length} 项结果</span>`;
      $('[data-rp-filter-summary]').textContent=[sample||'全部样本',view==='stages'?(chain?'明细链 '+chain:'全部处理链'):chain||'全部链',primer,view==='stages'?'':subset==='retained'?'最终保留':'全部观测',preparation==='trimmed'?'已去引物':preparation==='untrimmed'?'保留原序列':preparation==='not_recorded'?'无剪切记录':''].filter(Boolean).join(' · ');
      const charts=[];
      if(view!=='primer'){
        const overview=data.figures.find(r=>r.kind==='stage_overview');
        if(overview)charts.push(`<section class="rp-chart rp-stage-overview"><div class="rp-actions"><h4>Read counts across samples</h4><a class="button button-ghost button-small" href="${esc(fileURL(overview.png,true))}">下载全样本汇总 PNG</a></div><a target="_blank" rel="noopener" href="${esc(fileURL(overview.png))}"><img src="${esc(fileURL(overview.png))}" alt="Read counts across samples"></a><small>Raw reads 包含 R1 + R2；Merged reads 按合并后的每对 reads 计数。“—”表示缺少阶段计数。</small></section>`);
        else charts.push('<p class="muted">当前版本没有全样本汇总图，请重新生成后续分析。</p>');
      }
      if(view!=='stages'){
        if(data.matching_totals){
          charts.push(matchingComposition(sampleMatching(data.matching_totals.filter(r=>!chain||(r.chains||'').split(',').includes(chain)))));
        }else charts.push('<p class="muted">重新生成后续分析后，可查看去重后的引物匹配总统计。</p>');
        const geneGroups=new Map();data.gene_counts.filter(include).filter(r=>r.segment==='C'&&r.subset===subset&&(!primer||r.primer===primer)&&preparationInclude(r)).forEach(r=>{const key=r.sample+' / '+r.chain+' · '+r.segment;if(!geneGroups.has(key))geneGroups.set(key,new Map());const genes=geneGroups.get(key);genes.set(r.gene,(genes.get(r.gene)||0)+r.count);});
        for(const [title,genes] of [...geneGroups].slice(0,12)){const rows=[...genes].sort((a,b)=>b[1]-a[1]).map(([label,value])=>({label,value}));charts.push(bars(title+' 基因分布',rows,(subset==='retained'?'最终保留':'本次全部观测')+'，前 20 类；完整分布可下载。'));}
      }
      $('[data-rp-charts]').innerHTML=charts.join('');
      const kind=$('[data-rp-table-kind]').value,gene=$('[data-rp-gene]').value.trim(),segment=$('[data-rp-gene-segment]').value;
      const geneInclude=r=>!gene||(r.gene===gene&&r.segment===segment);
      const datasets={stages:[data.stages,['sample','pair','chain','stage','metric','count','unit','percent','note']],genes:[data.gene_counts.filter(r=>r.segment==='C'&&r.subset===subset&&(!primer||r.primer===primer)&&geneInclude(r)),['sample','chain','scope','subset','primer','segment','gene','count','denominator','percent']],coverage:[data.gene_coverage.filter(r=>r.segment==='C'&&r.subset===subset&&geneInclude(r)),['sample','chain','segment','gene','unique_count','reference_catalog','status']],matching:[data.match_counts,['sample','chain','scope','match_class','count']],primers:[data.primer_counts.filter(r=>r.subset===subset&&(!primer||r.primer===primer)),['sample','chain','scope','primer','count']],filters:[data.filter_counts,['sample','chain','scope','reason','count']]};
      datasets.volumes=[data.stage_volumes||[],['sample','chain','label','count','unit','previous_count','previous_unit','reduction','retention_percent','pair_count','note']];
      datasets.trim=[data.trim_counts||[],['sample','chain','scope','trim_state','count']];
      datasets.matching_total=[sampleMatching((data.matching_totals||[]).filter(r=>!chain||(r.chains||'').split(',').includes(chain))).flatMap(group=>{
        const denominator=group.unique+group.multiple+group.unmatched;
        return ['unique','multiple','unmatched'].map(match_class=>({sample:group.sample,unit:group.unit,match_class,count:group[match_class],denominator,percent:denominator?group[match_class]*100/denominator:null,unassessed_count:group.unassessed}));
      }),['sample','unit','match_class','count','denominator','percent','unassessed_count']];
      datasets.genes[0]=datasets.genes[0].filter(preparationInclude);
      datasets.genes[1]=['sample','chain','scope','subset','primer','trim_state','segment','gene','count','state_denominator','state_percent'];
      const visibleTable=datasets[kind][0].filter(r=>kind==='matching_total'||include(r)).filter(r=>r.unit!=='bases');$('[data-rp-table-count]').textContent=visibleTable.length.toLocaleString()+' 行';
      $('[data-rp-tables]').innerHTML=(kind==='coverage'&&preparation?'<p class="muted">参考 C 基因观察情况按所选统计范围的全部记录计算；剪切分组请查看“引物与 C 区分布”。</p>':'')+table(visibleTable,datasets[kind][1]);
      const category=$('[data-rp-figure-category]').value;
      const figures=(view==='stages'?[]:data.figures).filter(visibleFigure).filter(r=>!category||figureCategory(r)===category).filter(r=>!(r.kind==='stages'&&(r.id==='stage_summary'||data.stage_summary_scope==='sample'))).filter(r=>!['genes','heatmap','composition'].includes(r.kind)||(r.segment==='C'||/· C 基因分布|引物 × C（/.test(r.title))).filter(r=>(!sample||r.sample===sample||!r.sample)&&(!chain||r.chain===chain||!r.chain)&&(!primer||!r.primer||r.primer===primer)&&(!preparation||!r.preparation||r.preparation===preparation)&&(view==='all'||r.kind!=='stages'));
      page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(figures.length/7)-1)));
      $('[data-rp-gallery]').innerHTML=figures.slice(page*7,page*7+7).map(f=>`<figure class="rp-figure"><a class="rp-figure-image" target="_blank" rel="noopener" aria-label="${esc('打开 '+f.title)}" href="${esc(fileURL(f.png))}"><img loading="lazy" src="${esc(fileURL(f.png))}" alt="${esc(f.title)}"></a><figcaption><div><span class="rp-figure-category">${esc((figureCategories[figureCategory(f)]||'汇报图片')+(!f.sample?' · 全部样本':!f.primer&&['genes','heatmap','composition'].includes(f.kind)?' · 全部引物':''))}</span><strong>${esc(f.title)}</strong></div><a class="button button-ghost button-small" href="${esc(fileURL(f.png,true))}">下载 PNG ↓</a></figcaption></figure>`).join('')||'<div class="rp-gallery-empty"><strong>当前筛选下暂无图片</strong><span>可切换样本、图片类别，或清除筛选。</span></div>';
      $('[data-rp-page]').textContent=`${figures.length} 张图片 · 第 ${page+1} / ${Math.max(1,Math.ceil(figures.length/7))} 页`;
      $('[data-rp-prev]').disabled=page===0;$('[data-rp-next]').disabled=(page+1)*7>=figures.length;
    }
    async function display(){
      run=runs.find(r=>r.id===$('[data-rp-runs]').value)||null;
      $('[data-rp-results]').hidden=!run||run.status!=='succeeded'||run.archived;
      if(!run){$('[data-rp-status]').replaceChildren();$('[data-rp-run-actions]').replaceChildren();$('[data-rp-empty]').hidden=false;return;}
      $('[data-rp-empty]').hidden=true;
      if(run.archived){$('[data-rp-status]').innerHTML='<p class="rp-state-notice">来源任务已归档，PNG 和统计明细可从任务归档包获取。</p>';$('[data-rp-run-actions]').replaceChildren();return;}
      const phase=run.status==='succeeded'?'图片与统计表已生成':run.progress?.message||(run.status==='queued'?'等待可用资源':run.status==='running'?'正在整理统计与图片':'');
      const progress=run.status==='running'&&run.progress&&run.progress.total>0?`<div class="rp-run-progress"><progress max="${Number(run.progress.total)}" value="${Math.max(0,Math.min(Number(run.progress.total),Number(run.progress.done)||0))}" aria-label="已整理的结果项"></progress><span>${run.progress.phase==='figures'?'已绘制':'已整理'} ${Number(run.progress.done)||0} / ${Number(run.progress.total)} 项</span></div>`:'';
      $('[data-rp-status]').innerHTML=`<div class="rp-status" data-state="${esc(run.status)}"><span class="rp-status-badge">${esc(statusName[run.status]||run.status)}</span><span class="rp-status-description">${esc(phase)}</span><small>${esc((run.ended_at?'结束于 '+timeLabel(run.ended_at):'创建于 '+timeLabel(run.created_at)))}</small></div>${progress}${run.error?`<p class="page-error rp-run-error">${esc(run.error)}</p>`:''}`;
      $('[data-rp-run-actions]').innerHTML=['queued','running'].includes(run.status)?'<button type="button" class="button button-ghost button-small" data-rp-cancel>取消</button>':['failed','cancelled','interrupted'].includes(run.status)?'<button type="button" class="button button-ghost button-small" data-rp-retry>重试</button>':'';
      $('[data-rp-run-actions]').insertAdjacentHTML('beforeend',`<a class="button button-ghost button-small" href="${esc(fileURL('analysis.log',true))}">分析日志</a>`);
      const cancel=$('[data-rp-cancel]'),retry=$('[data-rp-retry]');
      if(cancel)cancel.onclick=()=>action(cancel,async()=>{await api(`/api/reports/${run.id}/cancel`,{});await load();});
      if(retry)retry.onclick=()=>action(retry,async()=>{await api(`/api/reports/${run.id}/retry`,{});loadedId='';await load();});
      if(run.status==='succeeded'&&loadedId!==run.id){
        const target=run.id,summary=await api(`/api/reports/${target}/summary?compact=true`);
        if(run?.id!==target)return;
        data=summary;loadedId=target;page=0;
        $('[data-rp-sample]').innerHTML='<option value="">全部样本</option>'+[...new Set(data.samples.map(r=>r.sample))].map(s=>`<option>${esc(s)}</option>`).join('');
        if(view==='stages')$('[data-rp-sample]').value=data.samples[0]?.sample||'';
        $('[data-rp-chain]').innerHTML='<option value="">全部链</option>'+[...new Set(data.samples.map(r=>r.chain))].map(s=>`<option>${esc(s)}</option>`).join('');
        $('[data-rp-primer]').innerHTML='<option value="">全部引物</option>'+[...new Set(data.primer_counts.map(r=>r.primer))].map(s=>`<option>${esc(s)}</option>`).join('');
        $('[data-rp-downloads]').innerHTML=`<a class="button button-ghost button-small" href="${esc(fileURL('report.xlsx',true))}">完整数据 XLSX</a><a class="button button-primary button-small" href="/api/reports/${encodeURIComponent(run.id)}/download">图片与数据 ZIP ↓</a>`;
        const available=new Set(data.figures.filter(visibleFigure).map(figureCategory));
        $('[data-rp-figure-category]').innerHTML='<option value="">全部类别</option>'+Object.entries(figureCategories).filter(([key])=>available.has(key)).map(([key,label])=>`<option value="${key}">${label}</option>`).join('');
        applyFilters();
      }
    }
    async function load(){
      if(loading)return;loading=true;
      try{
        const selected=$('[data-rp-runs]').value;
        runs=await api('/api/reports'+(jobId?'?job_id='+encodeURIComponent(jobId):''));
        $('[data-rp-runs]').innerHTML=runs.length?runs.map(r=>`<option value="${esc(r.id)}">${esc(timeLabel(r.created_at))} · ${esc(statusName[r.status])} · ${esc(r.id.slice(0,8))}</option>`).join(''):'<option value="">暂无分析记录</option>';
        if(runs.some(r=>r.id===selected))$('[data-rp-runs]').value=selected;
        if(!jobId&&!setupTouched)setSetup(!runs.length);
        await display();
      }catch(e){message(e.message,true);}finally{
        loading=false;clearTimeout(timer);
        if(runs.some(r=>['queued','running','cancelling'].includes(r.status)))timer=setTimeout(()=>{if(!document.hidden&&!root.closest('[hidden]'))load();else timer=setTimeout(load,6000);},4000);
      }
    }
    $('[data-rp-open]').onclick=()=>{setupTouched=true;setSetup($('[data-rp-setup]').hidden);};
    $('[data-rp-refresh]').onclick=()=>load();
    $('[data-rp-scan]').onclick=e=>action(e.target,async()=>{
      const candidate=await api('/api/reports/scan',jobId?{job_id:jobId}:{input_path:$('[data-rp-path]').value.trim()});
      scan=candidate;selection=new Set(scan.items.map(r=>r.id));
      const showDataset=new Set(scan.items.map(r=>r.dataset||'')).size>1;
      $('[data-rp-candidates]').innerHTML=`<div class="rp-candidate-heading"><strong>发现 ${scan.items.length} 项样本链结果</strong><span data-rp-selection-count></span><div class="rp-actions"><button type="button" class="button button-ghost button-small" data-rp-select-all>全选</button><button type="button" class="button button-ghost button-small" data-rp-select-none>清空</button></div></div><div class="rp-candidates">${scan.items.map(r=>`<label class="rp-candidate-row"><input type="checkbox" data-rp-item="${esc(r.id)}" checked><span class="rp-candidate-sample"><strong>${esc(r.sample_label)}</strong>${r.pair&&r.pair!==r.sample_label?`<span>${esc(r.pair)}</span>`:''}${showDataset?`<span>${esc((r.dataset||'导入目录').split('/').pop())}</span>`:''}</span><span class="rp-chain-tag">${esc(r.chain)}</span><small>${r.capabilities.prefilter_annotations?'过滤前注释可用':'仅最终结果'}</small></label>`).join('')}</div>`;
      const updateSelection=()=>{$('[data-rp-selection-count]').textContent='已选 '+selection.size+' / '+scan.items.length+' 项';$('[data-rp-run]').disabled=!selection.size;};
      $('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>input.onchange=()=>{if(input.checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);updateSelection();});
      for(const [key,checked] of [['all',true],['none',false]])$(`[data-rp-select-${key}]`).onclick=()=>{$('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>{input.checked=checked;if(checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);});updateSelection();};
      updateSelection();
      $('[data-rp-run]').disabled=false;message('扫描完成，可选择样本链后生成汇总。');
    });
    $('[data-rp-preview]').onclick=e=>action(e.target,async()=>{
      const preview=jobId&&job?.status!=='SUCCEEDED'?await api(`/api/reports/plans/${encodeURIComponent(jobId)}/preview`,cfg.value()):await api('/api/reports/preview',body());
      $('[data-rp-allocation]').innerHTML=`<div class="rp-allocation">${Object.entries(preview.assignments).map(([sample,primers])=>`<div class="rp-assignment-row"><strong>${esc(sample)}</strong><span>${primers.map(p=>`<span class="rp-primer-tag">${esc(p.name)}<small>${esc(p.group)}</small></span>`).join('')}</span></div>`).join('')||'<p class="rp-inline-empty">未补充引物表；将基于现有结果生成统计与图片。</p>'}</div>`;
      message('分配预览已更新。');
    });
    $('[data-rp-run]').onclick=e=>action(e.target,async()=>{const created=await api('/api/reports',body());message('已加入后续分析队列。');setSetup(false);await load();$('[data-rp-runs]').value=created.id;await display();});
    $('[data-rp-save-plan]').onclick=e=>action(e.target,async()=>{await api(`/api/reports/plans/${encodeURIComponent(jobId)}`,cfg.value(),'PUT');message('已保存；主分析完成后自动生成引物统计与图片。');await load();});
    $('[data-rp-runs]').onchange=()=>{loadedId='';display().catch(e=>message(e.message,true));};
    ['sample','chain','primer','subset','preparation','table-kind','figure-category'].forEach(key=>$(`[data-rp-${key}]`).onchange=()=>{page=0;applyFilters();});
    $('[data-rp-reset]').onclick=()=>{$('[data-rp-sample]').value='';$('[data-rp-chain]').value='';$('[data-rp-primer]').value='';$('[data-rp-figure-category]').value='';$('[data-rp-preparation]').value='';$('[data-rp-gene]').value='';$('[data-rp-subset]').value='retained';page=0;applyFilters();};
    $('[data-rp-gene-apply]').onclick=applyFilters;
    $('[data-rp-primer-png]').onclick=e=>action(e.target,async()=>{const created=await api(`/api/reports/${run.id}/figures`,{primer:$('[data-rp-primer]').value,sample:$('[data-rp-sample]').value,chain:$('[data-rp-chain]').value});message('已加入绘图队列，生成所选引物的 C 区分布 PNG。');await load();$('[data-rp-runs]').value=created.id;await display();});
    $('[data-rp-prev]').onclick=()=>{page--;applyFilters();};$('[data-rp-next]').onclick=()=>{page++;applyFilters();};
    async function records(append=false){
      if(!run)return;
      const params=new URLSearchParams({chain:$('[data-rp-chain]').value,match_class:$('[data-rp-record-class]').value,reason:$('[data-rp-record-reason]').value,trim_state:$('[data-rp-trim]').value,after:String(append?recordsAfter:0)});
      if(data?.usage&&workspace?.scope().group_id)params.set('group_id',workspace.scope().group_id);
      const sample=$('[data-rp-sample]').value;if(sample)params.set('sample',sample);
      const primer=$('[data-rp-primer]').value;if(primer)params.set('primer',primer);
      if(!$('[data-rp-trim]').value&&$('[data-rp-preparation]').value)params.set('trim_group',$('[data-rp-preparation]').value);
      if($('[data-rp-gene]').value.trim()){params.set('gene',$('[data-rp-gene]').value.trim());params.set('segment',$('[data-rp-gene-segment]').value);}
      const result=await api(`/api/reports/${run.id}/records?${params}`);$('[data-rp-records]').innerHTML=table(result.rows);recordsAfter=result.next_cursor||0;$('[data-rp-record-more]').hidden=!result.has_more;
      params.delete('after');
      $('[data-rp-record-downloads]').innerHTML=`<a class="button button-ghost button-small" href="/api/reports/${encodeURIComponent(run.id)}/sequences?${esc(params.toString())}&format=fasta">下载筛选序列 FASTA</a><a class="button button-ghost button-small" href="/api/reports/${encodeURIComponent(run.id)}/sequences?${esc(params.toString())}&format=tsv">下载关联表 TSV</a>`;
    }
    $('[data-rp-record-load]').onclick=e=>action(e.target,()=>records());$('[data-rp-record-more]').onclick=()=>records(true).catch(e=>message(e.message,true));
    const instance={update(value){job=value;if(!jobId)return;const supported=['igblast_base','pig_igblast'].includes(job.pipeline);root.hidden=!supported;if(!supported)return;root.querySelector('.rp-source').hidden=true;const canPlan=!['SUCCEEDED','ARCHIVED','ARCHIVING','DELETING'].includes(job.status);$('[data-rp-save-plan]').hidden=!canPlan;$('[data-rp-scan]').disabled=canPlan;$('[data-rp-source-note]').textContent=canPlan?'可在此保存引物配置，主分析完成后自动生成统计。':'扫描当前任务结果后，可重新生成汇总或补充引物分析。';},load};
    if(!jobId){const params=new URLSearchParams(location.search);if(params.get('job_id'))location.href='/jobs/'+encodeURIComponent(params.get('job_id'))+'#primer';}
    instances.push(instance);load();return instance;
  }
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-report-panel]').forEach(root=>mount(root,{jobId:window.SCIGBLAST_JOB_ID||'',view:root.dataset.reportView||'all'}));
    document.querySelectorAll('.rp-config').forEach(root=>configPanel(root));
  });
  return {configPanel,update:job=>instances.forEach(panel=>panel.update(job)),load:()=>instances.forEach(panel=>panel.load())};
})();

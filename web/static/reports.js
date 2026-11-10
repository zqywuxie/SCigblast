/* Report creation, history and trace. Visualizations live in report.html. */
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
    let allocationSamples=[],groupChoices=[],primerChoices=[],sampleGroups={},optionsReady=false,optionsEpoch=0;
    const manualMode=()=>$('[data-rp-assignment-mode]').value==='manual';
    function renderAllocation(){
      $('[data-rp-manual-allocation]').hidden=!manualMode();
      $('[data-rp-file-card="submission"]').hidden=manualMode();
      $('[data-rp-map-group="submission"]').hidden=manualMode()||!readyBook('submission');
      root.querySelectorAll('[data-rp-submission-setting]').forEach(node=>node.hidden=manualMode());
      const names=field('allow_primer_name_join').checked?primerChoices:[];
      const choices=[...groupChoices.map(name=>({name,label:name})),...names.filter(name=>!groupChoices.includes(name)).map(name=>({name,label:'引物 · '+name}))];
      $('[data-rp-sample-groups]').innerHTML=allocationSamples.map(({sample,chains})=>{
        const rows=chains.map(chain=>{
          const chosen=sampleGroups[sample]?.[chain]||[];
          return `<details class="rp-sample-allocation"><summary><strong>${esc(chain)}</strong><span data-rp-chosen>${chosen.length?chosen.map(esc).join('、'):'选择引物组'}</span></summary><div class="rp-group-choices">${choices.map(choice=>`<label><input type="checkbox" data-rp-group-sample="${esc(sample)}" data-rp-group-chain="${esc(chain)}" value="${esc(choice.name)}" ${chosen.includes(choice.name)?'checked':''}>${esc(choice.label)}</label>`).join('')||'<p class="muted">请先读取可选引物组。</p>'}</div></details>`;
        }).join('');
        return `<section class="rp-sample-block"><header><strong>${esc(sample)}</strong><small>${chains.length} 条链</small></header>${rows||'<p class="muted">请先指定处理链或完成 Match。</p>'}</section>`;
      }).join('')||'<p class="muted">扫描样本或完成 Match 后，在此分配引物组。</p>';
    }
    $('[data-rp-sample-groups]').addEventListener('change',event=>{
      const input=event.target.closest('[data-rp-group-sample]');if(!input)return;
      const sample=input.dataset.rpGroupSample,chain=input.dataset.rpGroupChain,values=new Set(sampleGroups[sample]?.[chain]||[]);
      if(input.checked)values.add(input.value);else values.delete(input.value);
      (sampleGroups[sample]??={})[chain]=[...values];
      input.closest('details').querySelector('[data-rp-chosen]').textContent=[...values].join('、')||'选择引物组';
    });
    $('[data-rp-assignment-mode]').addEventListener('change',renderAllocation);
    field('allow_primer_name_join').addEventListener('change',()=>{
      if(!field('allow_primer_name_join').checked)pruneGroups(new Set(groupChoices));
      renderAllocation();
    });
    function pruneGroups(allowed){
      for(const chains of Object.values(sampleGroups))for(const chain of Object.keys(chains))chains[chain]=chains[chain].filter(name=>allowed.has(name));
    }
    function invalidateGroups(){optionsReady=false;optionsEpoch++;groupChoices=[];primerChoices=[];renderAllocation();}
    $('[data-rp-load-groups]').onclick=async()=>{
      const button=$('[data-rp-load-groups]');button.disabled=true;
      try{
        const body=result.value(true),epoch=optionsEpoch;
        if(!body.primer_id)throw Error('请先上传引物表并选择对应列');
        const choices=await api('/api/reports/primer-options',body);
        if(epoch!==optionsEpoch)return;
        groupChoices=choices.groups;primerChoices=choices.primers;optionsReady=true;
        const allowed=new Set([...groupChoices,...primerChoices]);
        pruneGroups(allowed);
        renderAllocation();error.classList.add('hidden');
      }catch(e){fail(e);}finally{button.disabled=false;}
    };
    const columnNumber=text=>{text=text.trim().toUpperCase();if(!/^[A-Z]{1,3}$/.test(text))throw Error('列须用 Excel 字母，例如 B、I、AA');return [...text].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0)-1;};
    const columnName=index=>{let name='';for(let n=index+1;n>0;n=Math.floor((n-1)/26))name=String.fromCharCode(65+(n-1)%26)+name;return name;};
    const headerRow=key=>{const row=Number(field(key+'_header_row').value);if(!Number.isInteger(row)||row<1||row>500000)throw Error('表头行须为 1–500000 的整数');return row;};
    const mappingFields={submission:['sample_column','submission_join_column'],primer:['primer_join_column']};
    const pairs=()=>[...root.querySelectorAll('[data-rp-primer-pair]')].map(row=>[row.querySelector('[data-rp-pair-name]').value,row.querySelector('[data-rp-pair-sequence]').value]);
    const readyBook=key=>{try{return books[key]&&books[key].sheet===field(key+'_sheet').value&&(books[key].preview_start||0)===headerRow(key)-1;}catch{return false;}};
    function columnOptions(key){
      const book=books[key],header=headerRow(key)-1;
      return '<option value="">请选择列</option>'+Array.from({length:book.column_count},(_,index)=>{
        const letter=columnName(index),title=cell(book,header,index).value;
        const example=Array.from({length:5},(_,i)=>cell(book,header+i+1,index,true).value).find(Boolean)||'';
        const label=title||('空表头'+(example?' · '+example:''));
        return `<option value="${letter}">${esc(letter+' · '+label.slice(0,90))}</option>`;
      }).join('');
    }
    function columnsChanged(key){if(key==='primer')invalidateGroups();previewKey=key;showPreview();$('[data-rp-selected-columns]').open=true;error.classList.add('hidden');}
    function addPair(){
      if(!readyBook('primer')||pairs().length>=12)return;
      const row=document.createElement('div');row.className='rp-primer-pair';row.dataset.rpPrimerPair='';
      row.innerHTML=`<label>名称列<select data-rp-pair-name aria-label="引物名称列">${columnOptions('primer')}</select></label><span class="rp-pair-arrow" aria-hidden="true">→</span><label>序列列<select data-rp-pair-sequence aria-label="引物序列列">${columnOptions('primer')}</select></label><button type="button" class="button button-ghost button-small" data-rp-remove-pair aria-label="移除此名称与序列列对">移除</button>`;
      row.querySelectorAll('select').forEach(select=>select.addEventListener('change',()=>columnsChanged('primer')));
      row.querySelector('[data-rp-remove-pair]').onclick=()=>{row.remove();syncPairButtons();columnsChanged('primer');};
      $('[data-rp-primer-pairs]').append(row);syncPairButtons();
    }
    function syncPairButtons(){
      const rows=[...root.querySelectorAll('[data-rp-primer-pair]')];
      rows.forEach((row,index)=>{row.querySelector('[data-rp-remove-pair]').disabled=rows.length===1;row.querySelector('[data-rp-pair-name]').setAttribute('aria-label',`第 ${index+1} 对引物名称列`);row.querySelector('[data-rp-pair-sequence]').setAttribute('aria-label',`第 ${index+1} 对引物序列列`);});
      $('[data-rp-add-pair]').disabled=!readyBook('primer')||rows.length>=12;
    }
    function clearColumns(key){
      if(key==='primer')invalidateGroups();
      mappingFields[key].forEach(name=>{field(name).innerHTML='<option value="">上传后选择</option>';field(name).disabled=true;});
      if(key==='primer'){$('[data-rp-primer-pairs]').replaceChildren();syncPairButtons();}
    }
    function populateColumns(key){
      if(headerRow(key)>books[key].row_count)throw Error('所选表头行超出工作表，请调整表头行');
      const options=columnOptions(key);
      mappingFields[key].forEach(name=>{field(name).innerHTML=options;field(name).disabled=false;});
      if(key==='primer'){$('[data-rp-primer-pairs]').replaceChildren();addPair();}
    }
    $('[data-rp-add-pair]').onclick=()=>{addPair();columnsChanged('primer');};
    function fail(e){error.textContent=e.message;error.classList.remove('hidden');}
    function uploadState(key,state){
      const card=$(`[data-rp-file-card="${key}"]`);card.dataset.state=state;
      $(`[data-rp-upload-state="${key}"]`).textContent={empty:'未选择',loading:'读取中',ready:'已载入',error:'未完成'}[state];
      $(`[data-rp-upload-action="${key}"]`).textContent=state==='ready'?'更换文件':'选择文件';
    }
    function resetSheet(key){clearColumns(key);const select=field(key+'_sheet');select.disabled=false;select.innerHTML='<option value="">上传后选择</option>';select.closest('label').hidden=true;const note=$(`[data-rp-upload-note="${key}"]`);note.textContent=key==='submission'?'样本与引物组对应表':'引物名称与序列表';note.removeAttribute('title');uploadState(key,'empty');}
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
    function selected(key,required=false){
      const columns=key==='submission'?[['sample_column','样本',false],['submission_join_column','引物组',true]]:[['primer_join_column','引物组',true]];
      const result=[];
      for(const [name,role,inherit] of columns){const letter=field(name).value;if(!letter){if(required)throw Error(`请选择${key==='submission'?' Submission':'引物表'}的${role}列`);continue;}result.push({name,role,inherit,letter,index:columnNumber(letter)});}
      if(key==='primer'){
        const chosen=pairs();
        if(required&&(!chosen.length||chosen.length>12))throw Error('请选择 1–12 对引物名称与序列列');
        for(const [index,pair] of chosen.entries()){
          if(required&&pair.some(letter=>!letter))throw Error(`请选择第 ${index+1} 对引物的名称列和序列列`);
          if(required&&pair[0]===pair[1])throw Error(`第 ${index+1} 对引物的名称列和序列列不能相同`);
          pair.forEach((letter,i)=>{if(letter)result.push({name:'primer_columns',role:i?'序列':'名称',inherit:false,letter,index:columnNumber(letter)});});
        }
      }
      if(result.some(col=>col.index>=books[key].column_count))throw Error('所选列不在当前工作表中，请重新选择');
      return result;
    }
    function showPreview(){
      const sections=[];
      for(const key of ['submission','primer']){
        const book=readyBook(key)?books[key]:null,names=key==='submission'?['sample_column','submission_join_column']:['primer_join_column','primer_columns'];
        $(`[data-rp-map-group="${key}"]`).hidden=!book||(key==='submission'&&manualMode());
        names.forEach(name=>{const note=$(`[data-rp-column-note="${name}"]`);note.textContent='请选择对应列';note.classList.remove('rp-column-warning');});
        if(!book)continue;
        const title=(key==='submission'?'Submission':'引物表')+' · '+book.sheet;
        try{
          const header=headerRow(key)-1;
          if(book.sheet!==field(key+'_sheet').value||header!==(book.preview_start||0))throw Error('工作表或表头已更改，正在更新预览');
          const columns=selected(key),sampleRows=Array.from({length:Math.max(0,Math.min(5,book.row_count-header-1))},(_,i)=>header+i+1);
          for(const name of names){
            const note=$(`[data-rp-column-note="${name}"]`),chosen=columns.filter(c=>c.name===name);
             if(!chosen.length)continue;
            if(name==='primer_columns')note.textContent=chosen.map(c=>c.letter+'：'+(cell(book,header,c.index).value||'空列')).join('；');
            else{
              const col=chosen[0],values=[...new Set(sampleRows.map(r=>cell(book,r,col.index,col.inherit).value).filter(Boolean))].slice(0,3);
              note.textContent=(cell(book,header,col.index).value||col.letter+' 列')+'：'+(values.join('、')||'预览行内无内容，请核对');
              if(!values.length)note.classList.add('rp-column-warning');
            }
          }
          sections.push(`<section id="${previewId}-${key}" role="tabpanel" aria-labelledby="${previewId}-${key}-tab" data-rp-preview-section="${key}"><div class="rp-preview-heading"><strong>${key==='submission'?'Submission':'引物表'}</strong><span class="rp-sheet-name">${esc(book.sheet)}</span><span class="rp-row-count">${sampleRows.length} 条预览</span></div>${columns.length?previewTable(book,header,columns,sampleRows):'<p class="rp-preview-message">选择对应列后，在此核对内容。</p>'}</section>`);
        }catch(e){names.forEach(name=>{const note=$(`[data-rp-column-note="${name}"]`);note.textContent=e.message;note.classList.add('rp-column-warning');});sections.push(`<section id="${previewId}-${key}" role="tabpanel" aria-labelledby="${previewId}-${key}-tab" data-rp-preview-section="${key}"><p class="rp-preview-message">${esc(title)}：${esc(e.message)}</p></section>`);}
      }
      $('[data-rp-column-mapping]').hidden=!sections.length;$('[data-rp-column-empty]').hidden=!!sections.length;
      $('[data-rp-selected-columns]').hidden=!sections.length;
      $('[data-rp-selected-preview]').innerHTML=sections.join('');syncPreviewTabs();
      $('[data-rp-workbook-preview]').innerHTML=Object.entries(books).map(([key,book])=>`<h4>${key==='submission'?'Submission':'引物表'} · ${esc(book.sheet)}</h4>${table(book.preview.slice(0,10).map((row,index)=>Object.fromEntries([['行',(book.preview_start||0)+index+1],...row.map((value,col)=>[columnName(col),value])])) )}`).join('');
    }
    async function refreshBook(key){
      if(!uploads[key]){showPreview();return;}
      let header;try{header=headerRow(key);}catch(e){showPreview();fail(e);return;}
      const token=uploads[key],revision=generation,epoch=++revisions[key],sheet=field(key+'_sheet').value;
      delete books[key];clearColumns(key);pending++;showPreview();uploadState(key,'loading');
      try{
        const book=await api(`/api/reports/workbook/${token}?sheet=${encodeURIComponent(sheet)}&header_row=${header}`);
        if(revision!==generation||epoch!==revisions[key]||uploads[key]!==token)return;
        books[key]=book;populateColumns(key);uploadState(key,'ready');showPreview();error.classList.add('hidden');
      }catch(e){if(revision===generation&&epoch===revisions[key]){delete books[key];clearColumns(key);uploadState(key,'error');showPreview();fail(e);}}
      finally{pending--;}
    }
    root.querySelectorAll('[data-rp-upload]').forEach(input=>input.addEventListener('change',async()=>{
      const file=input.files[0],key=input.dataset.rpUpload,revision=generation,epoch=++revisions[key];
      delete uploads[key];delete books[key];resetSheet(key);showPreview();if(!file)return;
      let header;try{header=headerRow(key);}catch(e){fail(e);return;}
      pending++;error.classList.add('hidden');uploadState(key,'loading');field(key+'_sheet').disabled=true;$(`[data-rp-upload-note="${key}"]`).textContent='上传中…';
      try{
        const response=await fetch('/api/reports/upload?filename='+encodeURIComponent(file.name)+'&header_row='+header,{method:'POST',headers:{'X-SCIGBLAST-Request':'1'},body:file});
        const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'上传失败');
        if(revision!==generation||epoch!==revisions[key])return;
        uploads[key]=data.id;books[key]=data;
        const sheetSelect=field(key+'_sheet');sheetSelect.innerHTML=data.sheets.map(name=>`<option>${esc(name)}</option>`).join('');sheetSelect.value=data.sheet;sheetSelect.closest('label').hidden=data.sheets.length<=1;
        populateColumns(key);$(`[data-rp-upload-note="${key}"]`).textContent=file.name;$(`[data-rp-upload-note="${key}"]`).title=file.name;uploadState(key,'ready');previewKey=key;showPreview();
        if(headerRow(key)-1!==data.preview_start)await refreshBook(key);
      }catch(e){if(revision===generation&&epoch===revisions[key]){delete books[key];clearColumns(key);showPreview();uploadState(key,'error');fail(e);$(`[data-rp-upload-note="${key}"]`).textContent='读取失败，请重新选择文件或调整表头行';}}
      finally{pending--;if(revision===generation&&epoch===revisions[key])field(key+'_sheet').disabled=false;}
    }));
    for(const key of ['submission','primer']){
      field(key+'_sheet').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
      field(key+'_header_row').addEventListener('input',()=>{if(uploads[key])revisions[key]++;delete books[key];clearColumns(key);showPreview();});
      field(key+'_header_row').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
    }
    for(const [key,names] of Object.entries(mappingFields))for(const name of names)field(name).addEventListener('change',()=>columnsChanged(key));
    const result={setSamples(records){
      const bySample=new Map(),order=['IGH','IGK','IGL','TRA','TRB','TRD','TRG'];
      for(const row of records){
        const sample=typeof row==='string'?row:row.sample_label||row.sample_id||row.sample;
        if(!sample)continue;
        if(!bySample.has(sample))bySample.set(sample,new Set());
        const chains=typeof row==='string'?[]:row.chains||[row.chain];
        for(const chain of chains)if(order.includes(chain))bySample.get(sample).add(chain);
      }
      const next=[...bySample].sort(([a],[b])=>a.localeCompare(b)).map(([sample,chains])=>({sample,chains:order.filter(chain=>chains.has(chain))}));
      if(JSON.stringify(next)!==JSON.stringify(allocationSamples)){allocationSamples=next;renderAllocation();}
    },value(allowUnassigned=false){
      if(pending)throw Error('请等待工作簿预览加载完成');
      if(!uploads.submission&&!uploads.primer){if([...root.querySelectorAll('[data-rp-upload]')].some(input=>input.files.length))throw Error('数据表尚未成功读取，请重新上传后选择对应列');return {};}
      if(!uploads.primer||(!manualMode()&&!uploads.submission))throw Error(manualMode()?'请上传引物表':'请同时上传 Submission 和引物表');
      for(const key of manualMode()?['primer']:['submission','primer']){if(!readyBook(key))throw Error('请等待工作表和表头读取完成，读取失败时请重新上传');selected(key,true);}
      const columns={};root.querySelectorAll('[data-rp-field]').forEach(input=>columns[input.dataset.rpField]=input.type==='checkbox'?input.checked:input.value.trim());
      columns.primer_columns=pairs();
      if(manualMode()){
        if(!allowUnassigned&&(!optionsReady||!allocationSamples.length))throw Error('请扫描样本、读取引物组并完成分配');
        if(!allowUnassigned&&allocationSamples.some(({sample,chains})=>!chains.length||chains.some(chain=>!sampleGroups[sample]?.[chain]?.length)))throw Error('请为每个选中样本的每条链选择至少一个引物组');
        columns.sample_chain_groups=Object.fromEntries(allocationSamples.map(({sample,chains})=>[sample,Object.fromEntries(chains.map(chain=>[chain,sampleGroups[sample]?.[chain]||[]]))]));
      }
      if(!columns.primer_match_threshold)throw Error('请填写引物唯一匹配率阈值');
      ['submission_header_row','primer_header_row','umi_length','barcode_length','primer_match_threshold'].forEach(k=>columns[k]=Number(columns[k]));
      if(!Number.isFinite(columns.primer_match_threshold)||columns.primer_match_threshold<0||columns.primer_match_threshold>100)throw Error('引物唯一匹配率阈值须为 0–100 的数字');
      return {submission_id:manualMode()?null:uploads.submission,primer_id:uploads.primer,columns};
    },reset(){generation++;optionsEpoch++;optionsReady=false;sampleGroups={};allocationSamples=[];groupChoices=[];primerChoices=[];$('[data-rp-assignment-mode]').value='manual';Object.keys(uploads).forEach(k=>delete uploads[k]);Object.keys(books).forEach(k=>delete books[k]);root.querySelectorAll('input[type=file]').forEach(i=>i.value='');for(const key of ['submission','primer'])resetSheet(key);previewKey='submission';showPreview();renderAllocation();$('[data-rp-selected-columns]').open=false;error.classList.add('hidden');}};
    for(const key of ['submission','primer'])clearColumns(key);showPreview();renderAllocation();
    if(root.closest('#new-job-form'))document.addEventListener('scigblast:directory-samples',event=>result.setSamples(event.detail));
    for(const name of ['sequence_mode','adapter','umi_length','barcode_length'])field(name).addEventListener('change',invalidateGroups);
    const structureOptions=()=>{$('[data-rp-structure-settings]').hidden=!['sequence_mode','read_structure','mapping_read_structure'].some(name=>field(name).value==='oligo');};
    for(const name of ['sequence_mode','read_structure','mapping_read_structure'])field(name).addEventListener('change',structureOptions);
    structureOptions();
    configs.set(root,result);return result;
  }
  function table(rows,columns,{groupBySample=false}={}){
    if(!rows.length)return '<p class="muted">暂无数据</p>';
    columns=(columns||Object.keys(rows[0])).filter(key=>!['reference_id','reference_scope','aa_field','level','unit','previous_unit','reduction_unit'].includes(key));
    const cell=v=>v===null||v===undefined?'—':typeof v==='object'?JSON.stringify(v):typeof v==='number'?Number.isInteger(v)?v.toLocaleString():v.toFixed(3):String(v);
    const labels={sample:'样本',sample_key:'样本标识',pair:'配对',chain:'链',scope:'数据范围',subset:'统计范围',primer:'引物',segment:'基因区',gene:'基因',count:'数量',denominator:'分母',percent:'比例 (%)',stage:'阶段',metric:'指标',unit:'单位',note:'说明',match_class:'匹配类别',reason:'注释/过滤状态',unique_count:'唯一基因注释',ambiguous_count:'歧义候选记录',reference_catalog:'当次参考库收录',status:'状态',sequence_id:'序列 ID',primers:'本次统计的引物集合',mapping_primers:'mapping 前记录的引物归属',trim_state:'mapping 前引物处理',trim_length:'去除碱基数',filter_reason:'注释/过滤状态',total_reads:'Raw R1 reads',matched_reads:'唯一匹配 reads',match_percent:'唯一匹配率 (%)',tested_group:'其他引物组',unique_percent:'唯一匹配率 (%)'};
    Object.assign(labels,{state_denominator:'该引物同处理状态的分母',state_percent:'同处理状态内比例 (%)',label:'阶段',pair_count:'配对数',pair_scope:'配对范围',previous_count:'上一步数量',previous_unit:'上一步单位',reduction:'较上一步减少',reduction_unit:'减少量单位',retention_percent:'保留 / 合并率 (%)',rate_basis:'比例口径'});
    if(groupBySample)columns=columns.filter(key=>key!=='sample');
    const renderRow=row=>`<tr>${columns.map(c=>`<td${typeof row[c]==='number'?' class="rp-number"':''}>${esc(cell(row[c]))}</td>`).join('')}</tr>`;
    let body='';
    if(groupBySample){
      const groups=new Map();
      for(const row of rows.slice(0,300)){const sample=row.sample||'未记录样本';if(!groups.has(sample))groups.set(sample,[]);groups.get(sample).push(row);}
      let index=0;
      for(const [sample,values] of groups){
        index++;
        body+=`<tbody class="rp-table-sample" data-sample="${esc(sample)}"><tr class="rp-sample-divider"><th scope="rowgroup" colspan="${columns.length}"><div class="rp-table-sample-heading"><span class="rp-sample-index">${String(index).padStart(2,'0')}</span><strong>${esc(sample)}</strong><small>${values.length} 项</small></div></th></tr>${values.map(renderRow).join('')}</tbody>`;
      }
    }else body=`<tbody>${rows.slice(0,300).map(renderRow).join('')}</tbody>`;
    return `<div class="rp-table-wrap"><table class="rp-data-table"><thead><tr>${columns.map(c=>`<th scope="col">${esc(labels[c]||c)}</th>`).join('')}</tr></thead>${body}</table></div>${rows.length>300?'<p class="muted">页面显示前 300 行；完整统计请下载表格。</p>':''}`;
  }
  const statusName={queued:'排队中',running:'生成中',succeeded:'已完成',failed:'失败',cancelled:'已取消',cancelling:'正在取消',interrupted:'已中断'};
  function mount(root,{jobId=''}={}){
    const $=q=>root.querySelector(q),cfg=configPanel($('.rp-config'));
    let job=null,scan=null,runs=[],run=null,data=null,recordsAfter=0,busy=false,loading=false,timer=null,selection=new Set(),loadedId='',setupTouched=false,revision=0;
    let sampleContext='';
    let primerCheck=null,checkEpoch=0,checkTimer=null;
    const message=(text,bad=false)=>{const node=$('[data-rp-message]');node.textContent=text;node.classList.toggle('page-error',bad);node.dataset.tone=bad?'error':'info';};
    const setSetup=open=>{$('[data-rp-setup]').hidden=!open;$('[data-rp-open]').setAttribute('aria-expanded',String(open));$('[data-rp-open]').textContent=open?'收起配置':'生成报告';};
    const fileURL=(name,download=false)=>`/api/reports/${encodeURIComponent(run.id)}/file?name=${encodeURIComponent(name)}${download?'&download=true':''}`;
    function body(){if(!scan)throw Error('请先扫描已有结果');if(!selection.size)throw Error('请至少选择一项样本链结果');return {scan_id:scan.scan_id,selected_ids:[...selection],config:cfg.value(),comparison_species:$('[data-rp-comparison-species]').value,primer_check_id:primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked?primerCheck.id:''};}
    function updateGenerate(){
      let configured=false,valid=true;
      try{configured=Boolean(cfg.value().primer_id);}catch{valid=false;}
      $('[data-rp-run]').disabled=!selection.size||!valid||configured&&!(primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked);
    }
    function invalidateCheck(){
      const previous=Boolean(primerCheck);
      if(['queued','running'].includes(primerCheck?.status))api(`/api/reports/${primerCheck.id}/cancel`,{}).catch(()=>{});
      checkEpoch++;clearTimeout(checkTimer);primerCheck=null;
      $('[data-rp-check-confirm]').checked=false;$('[data-rp-check-confirm-label]').hidden=true;$('[data-rp-check-results]').hidden=true;
      const status=$('[data-rp-check-status]');status.dataset.tone='';
      status.textContent=previous?'配置或样本已变化，请重新运行预检查。':'选择分析结果并完成引物配置后开始。';
      $('[data-rp-check]').disabled=false;updateGenerate();
    }
    for(const event of ['input','change'])$('.rp-config').addEventListener(event,invalidateCheck);
    $('[data-rp-check-confirm]').onchange=updateGenerate;
    function checkResults(summary){
      const format=value=>value===null||value===undefined?'N/A':Number(value).toFixed(2)+'%';
      const states={ok:'达到阈值',low:'低于阈值',unavailable:'不可计算',review:'需核对'};
      const colors={unique:'#3A7DBF',multiple:'#E6A04B',unmatched:'#ADB7C3'};
      const node=$('[data-rp-check-results]');node.hidden=false;node.className='rp-check-results';
      const rows=summary.checks.map(row=>{
        const groups=Object.entries(row.groups).map(([chain,names])=>`<span><strong>${esc(chain)}</strong> · ${names.map(esc).join('、')}</span>`).join('');
        const bars=Object.entries(colors).map(([key,color])=>`<i style="width:${Math.max(0,Math.min(100,Number(row[key+'_percent'])||0))}%;background:${color}"></i>`).join('');
        return `<tr><td>${esc(row.sample)}</td><td class="rp-check-groups">${groups}</td><td class="rp-check-number">${row.total_reads===null?'N/A':Number(row.total_reads).toLocaleString()}</td><td><div class="rp-check-composition" aria-hidden="true">${bars}</div><strong>唯一 ${format(row.unique_percent)}</strong><small> · 多重 ${format(row.multiple_percent)} · 未匹配 ${format(row.unmatched_percent)}</small></td><td><span class="rp-check-badge" data-state="${esc(row.status)}">${esc(states[row.status]||row.status)}</span></td></tr>`;
      }).join('');
      const id=encodeURIComponent(primerCheck.id);
      const download=(name,label)=>`<a class="button button-ghost button-small" download="${esc(name)}" href="/api/reports/${id}/file?name=${encodeURIComponent(name)}&download=true">${label}</a>`;
      const suspected=summary.review.comparisons.filter(row=>row.status==='suspected');
      const sampleOrder=new Map(summary.checks.map((row,index)=>[row.sample,index]));
      const primerRows=[...summary.metrics.rows].sort((a,b)=>(sampleOrder.get(a.sample)??sampleOrder.size)-(sampleOrder.get(b.sample)??sampleOrder.size));
      node.innerHTML=`<div class="rp-check-legend">${Object.entries(colors).map(([key,color])=>`<span><i style="background:${color}"></i>${{unique:'唯一匹配',multiple:'多重匹配',unmatched:'未匹配'}[key]}</span>`).join('')}<span>唯一匹配率阈值 ${Number(summary.threshold)}%</span></div><div class="rp-table-wrap"><table class="rp-check-table"><thead><tr><th>样本</th><th>所选引物组</th><th>Raw R1 reads</th><th>匹配构成</th><th>核对提示</th></tr></thead><tbody>${rows}</tbody></table></div><div class="rp-check-downloads"><a class="button button-ghost button-small" download="primer_matching_report.html" href="/api/reports/${id}/report.html">下载匹配报告 HTML</a>${Object.values(summary.exports).map(name=>download(name,{'sample_matching.csv':'样本统计 CSV','chain_matching.csv':'分链统计 CSV','primer_matching.csv':'逐引物 CSV','alternative_groups.csv':'其他组测试 CSV'}[name]||name)).join('')}</div><details class="rp-check-details"><summary>查看逐引物匹配 · ${summary.metrics.rows.length} 项</summary>${table(primerRows,['sample','chain','primer','matched_reads','total_reads','match_percent'],{groupBySample:true})}</details>${suspected.length?`<details class="rp-check-details" open><summary>其他引物组 · Suspected</summary>${table(suspected,['sample','tested_group','unique_percent'],{groupBySample:true})}</details>`:''}`;
      $('[data-rp-check-confirm-label]').hidden=false;
    }
    async function pollCheck(epoch){
      if(epoch!==checkEpoch||!primerCheck)return;
      clearTimeout(checkTimer);
      try{
        const current=await api('/api/reports/'+encodeURIComponent(primerCheck.id));if(epoch!==checkEpoch)return;
        primerCheck=current;const status=$('[data-rp-check-status]');status.dataset.tone='';
        status.textContent={queued:'等待可用资源，预检查已排队。',running:'正在读取原始 _j 文件。',succeeded:'预检查完成，请核对所选引物组和匹配结果。'}[current.status]||current.error||statusName[current.status];
        if(['queued','running','cancelling'].includes(current.status)){
          if(current.status==='running'&&current.progress?.total){const progress=document.createElement('div');progress.className='rp-check-progress';progress.innerHTML=`<progress max="${Number(current.progress.total)}" value="${Number(current.progress.done)||0}"></progress><span>${Number(current.progress.done)||0} / ${Number(current.progress.total)}</span>`;status.append(progress);}
          const cancel=document.createElement('button');cancel.type='button';cancel.className='button button-ghost button-small';cancel.textContent='取消预检查';cancel.onclick=()=>action(cancel,async()=>{await api(`/api/reports/${current.id}/cancel`,{});await pollCheck(epoch);});status.append(' ',cancel);
          checkTimer=setTimeout(()=>pollCheck(epoch),2500);
        }else{
          $('[data-rp-check]').disabled=false;
          if(current.status==='succeeded'){const summary=await api(`/api/reports/${current.id}/summary`);if(epoch!==checkEpoch)return;checkResults(summary);}
          else status.dataset.tone='error';
        }
        updateGenerate();
      }catch(e){if(epoch!==checkEpoch)return;$('[data-rp-check-status]').textContent=e.message;$('[data-rp-check-status]').dataset.tone='error';$('[data-rp-check]').disabled=false;}
    }
    $('[data-rp-check]').onclick=e=>action(e.target,async()=>{
      const request=body();if(!request.config.primer_id)throw Error('请先上传引物表并分配引物组');
      invalidateCheck();const epoch=checkEpoch;
      $('[data-rp-check-status]').textContent='正在提交引物预检查…';
      const created=await api('/api/reports/primer-checks',request);if(epoch!==checkEpoch){if(['queued','running'].includes(created.status))api(`/api/reports/${created.id}/cancel`,{}).catch(()=>{});return;}
      primerCheck=created;
      if(['failed','cancelled','interrupted'].includes(created.status))primerCheck=await api(`/api/reports/${created.id}/retry`,{});
      await pollCheck(epoch);
    });
    async function action(button,fn){if(busy)return;busy=true;const old=button.textContent;button.disabled=true;button.setAttribute('aria-busy','true');try{await fn();}catch(e){message(e.message,true);}finally{busy=false;button.disabled=button.matches('[data-rp-check]')&&['queued','running','cancelling'].includes(primerCheck?.status);button.textContent=old;button.removeAttribute('aria-busy');updateGenerate();}}
    function traceOptions(){
      const options=(name,values)=>{const select=$(`[data-rp-${name}]`),previous=select.value;select.replaceChildren(new Option('全部',''),...[...new Set(values.filter(Boolean))].sort().map(v=>new Option(v,v)));if(values.includes(previous))select.value=previous;};
      options('sample',data.samples.map(r=>r.sample));options('chain',data.samples.map(r=>r.chain));
      const sample=$('[data-rp-sample]').value,chain=$('[data-rp-chain]').value;
      const groups=(data.usage?.groups||[]).filter(g=>(!sample||g.sample===sample)&&(!chain||g.chain===chain));
      options('primer',groups.flatMap(g=>g.primers||[]));
      const select=$('[data-rp-group]'),previous=select.value;
      select.replaceChildren(new Option('全部结果范围',''),...groups.map(g=>new Option(g.sample+' / '+g.chain+' / '+g.group_id.slice(0,8),g.group_id)));
      if(groups.some(g=>g.group_id===previous))select.value=previous;else if(groups.length===1)select.value=groups[0].group_id;
      $('[data-rp-group-label]').hidden=groups.length<2;
    }
    async function display(){
      const ticket=++revision;run=runs.find(r=>r.id===$('[data-rp-runs]').value)||null;
      $('[data-rp-results]').hidden=!run||run.status!=='succeeded'||run.archived;
      $('[data-rp-empty]').hidden=Boolean(run);
      if(!run){$('[data-rp-status]').replaceChildren();$('[data-rp-run-actions]').replaceChildren();return;}
      if(run.archived){$('[data-rp-status]').innerHTML='<p class="rp-state-notice">来源任务已归档，请从任务归档包获取报告。</p>';$('[data-rp-run-actions]').replaceChildren();return;}
      const phase=run.status==='succeeded'?'报告已生成':run.progress?.message||(run.status==='queued'?'等待可用资源':'');
      const progress=run.status==='running'&&run.progress?.total>0?`<div class="rp-run-progress"><progress max="${Number(run.progress.total)}" value="${Math.max(0,Math.min(Number(run.progress.total),Number(run.progress.done)||0))}" aria-label="报告生成进度"></progress><span>${Number(run.progress.done)||0} / ${Number(run.progress.total)}</span></div>`:'';
      $('[data-rp-status]').innerHTML=`<div class="rp-status" data-state="${esc(run.status)}"><span class="rp-status-badge">${esc(statusName[run.status]||run.status)}</span><span class="rp-status-description">${esc(phase)}</span><small>${esc(timeLabel(run.ended_at||run.created_at))}</small></div>${progress}${run.error?`<p class="page-error rp-run-error">${esc(run.error)}</p>`:''}`;
      $('[data-rp-run-actions]').innerHTML=(['queued','running'].includes(run.status)?'<button type="button" class="button button-ghost button-small" data-rp-cancel>取消</button>':['failed','cancelled','interrupted'].includes(run.status)?'<button type="button" class="button button-ghost button-small" data-rp-retry>重试</button>':'')+`<a class="button button-ghost button-small" download="analysis.log" href="${esc(fileURL('analysis.log',true))}">日志</a>`;
      const cancel=$('[data-rp-cancel]'),retry=$('[data-rp-retry]');
      if(cancel)cancel.onclick=()=>action(cancel,async()=>{await api(`/api/reports/${run.id}/cancel`,{});await load();});
      if(retry)retry.onclick=()=>action(retry,async()=>{await api(`/api/reports/${run.id}/retry`,{});loadedId='';await load();});
      if(run.status!=='succeeded')return;
      const id=encodeURIComponent(run.id);
      $('[data-rp-downloads]').innerHTML=`<a class="button button-primary" target="_blank" rel="noopener" href="/api/reports/${id}/report.html?download=false">打开报告 ↗</a><a class="button button-ghost" download="SCIGBLAST_report.html" href="/api/reports/${id}/report.html">轻量分享 HTML</a><a class="button button-ghost" download="report_${id}_check.zip" href="/api/reports/${id}/download?package=check">实验核查包 · HTML＋Excel</a><a class="button button-ghost" download="report.xlsx" href="${esc(fileURL('report.xlsx',true))}">Excel</a><a class="button button-ghost" download="report_${id}_png.zip" href="/api/reports/${id}/download?images_only=true">PNG 压缩包</a><a class="button button-ghost" download="report_${id}.zip" href="/api/reports/${id}/download">完整材料</a>`;
      if(loadedId!==run.id){
        const summary=await api(`/api/reports/${id}/summary?compact=true`);if(ticket!==revision)return;
        data=summary;loadedId=run.id;traceOptions();$('[data-rp-records]').replaceChildren();$('[data-rp-record-downloads]').replaceChildren();$('[data-rp-record-more]').hidden=true;
      }
      $('[data-rp-result-summary]').textContent=data.task_name||job?.dataset||'免疫组库分析报告';
      $('[data-rp-report-scope]').textContent=[...new Set(data.samples.map(r=>r.sample+' / '+r.chain))].join(' · ');
    }
    async function load(){
      if(loading)return;loading=true;
      try{const selected=$('[data-rp-runs]').value;runs=await api('/api/reports'+(jobId?'?job_id='+encodeURIComponent(jobId):''));
        $('[data-rp-runs]').innerHTML=runs.length?runs.map(r=>`<option value="${esc(r.id)}">${esc(timeLabel(r.created_at))} · ${esc(statusName[r.status])} · ${esc(r.id.slice(0,8))}</option>`).join(''):'<option value="">暂无报告</option>';
        if(runs.some(r=>r.id===selected))$('[data-rp-runs]').value=selected;
        if(!jobId&&!setupTouched)setSetup(!runs.length);await display();
      }catch(e){message(e.message,true);}finally{loading=false;clearTimeout(timer);if(runs.some(r=>['queued','running','cancelling'].includes(r.status)))timer=setTimeout(()=>{if(!document.hidden&&!root.closest('[hidden]'))load();else timer=setTimeout(load,6000);},4000);}
    }
    $('[data-rp-open]').onclick=()=>{setupTouched=true;setSetup($('[data-rp-setup]').hidden);};$('[data-rp-refresh]').onclick=load;
    $('[data-rp-scan]').onclick=e=>action(e.target,async()=>{
      invalidateCheck();scan=null;selection.clear();cfg.setSamples([]);$('[data-rp-candidates]').replaceChildren();updateGenerate();
      scan=await api('/api/reports/scan',jobId?{job_id:jobId}:{input_path:$('[data-rp-path]').value.trim()});selection=new Set(scan.items.map(r=>r.id));
      $('[data-rp-candidates]').innerHTML=`<div class="rp-candidate-heading"><strong>样本与链</strong><span data-rp-selection-count></span><div class="rp-actions"><button type="button" class="button button-ghost button-small" data-rp-select-all>全选</button><button type="button" class="button button-ghost button-small" data-rp-select-none>清空</button></div></div><div class="rp-candidates">${scan.items.map(r=>`<label class="rp-candidate-row"><input type="checkbox" data-rp-item="${esc(r.id)}" checked><span class="rp-candidate-sample"><strong>${esc(r.sample_label)}</strong>${r.pair&&r.pair!==r.sample_label?`<span>${esc(r.pair)}</span>`:''}<small>${esc(r.dataset||'')}</small></span><span class="rp-chain-tag">${esc(r.chain)}</span></label>`).join('')}</div>`;
      const update=()=>{$('[data-rp-selection-count]').textContent=selection.size+' / '+scan.items.length;cfg.setSamples(scan.items.filter(r=>selection.has(r.id)));invalidateCheck();};
      $('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>input.onchange=()=>{if(input.checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);update();});
      for(const [key,checked] of [['all',true],['none',false]])$(`[data-rp-select-${key}]`).onclick=()=>{$('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>{input.checked=checked;if(checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);});update();};update();message('已识别结果，请选择样本并核对引物配置。');
    });
    $('[data-rp-preview]').onclick=e=>action(e.target,async()=>{const preview=jobId&&job?.status!=='SUCCEEDED'?await api(`/api/reports/plans/${encodeURIComponent(jobId)}/preview`,cfg.value()):await api('/api/reports/preview',body());$('[data-rp-allocation]').innerHTML=`<div class="rp-allocation">${(Object.keys(preview.chain_assignments||{}).length?Object.entries(preview.chain_assignments).flatMap(([sample,chains])=>Object.entries(chains).map(([chain,primers])=>[sample+' / '+chain,primers])):Object.entries(preview.assignments)).map(([sample,primers])=>`<div class="rp-assignment-row"><strong>${esc(sample)}</strong><span>${primers.map(p=>`<span class="rp-primer-tag">${esc(p.name)}<small>${esc(p.group)}</small></span>`).join('')}</span></div>`).join('')||'<p class="rp-inline-empty">未配置引物表。</p>'}</div>`;message('引物分配已更新。');});
    $('[data-rp-run]').onclick=e=>action(e.target,async()=>{const created=await api('/api/reports',body());setSetup(false);await load();$('[data-rp-runs]').value=created.id;await display();message('已加入报告生成队列。');});
    $('[data-rp-save-plan]').onclick=e=>action(e.target,async()=>{await api(`/api/reports/plans/${encodeURIComponent(jobId)}`,cfg.value(),'PUT');message('已保存，主分析完成后自动生成报告。');await load();});
    $('[data-rp-runs]').onchange=()=>{loadedId='';display().catch(e=>message(e.message,true));};
    for(const key of ['sample','chain'])$(`[data-rp-${key}]`).onchange=traceOptions;
    async function records(append=false){
      if(!run||!data)return;const id=run.id;
      const params=new URLSearchParams({match_class:$('[data-rp-record-class]').value,reason:$('[data-rp-record-reason]').value,trim_state:$('[data-rp-trim]').value,after:String(append?recordsAfter:0)});
      if($('[data-rp-group]').value)params.set('group_id',$('[data-rp-group]').value);
      for(const key of ['sample','chain','primer','gene','segment']){const value=$(`[data-rp-${key}]`).value.trim();if(value)params.set(key,value);}
      const result=await api(`/api/reports/${id}/records?${params}`);if(run?.id!==id)return;
      $('[data-rp-records]').innerHTML=table(result.rows);recordsAfter=result.next_cursor||0;$('[data-rp-record-more]').hidden=!result.has_more;params.delete('after');
      $('[data-rp-record-downloads]').innerHTML=`<a class="button button-ghost button-small" download="records_${esc(id)}.fasta" href="/api/reports/${encodeURIComponent(id)}/sequences?${esc(params.toString())}&format=fasta">序列 FASTA</a><a class="button button-ghost button-small" download="records_${esc(id)}.tsv" href="/api/reports/${encodeURIComponent(id)}/sequences?${esc(params.toString())}&format=tsv">关联表 TSV</a>`;
    }
    $('[data-rp-record-load]').onclick=e=>action(e.target,()=>records());$('[data-rp-record-more]').onclick=e=>action(e.target,()=>records(true));
    const instance={update(value){job=value;if(!jobId)return;
      const context=[job.attempt_no,job.status,job.current_stage].join(':');
      if(context!==sampleContext){
        sampleContext=context;
        if(job.input_assignments?.length)cfg.setSamples(job.input_assignments);
        api('/api/reports/plans/'+encodeURIComponent(jobId)+'/samples').then(data=>{if(sampleContext===context&&data.items.length)cfg.setSamples(data.items);}).catch(()=>{});
      }
const supported=['igblast_base','pig_igblast'].includes(job.pipeline);root.hidden=!supported;if(!supported)return;root.querySelector('.rp-source').hidden=true;const canPlan=!['SUCCEEDED','ARCHIVED','ARCHIVING','DELETING'].includes(job.status);$('[data-rp-save-plan]').hidden=!canPlan;$('[data-rp-check-panel]').hidden=canPlan;$('[data-rp-scan]').disabled=canPlan;$('[data-rp-source-note]').textContent=canPlan?'保存引物配置，主分析完成后自动生成报告。':'扫描当前结果，可补充配置并生成新版报告。';},load};
    if(!jobId){const params=new URLSearchParams(location.search);if(params.get('job_id'))location.href='/jobs/'+encodeURIComponent(params.get('job_id'))+'#primer';}
    instances.push(instance);load();return instance;
  }
  document.addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('[data-report-panel]').forEach(root=>mount(root,{jobId:window.SCIGBLAST_JOB_ID||''}));document.querySelectorAll('.rp-config').forEach(configPanel);});
  return {configPanel,update:job=>instances.forEach(panel=>panel.update(job)),load:()=>instances.forEach(panel=>panel.load())};
})();

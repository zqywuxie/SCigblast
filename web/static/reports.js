/* Report creation, history. Visualizations live in report.html. */
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
    const guided=root.hasAttribute('data-rp-guided-config');
    let configPage='table',allocationChain='',allocationQuery='',onlyMissing=false;
    function showConfigPage(page){
      configPage=page;
      if(!guided)return;
      root.querySelectorAll('[data-rp-config-page]').forEach(node=>node.hidden=node.dataset.rpConfigPage!==page);
      root.querySelectorAll('[data-rp-config-tab]').forEach(button=>{
        const active=button.dataset.rpConfigTab===page;
        button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
      });
      root.dispatchEvent(new Event('scigblast:primer-page',{bubbles:true}));
    }
    function updateConfigState(){
      if(!guided)return;
      const total=allocationSamples.reduce((sum,row)=>sum+row.chains.length,0);
      const assigned=allocationSamples.reduce((sum,row)=>sum+row.chains.filter(chain=>sampleGroups[row.sample]?.[chain]?.length).length,0);
      $('[data-rp-allocation-count]').textContent=total?assigned+' / '+total:'';
      const chainRows=allocationSamples.filter(row=>row.chains.includes(allocationChain));
      const count=$('[data-rp-chain-count]');
      if(count)count.textContent=chainRows.length+' 个样本 · 未配置 '+chainRows.filter(row=>!sampleGroups[row.sample]?.[allocationChain]?.length).length;

      const tab=$('[data-rp-config-tab="allocation"]');tab.hidden=!manualMode();tab.disabled=!optionsReady;
      const button=$('[data-rp-to-allocation]');
      let tableReady=Boolean(uploads.primer&&readyBook('primer')&&(manualMode()||uploads.submission&&readyBook('submission')));
      if(tableReady){
        try{selected('primer',true);if(!manualMode())selected('submission',true);}
        catch{tableReady=false;}
      }
      button.disabled=Boolean(pending)||!tableReady;
      button.textContent=manualMode()?'读取引物组并分配 →':'确认配置，继续预检查 →';
      $('[data-rp-table-status]').textContent=optionsReady?groupChoices.length+' 个引物组可用':tableReady?'对应列已完整，请核对预览内容。':'上传引物表并选择分组、名称和序列列。';
    }

    const manualMode=()=>$('[data-rp-assignment-mode]').value==='manual';
    function renderAllocation(){
      root.dataset.assignmentMode=manualMode()?'manual':'submission';
      $('[data-rp-manual-allocation]').hidden=!manualMode();
      $('[data-rp-file-card="submission"]').hidden=manualMode();
      $('[data-rp-map-group="submission"]').hidden=manualMode()||!readyBook('submission');
      root.querySelectorAll('[data-rp-submission-setting]').forEach(node=>node.hidden=manualMode());
      const names=field('allow_primer_name_join').checked?primerChoices:[];
      const choices=[...groupChoices.map(name=>({name,label:name})),...names.filter(name=>!groupChoices.includes(name)).map(name=>({name,label:'引物 · '+name}))];
      const chainOrder=['IGH','IGK','IGL','TRA','TRB','TRD','TRG'];
      const availableChains=chainOrder.filter(chain=>allocationSamples.some(row=>row.chains.includes(chain)));
      const batch=choices.length&&availableChains.length?'<div class="rp-allocation-batch"><label>处理链<select data-rp-batch-chain>'+availableChains.map(chain=>'<option>'+chain+'</option>').join('')+'</select></label><label>引物组<select data-rp-batch-group>'+choices.map(choice=>'<option value="'+esc(choice.name)+'">'+esc(choice.label)+'</option>').join('')+'</select></label><button type="button" class="button button-ghost button-small" data-rp-batch-apply>批量添加到该链</button></div>':'';
      if(guided){renderGuidedAllocation(choices,availableChains);updateConfigState();return;}
      $('[data-rp-sample-groups]').innerHTML=batch+allocationSamples.map(({sample,chains})=>{
        const rows=chains.map(chain=>{
          const chosen=sampleGroups[sample]?.[chain]||[];
          return `<details class="rp-sample-allocation"><summary><strong>${esc(chain)}</strong><span data-rp-chosen>${chosen.length?chosen.map(esc).join('、'):'选择引物组'}</span></summary><div class="rp-group-choices">${choices.map(choice=>`<label><input type="checkbox" data-rp-group-sample="${esc(sample)}" data-rp-group-chain="${esc(chain)}" value="${esc(choice.name)}" ${chosen.includes(choice.name)?'checked':''}>${esc(choice.label)}</label>`).join('')||'<p class="muted">请先读取可选引物组。</p>'}</div></details>`;
        }).join('');
        return `<section class="rp-sample-block"><header><strong>${esc(sample)}</strong><small>${chains.length} 条链</small></header>${rows||'<p class="muted">请先指定处理链或完成 Match。</p>'}</section>`;
      }).join('')||'<p class="muted">扫描样本或完成 Match 后，在此分配引物组。</p>';
    }
    function renderGuidedAllocation(choices,chains){
      if(!chains.includes(allocationChain))allocationChain=chains[0]||'';
      const records=allocationSamples.filter(row=>row.chains.includes(allocationChain));
      const missing=records.filter(row=>!sampleGroups[row.sample]?.[allocationChain]?.length).length;
      const chainOptions=chains.map(chain=>'<option '+(chain===allocationChain?'selected':'')+'>'+chain+'</option>').join('');
      const groupOptions=choices.map(choice=>'<option value="'+esc(choice.name)+'">'+esc(choice.label)+'</option>').join('');
      const batch=[
        '<div class="rp-allocation-batch">',
        '<label>处理链<select data-rp-batch-chain>'+chainOptions+'</select></label>',
        '<label>统一添加引物组<select data-rp-batch-group>'+groupOptions+'</select></label>',
        '<button type="button" class="button button-primary button-small" data-rp-batch-apply '+(!choices.length?'disabled':'')+'>应用到本链全部样本</button>',
        '</div>'
      ].join('');
      const controls=[
        '<div class="rp-allocation-tools">',
        '<label><input type="search" data-rp-allocation-query aria-label="搜索需要分配引物的样本" placeholder="搜索样本" value="'+esc(allocationQuery)+'"></label>',
        '<label class="rp-missing-toggle"><input type="checkbox" data-rp-only-missing '+(onlyMissing?'checked':'')+'>只看未配置</label>',
        '<span data-rp-chain-count>'+records.length+' 个样本 · 未配置 '+missing+'</span></div>'
      ].join('');
      const rows=records.map(({sample})=>{
        const selected=sampleGroups[sample]?.[allocationChain]||[];
        const choicesHtml=choices.map(choice=>[
          '<label><input type="checkbox" data-rp-group-sample="'+esc(sample)+'" data-rp-group-chain="'+allocationChain+'" ',
          'value="'+esc(choice.name)+'" '+(selected.includes(choice.name)?'checked':'')+'>',
          esc(choice.label)+'</label>'
        ].join('')).join('');
        return [
          '<tr data-rp-allocation-row="'+esc(sample)+'" data-assigned="'+Boolean(selected.length)+'">',
          '<th scope="row">'+esc(sample)+'</th>',
          '<td><span class="rp-chain-tag">'+allocationChain+'</span></td>',
          '<td><span data-rp-row-state class="rp-assignment-state" data-ready="'+Boolean(selected.length)+'">'+(selected.length?'已配置':'未配置')+'</span></td>',
          '<td><details class="rp-assignment-editor"><summary>',
          '<span data-rp-chosen>'+esc(selected.join('、')||'选择引物组')+'</span><span class="rp-edit-label">修改</span></summary>',
          '<label class="rp-choice-search"><input type="search" data-rp-choice-query placeholder="搜索组名或引物" aria-label="搜索可选引物组"></label>',
          '<div class="rp-group-choices">'+choicesHtml+'</div></details></td></tr>'
        ].join('');
      }).join('');
      const table=[
        '<div class="rp-assignment-table-wrap"><table class="rp-assignment-table">',
        '<thead><tr><th>样本</th><th>链</th><th>状态</th><th>引物组</th></tr></thead>',
        '<tbody>'+rows+'</tbody></table>',
        '<p class="rp-search-empty" data-rp-allocation-empty hidden>没有符合条件的样本。</p></div>'
      ].join('');
      $('[data-rp-sample-groups]').innerHTML=batch+controls+table;
      filterAllocation();
    }
    function filterAllocation(){
      let visible=0;
      root.querySelectorAll('[data-rp-allocation-row]').forEach(row=>{
        row.hidden=!row.dataset.rpAllocationRow.toLocaleLowerCase().includes(allocationQuery.toLocaleLowerCase())||onlyMissing&&row.dataset.assigned==='true';
        if(!row.hidden)visible++;
      });
      const empty=$('[data-rp-allocation-empty]');if(empty)empty.hidden=visible>0;
    }
    $('[data-rp-sample-groups]').addEventListener('input',event=>{
      if(event.target.matches('[data-rp-choice-query]')){
        const query=event.target.value.trim().toLocaleLowerCase();
        event.target.closest('details').querySelectorAll('.rp-group-choices>label').forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase().includes(query));
        return;
      }
      if(event.target.matches('[data-rp-allocation-query]')){allocationQuery=event.target.value;filterAllocation();}
    });
    $('[data-rp-sample-groups]').addEventListener('toggle',event=>{
      const editor=event.target;if(!editor.matches('.rp-assignment-editor'))return;
      if(editor.open)root.querySelectorAll('.rp-assignment-editor[open]').forEach(other=>{if(other!==editor)other.open=false;});
      else if(onlyMissing)filterAllocation();
    },true);
    $('[data-rp-sample-groups]').addEventListener('click',event=>{
      if(!event.target.closest('[data-rp-batch-apply]'))return;
      const chain=$('[data-rp-batch-chain]').value,group=$('[data-rp-batch-group]').value;
      for(const row of allocationSamples){
        if(!row.chains.includes(chain))continue;
        const chosen=new Set(sampleGroups[row.sample]?.[chain]||[]);chosen.add(group);
        (sampleGroups[row.sample]??={})[chain]=[...chosen];
      }
      renderAllocation();
      root.dispatchEvent(new Event('change',{bubbles:true}));
    });
    $('[data-rp-sample-groups]').addEventListener('change',event=>{
      if(guided&&event.target.matches('[data-rp-batch-chain]')){allocationChain=event.target.value;renderAllocation();return;}
      if(event.target.matches('[data-rp-only-missing]')){onlyMissing=event.target.checked;filterAllocation();return;}
      const input=event.target.closest('[data-rp-group-sample]');if(!input)return;
      const sample=input.dataset.rpGroupSample,chain=input.dataset.rpGroupChain,values=new Set(sampleGroups[sample]?.[chain]||[]);
      if(input.checked)values.add(input.value);else values.delete(input.value);
      (sampleGroups[sample]??={})[chain]=[...values];
      input.closest('details').querySelector('[data-rp-chosen]').textContent=[...values].join('、')||'选择引物组';
      const row=input.closest('[data-rp-allocation-row]');
      if(row){row.dataset.assigned=String(values.size>0);const status=row.querySelector('[data-rp-row-state]');status.dataset.ready=String(values.size>0);status.textContent=values.size?'已配置':'未配置';}
      updateConfigState();

    });
    $('[data-rp-assignment-mode]').addEventListener('change',renderAllocation);
    field('allow_primer_name_join').addEventListener('change',()=>{
      if(!field('allow_primer_name_join').checked)pruneGroups(new Set(groupChoices));
      renderAllocation();
    });
    function pruneGroups(allowed){
      for(const chains of Object.values(sampleGroups))for(const chain of Object.keys(chains))chains[chain]=chains[chain].filter(name=>allowed.has(name));
    }
    function invalidateGroups(){optionsReady=false;optionsEpoch++;groupChoices=[];primerChoices=[];if(guided)showConfigPage('table');renderAllocation();}
    async function loadGroups(){
      const button=$('[data-rp-load-groups]');button.disabled=true;
      try{
        const body=result.value(true),epoch=optionsEpoch;
        if(!body.primer_id)throw Error('请上传引物表并选择分组、名称和序列列。');
        const choices=await api('/api/reports/primer-options',body);
        if(epoch!==optionsEpoch)return false;
        groupChoices=choices.groups;primerChoices=choices.primers;optionsReady=true;
        pruneGroups(new Set([...groupChoices,...primerChoices]));
        renderAllocation();error.classList.add('hidden');root.dispatchEvent(new Event('change',{bubbles:true}));return true;
      }finally{button.disabled=false;}
    }
    $('[data-rp-load-groups]').onclick=()=>loadGroups().catch(fail);
    if(guided){
      root.querySelectorAll('[data-rp-config-tab]').forEach(button=>{
        const name=button.dataset.rpConfigTab;
        button.id=previewId+'-config-'+name;button.setAttribute('aria-controls',button.id+'-panel');
        const panel=$('[data-rp-config-page="'+name+'"]');
        panel.id=button.id+'-panel';panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);
        button.onclick=()=>showConfigPage(name);
        button.onkeydown=event=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
          event.preventDefault();
          const buttons=[...root.querySelectorAll('[data-rp-config-tab]')].filter(item=>!item.disabled&&!item.hidden);
          const index=buttons.indexOf(button);
          const next=event.key==='Home'?buttons[0]:event.key==='End'?buttons.at(-1):buttons[(index+1)%buttons.length];
          next?.click();next?.focus({preventScroll:true});
        };
      });
      $('[data-rp-to-allocation]').onclick=async()=>{
        try{
          if(manualMode()){if(await loadGroups()){showConfigPage('allocation');root.scrollIntoView({block:'start'});}}
          else{result.value();root.dispatchEvent(new Event('scigblast:primer-config-ready',{bubbles:true}));}
        }catch(error){fail(error);}
      };
    }
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
    function suggestColumns(key){
      if(!guided)return;
      const book=books[key],header=headerRow(key)-1;
      const titles=Array.from({length:book.column_count},(_,index)=>String(cell(book,header,index).value).toLowerCase().replace(/[\s_'’".()（）\-:]/g,''));
      const unique=pattern=>{const found=titles.map((value,index)=>pattern.test(value)?index:-1).filter(index=>index>=0);return found.length===1?columnName(found[0]):'';};
      if(key==='submission'){
        field('sample_column').value=unique(/^(sample|sampleid|samplename|样本|样本名|样本名称)$/);
        field('submission_join_column').value=unique(/^(group|groupname|primergroup|primerdesign|chain|引物组|引物设计|分组|链)$/);
        return;
      }
      field('primer_join_column').value=unique(/^(group|groupname|primergroup|chain|引物组|分组|链)$/);
      const inferred=[];
      for(let i=0;i<titles.length-1;i++){
        if(/^(name|primername|引物名|引物名称)$/.test(titles[i])&&(/^(seq|sequence|primersequence)$/.test(titles[i+1])||titles[i+1].includes('序列')))inferred.push([columnName(i),columnName(i+1)]);
      }
      for(const [index,pair] of inferred.slice(0,12).entries()){
        if(index)addPair();
        const row=$('[data-rp-primer-pairs]').children[index];row.querySelector('[data-rp-pair-name]').value=pair[0];row.querySelector('[data-rp-pair-sequence]').value=pair[1];
      }
      if(inferred.length)$('[data-rp-selected-columns]').open=false;
    }
    function populateColumns(key){
      if(headerRow(key)>books[key].row_count)throw Error('所选表头行超出工作表，请调整表头行');
      const options=columnOptions(key);
      mappingFields[key].forEach(name=>{field(name).innerHTML=options;field(name).disabled=false;});
      if(key==='primer'){$('[data-rp-primer-pairs]').replaceChildren();addPair();}
      suggestColumns(key);
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
      finally{pending--;updateConfigState();root.dispatchEvent(new Event('change',{bubbles:true}));}
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
      finally{pending--;if(revision===generation&&epoch===revisions[key])field(key+'_sheet').disabled=false;updateConfigState();root.dispatchEvent(new Event('change',{bubbles:true}));}
    }));
    for(const key of ['submission','primer']){
      field(key+'_sheet').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
      field(key+'_header_row').addEventListener('input',()=>{if(uploads[key])revisions[key]++;delete books[key];clearColumns(key);showPreview();});
      field(key+'_header_row').addEventListener('change',()=>{previewKey=key;refreshBook(key);});
    }
    for(const [key,names] of Object.entries(mappingFields))for(const name of names)field(name).addEventListener('change',()=>columnsChanged(key));
    const result={page(){return guided&&manualMode()?configPage:'';},back(){
      if(guided&&configPage==='allocation'){showConfigPage('table');root.scrollIntoView({block:'start'});return true;}
      return false;
    },async advance(){
      if(guided&&manualMode()&&configPage==='table'){
        if(await loadGroups()){showConfigPage('allocation');root.scrollIntoView({block:'start'});}
        return false;
      }
      result.value();return true;
    },setSamples(records){
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
    structureOptions();updateConfigState();
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
    let job=null,scan=null,runs=[],run=null,data=null,busy=false,loading=false,timer=null,selection=new Set(),loadedId='',setupTouched=false,revision=0;
    let sampleContext='',step=1,planMode=false,scanEpoch=0;
    const reportConfig=()=>$('[data-rp-use-primer]').checked?cfg.value():{};
    let primerCheck=null,checkEpoch=0,checkTimer=null;
    const message=(text,bad=false)=>{const node=$('[data-rp-message]');node.textContent=text;node.classList.toggle('page-error',bad);node.dataset.tone=bad?'error':'info';};
    function setSetup(open){
      $('[data-rp-setup]').hidden=!open;
      $('[data-rp-open]').setAttribute('aria-expanded',String(open));
      $('[data-rp-open]').textContent=open?'收起配置':'生成报告';
      root.dataset.configuring=String(open);
    }
    function showStep(next,focus=false){
      step=next;
      root.querySelectorAll('[data-rp-step-pane]').forEach(pane=>{
        pane.hidden=planMode?!['2','4'].includes(pane.dataset.rpStepPane):Number(pane.dataset.rpStepPane)!==step;
      });
      root.querySelectorAll('[data-rp-step]').forEach(button=>{
        if(Number(button.dataset.rpStep)===step)button.setAttribute('aria-current','step');
        else button.removeAttribute('aria-current');
        button.dataset.complete=String(Number(button.dataset.rpStep)<step);
      });
      $('[data-rp-workflow]').hidden=planMode;
      $('[data-rp-workflow-footer]').hidden=planMode;
      $('[data-rp-back]').hidden=step===1;
      $('[data-rp-next]').hidden=step===4;
      $('[data-rp-step-caption]').textContent='第 '+step+' / 4 步';
      $('[data-rp-step-feedback]').textContent='';
      updateGenerate();
      if(focus){const pane=$('[data-rp-step-pane="'+step+'"]');pane.tabIndex=-1;pane.focus({preventScroll:true});pane.scrollIntoView({block:'nearest'});}
    }
    function moveStep(next){
      try{
        if(next>1&&!selection.size)throw Error('请扫描结果，并至少选择一个样本链。');
        if(next>2&&$('[data-rp-use-primer]').checked&&!reportConfig().primer_id)throw Error('请上传引物表并完成引物组分配。');
        if(next>3&&$('[data-rp-use-primer]').checked&&!(primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked))throw Error('请完成引物预检查，并勾选核对确认。');
        showStep(next,true);
      }catch(error){$('[data-rp-step-feedback]').textContent=error.message;}
    }
    root.querySelectorAll('[data-rp-step]').forEach(button=>button.onclick=()=>moveStep(Number(button.dataset.rpStep)));
    $('[data-rp-next]').onclick=async()=>{
      try{
        if(step===2&&$('[data-rp-use-primer]').checked&&!await cfg.advance())return;
        moveStep(step===2&&!$('[data-rp-use-primer]').checked?4:step+1);
      }catch(error){$('[data-rp-step-feedback]').textContent=error.message;}
    };
    $('.rp-config').addEventListener('scigblast:primer-config-ready',()=>moveStep(3));
    $('.rp-config').addEventListener('scigblast:primer-page',()=>updateGenerate());
    $('[data-rp-back]').onclick=()=>{
      if(step===2&&$('[data-rp-use-primer]').checked&&cfg.back())return;
      showStep(step===4&&!$('[data-rp-use-primer]').checked?2:step-1,true);
    };
    $('[data-rp-close]').onclick=()=>{setSetup(false);$('[data-rp-open]').focus({preventScroll:true});};
    $('.rp-config').hidden=true;
    $('[data-rp-use-primer]').onchange=()=>{
      const enabled=$('[data-rp-use-primer]').checked;
      $('.rp-config').hidden=!enabled;$('.rp-config').open=enabled;
      $('[data-rp-preview]').hidden=!enabled;
      invalidateCheck();
    };
    const fileURL=(name,download=false)=>`/api/reports/${encodeURIComponent(run.id)}/file?name=${encodeURIComponent(name)}${download?'&download=true':''}`;
    function body(){if(!scan)throw Error('请先扫描已有结果');if(!selection.size)throw Error('请至少选择一项样本链结果');return {scan_id:scan.scan_id,selected_ids:[...selection],config:reportConfig(),comparison_species:$('[data-rp-comparison-species]').value,primer_check_id:primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked?primerCheck.id:''};}
    function updateGenerate(){
      let configured=false,valid=true;
      try{configured=Boolean(reportConfig().primer_id);if($('[data-rp-use-primer]').checked&&!configured)valid=false;}catch{valid=false;}
      $('[data-rp-run]').disabled=!selection.size||!valid||configured&&!(primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked);
      $('[data-rp-run]').hidden=planMode;
      $('[data-rp-next]').textContent=step===2&&$('[data-rp-use-primer]').checked?(cfg.page()==='table'?'分配引物 →':'匹配预检查 →'):'下一步';

      $('[data-rp-preview]').disabled=!valid||!configured||!planMode&&!selection.size;
      $('[data-rp-check]').disabled=busy||!valid||!configured||!selection.size||['queued','running','cancelling'].includes(primerCheck?.status);
      $('[data-rp-check-panel]').hidden=planMode||!$('[data-rp-use-primer]').checked;
      $('[data-rp-no-primer-note]').hidden=$('[data-rp-use-primer]').checked;
      const chosen=(scan?.items||[]).filter(item=>selection.has(item.id));
      const samples=new Set(chosen.map(item=>item.sample_label));
      const summary=[['任务',job?.dataset||'已有分析结果'],['样本范围',planMode?'主分析完成后读取结果':samples.size+' 个样本 · '+selection.size+' 项链结果'],
        ['引物分析',configured?'已配置引物表':$('[data-rp-use-primer]').checked?'待完成配置':'未启用'],
        ['匹配预检查',planMode?'主分析完成后执行':configured?(primerCheck?.status==='succeeded'&&$('[data-rp-check-confirm]').checked?'已核对':'待核对'):'无需预检查'],
        ['输出内容','HTML 报告、PNG 与 Excel']];
      $('[data-rp-confirm-summary]').innerHTML=summary.map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join('');

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
    for(const event of ['input','change'])$('.rp-config').addEventListener(event,change=>{
      if(change.target.matches('[data-rp-allocation-query],[data-rp-only-missing],[data-rp-batch-chain],[data-rp-batch-group],[data-rp-choice-query]'))return;
      invalidateCheck();
    });
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
    async function action(button,fn){if(busy)return;busy=true;const old=button.textContent;button.disabled=true;button.setAttribute('aria-busy','true');try{await fn();}catch(e){message(e.message,true);if(button.matches('[data-rp-scan]'))$('[data-rp-candidates]').textContent='扫描失败，请点击扫描按钮重试。';}finally{busy=false;button.disabled=button.matches('[data-rp-check]')&&['queued','running','cancelling'].includes(primerCheck?.status);button.textContent=button.matches('[data-rp-scan]')&&scan?'重新扫描':old;button.removeAttribute('aria-busy');updateGenerate();}}
    async function display(){
      const ticket=++revision;run=runs.find(r=>r.id===$('[data-rp-runs]').value)||null;
      $('[data-rp-results]').hidden=!run||run.status!=='succeeded'||run.archived;
      $('[data-rp-empty]').hidden=Boolean(run);
      if(!run){$('[data-rp-status]').replaceChildren();$('[data-rp-run-actions]').replaceChildren();return;}
      if(run.archived){$('[data-rp-status]').innerHTML='<p class="rp-state-notice">来源任务已归档，请从任务归档包获取报告。</p>';$('[data-rp-run-actions]').replaceChildren();return;}
      const phase=({succeeded:'报告已生成',cancelled:'本次生成已取消，可重试或重新配置。',failed:'生成失败，可查看日志后重试。',interrupted:'生成已中断，可恢复或重新配置。'})[run.status]||run.progress?.message||(run.status==='queued'?'等待可用资源':'');
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
        data=summary;loadedId=run.id;
      }
      $('[data-rp-result-summary]').textContent=data.task_name||job?.dataset||'免疫组库分析报告';
      $('[data-rp-report-scope]').textContent=[...new Set(data.samples.map(r=>r.sample+' / '+r.chain))].join(' · ');
    }
    async function load(){
      if(loading)return;loading=true;
      try{const selected=$('[data-rp-runs]').value;runs=await api('/api/reports'+(jobId?'?job_id='+encodeURIComponent(jobId):''));
        $('[data-rp-runs]').innerHTML=runs.length?runs.map(r=>`<option value="${esc(r.id)}">${esc(timeLabel(r.created_at))} · ${esc(statusName[r.status])} · ${esc(r.id.slice(0,8))}</option>`).join(''):'<option value="">暂无报告</option>';
        if(runs.some(r=>r.id===selected))$('[data-rp-runs]').value=selected;
        else if(runs.length)$('[data-rp-runs]').value=(runs.find(r=>['queued','running','cancelling'].includes(r.status))||runs.find(r=>r.status==='succeeded')||runs[0]).id;
        if(!jobId&&!setupTouched)setSetup(!runs.length);await display();
      }catch(e){message(e.message,true);}finally{loading=false;clearTimeout(timer);if(runs.some(r=>['queued','running','cancelling'].includes(r.status)))timer=setTimeout(()=>{if(!document.hidden&&!root.closest('[hidden]'))load();else timer=setTimeout(load,6000);},4000);}
    }
    $('[data-rp-open]').onclick=()=>{
      setupTouched=true;const open=$('[data-rp-setup]').hidden;setSetup(open);
      if(open){showStep(step);$('[data-rp-setup]').scrollIntoView({block:'start'});if(jobId&&!planMode&&!scan&&!busy)$('[data-rp-scan]').click();}
    };$('[data-rp-refresh]').onclick=load;
    $('[data-rp-scan]').onclick=e=>action(e.target,async()=>{
      const epoch=++scanEpoch;
      invalidateCheck();scan=null;selection.clear();cfg.setSamples([]);$('[data-rp-candidates]').textContent='正在读取可用样本与链…';updateGenerate();
      const result=await api('/api/reports/scan',jobId?{job_id:jobId}:{input_path:$('[data-rp-path]').value.trim()});
      if(epoch!==scanEpoch)return;
      scan=result;selection=new Set(scan.items.map(r=>r.id));
      const samples=new Map();
      for(const item of scan.items){if(!samples.has(item.sample_label))samples.set(item.sample_label,[]);samples.get(item.sample_label).push(item);}
      $('[data-rp-candidates]').innerHTML='<div class="rp-candidate-heading"><strong>样本与链</strong><span data-rp-selection-count></span><div class="rp-actions"><button type="button" class="button button-ghost button-small" data-rp-select-all>全选</button><button type="button" class="button button-ghost button-small" data-rp-select-none>清空</button></div></div><label class="rp-sample-search"><input type="search" data-rp-sample-search placeholder="搜索样本名称" aria-label="搜索报告样本"></label><div class="rp-candidates rp-sample-selections">'+[...samples].map(([sample,items])=>'<div class="rp-selection-sample" data-rp-sample-name="'+esc(sample)+'"><strong>'+esc(sample)+'</strong><div>'+items.map(item=>'<label class="rp-selection-chain"><input type="checkbox" data-rp-item="'+esc(item.id)+'" checked><span>'+esc(item.chain)+'</span></label>').join('')+'</div></div>').join('')+'</div><p class="rp-search-empty" data-rp-search-empty hidden>没有符合搜索条件的样本。</p>';
      $('[data-rp-sample-search]').oninput=event=>{
        const query=event.target.value.trim().toLocaleLowerCase();let visible=0;
        root.querySelectorAll('[data-rp-sample-name]').forEach(row=>{row.hidden=!row.dataset.rpSampleName.toLocaleLowerCase().includes(query);if(!row.hidden)visible++;});
        $('[data-rp-search-empty]').hidden=visible>0;
      };
      const update=()=>{$('[data-rp-selection-count]').textContent=new Set(scan.items.filter(item=>selection.has(item.id)).map(item=>item.sample_label)).size+' 个样本 · '+selection.size+' / '+scan.items.length+' 项链';cfg.setSamples(scan.items.filter(r=>selection.has(r.id)));invalidateCheck();};
      $('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>input.onchange=()=>{if(input.checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);update();});
      for(const [key,checked] of [['all',true],['none',false]])$(`[data-rp-select-${key}]`).onclick=()=>{$('[data-rp-candidates]').querySelectorAll('[data-rp-item]').forEach(input=>{input.checked=checked;if(checked)selection.add(input.dataset.rpItem);else selection.delete(input.dataset.rpItem);});update();};update();message('已识别结果，请选择样本并核对引物配置。');if(step===1&&!$('[data-rp-setup]').hidden)$('[data-rp-setup]').scrollIntoView({block:'start'});
    });
    $('[data-rp-preview]').onclick=e=>action(e.target,async()=>{const preview=jobId&&planMode?await api(`/api/reports/plans/${encodeURIComponent(jobId)}/preview`,reportConfig()):await api('/api/reports/preview',body());$('[data-rp-allocation]').innerHTML=`<div class="rp-allocation">${(Object.keys(preview.chain_assignments||{}).length?Object.entries(preview.chain_assignments).flatMap(([sample,chains])=>Object.entries(chains).map(([chain,primers])=>[sample+' / '+chain,primers])):Object.entries(preview.assignments)).map(([sample,primers])=>`<div class="rp-assignment-row"><strong>${esc(sample)}</strong><span>${primers.map(p=>`<span class="rp-primer-tag">${esc(p.name)}<small>${esc(p.group)}</small></span>`).join('')}</span></div>`).join('')||'<p class="rp-inline-empty">未配置引物表。</p>'}</div>`;message('引物分配已更新。');});
    $('[data-rp-run]').onclick=e=>action(e.target,async()=>{const created=await api('/api/reports',body());setSetup(false);await load();$('[data-rp-runs]').value=created.id;await display();message('已加入报告生成队列。');});
    $('[data-rp-save-plan]').onclick=e=>action(e.target,async()=>{await api(`/api/reports/plans/${encodeURIComponent(jobId)}`,reportConfig(),'PUT');message('已保存，主分析完成后自动生成报告。');await load();});
    $('[data-rp-runs]').onchange=()=>{loadedId='';display().catch(e=>message(e.message,true));};
    const instance={update(value){job=value;if(!jobId)return;
      const context=[job.attempt_no,job.status,job.current_stage].join(':');
      if(context!==sampleContext){
        sampleContext=context;
        if(scan){scanEpoch++;scan=null;selection.clear();invalidateCheck();$('[data-rp-candidates]').textContent='任务状态已变化，请重新扫描结果。';showStep(1);}

        if(job.input_assignments?.length)cfg.setSamples(job.input_assignments);
        api('/api/reports/plans/'+encodeURIComponent(jobId)+'/samples').then(data=>{if(sampleContext===context&&data.items.length)cfg.setSamples(data.items);}).catch(()=>{});
      }
      const supported=['igblast_base','pig_igblast'].includes(job.pipeline);root.hidden=!supported;if(!supported)return;
      const previousPlanMode=planMode;
      planMode=!['SUCCEEDED','ARCHIVED','ARCHIVING','DELETING'].includes(job.status);
      $('[data-rp-path-field]').hidden=true;
      $('[data-rp-current-source]').hidden=false;
      $('[data-rp-current-source]').textContent=job.dataset+' · 当前任务结果';
      $('[data-rp-scan]').textContent=scan?'重新扫描':'扫描当前任务结果';
      $('[data-rp-save-plan]').hidden=!planMode;
      $('[data-rp-scan]').disabled=planMode||['ARCHIVED','ARCHIVING','DELETING'].includes(job.status);
      $('[data-rp-source-note]').textContent=planMode?'保存引物配置，主分析完成后自动生成报告。':'仅扫描当前任务已发布的结果。';
      if(previousPlanMode!==planMode)showStep(step);updateGenerate();
    },load};
    if(!jobId){const params=new URLSearchParams(location.search);if(params.get('job_id'))location.href='/jobs/'+encodeURIComponent(params.get('job_id'))+'#primer';}
    showStep(1);updateGenerate();instances.push(instance);load();return instance;
  }
  document.addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('[data-report-panel]').forEach(root=>mount(root,{jobId:window.SCIGBLAST_JOB_ID||''}));document.querySelectorAll('.rp-config').forEach(configPanel);});
  return {configPanel,update:job=>instances.forEach(panel=>panel.update(job)),load:()=>instances.forEach(panel=>panel.load())};
})();

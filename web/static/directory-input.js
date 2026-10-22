/* Directory input selection state is shared by creation and re-match forms. */
window.DirectoryInput = (() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let context='', generation=0, rows=[], scanId='', selected=new Map(), drafts=new Map(), page=0, changed=()=>{}, editing=null;
  const size=20;
  function active(){return $('[name=sample_source]:checked')?.value==='directory' && ['igblast_base','pig_igblast'].includes($('#pipeline').value);}
  function clear(){generation++;rows=[];scanId='';selected.clear();drafts.clear();page=0;$('#directory-search').value='';$('#directory-status').textContent='扫描目录，识别 FASTQ 配对后指定 Chain。';$('#directory-scan').disabled=false;render();}
  function ready(){return !active() || Boolean(scanId&&selected.size&&[...selected.values()].every(r=>r.chains.length&&r.sample_id.trim()));}
  function scope(){
    if(!scanId)return '请先扫描目录，识别样本';
    if(!selected.size)return '请选择至少一个有效样本';
    const missing=[...selected.values()].filter(r=>!r.chains.length||!r.sample_id.trim()).length;
    return `已选 ${selected.size} 个样本 · ${selected.size} 对 FASTQ${missing?` · ${missing} 个样本需补全名称或 Chain`:''}`;
  }
  function syncBatch(){
    const values=[...$('#directory-global-chains').querySelectorAll('input:checked')].map(input=>input.value);
    $('#directory-chain-selection').textContent=values.length?`批量选择 ${values.length} 条链 · ${values.join('、')}`:'尚未选择批量链';
    const same=[...selected.values()].filter(row=>[...row.chains].sort().join(',')===[...values].sort().join(',')).length;
    $('#directory-chain-feedback').textContent=!scanId?'先扫描目录，再选择处理链':!selected.size?'请先选择需要处理的样本':!values.length?'勾选上方的链，点击应用后生效':same===selected.size?`全部 ${selected.size} 个已选样本已使用此组合`:`${selected.size} 个已选样本中，${same} 个使用此组合；点击应用统一设置`;
    $('#directory-apply').textContent=selected.size?`应用到 ${selected.size} 个已选样本`:'应用到已选样本';
    $('#directory-apply').disabled=!scanId||!selected.size||!values.length;
  }
  function render(notify=true){
    const focused=document.activeElement?.matches('[data-select]')?document.activeElement.closest('[data-id]')?.dataset.id:null;
    const query=$('#directory-search').value.trim().toLowerCase();const filtered=rows.filter(r=>`${r.pair_id} ${(selected.get(r.id)||drafts.get(r.id))?.sample_id||r.sample_id}`.toLowerCase().includes(query));page=Math.min(page,Math.max(0,Math.ceil(filtered.length/size)-1));
    $('#directory-rows').innerHTML=filtered.slice(page*size,(page+1)*size).map(r=>{
      const value=selected.get(r.id)||drafts.get(r.id)||{sample_id:r.sample_id,chains:[]};
      return `<div class="directory-row ${r.valid?'':'invalid'}" data-id="${r.id}"><div class="directory-row-main"><input aria-label="选择 ${esc(r.sample_id||r.pair_id)}" type="checkbox" data-select ${selected.has(r.id)?'checked':''} ${r.valid?'':'disabled'}><div><input aria-label="样本名称 ${esc(r.pair_id)}" class="directory-name" value="${esc(value.sample_id)}" data-name ${r.valid?'':'disabled'} maxlength="160"><small title="${esc(r.pair_id)}">${esc(r.pair_id)}</small><small>${r.valid?'R1 / R2 配对完整':esc(r.error)}</small><details><summary>查看文件</summary><small>R1：${esc(r.r1||'缺失')}</small><small>R2：${esc(r.r2||'缺失')}</small></details></div></div>${r.valid?`<details class="directory-chain-details"><summary>Chain：${value.chains.length?esc(value.chains.join(', ')):'尚未指定'}</summary><div class="directory-chain-options chain-picker chain-picker-row">${UI.chainPicker({selected:value.chains,row:true})}</div></details>`:''}</div>`;
    }).join('')||'<p class="field-hint">没有符合条件的样本</p>';
    const missing=[...selected.values()].filter(r=>!r.chains.length).length;
    $('#directory-scope').textContent=scanId?`已选 ${selected.size} / ${rows.filter(r=>r.valid).length} 个有效样本（${selected.size} 对 FASTQ）${missing?`；${missing} 个样本尚未指定 Chain`:''}`:'';
    $('#directory-page').textContent=`${filtered.length} 项 · 第 ${page+1} / ${Math.max(1,Math.ceil(filtered.length/size))} 页`;
    $('#directory-prev').disabled=page===0;$('#directory-next').disabled=(page+1)*size>=filtered.length;
    syncBatch();
    if(notify)changed();
    if(focused)$(`#directory-rows [data-id="${focused}"] [data-select]`)?.focus();
  }
  async function scan(){
    clear();const run=generation,pipeline=$('#pipeline').value,path=$('[name=input_path]').value.trim();$('#directory-scan').disabled=true;$('#directory-status').textContent='正在识别 FASTQ 配对…';
    try{
      const result=await Submission.api('/api/input/scan',{pipeline,input_path:path});const found=[];
      for(let offset=0;offset<result.total;offset+=200){const params=new URLSearchParams({pipeline,input_path:path,offset,limit:200});const chunk=await Submission.api(`/api/input/scan/${result.scan_id}?${params}`);if(run!==generation)return;found.push(...chunk.rows);}
      if(run!==generation)return;rows=found;scanId=result.scan_id;
      rows.filter(r=>r.valid).forEach(r=>{const value={id:r.id,sample_id:r.sample_id,chains:[]};drafts.set(r.id,value);selected.set(r.id,value);});
      if(editing){const prior=new Map(editing.assignments.map(r=>[r.id,r]));selected.clear();for(const row of rows)if(row.valid&&prior.has(row.id))selected.set(row.id,{...prior.get(row.id),chains:[...prior.get(row.id).chains]});}
      selected.forEach((value,id)=>drafts.set(id,value));
      $('#directory-status').textContent=`识别 ${result.valid_count} 个有效配对，${result.total-result.valid_count} 个异常项。${editing?'已恢复原任务选择；新增文件默认不选。':'有效样本默认全选，请指定 Chain。'}`;
      render();
    }catch(e){if(run===generation)$('#directory-status').textContent=e.message;}
    finally{if(run===generation){$('#directory-scan').disabled=false;changed();}}
  }
  function init(callback){
    changed=callback;$('#directory-global-chains').innerHTML=UI.chainPicker();UI.bindChainPicker($('#directory-global-chains'));$('#directory-global-chains').addEventListener('change',syncBatch);
    $('#directory-scan').onclick=scan;
    $('#directory-apply').onclick=()=>{const values=[...$('#directory-global-chains').querySelectorAll('input:checked')].map(n=>n.value);selected.forEach(v=>v.chains=[...values]);render();};
    $('#directory-all').onclick=()=>{rows.filter(r=>r.valid).forEach(r=>{if(!selected.has(r.id))selected.set(r.id,drafts.get(r.id)||{id:r.id,sample_id:r.sample_id,chains:[]});});render();};
    $('#directory-none').onclick=()=>{selected.clear();render();};
    $('#directory-search').oninput=()=>{page=0;render(false);};$('#directory-prev').onclick=()=>{page--;render(false);};$('#directory-next').onclick=()=>{page++;render(false);};
    $('#directory-rows').addEventListener('change',e=>{const row=e.target.closest('[data-id]');if(!row)return;const id=row.dataset.id,candidate=rows.find(r=>r.id===id);if(e.target.matches('[data-select]')){if(e.target.checked)selected.set(id,{id,sample_id:row.querySelector('[data-name]').value,chains:[...row.querySelectorAll('[data-chain]:checked')].map(n=>n.dataset.chain)});else {drafts.set(id,selected.get(id));selected.delete(id);}}else if(selected.has(id)){const value=selected.get(id);value.sample_id=row.querySelector('[data-name]').value;value.chains=[...row.querySelectorAll('[data-chain]:checked')].map(n=>n.dataset.chain);row.querySelector('.directory-chain-details summary').textContent=`Chain：${value.chains.join(', ')||'尚未指定'}`;changed();$('#directory-scope').textContent=`已选 ${selected.size} 个样本（${selected.size} 对 FASTQ）`;}else if(e.target.matches('[data-chain]')){selected.set(id,{id,sample_id:row.querySelector('[data-name]').value,chains:[...row.querySelectorAll('[data-chain]:checked')].map(n=>n.dataset.chain)});row.querySelector('[data-select]').checked=true;changed();}syncBatch();if(e.target.matches('[data-select]'))render();});
    $('#directory-rows').addEventListener('input',e=>{if(e.target.matches('[data-name]')){const row=e.target.closest('[data-id]');if(selected.has(row.dataset.id))selected.get(row.dataset.id).sample_id=e.target.value;changed();}});
  }
  function setContext(){const next=`${$('#pipeline').value}|${$('[name=input_path]').value.trim()}|${active()}`;if(next!==context){context=next;clear();}}
  return {init,active,ready,scope,scan,setContext,reset(){editing=null;context='';$('#directory-global-chains').querySelectorAll('input').forEach(input=>input.checked=false);clear();},edit(value){editing=value;},editingJob(){return editing?.job_id;},body(){if(!ready())throw new Error('请扫描目录，选择样本并为全部选中样本指定 Chain');return {sample_source:'directory',input_scan_id:scanId,input_assignments:[...selected.values()],submission_path:'',submission_revision:'',barcode_csv:null};}};
})();

/* Immutable workbook editor, shared by job creation and rematching. */
window.Submission = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(url, body) { const r = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-SCIGBLAST-Request':'1'}, body:JSON.stringify(body)}); const data = await r.json(); if (r.status===401) location.href='/login'; if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)); return data; }
  function open(initial, accept) {
    let data = initial, sheetIndex = 0, page = 0; const edits = new Map();
    const modal = document.createElement('dialog'); modal.className = 'submission-dialog';
    modal.innerHTML = `<header><div><p class="eyebrow">SUBMISSION WORKING COPY</p><h2>检查样本与 Note 路径</h2></div><button class="close-button" data-close aria-label="关闭">×</button></header><p>只修改工作副本。合并单元格的 Note 会应用到对应的所有样本。灰色字段仅供查看。</p><div class="editor-toolbar"><select aria-label="工作表" data-sheet></select><a data-download>下载当前 XLSX</a><a data-original>下载原始副本</a></div><div class="editor-toolbar"><input data-old placeholder="原 Note 路径前缀" aria-label="原路径前缀"><input data-new placeholder="分析服务器路径前缀" aria-label="新路径前缀"><button class="button button-ghost" data-replace>预览并替换 Note 前缀</button></div><div data-grid class="table-wrap editor-grid"></div><div class="pagination"><button class="button button-ghost" data-prev>上一页</button><span data-page></span><button class="button button-ghost" data-next>下一页</button></div><p data-message role="status"></p><footer><span data-revision></span><button class="button button-primary" data-save>保存工作副本并使用</button></footer>`;
    document.body.append(modal); modal.showModal();
    modal.querySelector('[data-new]').insertAdjacentHTML('afterend','<button class="button button-ghost" data-browse>浏览目标目录</button>');
    const tree=document.createElement('div');tree.className='file-tree hidden';modal.querySelector('[data-grid]').before(tree);
    async function browse(path='') {
      try {const result=await api(`/api/browse?${new URLSearchParams({kind:'input',path})}`);tree.classList.remove('hidden');tree.innerHTML=`<p>${esc(result.path||'选择允许的根目录')}</p>${result.path?'<button class="button button-primary" data-choose>使用此路径</button>':''}${result.parent?`<button class="button button-ghost" data-path="${esc(result.parent)}">上一级</button>`:''}<button class="button button-ghost" data-cancel>关闭</button>`+(result.entries||[]).filter(e=>e.type==='directory').map(e=>`<button class="file-tree-entry" data-path="${esc(e.path)}">${esc(e.name)} →</button>`).join('');tree.onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-choose')){modal.querySelector('[data-new]').value=result.path;tree.classList.add('hidden');}else if(b.hasAttribute('data-cancel'))tree.classList.add('hidden');else browse(b.dataset.path);};}catch(e){modal.querySelector('[data-message]').textContent=e.message;}
    }
    modal.querySelector('[data-browse]').onclick=()=>browse();
    const replacement=document.createElement('label');replacement.textContent='用补齐后的 XLSX 替换工作副本：';
    const upload=document.createElement('input');upload.type='file';upload.accept='.xlsx';replacement.append(upload);modal.querySelector('[data-sheet]').parentElement.append(replacement);
    upload.onchange=async()=>{const file=upload.files[0];if(!file)return;try{const response=await fetch(`/api/submissions/upload?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'X-SCIGBLAST-Request':'1'},body:file});const result=await response.json();if(response.status===401)location.href='/login';if(!response.ok)throw new Error(result.detail);data=result;sheetIndex=0;page=0;edits.clear();render();}catch(error){modal.querySelector('[data-message]').textContent=error.message;}};
    const $ = q => modal.querySelector(q);
    const close = () => {modal.close(); modal.remove();}; $('[data-close]').onclick = close;
    modal.addEventListener('cancel', event => {event.preventDefault(); close();});
    const key = (s,c) => JSON.stringify([s.file,s.sheet,c.cell]);
    function render() {
      const s = data.sheets[sheetIndex]; if (!s) return;
      $('[data-sheet]').innerHTML = data.sheets.map((s,i) => `<option value="${i}" ${i===sheetIndex?'selected':''}>${esc(s.file)} / ${esc(s.sheet)}</option>`).join('');
      const params = new URLSearchParams({revision:data.revision,filename:s.file}); $('[data-download]').href = `/api/submissions/download?${params}`;
      $('[data-original]').href = `/api/submissions/download?${params}&original=true`;
      $('[data-grid]').innerHTML = `<table><thead><tr><th>Excel 行</th>${s.headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${s.rows.slice(page*40,page*40+40).map(r=>`<tr><td>${r.row}</td>${r.values.map((v,i)=> {const c=r.editable[i],k=key(s,c); return `<td>${c.kind ? `<input data-key="${esc(k)}" data-kind="${c.kind}" aria-label="${esc(s.headers[i])} ${r.row}" title="${esc(c.merged || c.cell)}${c.inherited?' · 继承单元格':''}" value="${esc(edits.get(k)?.value ?? v)}">` : esc(v)}</td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
      $('[data-page]').textContent = `${s.rows.length} 行 · 第 ${page+1} / ${Math.max(1,Math.ceil(s.rows.length/40))} 页`;
      $('[data-prev]').disabled=page===0; $('[data-next]').disabled=(page+1)*40>=s.rows.length;
      $('[data-revision]').textContent = `工作副本 · ${edits.size} 个单元格变更`;
    }
    $('[data-grid]').onchange = event => {const input=event.target;if(!input.dataset.key)return;const [file,sheet,cell]=JSON.parse(input.dataset.key);edits.set(input.dataset.key,{file,sheet,cell,value:input.value});render();};
    $('[data-sheet]').onchange = e => {sheetIndex=Number(e.target.value);page=0;render();};
    $('[data-prev]').onclick=()=>{page--;render();}; $('[data-next]').onclick=()=>{page++;render();};
    $('[data-replace]').onclick = () => {
      const old=$('[data-old]').value.replace(/\/+$/,''), next=$('[data-new]').value.replace(/\/+$/,'');
      if(!old || !next) {$('[data-message]').textContent='请填写两个路径前缀';return;}
      const changes=new Map();let affected=0;
      data.sheets.forEach(s=>s.rows.forEach(r=>r.editable.forEach((c,i)=>{if(c.kind!=='note')return;const k=key(s,c),v=edits.get(k)?.value ?? r.values[i];const parts=v.split(/([\r\n;,]+)/);let changed=false;const value=parts.map(part=>{const p=part.trim();if(p===old || p.startsWith(old+'/')){changed=true;return next+p.slice(old.length);}return part;}).join('');if(changed){affected++;changes.set(k,{file:s.file,sheet:s.sheet,cell:c.cell,value});}})));
      if(changes.size && confirm(`将修改 ${changes.size} 个 Note 单元格，影响 ${affected} 行。\n${old}\n→ ${next}\n保存前仍可检查表格。`)){changes.forEach((v,k)=>edits.set(k,v));render();}
      $('[data-message]').textContent=`前缀匹配 ${affected} 行；路径存在性在保存时检查。`;
    };
    $('[data-save]').onclick=async()=>{const b=$('[data-save]');b.disabled=true;try{if(edits.size)data=await api('/api/submissions/revise',{revision:data.revision,changes:[...edits.values()]});await accept(data);close();}catch(e){$('[data-message]').textContent=e.message;}finally{b.disabled=false;}};
    render();
  }
  function deleteJob(job) {
    return new Promise(resolve => {
      const modal = document.createElement('dialog'); modal.className = 'delete-dialog';
      modal.innerHTML = `<header><h2>删除任务与结果</h2><button class="close-button" data-cancel aria-label="取消删除">×</button></header><p><strong>${esc(job.dataset)}</strong> · ${esc(job.pipeline_label)} · ${esc(job.operator)}</p><p>将删除任务列表记录、操作记录及下方服务器结果目录内的全部文件。此操作不可撤销。</p><code class="delete-path">${esc(job.output_root)}</code><p class="muted">不会删除原始数据或 Submission。共享目录、运行中任务及不安全路径会被服务器拒绝。</p><label>输入 <strong>DELETE</strong> 确认<input data-confirm autocomplete="off" spellcheck="false" aria-label="输入 DELETE 确认删除"></label><p data-message role="status" aria-live="polite"></p><footer><button class="button button-ghost" data-cancel>取消</button><button class="button button-danger" data-delete disabled>删除任务及结果</button></footer>`;
      document.body.append(modal); modal.showModal();
      let deleting = false;
      const close = value => { modal.close(); modal.remove(); resolve(value); };
      modal.querySelectorAll('[data-cancel]').forEach(b => b.onclick = () => { if(!deleting) close(false); });
      modal.addEventListener('cancel', e => { e.preventDefault(); if(!deleting) close(false); });
      const input = modal.querySelector('[data-confirm]'), button = modal.querySelector('[data-delete]');
      input.oninput = () => { button.disabled = input.value !== 'DELETE'; };
      input.focus();
      button.onclick = async () => {
        if(deleting || input.value !== 'DELETE') return;
        deleting = true; button.disabled = true; input.disabled = true;
        modal.querySelectorAll('[data-cancel]').forEach(b => b.disabled = true);
        modal.querySelector('[data-message]').textContent = '正在删除，请勿关闭页面…';
        try { await api(`/api/jobs/${job.id}/delete`, {confirm:job.id,output_root:job.output_root}); close(true); }
        catch(e) { modal.querySelector('[data-message]').textContent = e.message; deleting = false; button.disabled = false; input.disabled = false; modal.querySelectorAll('[data-cancel]').forEach(b => b.disabled = false); }
      };
    });
  }
  return {api,open,esc,deleteJob};
})();

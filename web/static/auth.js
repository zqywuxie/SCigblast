(() => {
  const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(url,body){
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-SCIGBLAST-Request':'1'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok){if(response.status===401&&!$('#login-form'))location.href='/login';throw new Error(typeof data.detail==='string'?data.detail:'请检查填写内容');}
    return data;
  }
  const message=(id,text)=>{const node=$(id);if(node)node.textContent=text;};
  document.querySelectorAll('[data-logout]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/auth/logout',{});location.href='/login';}catch(e){alert(e.message);b.disabled=false;}});
  function authTab(tab){document.querySelectorAll('[data-auth-tab]').forEach(b=>{const active=b.dataset.authTab===tab;b.classList.toggle('button-primary',active);b.classList.toggle('button-ghost',!active);b.setAttribute('aria-selected',String(active));});$('#login-form').classList.toggle('hidden',tab!=='login');$('#register-form').classList.toggle('hidden',tab!=='register');message('#auth-message','');}
  document.querySelectorAll('[data-auth-tab]').forEach(b=>b.onclick=()=>authTab(b.dataset.authTab));
  for(const kind of ['login','register']){
    const form=$(`#${kind}-form`);if(!form)continue;
    form.onsubmit=async e=>{e.preventDefault();const b=form.querySelector('[type=submit]');b.disabled=true;message('#auth-message','正在处理…');
      try{const body=Object.fromEntries(new FormData(form));if(kind==='register'&&body.password!==body.confirm_password)throw new Error('两次密码不一致');delete body.confirm_password;await api(`/api/auth/${kind}`,body);
        if(kind==='login')location.href='/';else{form.reset();authTab('login');$('#login-form [name=display_name]').value=body.display_name;message('#auth-message','注册成功，请登录。');}
      }catch(error){message('#auth-message',error.message);}finally{b.disabled=false;}};
  }
  if($('#password-form'))$('#password-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const body=Object.fromEntries(new FormData(e.target));if(body.new_password!==body.confirm_password)throw new Error('两次新密码不一致');delete body.confirm_password;await api('/api/auth/password',body);location.href='/login';}catch(error){message('#auth-message',error.message);}finally{b.disabled=false;}};
  function serviceStatus(enabled){message('#mail-service-status',enabled?'邮件服务已启用。':'管理员尚未启用邮件服务，您可以先保存提醒邮箱。');}
  const notificationForm=$('#notification-form');
  if(notificationForm){
    api('/api/notifications/preferences').then(data=>{notificationForm.elements.email.value=data.email;serviceStatus(data.service_enabled);notificationForm.elements.email.disabled=false;notificationForm.querySelector('[type=submit]').disabled=false;message('#notification-message','');}).catch(error=>message('#notification-message',error.message));
    notificationForm.onsubmit=async event=>{event.preventDefault();const input=notificationForm.elements.email,button=notificationForm.querySelector('[type=submit]');button.disabled=true;input.disabled=true;message('#notification-message','正在保存…');try{const result=await api('/api/notifications/preferences',{email:input.value.trim()});input.value=result.email;message('#notification-message',result.email?'提醒邮箱已保存，后续通知使用此邮箱。':'已关闭邮件提醒。');}catch(error){message('#notification-message',error.message);}finally{button.disabled=false;input.disabled=false;}};
  }
  const mailForm=$('#mail-settings-form');
  if(mailForm){
    const fields=$('#mail-settings-fields'),save=mailForm.querySelector('[type=submit]'),test=$('#mail-test');
    let savedMail=null,mailBusy=false,mailDirty=false,testPolling=false;
    function syncMail(){const enabled=mailForm.elements.enabled.checked;['host','sender_email','public_base_url'].forEach(name=>mailForm.elements[name].required=enabled);test.disabled=mailBusy||mailDirty||testPolling||!savedMail?.enabled;save.disabled=mailBusy;}
    function fillMail(data){savedMail=data;for(const name of ['host','port','sender_email','encryption','username','public_base_url'])mailForm.elements[name].value=data[name];mailForm.elements.enabled.checked=data.enabled;mailForm.elements.password.value='';mailForm.elements.clear_password.checked=false;message('#mail-password-hint',data.password_configured?'授权码已保存；留空保留原值，输入新值可替换。':'尚未设置授权码；内容不会回显。');mailDirty=false;fields.disabled=false;syncMail();}
    api('/api/admin/mail-settings').then(data=>{fillMail(data);message('#mail-settings-message',data.enabled?'邮件服务已启用。':'邮件服务尚未启用。');}).catch(error=>message('#mail-settings-message',error.message));
    mailForm.oninput=()=>{mailDirty=true;syncMail();message('#mail-settings-message','配置尚未保存；请先保存再测试。');};mailForm.onchange=mailForm.oninput;
    mailForm.elements.sender_email.addEventListener('change',()=>{if(mailForm.elements.host.value.trim()==='smtp.qq.com'&&!mailForm.elements.username.value.trim())mailForm.elements.username.value=mailForm.elements.sender_email.value.trim();});
    mailForm.onsubmit=async event=>{event.preventDefault();if(mailBusy)return;const body=Object.fromEntries(new FormData(mailForm));body.enabled=mailForm.elements.enabled.checked;body.clear_password=mailForm.elements.clear_password.checked;body.port=Number(body.port);mailBusy=true;fields.disabled=true;syncMail();message('#mail-settings-message','正在保存…');try{const data=await api('/api/admin/mail-settings',body);fillMail(data);serviceStatus(data.enabled);message('#mail-settings-message',data.enabled?'发件配置已保存，邮件服务已启用。':'发件配置已保存，邮件服务已关闭。');}catch(error){message('#mail-settings-message',error.message);}finally{mailBusy=false;fields.disabled=false;syncMail();}};
    test.onclick=async()=>{if(testPolling)return;testPolling=true;syncMail();message('#mail-test-message','正在准备测试邮件…');try{const result=await api('/api/admin/mail-settings/test',{});let polls=0;const poll=async()=>{try{const state=await api(`/api/admin/mail-settings/test/${encodeURIComponent(result.id)}`);const labels={queued:'测试邮件已排队…',sending:'正在发送测试邮件…',retry:'发送暂时失败，将自动重试。',sent:'测试邮件已提交给 SMTP 服务器，请查收邮箱（含垃圾箱）。',failed:'测试邮件发送失败。',skipped:'账户或提醒邮箱已改变，本次测试未发送。'};message('#mail-test-message',(labels[state.status]||state.status)+(state.last_error?' '+state.last_error:''));if(['sent','failed','skipped'].includes(state.status)||++polls>=45){if(polls>=45)message('#mail-test-message','测试邮件仍在队列中，后台将继续处理；请检查邮箱或稍后再次测试。');testPolling=false;syncMail();return;}setTimeout(poll,2000);}catch(error){message('#mail-test-message',error.message);testPolling=false;syncMail();}};await poll();}catch(error){message('#mail-test-message',error.message);testPolling=false;syncMail();}};
  }
  if(!$('#invite-form'))return;
  const fmt=v=>v?new Date(v*1000).toLocaleString('zh-CN'):'—';
  let loading=false, refreshAgain=false, inviteOffset=0, refreshVersion=0, searchTimer;
  const inviteLimit=50;
  const statusClass={'可使用':'available','已使用':'used','已撤销':'revoked','已过期':'expired','已删除':'deleted'};
  function invitationActions(r){
    const actions=[];
    if(r.status==='可使用')actions.push(`<button type="button" class="button button-danger-ghost" data-revoke="${esc(r.id)}">撤销注册码</button>`);
    if(r.used_by)actions.push(`<button type="button" class="button button-ghost" data-view-user="${esc(r.used_by)}">查看用户</button>`);
    if(r.status!=='已删除')actions.push(`<button type="button" class="button button-danger-ghost" data-delete-invite="${esc(r.id)}">删除记录</button>`);
    return `<div class="invitation-actions">${actions.join('')||'—'}</div>`;
  }
  function focusRecord(selector){const row=$(selector);if(!row)return;document.querySelectorAll('.account-record-selected').forEach(el=>el.classList.remove('account-record-selected'));row.classList.add('account-record-selected');row.scrollIntoView({block:'center'});row.focus({preventScroll:true});}
  async function refresh(){if(loading){refreshAgain=true;return;}loading=true;const version=refreshVersion;try{
    const params=new URLSearchParams({status:$('#invite-status').value,q:$('#invite-search').value.trim(),limit:inviteLimit,offset:inviteOffset});
    const [invites,users]=await Promise.all([api(`/api/admin/invitations?${params}`),api('/api/admin/users')]);
    if(version!==refreshVersion)return;
    if(inviteOffset && inviteOffset>=invites.total){inviteOffset=Math.max(0,Math.ceil(invites.total/inviteLimit)-1)*inviteLimit;refreshAgain=true;return;}
    $('#invite-rows').innerHTML=invites.invitations.map(r=>`<tr data-invite-row="${esc(r.id)}" tabindex="-1"><td><code>${esc(r.id)}</code></td><td><span class="invite-status ${statusClass[r.status]||''}">${esc(r.status)}</span></td><td><strong>${esc(r.created_display_name||'—')}</strong></td><td>${fmt(r.created_at)}</td><td>${fmt(r.expires_at)}</td><td>${r.used_by?`<strong>${esc(r.used_display_name||r.used_username)}</strong><small class="record-meta">${r.used_active?'启用':'停用'}</small>`:'尚未注册'}</td><td>${fmt(r.used_at)}</td><td>${invitationActions(r)}</td></tr>`).join('')||'<tr><td colspan="8">没有符合条件的注册码记录</td></tr>';
    $('#invite-page-summary').textContent=invites.total?`第 ${inviteOffset+1}–${inviteOffset+invites.invitations.length} 条，共 ${invites.total} 条`:'共 0 条记录';
    $('#invite-prev').disabled=inviteOffset===0;$('#invite-next').disabled=inviteOffset+inviteLimit>=invites.total;
    $('#user-rows').innerHTML=users.users.map(u=>`<tr data-user-row="${esc(u.id)}" tabindex="-1"><td>${esc(u.display_name)}</td><td>${u.role==='admin'?'管理员':'普通用户'}</td><td>${u.active?'启用':'停用'}</td><td>${u.invitation_id?`<button type="button" class="button button-ghost button-small" data-view-invite="${esc(u.invitation_id)}" data-invite-deleted="${u.invitation_deleted_at!=null}">${esc(u.invitation_id)}</button>${u.invitation_deleted_at!=null?'<small class="record-meta">注册码记录已删除</small>':''}`:'管理员创建 / 历史账户'}</td><td>${fmt(u.created_at)}</td><td>${fmt(u.last_login)}</td><td><button class="button button-ghost button-small" data-rename="${esc(u.id)}" data-name="${esc(u.display_name)}">修改姓名</button><button class="button button-ghost button-small" data-reset="${esc(u.id)}" data-name="${esc(u.display_name)}">重置密码</button>${u.id===$('#user-rows').dataset.currentUser?'当前用户':`<button type="button" class="button button-ghost" data-user="${esc(u.id)}" data-active="${u.active?'0':'1'}">${u.active?'停用':'启用'}</button>`}</td></tr>`).join('');
    const current=invites.invitations.find(r=>r.id===$('#new-invite').dataset.id);
    if(current && current.status!=='可使用'){$('#invite-code').value='';$('#new-invite').classList.add('hidden');}
  }catch(error){message('#admin-message',error.message);}finally{loading=false;if(refreshAgain){refreshAgain=false;refresh();}}}
  function filterInvites(){inviteOffset=0;refreshVersion++;return refresh();}
  $('#invite-status').onchange=filterInvites;
  $('#invite-search').oninput=()=>{clearTimeout(searchTimer);refreshVersion++;searchTimer=setTimeout(filterInvites,200);};
  $('#invite-prev').onclick=()=>{inviteOffset=Math.max(0,inviteOffset-inviteLimit);refreshVersion++;refresh();};
  $('#invite-next').onclick=()=>{inviteOffset+=inviteLimit;refreshVersion++;refresh();};
  $('#invite-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const r=await api('/api/admin/invitations',{hours:Number(new FormData(e.target).get('hours'))});$('#invite-code').value=r.code;$('#new-invite').dataset.id=r.id;$('#new-invite-details').textContent=`编号：${r.id} · 失效时间：${fmt(r.expires_at)}`;$('#new-invite').classList.remove('hidden');$('#invite-status').value='';$('#invite-search').value='';message('#admin-message','注册码已生效，请复制后交给注册人。');await filterInvites();}catch(error){message('#admin-message',error.message);}finally{b.disabled=false;}};
  $('#copy-invite').onclick=async()=>{try{await navigator.clipboard.writeText($('#invite-code').value);message('#admin-message','已复制');}catch{$('#invite-code').select();message('#admin-message','请按 Ctrl+C 复制选中的注册码');}};
  $('#invite-rows').onclick=async e=>{
    const view=e.target.closest('[data-view-user]');if(view){focusRecord(`[data-user-row="${view.dataset.viewUser}"]`);return;}
    const b=e.target.closest('[data-revoke],[data-delete-invite]');if(!b)return;
    const deleting=!!b.dataset.deleteInvite,id=b.dataset.deleteInvite||b.dataset.revoke;
    if(deleting&&!confirm('删除这条注册码记录？未使用的注册码将立即失效，已注册用户及其注册来源仍会保留。'))return;
    b.disabled=true;
    try{await api(`/api/admin/invitations/${id}/${deleting?'delete':'revoke'}`,{});if($('#new-invite').dataset.id===id){$('#invite-code').value='';$('#new-invite').classList.add('hidden');}message('#admin-message',deleting?'记录已移除，可在“已删除”中查看历史注册来源。':'已撤销，旧注册码立即失效；记录继续保留。');await refresh();}
    catch(error){message('#admin-message',error.message);b.disabled=false;}
  };
  $('#user-rows').onclick=async e=>{const edit=e.target.closest('[data-rename],[data-reset]');if(edit){editUser(edit);return;}const source=e.target.closest('[data-view-invite]');if(source){$('#invite-status').value=source.dataset.inviteDeleted==='true'?'deleted':'';$('#invite-search').value=source.dataset.viewInvite;await filterInvites();$('#invitation-management').scrollIntoView({block:'start'});$('#invite-search').focus({preventScroll:true});return;}const b=e.target.closest('[data-user]');if(!b)return;if(!confirm(`${b.dataset.active==='1'?'启用':'停用'}该账户？`))return;b.disabled=true;try{await api(`/api/admin/users/${b.dataset.user}/active`,{active:b.dataset.active==='1'});message('#admin-message',b.dataset.active==='1'?'用户已启用。':'用户已停用，注册来源记录已保留。');await refresh();}catch(error){message('#admin-message',error.message);b.disabled=false;}};
  function editUser(button){
    const resetting=!!button.dataset.reset,userId=button.dataset.reset||button.dataset.rename;
    const dialog=document.createElement('dialog');dialog.className='record-detail';
    dialog.innerHTML=`<form class="auth-form"><h2>${resetting?'重置密码':'修改姓名'} · ${esc(button.dataset.name)}</h2><p>${resetting?'保存后该用户需重新登录。':'保存后使用新姓名登录。'}</p>${resetting?'<label class="field">新密码<input name="new_password" type="password" minlength="6" maxlength="128" required autocomplete="new-password"></label><label class="field">确认密码<input name="confirm_password" type="password" required autocomplete="new-password"></label>':`<label class="field">姓名<input name="display_name" value="${esc(button.dataset.name)}" maxlength="60" required pattern="[A-Za-z][A-Za-z0-9 ._\\-]*"></label>`}<p data-message role="status"></p><div><button type="button" class="button button-ghost" data-cancel>取消</button><button type="submit" class="button button-primary">保存</button></div></form>`;
    document.body.append(dialog);dialog.showModal();dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();const save=dialog.querySelector('[type=submit]');save.disabled=true;
      try{const body=Object.fromEntries(new FormData(e.target));if(resetting&&body.new_password!==body.confirm_password)throw new Error('两次密码不一致');delete body.confirm_password;
        await api(`/api/admin/users/${userId}/${resetting?'password':'name'}`,body);dialog.close();if(userId===$('#user-rows').dataset.currentUser){location.href=resetting?'/login':'/admin';return;}await refresh();message('#admin-message',resetting?'密码已重置':'姓名已更新');
      }catch(error){dialog.querySelector('[data-message]').textContent=error.message;}finally{save.disabled=false;}
    };
  }
  $('#admin-refresh').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
})();

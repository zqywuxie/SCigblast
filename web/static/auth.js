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
        if(kind==='login')location.href='/';else{form.reset();authTab('login');$('#login-form [name=username]').value=body.username;message('#auth-message','注册成功，请登录。');}
      }catch(error){message('#auth-message',error.message);}finally{b.disabled=false;}};
  }
  if($('#password-form'))$('#password-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const body=Object.fromEntries(new FormData(e.target));if(body.new_password!==body.confirm_password)throw new Error('两次新密码不一致');delete body.confirm_password;await api('/api/auth/password',body);location.href='/login';}catch(error){message('#auth-message',error.message);}finally{b.disabled=false;}};
  if(!$('#invite-form'))return;
  const fmt=v=>v?new Date(v*1000).toLocaleString('zh-CN'):'—';
  let loading=false;
  async function refresh(){if(loading)return;loading=true;try{
    const [invites,users]=await Promise.all([api('/api/admin/invitations'),api('/api/admin/users')]);
    $('#invite-rows').innerHTML=invites.invitations.map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.status)}</td><td>${fmt(r.created_at)}</td><td>${fmt(r.expires_at)}</td><td>${esc(r.used_username||'—')}</td><td>${r.status==='可使用'?`<button class="button button-danger-ghost" data-revoke="${r.id}">撤销</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="6">尚未生成注册码</td></tr>';
    $('#user-rows').innerHTML=users.users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${u.role==='admin'?'管理员':'普通用户'}</td><td>${u.active?'启用':'停用'}</td><td>${fmt(u.last_login)}</td><td>${u.id===$('#user-rows').dataset.currentUser?'当前用户':`<button class="button button-ghost" data-user="${u.id}" data-active="${u.active?'0':'1'}">${u.active?'停用':'启用'}</button>`}</td></tr>`).join('');
  }catch(error){message('#admin-message',error.message);}finally{loading=false;}}
  $('#invite-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const r=await api('/api/admin/invitations',{hours:Number(new FormData(e.target).get('hours'))});$('#invite-code').value=r.code;$('#new-invite').classList.remove('hidden');message('#admin-message','注册码已生效。');await refresh();}catch(error){message('#admin-message',error.message);}finally{b.disabled=false;}};
  $('#copy-invite').onclick=async()=>{try{await navigator.clipboard.writeText($('#invite-code').value);message('#admin-message','已复制');}catch{$('#invite-code').select();message('#admin-message','请按 Ctrl+C 复制选中的注册码');}};
  $('#invite-rows').onclick=async e=>{const b=e.target.closest('[data-revoke]');if(!b)return;b.disabled=true;try{await api(`/api/admin/invitations/${b.dataset.revoke}/revoke`,{});message('#admin-message','已撤销，旧注册码立即失效。');await refresh();}catch(error){message('#admin-message',error.message);b.disabled=false;}};
  $('#user-rows').onclick=async e=>{const b=e.target.closest('[data-user]');if(!b)return;if(!confirm(`${b.dataset.active==='1'?'启用':'停用'}该账户？`))return;b.disabled=true;try{await api(`/api/admin/users/${b.dataset.user}/active`,{active:b.dataset.active==='1'});await refresh();}catch(error){message('#admin-message',error.message);b.disabled=false;}};
  $('#admin-refresh').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
})();

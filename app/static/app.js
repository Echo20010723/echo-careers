const $ = id => document.getElementById(id);
let state = {}, selected = null, refreshing = false, busy = 0, currentResume = null, initialized = false;
const statusNames = {draft:'草稿已准备',queued:'待自动处理',blocked:'需要确认',sending:'正在发送',sent:'已发送',failed:'处理失败',unknown:'发送结果待核实',cancelled:'已取消',active:'初聊中',waiting_response:'等待回复',handoff:'需要你接手'};
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function notice(text,error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; $('notice').style.display='block'; clearTimeout(window.noticeTimer); window.noticeTimer=setTimeout(()=>$('notice').style.display='none',12000); }
async function api(path,method='GET',body) {
  const options={method,signal:AbortSignal.timeout(65000)};
  if(body instanceof FormData)options.body=body;
  else if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
  let r;try{r=await fetch(path,options);}catch(e){throw Error(e.name==='TimeoutError'?'操作仍在等待响应，请稍后检查状态。':'无法连接本机助手，请双击项目里的START.cmd重新启动。');}
  const data=await r.json();if(!r.ok)throw Error(data.error?.message||'操作未完成，请稍后重试。');return data;
}
async function run(fn,button,workingText){busy++;const old=button?.textContent;try{if(button){button.disabled=true;button.setAttribute('aria-busy','true');if(workingText)button.textContent=workingText;}await fn();await refresh();}catch(e){notice(e.message,true);}finally{busy--;if(button){button.disabled=false;button.removeAttribute('aria-busy');if(workingText)button.textContent=old;}}}
function action(label,fn,cls='secondary'){const b=el('button',label,cls);b.onclick=()=>run(fn,b);return b;}
function list(id,items,render,empty='暂无记录'){const n=$(id);n.replaceChildren();if(!items.length)n.append(el('p',empty,'empty'));else items.forEach(x=>n.append(render(x)));}
function tab(id){
 document.querySelectorAll('.tab').forEach(section=>section.classList.toggle('active',section.id===id));
 document.querySelectorAll('nav button').forEach(button=>{
  const active=button.dataset.tab===id;
  button.classList.toggle('active',active);
  if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
 });
 if(id!=='discovery') refresh(!initialized).catch(e=>notice(e.message,true));
}
document.querySelectorAll('nav button').forEach(n=>n.onclick=()=>tab(n.dataset.tab));
function details(title,key){const d=el('details');d.dataset.key=key;d.append(el('summary',title));return d;}
function jobData(row){return JSON.parse(row.payload);}
function isDemoJob(row){return (jobData(row).title||'').startsWith('界面验收 · ')||jobData(row).source?.startsWith('mock')||state.conversations.some(c=>c.job_id===row.id&&c.channel==='mock')&&!state.conversations.some(c=>c.job_id===row.id&&c.channel==='boss');}

function matchingDetails(result,key){const d=details('查看匹配依据',key),info=result.score_detail;d.append(el('p',result.provider.simulated?'演示模型分析，仅供流程体验。':'根据当前简历分析。'));
 for(const x of [...info.skill_evidence,...info.project_evidence]){const n=el('div',undefined,'evidence');n.append(el('p',x.requirement+' · '+({demonstrated:'有材料支持',partial:'部分支持',not_evidenced:'简历未体现'}[x.status])),el('p',x.evidence||'需要补充事实依据。','muted'));d.append(n);}
 for(const x of info.eligibility_checks.filter(x=>x.status!=='met'))d.append(el('p','待核对：'+x.condition));return d;}
function jobCard(row){const p=jobData(row),a=row.analysis?JSON.parse(row.analysis):null,n=el('div',undefined,'row'),h=el('div',undefined,'job-heading');h.append(el('h3',p.title||'导入的岗位'),el('span',a?Math.round(a.match_score*100)+'/100':'处理中','score'));n.append(h,el('p',[p.company,p.location,p.salary].filter(Boolean).join(' · '),'muted'));if(isDemoJob(row))n.append(el('span','演示岗位','pill demo'));if(a){n.append(el('span',a.score_detail.recommendation,'pill'),matchingDetails(a,'match-'+row.id));}return n;}
async function showConversation(cid){selected=cid;const data=await api('/api/conversations/'+cid);$('conversation-detail').replaceChildren();for(const m of data.messages.slice(-3))$('conversation-detail').append(el('div',m.text,'message incoming'));renderOutbox();$('demo-controls').hidden=data.conversation.channel!=='mock';}
function renderOutbox(){const rows=state.outbox.filter(x=>x.conversation_id===selected);list('outbox',rows.slice(0,3),x=>{const n=el('div',undefined,'message');n.append(el('small',statusNames[x.status]||x.status),el('div',x.text));if(['draft','blocked'].includes(x.status))n.append(action('复制草稿',async()=>{await navigator.clipboard.writeText(x.text);notice('已复制草稿。');},'text-button'));const c=state.conversations.find(c=>c.id===x.conversation_id);if(c?.channel==='mock')n.append(el('span','演示内容','pill demo'));if(x.status==='unknown')n.append(action('核实发送结果',()=>api('/api/outbox/'+x.id+'/reconcile','POST')));return n;},selected?'暂无待处理内容':'选择一个会话查看草稿');}
async function refresh(initial=false){if(refreshing)return;refreshing=true;const opened=[...document.querySelectorAll('details[open][data-key]')].map(n=>n.dataset.key);try{
 state=await api('/api/state');const visibleJobs=state.jobs.filter(j=>isDemoJob(j)===(state.active_channel==='mock'));const visibleConversations=state.conversations.filter(c=>c.channel===state.active_channel&&c.resume_id===state.current_resume&&visibleJobs.some(j=>j.id===c.job_id));
 const isDemo = state.active_channel === 'mock';
 const isPaused = state.task === 'paused';
 $('mode-badge').textContent = document.body.classList.contains('public-workbench')
   ? (document.body.dataset.publicModel === 'mock' ? '公开演示' : '公开体验')
   : isDemo ? '模拟演示' : '本机工作台';
 $('task-state').textContent = isPaused ? '岗位分析已暂停' : '已开启自动分析';
 $('start').textContent = isDemo ? '返回本机队列' : '开始处理';
 $('pause').disabled = isPaused;
 const jobs = state.jobs.filter(row => !isDemoJob(row));
 const today = localDate();
 const applications = new Map(state.applications.map(entry => [entry.job_id, entry]));
 const activeEntries = state.applications.filter(entry => entry.status !== 'closed');
 const counts = [
  ['待复核', jobs.filter(row => (applications.get(row.id)?.status ?? 'review') === 'review').length],
  ['已分析', jobs.filter(row => row.analysis).length],
  ['今日到期或逾期', activeEntries.filter(entry => entry.due_on && entry.due_on <= today).length],
 ];
 $('metrics').replaceChildren(...counts.map(([name, count]) => {
  const metric = el('div', undefined, 'metric');
  metric.append(el('span', name), el('strong', count));
  return metric;
 }));
 const pending=state.pipeline.filter(x=>['pending','processing','failed'].includes(x.status));list('pipeline-list',pending,x=>{const job=state.jobs.find(j=>j.id===x.job_id),n=el('div',undefined,'progress-row');n.append(el('span',x.status==='processing'?'◌':'·'),el('span',(job?jobData(job).title||'导入岗位':'岗位')+' · '+({pending:state.task==='paused'?'等待启动':'等待处理',processing:'正在匹配并准备沟通内容',failed:'未完成，请重试'}[x.status]),'grow'));if(x.status==='failed')n.append(action('重试',()=>api('/api/assistant/pipeline/'+x.job_id+'/retry','POST')));return n;},'');
 list('conversations',visibleConversations.slice(0,3),c=>{const n=el('div',undefined,'row');n.append(el('h3',c.recruiter),el('span',statusNames[c.state]||c.state,'pill'));if(c.channel==='mock')n.append(el('span','演示','pill demo'));n.append(action('查看',()=>showConversation(c.id),'text-button'));if(c.state!=='handoff')n.append(action('我来接手',()=>api('/api/conversations/'+c.id+'/handoff','POST'),'text-button'));return n;},'助手会为合适的岗位准备初次沟通。');
 if(!visibleConversations.some(c=>c.id===selected))selected=visibleConversations[0]?.id||null;
 if(!selected){$('demo-controls').hidden=true;$('conversation-detail').replaceChildren();renderOutbox();}
 if(selected){const c=state.conversations.find(x=>x.id===selected);$('demo-controls').hidden=c?.channel!=='mock';renderOutbox();}
 const handoffs=state.notifications.filter(x=>visibleConversations.some(c=>c.id===x.conversation_id));list('notifications',handoffs.slice(0,5),x=>{const n=el('div',undefined,'notification');n.append(el('strong',x.reason),el('small',new Date(x.created).toLocaleString('zh-CN')));return n;},'暂时没有需要接手的对话。');
 list('resumes',state.resumes.slice(0,5),x=>{const n=el('div',undefined,'row');n.append(el('h3',x.name),el('span',x.id===state.current_resume?'使用中':'历史版本','pill'));if(x.id!==state.current_resume)n.append(action('使用这份',()=>api('/api/resumes/'+x.id+'/select','POST'),'text-button'));return n;});
 if(currentResume!==state.current_resume){currentResume=state.current_resume;$('resume-preview').textContent=(await api('/api/resumes/'+currentResume)).text;}
 if(initial){const p=state.preferences;$('cities').value=p.cities.join('，');$('keywords').value=p.keywords.join('，');$('remote').checked=p.remote_acceptable;$('daily-min').value=p.salary_daily[0];$('daily-max').value=p.salary_daily[1];$('monthly-min').value=p.salary_monthly[0];$('monthly-max').value=p.salary_monthly[1];}
 renderApplications();
 $('system-summary').textContent = state.model_mode === 'mock' ? '本机规则分析（Mock）' : '已配置在线模型';
 for(const key of opened)document.querySelectorAll('details[data-key]').forEach(n=>{if(n.dataset.key===key)n.open=true;});
 initialized=true;
 }finally{refreshing=false;}}
$('start').onclick=()=>run(async()=>{
 await api('/api/assistant/start','POST',{channel:'boss'});
 notice('已开启本地岗位分析。');
},$('start'));
$('pause').onclick=()=>run(()=>api('/api/task','POST',{action:'pause'}),$('pause'));
$('demo').onclick=()=>run(async()=>{await api('/api/assistant/demo','POST');selected=null;tab('assistant');notice('模拟演示已启动。');},$('demo'));
$('upload-form').onsubmit=e=>{e.preventDefault();run(async()=>{const f=new FormData();f.append('file',$('resume-file').files[0]);await api('/api/resumes','POST',f);notice('简历已更新，后续分析和回复会使用这份资料。');});};
$('preferences-form').onsubmit=e=>{e.preventDefault();const split=x=>x.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean);run(async()=>{await api('/api/preferences','PUT',{cities:split($('cities').value),keywords:split($('keywords').value),remote_acceptable:$('remote').checked,salary_daily:[+$('daily-min').value,+$('daily-max').value],salary_monthly:[+$('monthly-min').value,+$('monthly-max').value]});notice('偏好已保存。');});};
$('job-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/jobs/import','POST',{jobs:[{title:$('job-title').value||null,location:$('job-city').value||null,company:$('job-company').value||null,source_url:$('job-url').value||null,raw_jd:$('job-jd').value,source:'manual'}]});notice(state.task==='paused'?'岗位已保存，点击开始后自动匹配。':'岗位已加入自动处理，无需再点匹配或创建会话。');});};
$('jobs-file').onchange=()=>run(async()=>{
 const input=$('jobs-file');
 try {
  const file=input.files[0];
  if(!file)return;
  const value=JSON.parse(await file.text());
  const jobs=Array.isArray(value)?value:value?.jobs;
  if(!Array.isArray(jobs))throw Error('请导入岗位数组或包含 jobs 数组的 JSON。');
  const result=await api('/api/jobs/import','POST',{jobs});
  notice('已接收 '+result.jobs.length+' 条岗位记录，重复岗位沿用原有进度。');
 } finally { input.value=''; }
});
async function incoming(text){if(!selected)throw Error('请先打开模拟会话。');await api('/api/conversations/'+selected+'/messages','POST',{platform_id:crypto.randomUUID(),text});await showConversation(selected);}
$('demo-question').onclick=()=>run(()=>incoming('接触过RAG吗？'),$('demo-question'));
$('demo-invite').onclick=()=>run(()=>incoming('邀请你明天下午三点参加面试'),$('demo-invite'));
$('receive').onclick=()=>run(()=>incoming($('incoming').value),$('receive'));
$('rebuild').onclick=()=>run(async()=>{await api('/api/knowledge/rebuild','POST');notice('资料索引已更新。');},$('rebuild'));
const applicationNames={review:'待复核',shortlisted:'准备联系',applied:'本人已投递',follow_up:'待跟进',interview:'面试',closed:'已结束'};
let editingApplication=null;

function localDate(){
 const now=new Date();
 return [now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
}

function editApplication(row, entry){
 editingApplication=row.id;
 $('application-title').textContent='跟进 · '+(jobData(row).title||'未命名岗位');
 $('application-status').value=entry.status||'review';
 $('application-date').value=entry.due_on||'';
 $('application-note').value=entry.note||'';
 $('application-form').hidden=false;
 $('application-status').focus();
}

function renderApplications(){
 const entries=new Map(state.applications.map(entry=>[entry.job_id,entry]));
 const rows=state.jobs.slice().reverse().sort((a,b)=>{
  const rank=row=>{
   const entry=entries.get(row.id);
   if(isDemoJob(row))return '3';
   if(entry?.status==='closed')return '2';
   return entry?.due_on?'0'+entry.due_on:'1';
  };
  return rank(a).localeCompare(rank(b));
 });
 const today=localDate();
 list('record-list',rows,row=>{
  const card=el('article');
  card.append(jobCard(row));
  if(isDemoJob(row))return card;
  const entry=entries.get(row.id)||{};
  const controls=el('div',undefined,'actions');
  controls.append(el('span',applicationNames[entry.status||'review'],'pill'));
  if(entry.due_on){
   const prefix=entry.status==='closed'?'截止 ':entry.due_on<today?'已逾期 · ':entry.due_on===today?'今天截止 · ':'截止 ';
   controls.append(el('span',prefix+entry.due_on));
  }
  const edit=el('button','更新跟进','secondary');
  edit.onclick=()=>editApplication(row,entry);
  controls.append(edit);
  try {
   const url=new URL(jobData(row).source_url);
   if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password){
    const link=el('a','打开岗位原页');
    link.href=url.href;
    link.target='_blank';
    link.rel='noopener noreferrer';
    controls.append(link);
   }
  } catch {}
  card.append(controls);
  if(entry.note)card.append(el('p',entry.note,'muted'));
  return card;
 },'队列还是空的。上传岗位 JSON 或手动添加岗位后，就可以在这里复核和跟进。');
}
$('application-cancel').onclick=()=>{
 editingApplication=null;
 $('application-form').hidden=true;
};
$('application-form').onsubmit=event=>{
 event.preventDefault();
 if(!editingApplication)return;
 run(async()=>{
  await api('/api/applications/'+editingApplication,'PUT',{
   status:$('application-status').value,
   due_on:$('application-date').value||null,
   note:$('application-note').value
  });
  $('application-cancel').click();
  notice('跟进已保存。');
 },event.submitter);
};
setInterval(()=>{
 if(!busy&&document.visibilityState==='visible'&&!$('discovery').classList.contains('active')) {
  refresh(!initialized).catch(e=>notice(e.message,true));
 }
},2500);

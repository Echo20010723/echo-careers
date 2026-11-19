(() => {
 let stopped=false,timer=null,inflight=false,lastSent='',latest=null,enabled=true;
 const done=new Set();
 const visible=n=>n&&n.getClientRects().length>0;
 function view(){
  const snapshot=globalThis.readBossSnapshot();
  const anchors=[...document.querySelectorAll('a[href]')].filter(visible);
  const jobs=[];const seen=new Set();
  for(const a of anchors){try{const u=new URL(a.href,location.href);if(['zhipin.com','www.zhipin.com'].includes(u.hostname)&&u.protocol==='https:'&&u.pathname.startsWith('/job_detail/')&&!u.searchParams.has('_security_check')&&!seen.has(u.href)){jobs.push({href:u.href,title:a.innerText.trim().slice(0,200)});seen.add(u.href);}}catch{}if(jobs.length>=12)break;}
  snapshot.job_links=jobs;
  snapshot.cards=[...document.querySelectorAll('.job-card-wrap')].filter(visible).slice(0,12).map(n=>({key:n.innerText.trim().replace(/\s+/g,' ').slice(0,1200),title:(n.querySelector('.job-name')?.innerText||'').trim().slice(0,300),active:n.classList.contains('active')}));
  if(snapshot.job){const active=[...document.querySelectorAll('.job-card-wrap.active')].find(visible);const footer=active?.querySelector('.job-card-footer');if(!snapshot.job.company&&footer)snapshot.job.company=footer.innerText.trim().split('\n').map(x=>x.trim()).find(Boolean)?.slice(0,300)||null;}
  snapshot.search_available=[...document.querySelectorAll('input')].some(n=>visible(n)&&n.placeholder==='搜索职位、公司')&&[...document.querySelectorAll('a.search-btn')].some(n=>visible(n)&&n.innerText.trim()==='搜索');
  snapshot.navigate_jobs=anchors.some(a=>{try{return a.innerText.trim()==='职位'&&new URL(a.href,location.href).pathname==='/web/geek/jobs'}catch{return false}});
  snapshot.view=location.pathname.includes('/job_detail/')?'job_detail':location.pathname.startsWith('/web/geek/jobs')?'jobs':location.pathname.startsWith('/web/geek/chat')?'chat':'home';
  snapshot.diagnostic={classes:[...new Set([...document.querySelectorAll('[class]')].filter(visible).map(n=>typeof n.className==='string'?n.className:'').filter(s=>/job|search|card|detail/.test(s)))].slice(0,65)};
  return snapshot;
 }
 async function request(payload,type='BOSS_SNAPSHOT'){return await chrome.runtime.sendMessage({type,payload});}
 async function command(c){
  if(!c||done.has(c.id)||!enabled)return;
  const claim=await request({id:c.id},'BOSS_CLAIM');if(!claim.ok||!claim.claimed)return;done.add(c.id);
  let success=false,reason='';
  try{
   const fresh=globalThis.readBossSnapshot();if(fresh.safety||!fresh.authenticated)throw Error('LOGIN_OR_VERIFICATION_REQUIRED');
   if(c.kind==='navigate_jobs'){
    const links=[...document.querySelectorAll('a[href]')].filter(a=>visible(a)&&a.innerText.trim()==='职位'&&new URL(a.href,location.href).pathname==='/web/geek/jobs');
    if(links.length!==1)throw Error('JOBS_LINK_AMBIGUOUS');const ack=await request({kind:'ack',command_id:c.id,success:true},'BOSS_ACK');if(!ack.ok||ack.enabled===false)return;links[0].click();return;
   }else if(c.kind==='search'){
    const inputs=[...document.querySelectorAll('input')].filter(n=>visible(n)&&n.placeholder==='搜索职位、公司');
    const buttons=[...document.querySelectorAll('a.search-btn')].filter(n=>visible(n)&&n.innerText.trim()==='搜索');
    if(inputs.length!==1||buttons.length!==1)throw Error('SEARCH_CONTROL_CHANGED');
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(inputs[0],c.query);
    inputs[0].dispatchEvent(new Event('input',{bubbles:true}));inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise(r=>setTimeout(r,150));const ack=await request({kind:'ack',command_id:c.id,success:true},'BOSS_ACK');if(!ack.ok||ack.enabled===false)return;buttons[0].click();return;
   }else if(c.kind==='open_card'){
    const cards=[...document.querySelectorAll('.job-card-wrap')].filter(n=>visible(n)&&n.innerText.trim().replace(/\s+/g,' ').slice(0,1200)===c.key);
    if(cards.length!==1)throw Error('JOB_CARD_CHANGED');
    if(cards[0].classList.contains('active')){await request({kind:'ack',command_id:c.id,success:true},'BOSS_ACK');return;}
    const ack=await request({kind:'ack',command_id:c.id,success:true},'BOSS_ACK');if(!ack.ok||ack.enabled===false)return;(cards[0].querySelector('.job-card-box')||cards[0]).click();return;
   }else if(c.kind==='open_job'){
    const url=new URL(c.href);if(!['www.zhipin.com','zhipin.com'].includes(url.hostname)||!url.pathname.startsWith('/job_detail/')||url.protocol!=='https:')throw Error('UNEXPECTED_JOB_LINK');
    const anchors=[...document.querySelectorAll('a[href]')].filter(a=>visible(a)&&a.href===c.href);if(!anchors.length)throw Error('JOB_LINK_NO_LONGER_VISIBLE');
    success=true;
    const ack=await request({kind:'ack',command_id:c.id,success:true},'BOSS_ACK');if(!ack.ok||ack.enabled===false)return;
    location.assign(anchors[0].href);return;
   }else throw Error('UNSUPPORTED_ACTION');
  }catch(error){reason=String(error.message||'ACTION_FAILED').slice(0,80);}
  await request({kind:'ack',command_id:c.id,success,error:reason},'BOSS_ACK').catch(()=>{});
 }
 async function transmit(force=false,cached=false){
  if(inflight||stopped)return;
  inflight=true;
  try{
   if(enabled&&!cached)latest=view();
   const fingerprint=JSON.stringify(latest);
   if(!latest||(!force&&fingerprint===lastSent))return;
   const result=await request({kind:'snapshot',snapshot:latest});
   if(!result.ok)return;
   lastSent=fingerprint;const wasEnabled=enabled;enabled=result.enabled!==false;if(enabled&&!wasEnabled)setTimeout(()=>transmit(true),0);
   if(enabled&&result.command)await command(result.command);
  }catch{}finally{inflight=false;}
 }
 const observer=new MutationObserver(()=>{if(!enabled)return;clearTimeout(timer);timer=setTimeout(()=>transmit(),900);});
 observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true});
 // Heartbeat only contacts localhost with the cached snapshot. It never reloads, navigates or re-reads BOSS.
 setInterval(()=>transmit(true,true),15000);
 transmit(true);
})();

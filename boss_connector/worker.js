let configuration=null;
async function config(){if(!configuration){const r=await fetch(chrome.runtime.getURL('connection.json'));configuration=await r.json();}return configuration;}
async function send(payload){const c=await config();const r=await fetch(c.endpoint+'/api/boss/session/event',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+c.token},body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('LOCAL_SERVICE_'+r.status);return await r.json();}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(!['BOSS_SNAPSHOT','BOSS_ACK','BOSS_CLAIM'].includes(message.type))return;
 const c=config();c.then(async cfg=>{const host=new URL(sender.url||'').hostname;if(!cfg.allowed_hosts.includes(host))throw Error('UNEXPECTED_HOST');if(message.type==='BOSS_CLAIM'){const key='command:'+message.payload.id;const previous=await chrome.storage.session.get(key);if(previous[key]){respond({ok:true,claimed:false});return;}await chrome.storage.session.set({[key]:true});respond({ok:true,claimed:true});return;}const result=await send(message.payload);respond({ok:true,...result});}).catch(()=>respond({ok:false,reason:'本机助手暂不可用，页面不会刷新或重试点击。'}));
 return true;
});
let starting=null;
function startup(){
 if(starting)return starting;
 starting=(async()=>{
  await send({kind:'hello'}).catch(()=>{});
  try{
   const tabs=await chrome.tabs.query({});
   const boss=tabs.some(t=>{try{return ['www.zhipin.com','zhipin.com'].includes(new URL(t.url||t.pendingUrl||'').hostname)}catch{return false}});
   await send({kind:'browser',pages:tabs.map(t=>{try{const u=new URL(t.url||t.pendingUrl||'');return u.origin+u.pathname}catch{return 'address_unavailable'}}).slice(0,30)}).catch(()=>{});
   if(!boss)await chrome.tabs.create({url:'https://www.zhipin.com/'});
  }catch{await send({kind:'browser',pages:[],error:'BROWSER_OPEN_FAILED'}).catch(()=>{});}
 })();return starting;
}
chrome.runtime.onInstalled.addListener(()=>startup());
chrome.runtime.onStartup.addListener(()=>startup());

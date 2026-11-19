import json,re,hashlib,threading,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from .settings import Settings
from .store import Store,uid,now,dumps
from .resumes import Resumes,digest
from .providers import MockProvider,OpenAIProvider
from .rag import Rag
from .schemas import JobInput,JobRequirement,MatchAnalysis
from .matching import calculate
from .policy import handoff_reason,unknown_fact,unsafe_instruction,validate_reply
from .channels import MockChannel,BossChannel
from .errors import AppError
from .audit import Audit
from .applications import job_link
from .automation import Automation
from .browser_share import BrowserShare
from .managed_boss import ManagedBoss
from .boss_outreach import inquiry_candidate,inquiry_text,is_demo_payload

class Service:
    def __init__(self,settings,recover=True):
        self.settings=settings; self.store=Store(settings.data_dir/'assistant.sqlite3',recover=recover); self.audit=Audit(settings.root,settings.data_dir)
        self.resumes=Resumes(self.store,settings); self.resumes.seed()
        self.provider=MockProvider(settings,self.store,self.audit)
        if settings.model_mode == 'live':
            try:
                self.provider=OpenAIProvider(settings,self.store,self.audit)
            except AppError as error:
                if error.code != 'SERVICE_NOT_CONFIGURED':
                    raise
        rag_settings = settings.model_copy(update={'model_mode':'mock'}) if self.provider.simulated else settings
        self.rag=Rag(rag_settings,self.store,self.audit); self.channels={'mock':MockChannel(self.store)}
        if not settings.public_mode: self.channels['boss']=BossChannel(settings)
        self.action_gate=threading.RLock()
        if not self.store.get('preferences'):
            path = settings.root/'knowledge_base/preferences/personal_preferences.json'
            p = {'cities':['上海'], 'search_keywords':['Agent 开发实习','大模型应用实习','Python 后端实习']}
            if not settings.use_demo_resume and path.is_file():
                p = json.loads(path.read_text(encoding='utf-8'))['job_preferences']
            self.store.set('preferences',{'cities':p['cities'],'keywords':p['search_keywords'],'remote_acceptable':True,'salary_daily':[100,600],'salary_monthly':[3000,8000]})
        if not self.store.get('workflow_defaults_v2'):
            self.store.set('mode', settings.default_mode)
            self.store.set('workflow_defaults_v2', True)
        self.automation = Automation(self)
        self.browser_share=None if settings.public_mode else BrowserShare(self)
        self.managed_boss=None if settings.public_mode else ManagedBoss(self)
        if not settings.public_mode and not self.settings.boss_cdp_enabled and not self.store.get('browser_share_auth'):
            self.channels['boss']._set('browser_share_required','网页接管和后台读取已停止。可先粘贴岗位，或配对单次分享组件；不会刷新BOSS。')
        if not settings.public_mode and self.store.get('browser_share_auth'):
            self.channels['boss']._set('browser_share_connected','浏览器分享已配对，仅在本人点击时接收当前岗位；不会刷新或接管页面。')

    def prompt(self,name,**kwargs):
        text=(self.settings.root/'prompts'/name).read_text(encoding='utf-8')
        return text.format(**kwargs),digest(text)
    def import_job(self,item):
        payload=item.model_dump(exclude_none=True)
        # Trusted schema allowlist prevents test labels leaking into model calls.
        if payload['source'] == 'manual':
            link = job_link(payload.get('source_url'))
            if link and not link.endswith('/'):
                identity = ['manual-link', link]
            else:
                fields = ('company', 'title', 'location', 'raw_jd')
                identity = ['manual-text', *(
                    ' '.join((payload.get(field) or '').split()) for field in fields
                )]
            key = digest(dumps(identity))
            jid = uid()
            with self.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,NULL)', (jid, key, dumps(payload)))
                return dict(db.execute('SELECT * FROM jobs WHERE source_key=?', (key,)).fetchone())
        key=digest(dumps({'source':payload['source'],'id':payload.get('id'),'url':payload.get('source_url'),'company':payload.get('company'),'title':payload.get('title'),'raw_jd':payload['raw_jd']}))
        existing=self.store.one('SELECT * FROM jobs WHERE source_key=?',(key,))
        if existing: return existing
        jid=uid()
        with self.store.tx() as db: db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,NULL)',(jid,key,dumps(payload)))
        return self.store.one('SELECT * FROM jobs WHERE source_key=?',(key,))
    def match(self,jid):
        row=self.store.one('SELECT * FROM jobs WHERE id=?',(jid,))
        if not row: raise AppError('INVALID_INPUT')
        job=json.loads(row['payload']); resume=self.resumes.current(); pref=self.store.get('preferences')
        prompt,extract_hash=self.prompt('jd_extract_prompt.txt',jd_text=dumps(job))
        req,meta_extract=self.provider.run('extract',prompt,JobRequirement,{'job':job})
        # Extracted phrases must be traceable to source; mock may strip simple requirement prefixes.
        source=(job.get('title') or '')+' '+job['raw_jd']
        if req.job_title and req.job_title not in source: raise AppError('MODEL_OUTPUT_INVALID')
        for value in req.required_skills+req.preferred_skills+req.responsibilities:
            if unsafe_instruction(value): raise AppError('MODEL_OUTPUT_INVALID')
        prompt,match_hash=self.prompt('match_prompt.txt',resume_context=resume['text'],job_context=dumps({'job':job,'requirement':req.model_dump()}),preferences_context=dumps(pref),match_weights=dumps(self.settings.weights))
        analysis,meta=self.provider.run('match',prompt,MatchAnalysis,{'resume':resume,'requirement':req,'job':job})
        result=calculate(job,req,analysis,resume,pref,self.settings.weights)
        result.update(job_id=jid,job_title=job.get('title'),company=job.get('company'),provider=meta,extraction_provider=meta_extract,prompt_versions={'extract':extract_hash,'match':match_hash})
        with self.store.tx() as db: db.execute('UPDATE jobs SET analysis=? WHERE id=?',(dumps(result),jid))
        return result
    def create_conversation(self,data):
        if not self.store.one('SELECT id FROM jobs WHERE id=?',(data.job_id,)): raise AppError('INVALID_INPUT')
        resume=self.resumes.current(); cid=uid()
        with self.store.tx() as db:
            db.execute('INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?,?,?,?)',(cid,data.job_id,data.recruiter,data.channel,data.account,'active',resume['id'],now()))
        return self.store.one('SELECT * FROM conversations WHERE job_id=? AND recruiter=? AND channel=? AND account=? AND resume_id=?',(data.job_id,data.recruiter,data.channel,data.account,resume['id']))
    def conversation(self,cid):
        c=self.store.one('SELECT * FROM conversations WHERE id=?',(cid,))
        if not c: raise AppError('INVALID_INPUT')
        return c
    def handoff(self,cid,reason):
        with self.action_gate, self.store.tx() as db:
            row=db.execute('SELECT state FROM conversations WHERE id=?',(cid,)).fetchone()
            if not row: raise AppError('INVALID_INPUT')
            db.execute("UPDATE conversations SET state='handoff' WHERE id=?",(cid,))
            db.execute("UPDATE outbox SET status='cancelled' WHERE conversation_id=? AND status IN ('draft','queued','blocked')",(cid,))
            if row['state']!='handoff': db.execute('INSERT INTO notifications VALUES(?,?,?,?)',(uid(),cid,reason,now()))
        self.audit.event('handoff_required',uid(),stage='workflow',status='handoff')
    def queue(self,cid,text,kind,message_id=None,blocked=False,meta=None):
        resume=self.resumes.current(); c=self.conversation(cid); mode=self.store.get('mode'); authorized=self.store.get('authorized_channel')==c['channel']
        with self.action_gate,self.store.tx() as db:
            current=db.execute('SELECT state,resume_id FROM conversations WHERE id=?',(cid,)).fetchone()
            current_rid=json.loads(db.execute("SELECT value FROM state WHERE key='current_resume'").fetchone()[0])
            if current['state']=='handoff' or current['resume_id']!=resume['id'] or current_rid!=resume['id']: raise AppError('CONFLICT')
            if message_id:
                newer=db.execute('SELECT 1 FROM messages WHERE conversation_id=? AND rowid>(SELECT rowid FROM messages WHERE id=?) LIMIT 1',(cid,message_id)).fetchone()
                if newer: raise AppError('CONFLICT')
            if kind=='greeting':
                existing=db.execute("SELECT * FROM outbox WHERE conversation_id=? AND kind='greeting' AND status<>'cancelled'",(cid,)).fetchone()
                if existing: return dict(existing)
            oid=uid(); status='blocked' if blocked else 'queued' if mode=='auto' and authorized else 'draft'
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?,?,?)',(oid,cid,message_id,resume['id'],resume['version'],kind,text,status,dumps(meta or {}),now()))
        if status=='queued' and self.store.get('task')=='running' and c['channel']=='mock': return self.send(oid)
        return self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
    def greeting(self,cid):
        c=self.conversation(cid); job=self.store.one('SELECT analysis FROM jobs WHERE id=?',(c['job_id'],))
        if not job or not job['analysis']: raise AppError('CONFLICT')
        result=json.loads(job['analysis']); resume=self.resumes.current()
        if c['channel']=='boss':
            payload=json.loads(self.store.one('SELECT payload FROM jobs WHERE id=?',(c['job_id'],))['payload'])
            allowed,reason=inquiry_candidate(payload,self.store.get('preferences'))
            if allowed and result['score_detail']['eligibility_status']!='ineligible':
                with self.action_gate,self.store.tx() as db:
                    old=db.execute("SELECT id,result FROM outbox WHERE conversation_id=? AND kind='greeting' AND status IN ('draft','blocked','queued')",(cid,)).fetchall()
                    for item in old:
                        if json.loads(item['result']).get('generation')!='rule_based_initial_inquiry':
                            db.execute("UPDATE outbox SET status='cancelled' WHERE id=? AND status IN ('draft','blocked','queued')",(item['id'],))
                return self.queue(cid,inquiry_text(payload),'greeting',meta={'simulated':False,'generation':'rule_based_initial_inquiry','attachment_status':'not_requested','screening_reason':reason})
        blocked=result['resume_version']!=resume['version'] or result['score_detail']['eligibility_status'] in {'ineligible','needs_confirmation'} or result['score_detail']['missing_essential_input'] or result['score_detail']['recommendation'] not in {'建议优先沟通投递','可以尝试投递'}
        return self.queue(cid,'您好，我对这个岗位很感兴趣，希望进一步了解岗位职责和实习机会，方便沟通吗？','greeting',blocked=blocked,meta={'simulated':c['channel']=='mock','attachment_status':'not_requested','match':result['score_detail']['recommendation']})
    def receive(self,cid,data,retry=False):
        c=self.conversation(cid)
        if c['channel']!='mock': raise AppError('CHANNEL_BLOCKED')
        if data.observed_at:
            try:
                timestamp=datetime.fromisoformat(data.observed_at.replace('Z','+00:00'))
                if timestamp.tzinfo is None: raise ValueError()
            except ValueError: raise AppError('INVALID_INPUT')
            if timestamp<datetime.fromisoformat(c['created']): return {'state':'historical_ignored','simulated':True}
        dedupe='platform:'+data.platform_id if data.platform_id else 'fallback:'+digest(re.sub(r'\s+',' ',data.text.strip()))
        mid=uid()
        with self.store.tx() as db:
            old=db.execute('SELECT * FROM messages WHERE conversation_id=? AND dedupe_key=?',(cid,dedupe)).fetchone()
            if old:
                if not retry or old['status']!='failed': return {'state':'duplicate','message_id':old['id'],'status':old['status']}
                mid=old['id'];db.execute("UPDATE messages SET status='processing' WHERE id=?",(mid,))
            else: db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,cid,dedupe,data.text,data.observed_at or now(),'processing'))
            db.execute("UPDATE outbox SET status='cancelled' WHERE conversation_id=? AND status IN ('draft','queued','blocked')",(cid,))
        try:
            reason=handoff_reason(data.text)
            if c['state']=='handoff' or reason:
                if reason: self.handoff(cid,reason)
                self.finish_message(mid,'handoff'); return {'state':'handoff','message_id':mid,'reason':reason,'simulated':True}
            resume=self.resumes.current()
            if c['resume_id']!=resume['id']: raise AppError('CONFLICT')
            retrieval=self.rag.search(data.text,resume)
            if unknown_fact(data.text) or unsafe_instruction(data.text):
                answer='这部分当前信息还需要我本人确认，确认后再向您说明。'; meta={'simulated':self.provider.simulated,'model':None,'usage':None}; blocked=True; version=None
            else:
                job=self.store.one('SELECT payload FROM jobs WHERE id=?',(c['job_id'],))
                context=dumps({'resume':resume['text'],'preferences':self.store.get('preferences'),'current_facts':{'arrival':None,'weekly_days':None,'duration':None},'job':json.loads(job['payload']),'conversation_state':c['state'],'relevant_facts':retrieval['facts']})
                prompt,version=self.prompt('interview_reply_prompt.txt',resume_context=context,retrieved_examples=dumps(retrieval['references']) if retrieval['references'] else '无相关示例',user_question=data.text)
                answer,meta=self.provider.run('reply',prompt,None,{'resume':resume,'question':data.text,'retrieval':retrieval,'preferences':self.store.get('preferences')})
                blocked=not validate_reply(answer,resume['text'],data.text)
            out=self.queue(cid,answer,'reply',mid,blocked=blocked,meta={**meta,**retrieval,'prompt_version':version})
            self.finish_message(mid,'processed')
            return {'state':'needs_confirmation' if blocked else 'processed','message_id':mid,'outbox':out,'simulated':True}
        except Exception:
            self.finish_message(mid,'failed'); raise
    def finish_message(self,mid,status):
        with self.store.tx() as db: db.execute('UPDATE messages SET status=? WHERE id=?',(status,mid))
    def send(self,oid,reviewed=False):
        with self.action_gate:
            item=self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
            if not item: raise AppError('INVALID_INPUT')
            if item['status']=='sent': return item
            if item['status'] not in {'draft','queued'}: raise AppError('CONFLICT')
            c=self.conversation(item['conversation_id']); resume=self.resumes.current()
            if c['state']=='handoff' or item['resume_id']!=resume['id'] or item['version']!=resume['version']: raise AppError('CONFLICT')
            if self.store.get('task')!='running': raise AppError('CONFLICT')
            auto=self.store.get('mode')=='auto' and self.store.get('authorized_channel')==c['channel']
            if not reviewed and not auto: raise AppError('CONFLICT')
            # Real BOSS sends cannot be enabled until the adapter has observed and verified page behavior.
            if c['channel']=='boss':
                payload=json.loads(self.store.one('SELECT payload FROM jobs WHERE id=?',(c['job_id'],))['payload'])
                if is_demo_payload(payload):raise AppError('CHANNEL_BLOCKED')
                if not self.settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
                if not self.settings.managed_boss_enabled or not self.settings.boss_auto_contact:raise AppError('CHANNEL_BLOCKED')
                with self.store.tx() as db:db.execute("UPDATE outbox SET status='queued' WHERE id=? AND status='draft'",(oid,))
                return self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
            with self.store.tx() as db:
                changed=db.execute("UPDATE outbox SET status='sending' WHERE id=? AND status IN ('draft','queued')",(oid,)).rowcount
                if changed!=1: raise AppError('CONFLICT')
            try:
                receipt=self.channels[c['channel']].send(item,c)
                status='sent'
            except TimeoutError: receipt={'status':'send_result_unknown','simulated':c['channel']=='mock'}; status='unknown'
            except AppError:
                self.pause('渠道限制或登录失效'); receipt={'status':'channel_paused','simulated':c['channel']=='mock'}; status='failed'
            except Exception: receipt={'status':'send_result_unknown','simulated':c['channel']=='mock'}; status='unknown'
            with self.store.tx() as db:
                db.execute('UPDATE outbox SET status=?,result=? WHERE id=?',(status,dumps({'generation':json.loads(item['result']), 'delivery':receipt}),oid))
                if status=='sent': db.execute("UPDATE conversations SET state='waiting_response' WHERE id=? AND state<>'handoff'",(c['id'],))
            return self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
    def reconcile(self,oid):
        item=self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
        if not item or item['status']!='unknown': raise AppError('CONFLICT')
        c=self.conversation(item['conversation_id']); receipt=self.channels[c['channel']].reconcile(item)
        if receipt:
            with self.store.tx() as db: db.execute("UPDATE outbox SET status='sent',result=? WHERE id=?",(dumps({'delivery':receipt,'generation':json.loads(item['result'])}),oid))
        return self.store.one('SELECT * FROM outbox WHERE id=?',(oid,))
    def pause(self,reason):
        with self.action_gate,self.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('task',dumps('paused')))
            db.execute('INSERT INTO notifications VALUES(?,?,?,?)',(uid(),None,reason,now()))
    def mode(self,data):
        if data.mode=='auto' and not data.authorized: raise AppError('CONFLICT')
        if data.mode=='auto' and data.channel=='boss': raise AppError('CHANNEL_BLOCKED')
        with self.action_gate:
            self.store.set('mode',data.mode); self.store.set('authorized_channel',data.channel if data.authorized else None)
        return {'mode':data.mode,'channel':data.channel,'simulated':data.channel=='mock'}
    def task(self,action):
        if self.settings.managed_boss_enabled:
            if action=='pause':self.managed_boss.pause()
            else:return self.start_assistant('boss')
        with self.action_gate:
            if action=='pause': self.pause('本人暂停')
            else: self.store.set('task','running')
        return {'task':self.store.get('task')}
    def drain(self):
        sent=[]
        for item in self.store.all("SELECT o.id FROM outbox o JOIN conversations c ON c.id=o.conversation_id WHERE o.status='queued' AND c.channel='mock' ORDER BY o.created"):
            try: sent.append(self.send(item['id']))
            except AppError: continue
        return sent
    def connect_boss(self, launch=True):
        if not self.settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        if not self.settings.boss_cdp_enabled:
            status=self.channels['boss']._set('browser_share_required','已停止会触发刷新的浏览器连接。登录窗口保持手动操作；请使用单次岗位分享或粘贴岗位，不会自动刷新页面。')
            return {'connection':status,'imported':False,'message':status['reason']}
        status=self.channels['boss'].connect_readonly(launch=launch)
        self.store.set('active_channel','boss')
        self.store.set('authorized_channel',None)
        if status.get('authenticated'):
            self.store.set('task','running')
            return self.sync_current_boss()
        if status['state']=='security_or_limit': self.pause(status['reason'])
        else: self.store.set('task','waiting_connection')
        return {'connection':status,'imported':False,'message':status['reason']}

    def sync_current_boss(self):
        if not self.settings.boss_cdp_enabled: return self.connect_boss(launch=False)
        previous_auth=self.channels['boss'].status().get('authenticated')
        snapshot=self.channels['boss'].read_visible_job()
        if previous_auth and snapshot['state']=='login_required':self.pause('BOSS登录失效，请本人重新登录。')
        if snapshot.get('authenticated') and self.store.get('task')=='waiting_connection': self.store.set('task','running')
        if snapshot['state']=='security_or_limit': self.pause(snapshot['reason'])
        if not snapshot.get('job'):
            return {'connection':self.channels['boss'].status(),'imported':False,'message':snapshot['reason']}
        row=self.import_job(JobInput.model_validate(snapshot['job']))
        self.automation.enroll(row)
        return {'connection':self.channels['boss'].status(),'imported':True,'job_id':row['id'],'message':'已读取真实岗位，正在自动匹配；真实发送尚未启用。'}

    def start_assistant(self, channel='boss'):
        if self.settings.automatic_pipeline:
            for row in self.store.all('SELECT * FROM jobs'):
                payload=json.loads(row['payload'])
                if is_demo_payload(payload)==(channel=='mock'):self.automation.enroll(row)
        if channel=='boss' and self.settings.managed_boss_enabled:
            status=self.managed_boss.start()
            return {'task':self.store.get('task'),'mode':'auto','channel':'boss','connection':status,'message':status['message']}
        self.store.set('active_channel',channel)
        if channel=='mock':
            self.store.set('authorized_channel','mock')
            self.store.set('task','running')
        else:
            # Selecting automatic mode is not evidence of a working browser adapter.
            self.store.set('authorized_channel',None)
            status=self.channels['boss'].status()
            self.store.set('task','running' if not self.settings.managed_boss_enabled or status.get('authenticated') else 'waiting_connection')
        return {'task':self.store.get('task'),'mode':self.store.get('mode'),'channel':channel,
                'message':'自动处理演示岗位与普通消息。' if channel=='mock' else '岗位导入后会在本机匹配并准备草稿。'}

    def snapshot(self):
        return {'applications':self.store.all('SELECT * FROM applications'),'managed_boss':self.managed_boss.status() if self.managed_boss else {'state':'disabled'},'mode':self.store.get('mode'),'task':self.store.get('task'),'model_mode':self.settings.model_mode,'vector_backend':self.settings.vector_backend,'current_resume':self.store.get('current_resume'),'automatic_pipeline':self.settings.automatic_pipeline,'pipeline':self.automation.list(),'active_channel':self.store.get('active_channel','boss'),'channels':{k:v.status() for k,v in self.channels.items()},'preferences':self.store.get('preferences'),'resumes':self.store.all('SELECT id,name,version,created FROM resumes ORDER BY created DESC'),'jobs':self.store.all('SELECT * FROM jobs'),'conversations':self.store.all('SELECT * FROM conversations ORDER BY created DESC'),'outbox':self.store.all('SELECT * FROM outbox ORDER BY created DESC'),'notifications':self.store.all('SELECT * FROM notifications ORDER BY created DESC')}
    def close(self):
        if 'boss' in self.channels: self.channels['boss'].close()

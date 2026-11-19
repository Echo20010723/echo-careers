"""BOSS-only managed browser. Native browser + preconfigured isolated connector.

No CDP, cookie access, fingerprint patches, same-page reloads or message sending.
"""
import hashlib,hmac,json,os,secrets,shutil,subprocess,threading,time
from datetime import datetime,timezone
from urllib.parse import urlsplit
from .errors import AppError
from .store import now,uid,dumps
from .schemas import JobInput

class ManagedBoss:
    def __init__(self,service):
        self.service=service;self.settings=service.settings;self.store=service.store
        self.lock=threading.RLock();self.process=None
        self.source=self.settings.root/'boss_connector'
        revision=hashlib.sha256(b''.join((self.source/name).read_bytes() for name in ['manifest.json','reader.js','observer.js','worker.js'])).hexdigest()[:12]
        self.directory=self.settings.data_dir/'managed_extensions'/revision
        self.profile=(self.settings.data_dir/'boss_app_profile').resolve()
        self.origin=self.extension_origin()
    def extension_origin(self):
        # Chromium GenerateIdForPath: native FilePath bytes; Windows drive uppercase, UTF-16LE.
        path=str(self.directory.resolve())
        if os.name=='nt':path=path[0].upper()+path[1:]
        raw=hashlib.sha256(path.encode('utf-16-le' if os.name=='nt' else 'utf-8')).hexdigest()[:32]
        return 'chrome-extension://'+''.join(chr(97+int(c,16)) for c in raw)
    def executable(self):
        candidates=list((self.settings.root/'data/browser_runtime').glob('chromium-*/chrome-win64/chrome.exe'))
        candidates.sort(key=lambda p:int(p.parents[1].name.split('-')[-1]),reverse=True)
        return candidates[0] if candidates else None
    def prepare(self,endpoint='http://127.0.0.1:8000',allowed_hosts=None):
        with self.lock:
            self.directory.mkdir(parents=True,exist_ok=True);self.profile.mkdir(parents=True,exist_ok=True)
            manifest=json.loads((self.source/'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('manifest_version')!=3 or not manifest.get('background'):raise AppError('BROWSER_PACKAGE_INVALID')
            for name in ['manifest.json','reader.js','observer.js','worker.js']:
                source=self.source/name
                if not source.is_file():raise AppError('BROWSER_PACKAGE_INVALID')
                shutil.copy2(source,self.directory/name)
            config_path=self.directory/'connection.json'
            existing={}
            if config_path.exists():
                try:existing=json.loads(config_path.read_text(encoding='utf-8'))
                except Exception:pass
            auth=self.store.get('managed_boss_auth')
            token=existing.get('token','')
            if not auth or auth.get('origin')!=self.origin or not hmac.compare_digest(auth.get('hash',''),hashlib.sha256(token.encode()).hexdigest()):
                token=secrets.token_urlsafe(32)
                self.store.set('managed_boss_auth',{'hash':hashlib.sha256(token.encode()).hexdigest(),'origin':self.origin})
            config={'endpoint':endpoint,'token':token,'allowed_hosts':allowed_hosts or ['www.zhipin.com','zhipin.com']}
            config_path.write_text(json.dumps(config),encoding='utf-8')
            return {'origin':self.origin,'directory':self.directory}
    def _profile_pid(self):
        if self.process and self.process.poll() is None:return self.process.pid
        if os.name!='nt':return None
        env=os.environ.copy();env['JOB_ASSISTANT_PROFILE']=str(self.profile)
        script="""$target=$env:JOB_ASSISTANT_PROFILE; Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($target) -and $_.CommandLine -notlike '*--type=*' } | Select-Object -First 1 -ExpandProperty ProcessId"""
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode!=0:raise AppError('BROWSER_PROCESS_CHECK_FAILED')
        value=result.stdout.strip();return int(value) if value.isdigit() else None
    def status(self):
        state=self.store.get('managed_boss_state',{'phase':'idle','message':'点击开始即可自动打开BOSS；只需本人登录。'})
        if not self.settings.boss_platform_authorized:
            state={**state,'phase':'permission_required','message':'缺少BOSS平台接入许可，自动浏览和发送未启用；已有岗位仍可在本机分析。','authenticated':False}
        elif state.get('phase') in {'running','reading','matching','searching','connecting','opening','waiting_login'}:
            seen=self.store.get('managed_boss_last_seen')
            if seen and (datetime.now(timezone.utc)-datetime.fromisoformat(seen)).total_seconds()>60:
                state={**state,'phase':'disconnected','message':'专用浏览器已断开，自动页面操作已停止。','authenticated':False}
        return {**state,'platform_authorized':self.settings.boss_platform_authorized,'enabled':self.settings.managed_boss_enabled,'sending_enabled':False,'manual_setup_required':False,'last_page':self.store.get('managed_boss_last_page')}
    def _state(self,phase,message,**kwargs):
        value={**self.store.get('managed_boss_state',{}),'phase':phase,'message':message,'updated_at':now(),**kwargs}
        self.store.set('managed_boss_state',value)
        self.service.channels['boss']._set('managed_'+phase,message,managed=True,authenticated=kwargs.get('authenticated',value.get('authenticated',False)))
        return self.status()
    def start(self):
        if not self.settings.boss_platform_authorized:
            plan=self.store.get('managed_boss_plan',{});plan.update(enabled=False,pending=None)
            self.store.set('managed_boss_plan',plan);self.store.set('authorized_channel',None)
            self.store.set('active_channel','boss');self.store.set('mode','auto');self.store.set('task','running')
            return self._state('permission_required','缺少BOSS平台接入许可，自动浏览和发送未启用；已有岗位仍可在本机分析。',authenticated=False)
        if not self.settings.managed_boss_enabled:return self._state('disabled','托管浏览器未启用。')
        with self.lock:
            self.prepare()
            executable=self.executable()
            if executable is None:return self._state('error','配套浏览器缺失，启动器需要先完成浏览器准备。',authenticated=False)
            plan=self.store.get('managed_boss_plan')
            if not plan or plan.get('phase') in {'failed','blocked','complete'}:
                plan={'enabled':True,'phase':'initial','seen':[],'pending':None,'search_done':False,'navigated':False,'imported':[]}
            else:plan['enabled']=True
            self.store.set('managed_boss_plan',plan)
            self.store.set('active_channel','boss');self.store.set('mode','auto');self.store.set('authorized_channel',None)
            self.store.set('task','waiting_connection')
            if self._profile_pid():
                return self._state('connecting','正在自动连接已打开的BOSS窗口，无需配对。')
            plan.update(pending=None,search_done=False,navigated=False,imported=[],seen_cards=[],settle_until=0)
            plan.pop('transition',None)
            self.store.set('managed_boss_plan',plan)
            args=[str(executable),'--user-data-dir='+str(self.profile),'--load-extension='+str(self.directory.resolve()),'--disable-extensions-except='+str(self.directory.resolve()),'--no-first-run','--no-default-browser-check','--disable-background-mode','--new-window','about:blank']
            self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return self._state('opening','正在打开BOSS专用窗口。请本人登录一次，之后自动识别并继续。',authenticated=False)
    def pause(self):
        with self.lock:
            plan=self.store.get('managed_boss_plan',{})
            plan['enabled']=False;plan['pending']=None
            self.store.set('managed_boss_plan',plan)
            return self._state('paused','已暂停自动任务，BOSS页面不会被刷新。')
    def authorize(self,origin,authorization):
        current=self.store.get('managed_boss_auth') or {}
        token=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if not current or origin!=self.origin or not token or not hmac.compare_digest(current.get('hash',''),hashlib.sha256(token.encode()).hexdigest()):raise AppError('BROWSER_TOKEN_INVALID')
    def query(self):
        value=(self.store.get('preferences',{}).get('keywords') or [self.settings.boss_search_query])[0].strip()[:70]
        return value if '实习' in value else value+' 实习'
    def command(self,plan,kind,**kwargs):
        cmd={'id':uid(),'kind':kind,**kwargs};plan['pending']=cmd
        self.store.set('managed_boss_plan',plan)
        return cmd
    def accept(self,event,origin,authorization):
        self.authorize(origin,authorization)
        if not self.settings.boss_platform_authorized:return {'enabled':False}
        with self.lock:
            self.store.set('managed_boss_last_seen',now())
            plan=self.store.get('managed_boss_plan',{'enabled':False,'pending':None,'phase':'initial','seen':[],'search_done':False,'navigated':False,'imported':[]})
            if event.kind=='browser':
                self.store.set('managed_boss_browser_pages',event.pages)
                if event.error:self._state('error','BOSS窗口未能自动打开，请重新开始一次。',authenticated=False)
                return {'enabled':plan.get('enabled',False)}
            if event.kind=='hello':
                if plan.get('enabled'):self._state('waiting_login','连接组件已自动就绪，等待BOSS页面和本人登录。',authenticated=False)
                return {'enabled':plan.get('enabled',False)}
            if event.kind=='ack':
                pending=plan.get('pending')
                if not pending or pending['id']!=event.command_id:return {'enabled':plan.get('enabled',False)}
                if not event.success:
                    plan.update(enabled=False,phase='failed',pending=None)
                    self.store.set('managed_boss_plan',plan)
                    self.store.set('task','paused')
                    self._state('error','页面控件变化，自动操作已停止：'+(event.error or 'ACTION_FAILED'))
                    return {'enabled':False}
                if pending['kind']=='search':plan['search_done']=True;plan['imported']=[];plan['seen_cards']=[]
                if pending['kind']=='navigate_jobs':plan['navigated']=True
                if pending['kind']=='open_job':plan['seen'].append(pending['href'])
                if pending['kind']=='open_card':
                    plan.setdefault('seen_cards',[]).append(pending['key'])
                    plan['transition']={'title':pending.get('title',''),'deadline':time.time()+35}
                plan['pending']=None;plan['settle_until']=time.time()+3;self.store.set('managed_boss_plan',plan)
                return {'enabled':plan.get('enabled',False)}
            snapshot=event.snapshot
            if snapshot is None:raise AppError('INVALID_INPUT')
            self.store.set('managed_boss_last_page',{'view':snapshot.view,'authenticated':snapshot.authenticated,'loading':snapshot.loading,'has_job':snapshot.job is not None,'search_available':snapshot.search_available,'navigate_jobs':snapshot.navigate_jobs,'cards':len(snapshot.cards),'checked_at':now()})
            if not plan.get('enabled'):return {'enabled':False}
            if snapshot.safety:
                plan.update(enabled=False,phase='blocked',pending=None)
                self.store.set('managed_boss_plan',plan);self.store.set('task','paused')
                self._state('blocked','BOSS需要本人验证或限制访问，已停止自动操作。',authenticated=False)
                return {'enabled':False}
            if snapshot.loading or time.time()<plan.get('settle_until',0):return {'enabled':True}
            if not snapshot.authenticated:
                self.store.set('task','waiting_connection')
                plan['pending']=None;self.store.set('managed_boss_plan',plan)
                self._state('waiting_login','请在BOSS窗口登录。登录后自动继续，无需再点连接。',authenticated=False)
                return {'enabled':True}
            self.store.set('task','running')
            self._state('running','已自动连接并识别登录，正在读取岗位和准备匹配。',authenticated=True)
            if snapshot.diagnostic:self.store.set('managed_boss_dom_observation',snapshot.diagnostic)
            transition=plan.get('transition')
            if transition:
                title=(snapshot.job.title or '') if snapshot.job else ''
                if ''.join(title.split()).lower()==''.join(transition['title'].split()).lower():
                    plan.pop('transition',None);self.store.set('managed_boss_plan',plan)
                elif time.time()<transition['deadline']:
                    self._state('reading','正在等待选中岗位的完整详情，不会重复点击。',authenticated=True)
                    return {'enabled':True}
                else:
                    plan.update(enabled=False,phase='failed',pending=None);self.store.set('managed_boss_plan',plan)
                    self.store.set('task','paused');self._state('error','岗位详情未能加载，已停止；没有重复点击或刷新。',authenticated=True)
                    return {'enabled':False}
            if not plan.get('pending') and self.settings.boss_auto_search:
                if snapshot.view=='home' and snapshot.navigate_jobs and not plan['navigated']:
                    return {'enabled':True,'command':self.command(plan,'navigate_jobs')}
                if snapshot.view=='jobs' and snapshot.search_available and not plan['search_done']:
                    self._state('searching','正在按偏好搜索岗位。',authenticated=True)
                    return {'enabled':True,'command':self.command(plan,'search',query=self.query())}
            if snapshot.job:
                active=next((c for c in snapshot.cards if c.active),None)
                if active and active.key not in plan.setdefault('seen_cards',[]):plan['seen_cards'].append(active.key)
                payload=snapshot.job.model_dump();payload['source']='boss_managed_browser'
                address=urlsplit(payload.get('source_url') or '')
                if address.scheme!='https' or address.hostname not in {'www.zhipin.com','zhipin.com'}:raise AppError('INVALID_INPUT')
                row=self.service.import_job(JobInput.model_validate(payload));self.service.automation.enroll(row)
                imported=plan.setdefault('imported',[])
                if row['id'] not in imported:imported.append(row['id'])
                self.store.set('managed_boss_plan',plan)
                self.store.set('managed_boss_last_job',row['id'])
                self._state('matching','已读取真实岗位，正在自动匹配。当前未启用真实发送。',authenticated=True,last_job_id=row['id'])
            if plan.get('pending'):return {'enabled':True,'command':plan['pending']}
            if not self.settings.boss_auto_search:return {'enabled':True}
            if snapshot.view=='home' and snapshot.navigate_jobs and not plan['navigated']:
                return {'enabled':True,'command':self.command(plan,'navigate_jobs')}
            if snapshot.view=='jobs' and snapshot.search_available and not plan['search_done']:
                self._state('searching','正在按偏好搜索AI实习岗位。',authenticated=True)
                return {'enabled':True,'command':self.command(plan,'search',query=self.query())}
            if len(plan.get('imported',[]))>=self.settings.boss_batch_size:
                plan.update(phase='complete',enabled=False);self.store.set('managed_boss_plan',plan)
                self._state('complete','本轮岗位已读取，匹配和草稿在助手中自动处理。',authenticated=True)
                return {'enabled':False}
            for card in snapshot.cards:
                if not card.active and card.key and card.key not in plan.get('seen_cards',[]):
                    return {'enabled':True,'command':self.command(plan,'open_card',key=card.key,title=card.title)}
            if snapshot.empty:
                plan.update(enabled=False,phase='complete');self.store.set('managed_boss_plan',plan)
                self._state('complete','当前搜索没有结果，可在我的资料中调整偏好后再开始。',authenticated=True)
                return {'enabled':False}
            for candidate in snapshot.job_links:
                parsed=urlsplit(candidate.href)
                if parsed.scheme=='https' and parsed.hostname in {'www.zhipin.com','zhipin.com'} and parsed.path.startswith('/job_detail/') and candidate.href not in plan['seen'] and '_security_check' not in parsed.query:
                    return {'enabled':True,'command':self.command(plan,'open_job',href=candidate.href)}
            return {'enabled':True}

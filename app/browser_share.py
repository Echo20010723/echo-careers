"""User-initiated current-tab sharing. No browser polling or browser control."""
import hashlib,hmac,re,secrets
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from .errors import AppError
from .schemas import JobInput
from .store import now,dumps

ORIGIN=re.compile(r'^chrome-extension://[a-p]{32}$')
def valid_origin(origin):return bool(origin and ORIGIN.fullmatch(origin))
def hashed(value):return hashlib.sha256(value.encode()).hexdigest()
class BrowserShare:
    def __init__(self,service):self.service=service;self.store=service.store
    def issue_code(self):
        if not self.service.settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        code=f'{secrets.randbelow(1000000):06d}';salt=secrets.token_hex(16)
        expires=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()
        self.store.set('browser_pair_code',{'hash':hashed(salt+code),'salt':salt,'expires':expires,'attempts':0})
        return {'code':code,'expires_at':expires,'message':'在浏览器分享组件输入此6位码，仅本次配对使用，2分钟有效。'}
    def pair(self,code,origin):
        if not self.service.settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        if not valid_origin(origin):raise AppError('BROWSER_ORIGIN_INVALID')
        with self.store.tx() as db:
            row=db.execute("SELECT value FROM state WHERE key='browser_pair_code'").fetchone()
            import json
            pending=json.loads(row['value']) if row else None
            if not pending or datetime.fromisoformat(pending['expires'])<datetime.now(timezone.utc):raise AppError('BROWSER_CODE_EXPIRED')
            valid=pending['attempts']<5 and hmac.compare_digest(pending['hash'],hashed(pending['salt']+code))
            if not valid:
                pending['attempts']+=1
                db.execute("UPDATE state SET value=? WHERE key='browser_pair_code'",(dumps(pending),))
            else:
                token=secrets.token_urlsafe(32)
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('browser_share_auth',dumps({'hash':hashed(token),'origin':origin,'paired_at':now()})))
                db.execute("DELETE FROM state WHERE key='browser_pair_code'")
        if not valid:raise AppError('BROWSER_PAIR_FAILED')
        self.service.channels['boss']._set('browser_share_connected','浏览器单次分享已配对。请在BOSS详情页点分享组件的“读取当前岗位并匹配”；不会刷新或发送。')
        return {'token':token,'message':'配对成功。只在本人点击时读取一次当前岗位。'}
    def authorize(self,origin,authorization):
        current=self.store.get('browser_share_auth')
        token=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if not current or not valid_origin(origin) or origin!=current['origin'] or not token or not hmac.compare_digest(current['hash'],hashed(token)):
            raise AppError('BROWSER_TOKEN_INVALID')
    def import_job(self,job,origin,authorization):
        if not self.service.settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        self.authorize(origin,authorization)
        parsed=urlsplit(job.source_url or '')
        if parsed.scheme!='https' or parsed.hostname not in {'www.zhipin.com','zhipin.com'} or parsed.username or parsed.port not in {None,443} or not parsed.path.startswith(('/web/geek/jobs','/job_detail/')):
            raise AppError('INVALID_INPUT')
        # This is an explicit human share, not a crawler or a claimed send result.
        payload=job.model_dump();payload['source']='boss_browser_share'
        row=self.service.import_job(JobInput.model_validate(payload))
        self.service.automation.enroll(row)
        self.store.set('active_channel','boss');self.store.set('authorized_channel',None);self.store.set('task','running')
        self.store.set('browser_share_last_import',{'job_id':row['id'],'received_at':now()})
        self.service.channels['boss']._set('browser_share_received','已收到本人单次分享的岗位，正在自动匹配。BOSS页面不受助手控制，真实发送未启用。')
        return {'job_id':row['id'],'message':'岗位已收到，正在自动匹配；不会刷新BOSS或发送真实消息。','model_mode':self.service.settings.model_mode,'sending_enabled':False}

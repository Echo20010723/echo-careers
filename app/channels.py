from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json,re
from .errors import AppError
from .store import now
from .browser_bridge import BrowserBridge

class MockChannel:
    def __init__(self,store): self.store=store; self.behavior='success'
    def status(self): return {'channel':'mock','simulated':True,'state':'ready'}
    def send(self,item,conversation):
        if self.behavior=='restricted': raise AppError('CHANNEL_BLOCKED')
        result={'simulated':True,'status':'greeting_sent' if item['kind']=='greeting' else 'reply_sent','receipt':'mock-'+item['id'],'verified_at':now()}
        if self.behavior!='timeout_before': self.store.set('receipt:'+item['id'],result)
        if self.behavior in {'timeout','timeout_before'}: raise TimeoutError()
        return result
    def reconcile(self,item): return self.store.get('receipt:'+item['id'])

class BossChannel:
    """Dedicated local browser. Closed tabs/contexts are recoverable on explicit open.

    Opening a browser is separate from verified login and from permission to send.
    No selectors or production actions are inferred from a successful navigation.
    """
    ENTRY_URL = 'https://www.zhipin.com/'

    def __init__(self, settings):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='boss-browser')
        self.context = self.page = self.engine = None
        self.saw_boss_page = False
        self.bridge = BrowserBridge(settings)
        self.last = self._state('not_connected', '点击连接BOSS，在打开的浏览器中登录。')

    def _state(self, state, reason, **extra):
        return {'channel': 'boss', 'simulated': False, 'state': state,
                'sending_verified': False, 'reason': reason, 'checked_at': now(),
                'browser': self.settings.boss_browser, 'sandbox_enabled': True, **extra}

    def status(self):
        return dict(self.last)

    def _set(self, state, reason, **extra):
        self.last = self._state(state, reason, **extra)
        return self.status()

    def _call(self, operation, timeout=55):
        from concurrent.futures import TimeoutError as FutureTimeout
        try:
            return self.executor.submit(operation).result(timeout=timeout)
        except FutureTimeout:
            return self._set('browser_busy', '浏览器暂未响应，请稍后检查页面，勿反复点击。')

    def open_manual(self):
        import subprocess
        executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Google/Chrome/Application/chrome.exe')] if p.is_file()),None)
        if not executable:return self._set('browser_unavailable','未找到Chrome，请从官网链接手动打开。')
        profile=(self.settings.data_dir/'boss_manual_chrome').resolve()
        subprocess.Popen([str(executable),'--user-data-dir='+str(profile),'--restore-last-session'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return self._set('manual_browser_opened','已打开保留原登录资料的普通Chrome。助手不会接管、刷新或点击这个窗口。')

    def connect_readonly(self, launch=True):
        return self._call(lambda:self._connect_readonly(launch),timeout=40)

    def _connect_readonly(self, launch):
        try:
            connected=self.bridge.connect(launch=launch)
            if connected['state']!='browser_connected':
                return self._set(connected['state'],connected.get('reason','连接未就绪。'),read_only=True)
            snapshot=self.bridge.snapshot()
            public={k:v for k,v in snapshot.items() if k not in {'state','reason','job'}}
            return self._set(snapshot['state'],snapshot['reason'],browser_connected=True,**public)
        except Exception:
            return self._set('connection_error','连接未完成，请检查专用Chrome窗口后重试。',read_only=True)

    def read_visible_job(self):
        def read():
            snapshot=self.bridge.snapshot()
            self._set(snapshot['state'],snapshot['reason'],browser_connected=snapshot['state'] not in {'security_or_limit','browser_closed'},**{k:v for k,v in snapshot.items() if k not in {'state','reason','job'}})
            return snapshot
        return self._call(read,timeout=20)

    def open(self):
        if not self.settings.boss_cdp_enabled: return self._set('browser_share_required','浏览器自动接管已停止，避免干扰你的BOSS页面。')
        return self._call(self._open)

    def _watch_page(self, page):
        def navigated(frame):
            if frame != page.main_frame:
                return
            from urllib.parse import urlparse
            if urlparse(frame.url).hostname in {'www.zhipin.com', 'zhipin.com'}:
                self.saw_boss_page = True
            elif frame.url == 'about:blank' and self.saw_boss_page:
                self._set('page_redirected', 'BOSS页面加载后跳回了空白页，自动连接未成功。请使用普通Chrome手动登录；助手已停止自动连接。', page_address='about:blank')
        if hasattr(page, 'on'):
            page.on('framenavigated', navigated)


    def _pick_page(self):
        from urllib.parse import urlparse
        if self.context is None:
            return None
        pages = [p for p in self.context.pages if not p.is_closed()]
        if self.page in pages:
            return self.page
        boss_pages = [p for p in pages if urlparse(p.url).hostname in {'www.zhipin.com', 'zhipin.com'}]
        self.page = (boss_pages or pages or [None])[-1]
        return self.page

    def _dispose(self):
        context, engine = self.context, self.engine
        self.context = self.page = self.engine = None
        for resource, method in [(context, 'close'), (engine, 'stop')]:
            if resource:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass

    def _ensure_page(self):
        # A user may close a tab or the whole browser. Never reuse that stale object.
        if self.context:
            try:
                page = self._pick_page()
                if page:
                    # pages[] alone can remain stale after an unexpected process exit.
                    page.title()
                    return page
                self.page = self.context.new_page()
                self._watch_page(self.page)
                return self.page
            except Exception:
                self._dispose()
        from playwright.sync_api import sync_playwright
        self.engine = sync_playwright().start()
        self.context = self.engine.chromium.launch_persistent_context(
            str(self.settings.data_dir / ('boss_profile_' + self.settings.boss_browser)), headless=False,
            channel=self.settings.boss_browser, accept_downloads=False, chromium_sandbox=True,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._watch_page(self.page)
        return self.page

    def _navigation_error(self, error):
        # Keep only safe error codes. Error strings may contain signed URLs or page text.
        text = str(error)
        match = re.search(r'net::(ERR_[A-Z_]+)', text)
        if match:
            code = match.group(1)
            reasons = {
                'ERR_NAME_NOT_RESOLVED': 'BOSS域名无法解析，请检查本机网络或DNS。',
                'ERR_PROXY_CONNECTION_FAILED': '浏览器代理连接失败，请检查本机代理是否可用。',
                'ERR_TUNNEL_CONNECTION_FAILED': '浏览器代理隧道连接失败，请检查代理。',
                'ERR_CONNECTION_TIMED_OUT': 'BOSS连接超时，可先在自己的浏览器检查能否正常访问。',
                'ERR_CONNECTION_RESET': '到BOSS的网络连接被重置。',
                'ERR_INTERNET_DISCONNECTED': '浏览器未连接网络。',
                'ERR_CERT_AUTHORITY_INVALID': '网站证书校验失败，请本人检查；助手不会跳过证书检查。',
            }
            return self._set('network_error', reasons.get(code, 'BOSS页面网络连接失败。'), error_code=code)
        if 'Timeout' in type(error).__name__:
            return self._set('navigation_timeout', '浏览器已打开，但BOSS加载较慢。请查看窗口，加载后点“我已登录，检查连接”。')
        if 'closed' in text.lower():
            return self._set('browser_closed', '浏览器已关闭，点击连接BOSS可以重新打开。')
        if 'SingletonLock' in text or 'user data directory is already in use' in text.lower():
            return self._set('profile_in_use', '专用浏览器资料被另一个窗口占用，请先关闭之前的专用窗口再连接。')
        return self._set('browser_unavailable', '专用'+('Chrome' if self.settings.boss_browser=='chrome' else 'Edge')+'未能启动，请确认已安装该浏览器。', error_code='BROWSER_START_FAILED')

    def _open(self):
        try:
            page = self._ensure_page()
            from urllib.parse import urlparse
            # Start at the public homepage, not an anonymous job-list deep link.
            # Do not refresh a live login form or an already-open job page.
            if urlparse(page.url).hostname not in {'www.zhipin.com', 'zhipin.com'}:
                page.goto(self.ENTRY_URL, wait_until='domcontentloaded', timeout=25000)
            page.bring_to_front()
            # Observe one bounded startup interval; do not retry a redirected page.
            page.wait_for_timeout(5000)
            return self._inspect()
        except Exception as error:
            return self._navigation_error(error)

    def retry_once(self):
        if not self.settings.boss_cdp_enabled: return self._set('browser_share_required','不会再次尝试导致刷新的连接，请使用单次岗位分享。')
        return self._call(self._retry_once)

    def _retry_once(self):
        try:
            page = self._pick_page()
            if not page:
                return self._open()
            page.reload(wait_until='domcontentloaded', timeout=25000)
            return self._inspect()
        except Exception as error:
            if 'closed' in str(error).lower():
                self._dispose()
                return self._open()
            return self._navigation_error(error)

    def inspect(self):
        return self._call(self._inspect, timeout=20)

    def _inspect(self):
        try:
            page = self._pick_page()
            if not page:
                if self.last['state']=='manual_browser_opened': return self.status()
                return self._set('browser_closed', '没有打开的专用浏览器，点击连接BOSS重新打开。')
            if page.url == 'about:blank' and self.saw_boss_page:
                return self._set('page_redirected', 'BOSS页面加载后跳回空白页，自动连接失败。请在普通Chrome中手动打开，助手不会循环重试。', page_address='about:blank')
            if page.url == 'about:blank':
                return self._set('blank_page', '窗口仍停在空白页，请点击连接BOSS打开首页。', page_address='about:blank')
            from urllib.parse import urlparse
            address=urlparse(page.url)
            safe_address=address.scheme+'://'+(address.hostname or '')+address.path
            text = page.locator('body').inner_text(timeout=6000)
            if re.search(r'验证码|安全验证|安全检查|访问异常|操作频繁|账号异常|访问受限|今日.*上限|点击.{0,4}验证|环境异常|请求异常', text) or re.search(r'security|captcha|verify', page.url, re.I):
                return self._set('security_or_limit', 'BOSS要求本人验证或限制了访问。请在浏览器中按平台提示处理，助手已暂停。')
            if page.url.startswith(('chrome-error:', 'edge-error:')):
                return self._set('network_error', '浏览器显示网络错误，请检查网络后手动重试。')
            if len(text.strip()) < 20:
                return self._set('page_loading', 'BOSS页面暂未加载出内容，请查看浏览器窗口。')
            if re.search(r'扫码登录|密码登录|登录注册|手机号登录|登录/注册', text):
                return self._set('login_required', 'BOSS页面已在专用'+('Chrome' if self.settings.boss_browser=='chrome' else 'Edge')+'中打开，请本人完成登录。', page_address=safe_address)
            return self._set('observation_required', '页面已打开，尚未验证账号和岗位读取；当前仅处理本地导入的岗位草稿。', page_address=safe_address)
        except Exception as error:
            if 'closed' in str(error).lower():
                self.page = None
                return self._set('browser_closed', '浏览器或标签页已关闭，点击连接BOSS重新打开。')
            return self._set('page_unavailable', '暂时无法读取这个标签页，请在专用浏览器打开BOSS首页后检查连接。')

    def search(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def read_job(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def open_conversation(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def apply_resume(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def fetch_messages(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def send(self, *args, **kwargs): raise AppError('CHANNEL_BLOCKED')
    def reconcile(self, *args, **kwargs): return None

    def close(self):
        try:
            self.executor.submit(lambda:(self.bridge.close(),self._dispose())).result(timeout=10)
        finally:
            self.executor.shutdown(wait=False)

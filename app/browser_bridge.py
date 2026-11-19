"""Read-only local CDP bridge for the project's dedicated human-operated Chrome.

No daily profiles, cookies, internal BOSS APIs, fingerprint changes, clicks or sends.
"""
import json,os,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit

class BrowserBridge:
    def __init__(self, settings):
        self.settings=settings
        self.profile=(settings.data_dir/'boss_manual_chrome').resolve()
        self.engine=self.browser=None
        self.profile.mkdir(parents=True,exist_ok=True)

    def port(self):
        try:
            # Only the port number is needed. Never expose the debugger capability path.
            value=int((self.profile/'DevToolsActivePort').read_text(encoding='utf-8').splitlines()[0])
            return value if 1<=value<=65535 else None
        except (OSError,ValueError,IndexError):return None

    def profile_running(self):
        if os.name!='nt':return False
        env=os.environ.copy();env['JOB_ASSISTANT_PROFILE']=str(self.profile)
        script = """$target=$env:JOB_ASSISTANT_PROFILE; $found=@(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($target) -and $_.CommandLine -notlike '*--type=*' }); if ($found.Count -gt 0) { 'running' }"""
        # No UI automation, shell interpolation, process termination or credential access.
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode!=0: raise RuntimeError('Dedicated browser process check failed')
        return result.stdout.strip()=='running'

    def connect(self,launch=False):
        from playwright.sync_api import sync_playwright
        if self.browser and self.browser.is_connected():return {'state':'browser_connected'}
        port=self.port()
        if port:
            try:
                if not self.engine:self.engine=sync_playwright().start()
                self.browser=self.engine.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',timeout=8000,no_defaults=True)
                return {'state':'browser_connected'}
            except Exception:
                self.browser=None
        if self.profile_running():
            return {'state':'connection_requires_restart','reason':'这个已登录窗口启动时没有开启本机连接接口。请只关闭BOSS专用Chrome窗口（不要退出账号），再点“连接已登录窗口”；会使用原资料重新打开。'}
        if not launch:
            return {'state':'not_connected','reason':'未找到可连接的专用Chrome，请点击连接已登录窗口。'}
        executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Google/Chrome/Application/chrome.exe')] if p.is_file()),None)
        if executable is None:return {'state':'browser_unavailable','reason':'未找到本机Chrome。'}
        subprocess.Popen([str(executable),'--user-data-dir='+str(self.profile),'--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--restore-last-session','--new-window','https://www.zhipin.com/web/geek/jobs'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            port=self.port()
            if port:
                try:
                    if not self.engine:self.engine=sync_playwright().start()
                    self.browser=self.engine.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',timeout=1000,no_defaults=True)
                    return {'state':'browser_connected'}
                except Exception:self.browser=None
            time.sleep(.25)
        return {'state':'connection_timeout','reason':'Chrome已尝试打开，但本机连接未就绪，请检查窗口后再连接。'}

    def page(self):
        if not self.browser or not self.browser.is_connected():return None
        pages=[p for context in self.browser.contexts for p in context.pages if not p.is_closed() and urlsplit(p.url).hostname in {'zhipin.com','www.zhipin.com'}]
        # Prefer the selected job view rather than an unrelated tab.
        job_pages=[p for p in pages if '/web/geek/jobs' in urlsplit(p.url).path or '/job_detail/' in urlsplit(p.url).path]
        for p in reversed(job_pages or pages):
            try:
                if p.evaluate('document.visibilityState')=='visible':return p
            except Exception:continue
        return (job_pages or pages or [None])[-1]

    def observe(self):
        if not self.browser or not self.browser.is_connected():return {'state':'browser_closed','authenticated':False,'reason':'专用Chrome连接已断开，请重新连接。','job':None}
        page=self.page()
        if page is None:return {'state':'boss_page_missing','reason':'已连接Chrome，请在该窗口打开BOSS岗位详情。'}
        address=urlsplit(page.url)
        if address.path.startswith('/web/geek/chat'):return {'state':'chat_view','reason':'当前是消息页，请先打开一个岗位详情用于只读验证。'}
        # DOM observation is explicitly requested job data, stored as a local development artifact, never an audit log.
        result=page.evaluate(r'''() => ({
          address: location.origin+location.pathname,
          headings: [...document.querySelectorAll('h1,h2,h3,h4')].filter(n=>n.getClientRects().length).map(n=>({tag:n.tagName,cls:n.className,text:n.innerText.slice(0,200)})).slice(0,30),
          links: [...document.querySelectorAll('a')].filter(n=>n.getClientRects().length&&/消息|登录|简历/.test(n.innerText)).map(n=>({text:n.innerText.slice(0,30),href:new URL(n.href,location.href).pathname,cls:n.className})).slice(0,12),
          regions: [...document.querySelectorAll('[class]')].filter(n=>n.getClientRects().length&&typeof n.className==='string'&&/job-detail|job-info|job-name|job-title|job-salary|company-name|job-card|job-sec|job-tags/.test(n.className)).slice(0,65).map(n=>({tag:n.tagName,cls:n.className,text:n.innerText.slice(0,700)}))
        })''')
        result.update(state='dom_observed',sending_verified=False)
        return result

    def snapshot(self):
        """Atomically read the visible page; a navigation is a normal, explicit state."""
        if not self.browser or not self.browser.is_connected():return {'state':'browser_closed','authenticated':False,'reason':'专用Chrome连接已断开，请重新连接。','job':None}
        page=self.page()
        if page is None:return {'state':'boss_page_missing','authenticated':False,'reason':'已连接Chrome，请在这个窗口打开BOSS岗位页。','job':None}
        try:
            result=page.evaluate(r'''() => {
              const visible=n=>n&&n.getClientRects().length>0;
              const nav=document.querySelector('.user-nav');
              const links=nav?[...nav.querySelectorAll('a')].filter(visible):[];
              const hasPath=path=>links.some(a=>{try{return new URL(a.href,location.href).pathname===path}catch{return false}});
              const authenticated=hasPath('/web/geek/chat')&&hasPath('/web/geek/resume')&&!links.some(a=>/登录\s*[/／]?\s*注册/.test(a.innerText));
              const body=document.body?.innerText||'';
              const safety=/^(安全验证|安全检查|访问受限)/.test(document.title)||/请完成.{0,8}验证|访问异常|账号异常|操作过于频繁/.test(body.slice(0,800));
              const heads=[...document.querySelectorAll('h1,h2,h3,h4,div,span,strong,p')].filter(n=>visible(n)&&n.innerText?.trim()==='职位描述');
              let region=null;
              for(const h of heads){let n=h;while(n&&n!==document.body){const t=n.innerText||'';if(t.includes('立即沟通')&&t.length>=80&&t.length<24000){region=n;break}n=n.parentElement}if(region)break}
              let job=null;
              if(region){
                const text=region.innerText.trim();
                const before=text.split('职位描述')[0].split('\n').map(s=>s.trim()).filter(Boolean);
                const title=before.find(t=>!/^(收藏|立即沟通|分享|举报)$/.test(t)&&!/^\d+(?:\.\d+)?\s*[-~—]/.test(t))||null;
                const salary=(before.join(' ').match(/\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*(?:[kK]|千|万|元)(?:[^\s]{0,8})?/)||[])[0]||null;
                const city=(before.join(' ').match(/上海|北京|苏州|杭州|深圳|成都|重庆|武汉|南京|广州/)||[])[0]||null;
                const experience=(before.join(' ').match(/\d+\s*[-—至]\s*\d+年|\d+年以上|经验不限|在校\/应届|应届生/)||[])[0]||null;
                const education=(before.join(' ').match(/博士|硕士|本科|大专|学历不限/)||[])[0]||null;
                const companyNode=[...region.querySelectorAll('[class]')].find(n=>visible(n)&&typeof n.className==='string'&&n.className.split(' ').includes('company-name'));
                const url=new URL(location.href);const externalId=url.searchParams.get('jobId');
                job={id:externalId,title,company:companyNode?.innerText?.trim()||null,location:city,salary,experience,education,raw_jd:text,source:'boss_browser_readonly',source_url:url.origin+url.pathname};
              }
              return {authenticated,safety,job,address:location.origin+location.pathname,loading:body.length<60&&!nav,empty:body.includes('没有找到相关职位')};
            }''')
            if result['safety']:state,reason='security_or_limit','BOSS要求本人验证或限制访问，已暂停读取。'
            elif result['loading']:state,reason='page_loading','Chrome已连接，BOSS页面仍在加载，请稍后检查。'
            elif not result['authenticated']:state,reason='login_required','Chrome已连接，请在专用窗口完成登录。'
            elif result['job']:state,reason='connected_readonly','已连接并识别登录，当前岗位可以读取和匹配；尚未启用真实发送。'
            else:state,reason='connected_no_job','已连接并识别登录。请在BOSS点开一个岗位详情，助手会自动读取并匹配。'
            return {'state':state,'reason':reason,'authenticated':result['authenticated'],'job':result['job'],'page_address':result['address'],'read_only':True}
        except Exception:
            return {'state':'page_navigating','reason':'BOSS页面正在切换，暂未读到稳定内容，请稍后检查。','authenticated':False,'job':None,'read_only':True}

    def close(self):
        # On a CDP connection Browser.close disconnects the transport; don't close a user's context or tabs.
        if self.browser:
            try:self.browser.close()
            except Exception:pass
        self.browser=None
        if self.engine:
            try:self.engine.stop()
            except Exception:pass
        self.engine=None

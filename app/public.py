"""Public HTTP boundary. Each visitor opens an independent local workbench."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

COOKIE = 'echo_visitor'
ID = '[a-f0-9]{32}'
RESUME = '(?:demo-resume|'+ID+')'
GET = re.compile(rf'/api/(?:state|preferences|resumes/{RESUME}|discovery/sources|'
                 rf'discovery/resumes/{RESUME}/terms|discovery/tasks/{ID}(?:/export)?|'
                 rf'discovery/agent/{ID}(?:/export)?|conversations/{ID}|applications/report)')
POST = re.compile(rf'/api/(?:resumes|resumes/{RESUME}/select|discovery/(?:example|resumes|resume-text|tasks|agent|drafts)|'
                  rf'discovery/tasks/{ID}/(?:retry|reanalyze)|discovery/agent/{ID}/answer|'
                  rf'discovery/drafts/{ID}/approve|conversations|conversations/{ID}/(?:messages|greeting)|jobs/{ID}/match)')
PUT = re.compile(rf'/api/(?:preferences|applications/{ID})')
CSP = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"


def create_public_app(settings, factory):
    sessions = {}
    gate = asyncio.Lock()
    cookie_secret = (os.getenv('PUBLIC_SESSION_SECRET') or secrets.token_hex(32)).encode()
    directory = settings.data_dir / 'public-sessions'

    def signature(token):
        return hmac.new(cookie_secret, token.encode(), hashlib.sha256).hexdigest()

    def visitor_cookie(request):
        token, _, mac = request.cookies.get(COOKIE, '').partition('.')
        return token if re.fullmatch(ID, token) and hmac.compare_digest(signature(token), mac) else None

    def attach_cookie(response, token, host):
        response.set_cookie(COOKIE, token+'.'+signature(token), httponly=True, samesite='lax',
                            secure=host not in {'127.0.0.1','localhost','testserver'}, max_age=86400)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for visitor in sessions.values():
            await visitor['stack'].aclose()

    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def decorate(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = CSP
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def reject(status, code, message):
        return decorate(JSONResponse({'error': {'code':code, 'message':message}}, status_code=status))

    def allowed_host(host):
        return host in {'localhost', '127.0.0.1', 'testserver', settings.public_host.lower()}

    @web.get('/health')
    def health():
        return decorate(JSONResponse({'status':'ok', 'app':'career-workbench', 'public':True,
                                     'model_mode':settings.model_mode,
                                     'model_configured':settings.model_mode == 'mock' or bool(os.getenv('OPENAI_API_KEY'))}))

    @web.get('/')
    def home(request: Request):
        host = request.url.hostname or ''
        if not allowed_host(host):
            return reject(404, 'NOT_AVAILABLE', '此公开地址不可用。')
        response = decorate(FileResponse(settings.root / 'app/static/index.html'))
        if not visitor_cookie(request):
            attach_cookie(response, secrets.token_hex(16), host)
        return response

    web.mount('/static', StaticFiles(directory=settings.root/'app/static'), name='static')

    class ScopeVisitors:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
                await self.app(scope, receive, send)
                return
            request = Request(scope)
            host = request.url.hostname or ''
            rule = {'GET':GET, 'POST':POST, 'PUT':PUT}.get(request.method)
            if not allowed_host(host) or not rule or not rule.fullmatch(scope['path']):
                await reject(404, 'NOT_AVAILABLE', '此公开接口不可用。')(scope, receive, send)
                return
            origin = request.headers.get('origin')
            if request.method != 'GET' and origin and origin not in {
                'https://'+request.headers['host'], 'http://'+request.headers['host']
            }:
                await reject(403, 'ORIGIN_REJECTED', '请从当前网站发起操作。')(scope, receive, send)
                return

            known = visitor_cookie(request)
            token = known or secrets.token_hex(16)
            async with gate:
                now = time.monotonic()
                if token not in sessions:
                    idle = [key for key, visitor in sessions.items() if not visitor['active'] and now-visitor['seen'] > 1800]
                    for key in idle:
                        await sessions.pop(key)['stack'].aclose()
                    if len(sessions) >= settings.public_sessions:
                        await reject(503, 'SESSION_CAPACITY', '演示服务当前会话已满，请稍后再试。')(scope, receive, send)
                        return
                    scoped = settings.model_copy(update={
                        'data_dir':directory/token, 'use_demo_resume':True, 'automatic_pipeline':False,
                        'boss_cdp_enabled':False, 'managed_boss_enabled':False, 'boss_platform_authorized':False,
                        'boss_auto_search':False, 'boss_auto_contact':False, 'default_mode':'draft',
                        'vector_backend':'memory', 'retries':0, 'max_calls':min(settings.max_calls, 24),
                    })
                    inner = await asyncio.to_thread(factory, scoped, _public_scope=True)
                    stack = AsyncExitStack()
                    await stack.enter_async_context(inner.router.lifespan_context(inner))
                    sessions[token] = {'app':inner, 'stack':stack, 'seen':now, 'active':0}
                visitor = sessions[token]
                visitor['active'] += 1
                visitor['seen'] = now

            async def send_with_cookie(message):
                if message['type'] == 'http.response.start' and not known:
                    response = JSONResponse({})
                    attach_cookie(response, token, host)
                    message['headers'] = [*message['headers'], (b'set-cookie', response.headers['set-cookie'].encode('latin-1'))]
                await send(message)

            try:
                await visitor['app'](scope, receive, send_with_cookie)
            finally:
                visitor['active'] -= 1
                visitor['seen'] = time.monotonic()

    web.add_middleware(ScopeVisitors)
    return web

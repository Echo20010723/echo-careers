from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import asyncio,json,re,time,os
from typing import Annotated
from fastapi import FastAPI,UploadFile,File,Query,Request
from fastapi.responses import JSONResponse,FileResponse,Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from .settings import Settings
from .runtime import runtime_revision
from .service import Service
from .schemas import JobBatch,ConversationInput,MessageInput,ModeInput,TaskInput,Preferences,AssistantStartInput,JobInput,BrowserPairInput,BrowserImportInput,BossNativeEvent
from .errors import AppError
from .offerdao import LABEL as OFFERDAO_LABEL, OfferDAO, OfferLimit, OfferSearch
from .discovery import Discovery
from .discovery_agent import AgentInput, Answer, DiscoveryAgent
from .discovery_drafts import DiscoveryDrafts, DraftInput
from .workers_drafts import WorkersDrafts
from .discovery_models import DiscoveryRequest, PasteResume, ReanalysisRequest
from .applications import ApplicationUpdate, save_application, review_report
from .audit import REQUEST_ID
from .browser_share import valid_origin


def create_app(settings=None, *, _public_scope=False, _offerdao=None, _workers_drafts=None):
    settings=settings or Settings.load()
    offerdao = _offerdao if _offerdao is not None else OfferDAO(settings.offerdao_base, settings.offerdao_api_key)
    workers_drafts = _workers_drafts if _workers_drafts is not None else WorkersDrafts(settings)
    if settings.public_mode and not _public_scope:
        from .public import create_public_app
        return create_public_app(settings, create_app, offerdao, workers_drafts)
    revision = runtime_revision(settings.root) if not _public_scope else None
    service=Service(settings)
    discovery=Discovery(service)
    agent=DiscoveryAgent(service, discovery, workers_drafts)
    discovery_drafts=DiscoveryDrafts(service, workers_drafts)
    discovery_ready=asyncio.Event()
    progress_changed=asyncio.Event()

    def notify_progress():
        nonlocal progress_changed
        progress_changed.set()
        progress_changed=asyncio.Event()

    async def discovery_snapshot(read, initial_stage, key, wait_ms):
        timeout = asyncio.get_running_loop().time() + wait_ms / 1000
        while wait_ms:
            changed = progress_changed
            if await asyncio.to_thread(initial_stage, key) not in {'queued','running','fetching','deduplicating','analyzing'}:
                break
            remaining = timeout - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except TimeoutError:
                break
        return await asyncio.to_thread(read, key)

    @asynccontextmanager
    async def lifespan(app):
        async def worker():
            while True:
                await asyncio.sleep(2)
                if service.settings.automatic_pipeline:
                    await asyncio.to_thread(service.automation.advance_one)
                if service.store.get('task')=='running' and service.store.get('mode')=='auto':
                    await asyncio.to_thread(service.drain)
        async def discovery_worker():
            discovery_ready.set()
            while True:
                await discovery_ready.wait()
                discovery_ready.clear()
                while True:
                    run_work = await asyncio.to_thread(agent.advance)
                    if run_work:
                        notify_progress()
                    search_work = await asyncio.to_thread(discovery.advance)
                    if search_work:
                        notify_progress()
                    if not (run_work or search_work): break
        runner=asyncio.create_task(worker())
        discovery_runner=asyncio.create_task(discovery_worker())
        yield
        discovery_runner.cancel()
        try: await discovery_runner
        except asyncio.CancelledError: pass
        runner.cancel()
        try: await runner
        except asyncio.CancelledError: pass
        await asyncio.to_thread(service.close)
        if not _public_scope:
            await offerdao.close()
            await asyncio.to_thread(workers_drafts.close)
    app=FastAPI(title='Echo Careers',version='1.4.0',lifespan=lifespan)
    app.state.service=service
    @app.middleware('http')
    async def boundary(request:Request,call_next):
        request_id=uuid4().hex; request.state.request_id=request_id; started=time.monotonic(); REQUEST_ID.set(request_id)
        host=request.headers.get('host','').split(':')[0].lower()
        # Loopback app: prevent DNS rebinding and browser cross-origin writes.
        if host not in {'127.0.0.1','localhost','testserver', settings.public_host.lower() if _public_scope else None}:
            return JSONResponse({'error':{'code':'LOCAL_ONLY','message':'仅允许本机访问。','request_id':request_id}},status_code=403)
        origin=request.headers.get('origin')
        share_request=not _public_scope and ((valid_origin(origin) and request.url.path in {'/api/browser/pair','/api/browser/import'}) or (origin==service.managed_boss.origin and request.url.path=='/api/boss/session/event'))
        share_headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Allow-Private-Network':'true','Vary':'Origin'} if share_request else {}
        if request.method=='OPTIONS' and share_request: return Response(status_code=204,headers=share_headers)
        if request.method not in {'GET','HEAD','OPTIONS'} and origin and origin not in {'http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')} and not share_request:
            return JSONResponse({'error':{'code':'ORIGIN_REJECTED','message':'请从本机操作界面发起操作。','request_id':request_id}},status_code=403)
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>6*1024*1024):
            return JSONResponse({'error':{'code':'INPUT_TOO_LARGE','message':'请求体过大。','request_id':request_id}},status_code=413)
        try: response=await call_next(request)
        except Exception:
            error=AppError('INTERNAL_ERROR')
            response=JSONResponse({'error':{'code':error.code,'message':error.message,'request_id':request_id}},status_code=500)
        response.headers.update(share_headers)
        response.headers['X-Request-ID']=request_id
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        duration_ms=round((time.monotonic()-started)*1000)
        response.headers['Server-Timing']=f'app;dur={duration_ms}'
        service.audit.event('request_completed',request_id,stage='http',duration_ms=duration_ms,status=str(response.status_code))
        return response
    @app.exception_handler(AppError)
    async def known_error(request,e): return JSONResponse({'error':{'code':e.code,'message':e.message,'request_id':request.state.request_id}},status_code=e.status)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,e): return JSONResponse({'error':{'code':'INVALID_INPUT','message':'输入内容或字段格式不正确。','request_id':request.state.request_id}},status_code=422)
    @app.get('/health')
    def health():
        return {'status':'ok','app':'career-workbench','version':'1.4.0','pid':os.getpid(),
                'model_mode':settings.model_mode,'simulated_model':service.provider.simulated,'schema_version':1,
                'runtime_revision':revision,'offerdao_ready':offerdao.ready,'draft_usage':workers_drafts.usage(),
                'agent_provider':agent.model_provider()}
    @app.get('/api/state')
    def state(): return service.snapshot()
    @app.get('/api/resumes/{rid}')
    def resume(rid:str):
        row=service.store.one('SELECT id,name,version,text,chunks,created FROM resumes WHERE id=?',(rid,))
        if not row: raise AppError('RESUME_NOT_FOUND')
        row['chunks']=json.loads(row['chunks']); return row
    @app.post('/api/resumes')
    async def upload(file:UploadFile=File(...)):
        content=await file.read(5*1024*1024+1)
        with service.action_gate: result=await asyncio.to_thread(service.resumes.upload,file.filename or 'resume.txt',content,audit=service.audit)
        return {k:v for k,v in result.items() if k!='path'}
    @app.post('/api/resumes/{rid}/select')
    def select_resume(rid:str):
        with service.action_gate: service.resumes.select(rid)
        return {'current_resume':rid}
    @app.get('/api/preferences')
    def preferences(): return service.store.get('preferences')
    @app.put('/api/preferences')
    def update_preferences(data:Preferences):
        with service.action_gate,service.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('preferences',json.dumps(data.model_dump(),ensure_ascii=False)))
            db.execute('UPDATE jobs SET analysis=NULL')
            db.execute("UPDATE outbox SET status='cancelled' WHERE status IN ('draft','queued','blocked')")
        return data
    @app.post('/api/jobs/import')
    def import_jobs(data:JobBatch):
        jobs=[service.import_job(j) for j in data.jobs]
        if settings.automatic_pipeline:
            for job in jobs: service.automation.enroll(job)
        return {'jobs':jobs,'automatic_processing':settings.automatic_pipeline}

    @app.post('/api/discovery/resumes')
    async def upload_discovery_resume(file:UploadFile=File(...)):
        content=await file.read(5*1024*1024+1)
        return await asyncio.to_thread(service.resumes.upload, file.filename or 'resume.txt', content, select=False, audit=service.audit)

    @app.post('/api/discovery/resume-text')
    def paste_resume(data: PasteResume):
        with service.action_gate:
            row = service.resumes.upload('粘贴的简历.txt', data.text.encode('utf-8'), audit=service.audit)
        return {'id':row['id'], 'name':row['name']}

    @app.get('/api/discovery/resumes/{resume_id}/terms')
    def discovery_terms(resume_id: str):
        return discovery.summary(resume_id)

    @app.post('/api/discovery/example')
    def discovery_example():
        sample = settings.root / 'examples/resume_demo.md'
        return service.resumes.upload('脱敏示例.md', sample.read_bytes(), select=False, audit=service.audit)

    @app.get('/api/discovery/sources')
    def discovery_sources():
        result = discovery.source_status()
        result['sources'].append({'id':'offerdao','name':OFFERDAO_LABEL,'ready':offerdao.ready})
        return result

    @app.get('/api/discovery/offerdao/search')
    async def search_offerdao(query: Annotated[OfferSearch, Query()]):
        try:
            result = await offerdao.search(query, service.audit)
            if settings.public_mode:
                result['jobs'] = [job.model_copy(update={'contact_emails':[]}) for job in result['jobs']]
            return result
        except OfferLimit as error:
            headers = {'Retry-After':str(error.seconds)} if error.seconds is not None else {}
            return JSONResponse({'error':{'code':error.code,'message':error.message,
                                          'request_id':REQUEST_ID.get()}}, status_code=429, headers=headers)

    @app.post('/api/discovery/drafts')
    def create_discovery_draft(data: DraftInput):
        started = time.monotonic()
        result = discovery_drafts.create(data)
        if 'generation' in result:
            trace = result['generation']
            trace.update(request_id=REQUEST_ID.get(), duration_ms=round((time.monotonic()-started)*1000, 2))
            service.audit.event('draft_generated', trace['request_id'],
                stage='application_statement' if data.statement else 'application_email',
                duration_ms=trace['duration_ms'], status=result['mode'], source=trace['source'],
                model=trace.get('response_model') or trace['configured_model'],
                error_code=trace.get('validation') or result.get('fallback_reason'), http_status=trace.get('http_status'))
        return result

    @app.post('/api/discovery/drafts/{draft_id}/approve')
    def approve_discovery_draft(draft_id: str):
        return discovery_drafts.approve(draft_id)

    @app.post('/api/discovery/agent', status_code=202)
    async def start_agent(data: AgentInput):
        run = await asyncio.to_thread(agent.create, data)
        service.audit.event('task_accepted', REQUEST_ID.get(), stage='agent', job_id=run['run_id'])
        discovery_ready.set()
        return run

    @app.get('/api/discovery/agent/{run_id}')
    async def agent_status(run_id: str, wait_ms: int = Query(0, ge=0, le=1000)):
        return await discovery_snapshot(agent.status, agent.current_stage, run_id, wait_ms)

    @app.get('/api/discovery/agent/{run_id}/export')
    def agent_export(run_id: str):
        result = agent.status(run_id)
        if result['status'] not in {'complete','failed','interrupted'}:
            raise AppError('CONFLICT')
        return JSONResponse(result, headers={'Content-Disposition': f'attachment; filename=career-agent-{run_id}.json'})

    @app.post('/api/discovery/agent/{run_id}/answer', status_code=202)
    async def answer_agent(run_id: str, data: Answer):
        run = await asyncio.to_thread(agent.answer, run_id, data.answer)
        discovery_ready.set()
        return run

    @app.post('/api/discovery/tasks', status_code=202)
    async def create_discovery(data: DiscoveryRequest):
        task = await asyncio.to_thread(discovery.create, data)
        service.audit.event('task_accepted', REQUEST_ID.get(), stage='discovery', job_id=task['id'])
        discovery_ready.set()
        return task

    @app.get('/api/discovery/tasks/{batch_id}')
    async def discovery_status(batch_id: str, wait_ms: int = Query(0, ge=0, le=1000)):
        return await discovery_snapshot(discovery.status, discovery.current_stage, batch_id, wait_ms)

    @app.post('/api/discovery/tasks/{batch_id}/reanalyze', status_code=202)
    async def reanalyze_discovery(batch_id: str, data: ReanalysisRequest):
        task = await asyncio.to_thread(discovery.reanalyze, batch_id, data.resume_id)
        discovery_ready.set()
        return task

    @app.post('/api/discovery/tasks/{batch_id}/retry', status_code=202)
    async def retry_discovery(batch_id: str, job_id: str | None = Query(None, max_length=100)):
        task = await asyncio.to_thread(discovery.retry, batch_id, job_id)
        discovery_ready.set()
        return task

    @app.get('/api/discovery/tasks/{batch_id}/export')
    def discovery_export(batch_id: str):
        batch = discovery.status(batch_id)
        if batch['stage'] not in {'complete', 'partial', 'failed'}:
            raise AppError('CONFLICT')
        return JSONResponse(batch, headers={
            'Content-Disposition': f'attachment; filename=career-discovery-{batch_id}.json',
        })

    @app.put('/api/applications/{job_id}')
    def update_application(job_id: str, data: ApplicationUpdate):
        return save_application(service.store, job_id, data)

    @app.get('/api/applications/report')
    def application_report():
        return Response(review_report(service.store), media_type='text/markdown',
                        headers={'Content-Disposition': 'attachment; filename=career-review.md'})

    @app.post('/api/assistant/start')
    def assistant_start(data:AssistantStartInput): return service.start_assistant(data.channel)
    @app.post('/api/assistant/pipeline/{jid}/retry')
    def retry_pipeline(jid:str): return service.automation.retry(jid)
    @app.post('/api/assistant/demo')
    def assistant_demo():
        service.start_assistant('mock')
        row=service.import_job(JobInput(id=uuid4().hex,title='AI应用开发实习（演示）',company='演示团队',location='上海',salary='200元/天',employment_type='实习',work_mode='线下',source='mock_demo',raw_jd='岗位职责：开发AI问答API。任职要求：熟悉Python、FastAPI。加分项：有RAG经验。'))
        service.automation.enroll(row)
        service.automation.advance_one()
        return {'job_id':row['id'],'simulated':True}
    @app.post('/api/jobs/{jid}/match')
    def match(jid:str): return service.match(jid)
    @app.post('/api/conversations')
    def create_conversation(data:ConversationInput):
        if _public_scope and data.channel != 'mock': raise AppError('CHANNEL_BLOCKED')
        return service.create_conversation(data)
    @app.get('/api/conversations/{cid}')
    def get_conversation(cid:str):
        return {'conversation':service.conversation(cid),'messages':service.store.all('SELECT * FROM messages WHERE conversation_id=? ORDER BY observed',(cid,)),'outbox':service.store.all('SELECT * FROM outbox WHERE conversation_id=? ORDER BY created',(cid,))}
    @app.post('/api/conversations/{cid}/greeting')
    def greeting(cid:str): return service.greeting(cid)
    @app.post('/api/conversations/{cid}/messages')
    def receive(cid:str,data:MessageInput): return service.receive(cid,data)
    @app.post('/api/messages/{mid}/retry')
    def retry_message(mid:str):
        row=service.store.one('SELECT * FROM messages WHERE id=?',(mid,))
        if not row or row['status']!='failed': raise AppError('CONFLICT')
        platform=row['dedupe_key'][9:] if row['dedupe_key'].startswith('platform:') else None
        return service.receive(row['conversation_id'],MessageInput(platform_id=platform,text=row['text'],observed_at=row['observed']),retry=True)
    @app.post('/api/conversations/{cid}/handoff')
    def handoff(cid:str): service.handoff(cid,'本人手动接管'); return {'state':'handoff'}
    @app.post('/api/outbox/{oid}/send-reviewed')
    def send_reviewed(oid:str): return service.send(oid,reviewed=True)
    @app.post('/api/outbox/{oid}/reconcile')
    def reconcile(oid:str): return service.reconcile(oid)
    @app.post('/api/mode')
    def mode(data:ModeInput): return service.mode(data)
    @app.post('/api/task')
    def task(data:TaskInput): return service.task(data.action)
    @app.post('/api/knowledge/rebuild')
    def rebuild():
        _,version=service.rag.build(service.resumes.current(),force=True)
        return {'index_version':version,'embedding':service.rag.signature,'vector_backend':settings.vector_backend,'model_mode':settings.model_mode}
    @app.get('/api/browser/extension.zip')
    def browser_extension_download(): return FileResponse(settings.root/'browser_extension.zip',filename='browser_extension.zip',media_type='application/zip')
    @app.post('/api/boss/session/event')
    def boss_session_event(data:BossNativeEvent,request:Request):
        return service.managed_boss.accept(data,request.headers.get('origin'),request.headers.get('authorization'))
    @app.post('/api/browser/pair-code')
    def browser_pair_code(): return service.browser_share.issue_code()
    @app.post('/api/browser/pair')
    def browser_pair(data:BrowserPairInput,request:Request): return service.browser_share.pair(data.code,request.headers.get('origin'))
    @app.post('/api/browser/import')
    def browser_import(data:BrowserImportInput,request:Request): return service.browser_share.import_job(data.job,request.headers.get('origin'),request.headers.get('authorization'))
    @app.post('/api/channels/boss/connect')
    def boss_connect(): return service.connect_boss(launch=True)
    @app.post('/api/channels/boss/read-current')
    def boss_read_current(): return service.sync_current_boss()
    @app.post('/api/channels/boss/open-manual')
    def boss_manual_open():
        if not settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        status=service.channels['boss'].open_manual()
        return {'connection':status,'imported':False,'message':status['reason']}
    @app.post('/api/channels/boss/open')
    def boss_open():
        if not settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        result=service.channels['boss'].open()
        if result['state'] in {'page_redirected','security_or_limit'}: service.pause(result['reason'])
        return result
    @app.post('/api/channels/boss/retry-once')
    def boss_retry():
        if not settings.boss_platform_authorized:raise AppError('PLATFORM_PERMISSION_REQUIRED')
        result=service.channels['boss'].retry_once(); return result
    @app.post('/api/channels/boss/inspect')
    def boss_inspect(): return service.connect_boss(launch=False)
    @app.get('/')
    def index(): return FileResponse(settings.root/'app/static/index.html')
    app.mount('/static',StaticFiles(directory=settings.root/'app/static'),name='static')
    return app

# uvicorn app.main:create_app --factory: construction never occurs during test imports.

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def test_public_sessions_and_blocked_routes(tmp_path):
    async def scenario():
        app = create_app(Settings(data_dir=tmp_path, public_mode=True))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as alice, \
                       httpx.AsyncClient(transport=transport, base_url='http://testserver') as bob:
                assert (await alice.get('/')).status_code == 200
                assert (await bob.get('/')).status_code == 200
                assert alice.cookies.get('echo_visitor') != bob.cookies.get('echo_visitor')
                resume = (await alice.post('/api/discovery/resume-text',
                    json={'text':'Private example Python FastAPI project'})).json()
                assert (await bob.get('/api/discovery/resumes/'+resume['id']+'/terms')).status_code == 404
                assert (await bob.get('/api/resumes/'+resume['id'])).status_code == 404
                example = (await alice.post('/api/discovery/example')).json()
                created = await alice.post('/api/discovery/tasks', json={'resume_id':example['id'],
                    'mode':'demo', 'terms':['Agent 开发实习'], 'limit':2})
                assert created.status_code == 202, created.text
                task = created.json()['id']
                for _ in range(100):
                    result = (await alice.get('/api/discovery/tasks/'+task)).json()
                    if result['stage'] in {'complete','partial','failed'}:
                        break
                    await asyncio.sleep(.03)
                assert result['stage'] == 'complete'
                assert (await bob.get('/api/discovery/tasks/'+task)).status_code >= 400
                assert (await bob.get('/api/discovery/tasks/'+task+'/export')).status_code >= 400
                assert (await alice.get('/api/discovery/tasks/'+task+'/export')).status_code == 200
                run = (await alice.post('/api/discovery/agent',json={
                    'resume_id':example['id'], 'goal':'AI application internships'})).json()['run_id']
                assert (await bob.get('/api/discovery/agent/'+run)).status_code == 404
                for path in ('/api/browser/pair','/api/boss/session/event','/api/channels/boss/connect',
                             '/api/assistant/start','/api/outbox/example/send-reviewed'):
                    assert (await alice.post(path, json={})).status_code == 404
                assert 'Private example' not in (await bob.get('/api/state')).text
                assert (await bob.post('/api/discovery/drafts', json={'resume_id':resume['id'],'question':'FastAPI 项目'})).status_code == 404
                draft = (await alice.post('/api/discovery/drafts', json={'resume_id':example['id'],
                    'question':'请简述你使用 FastAPI 开发接口的经历'})).json()
                assert draft['facts'] and draft['can_approve'] and not draft['examples']
                assert (await bob.post('/api/discovery/drafts/'+draft['id']+'/approve')).status_code >= 400
                assert (await alice.post('/api/discovery/drafts/'+draft['id']+'/approve')).status_code == 200
                second = (await alice.post('/api/discovery/drafts', json={'resume_id':example['id'],
                    'question':'说说你用 FastAPI 开发接口的经历'})).json()
                assert second['examples'][0]['id'] == draft['id']
                assert (await alice.post('/api/discovery/example',
                    headers={'Origin':'https://other.example'})).status_code == 403
    asyncio.run(scenario())


def test_public_health_and_home_at_configured_host(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, public_mode=True, public_host='echo.example'))
    with TestClient(app,base_url='https://echo.example') as client:
        assert client.get('/health').json()['public']
        response = client.get('/')
        assert response.status_code == 200
        assert 'HttpOnly' in response.headers['Set-Cookie'] and 'Secure' in response.headers['Set-Cookie']
        assert client.get('/static/discovery.js').status_code == 200
        assert client.get('/api/discovery/sources').status_code == 200

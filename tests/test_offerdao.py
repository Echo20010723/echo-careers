import json
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def test_offerdao_page_to_single_selected_job(tmp_path, monkeypatch):
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            'items': [{'posting_id':'post_1','organization':'示例研究院','team':'AI 应用',
                       'role':'顶层职位','location':'上海','published_at':'2026-05-12',
                       'source_url':'https://example.org/',
                       'description':'顶层正文不能混用','contact_email':'private@example.org',
                       'positions':json.dumps([
                           {'role':'平台研发','employment_type':['正式'],'description':'维护平台'},
                           {'role':'Agent 研发','employment_type':['实习'],'description':'开发 Python Agent 工作流',
                            'requirements':'掌握 FastAPI'},
                       ])}],
            'limit':8, 'offset':8, 'total':30, 'unexpected':True,
        })

    from app.offerdao import OfferDAO
    monkeypatch.setattr(OfferDAO, 'transport', httpx.MockTransport(respond))
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False,
                        offerdao_api_key='example-test-credential')
    with TestClient(create_app(settings)) as client:
        response = client.get('/api/discovery/offerdao/search',params={
            'q':'Agent 开发','location':'上海','employment_type':'实习','limit':8,'offset':8,
            'published_within_days':30,
        })
        assert response.status_code==200, response.text
        page = response.json()
        assert (page['limit'],page['offset'],page['total'])==(8,8,30)
        assert len(calls)==1 and calls[0].url.path=='/api/v1/postings/search'
        assert dict(calls[0].url.params)=={'q':'Agent 开发','location':'上海','employment_type':'实习',
                                          'limit':'8','offset':'8','published_within_days':'30'}
        assert calls[0].headers['authorization']=='Bearer example-test-credential'
        assert all(len(request.content)==0 for request in calls)
        job,=page['jobs']
        assert job['title']=='Agent 研发' and job['team']=='AI 应用' and job['published_at']=='2026-05-12'
        assert job['url']=='https://offerdao.ai/j/post_1' and job['original_source_url']=='https://example.org/'
        assert job['application_url'] is None and 'private@example.org' not in response.text
        assert 'FastAPI' in job['jd'] and '维护平台' not in job['jd'] and '顶层正文' not in job['jd']
        rid=client.post('/api/discovery/example').json()['id']
        task=client.post('/api/discovery/tasks',json={'resume_id':rid,'platform':'offerdao','mode':'paste','terms':[],'jobs':[job]})
        assert task.status_code==202
        result=client.get('/api/discovery/tasks/'+task.json()['id'],params={'wait_ms':1000}).json()
        assert result['stage']=='complete' and result['platform']=='offerdao'
        assert result['jobs'][0]['source']=='offerdao' and result['jobs'][0]['analysis']['evidence']
        assert len(calls)==1
    logs = [json.loads(line) for file in (tmp_path/'logs').glob('audit-*.jsonl')
            for line in file.read_text(encoding='utf-8').splitlines()]
    record, = [item for item in logs if item['event']=='offerdao_search']
    assert (record['source'],record['http_status'],record['returned_count'],record['retry_count']) == ('offerdao',200,1,0)
    assert record['duration_ms'] >= 0
    assert all(secret not in json.dumps(logs) for secret in (
        'example-test-credential','private@example.org','FastAPI','Agent 开发',
    ))


def test_empty_and_optional_fields_with_public_scope(tmp_path, monkeypatch):
    from app.offerdao import OfferDAO
    requests = []

    def respond(request):
        requests.append(request)
        rows = [{'posting_id':'solo','role':'算法','description':'部署模型',
                 'source_url':'javascript:alert(1)','positions':None},
                {'posting_id':'bad_positions','positions':'{invalid','description':'混合正文'},
                {'posting_id':'array','positions':[None,{'role':'独立子岗位','requirements':'掌握 Python'}]},
                {'posting_id':'../private','role':'危险标识'}]
        return httpx.Response(200,json={'items':rows if request.url.params['offset']=='0' else [],
                                         'total':4,'limit':4,'offset':int(request.url.params['offset'])})

    monkeypatch.setattr(OfferDAO,'transport',httpx.MockTransport(respond))
    settings = Settings(data_dir=tmp_path, public_mode=True, offerdao_api_key='example-test-credential')
    with TestClient(create_app(settings)) as client:
        home = client.get('/')
        assert home.status_code == 200
        first = client.get('/api/discovery/offerdao/search', params={'q':'算法','limit':4})
        assert first.status_code==200
        solo, malformed, child=first.json()['jobs']
        assert solo['url'].endswith('/j/solo') and solo['original_source_url'] is None
        assert solo['company'] is None and solo['salary_raw'] is None and solo['published_at'] is None
        assert malformed['jd'] is None and child['source_id']=='array:1'
        assert child['jd']=='任职要求：掌握 Python'
        empty=client.get('/api/discovery/offerdao/search',params={'q':'算法','limit':4,'offset':4})
        assert empty.status_code==200 and empty.json()['jobs']==[] and empty.json()['offset']==4
        assert len(requests)==2 and first.json()['returned']==4
        assert client.get('/api/discovery/offerdao/search',params={'q':'算法','limit':31}).status_code==422
        assert len(requests)==2


@pytest.mark.parametrize(('upstream','payload','expected','code'), [
    (401,{},401,'OFFERDAO_AUTH'),
    (403,{},403,'OFFERDAO_FORBIDDEN'),
    (429,{'code':'rate_limit_exceeded'},429,'OFFERDAO_RATE_LIMIT'),
    (429,{'code':'rate_limited'},429,'OFFERDAO_DAILY_LIMIT'),
    (500,{},502,'OFFERDAO_UPSTREAM'),
    (200,{'items':'bad'},502,'OFFERDAO_UPSTREAM'),
])
def test_offerdao_upstream_errors_are_safe_and_never_retried(tmp_path,monkeypatch,upstream,payload,expected,code):
    from app.offerdao import OfferDAO
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(upstream,json={**payload,'error':'private upstream response'},
                              headers={'Retry-After':'17'})
    monkeypatch.setattr(OfferDAO,'transport',httpx.MockTransport(respond))
    with TestClient(create_app(Settings(data_dir=tmp_path,use_demo_resume=True,offerdao_api_key='example-test-credential'))) as client:
        response=client.get('/api/discovery/offerdao/search',params={'q':'算法'})
        assert response.status_code==expected and response.json()['error']['code']==code
        assert len(calls)==1 and 'private upstream response' not in response.text
        if upstream==429:
            assert response.headers['Retry-After']=='17'


def test_offerdao_missing_key_and_total_timeout(tmp_path,monkeypatch):
    from app import offerdao
    calls = []
    async def respond(request):
        calls.append(request)
        await asyncio.sleep(2)
        return httpx.Response(200,json={'items':[],'total':0})
    monkeypatch.setattr(offerdao.OfferDAO,'transport',httpx.MockTransport(respond))
    monkeypatch.setattr(offerdao,'TOTAL_TIMEOUT',0.02)
    for key,status,code in ((None,503,'OFFERDAO_NOT_CONFIGURED'),
                             ('example-test-credential',504,'OFFERDAO_TIMEOUT')):
        with TestClient(create_app(Settings(data_dir=tmp_path,use_demo_resume=True,offerdao_api_key=key))) as client:
            response=client.get('/api/discovery/offerdao/search',params={'q':'算法'})
            assert response.status_code==status and response.json()['error']['code']==code
    assert len(calls)==1


def test_offerdao_batched_children_and_contact_scope(tmp_path, monkeypatch):
    from app.offerdao import OfferDAO
    calls = []

    def search(request):
        calls.append(request)
        return httpx.Response(200, json={'limit':12, 'offset':0, 'total':1, 'items':[{
            'posting_id':'mixed', 'organization':'示例公司', 'contact_email':'parent@example.org',
            'positions':[
                {'role':'博士研究实习', 'description':'仅限博士生，开发 Python 服务。每周至少出勤 4 天。',
                 'contact_email':'research@example.org'},
                {'role':'Agent 应用实习', 'description':'开发 Python 和 FastAPI API。'},
            ],
        }]})

    monkeypatch.setattr(OfferDAO, 'transport', httpx.MockTransport(search))
    with TestClient(create_app(Settings(data_dir=tmp_path, use_demo_resume=True,
                                       offerdao_api_key='test-key'))) as client:
        page = client.get('/api/discovery/offerdao/search', params={'q':'Agent'}).json()
        first, second = page['jobs']
        assert first['contact_emails'] == ['research@example.org']
        assert second['contact_emails'] == []
        rid = client.post('/api/discovery/example').json()['id']
        task = client.post('/api/discovery/tasks', json={
            'resume_id':rid,'platform':'offerdao','mode':'source_page','terms':[],'jobs':page['jobs'],
            'degree':'master','source_total':page['total'],
        })
        assert task.status_code == 202
        url = '/api/discovery/tasks/'+task.json()['id']
        for _ in range(5):
            result = client.get(url, params={'wait_ms':1000}).json()
            if result['stage'] == 'complete':
                break
        assert result['stage'] == 'complete' and result['counts']['analyzed'] == 2
        assert len(calls) == 1
        research = next(job for job in result['jobs'] if job['source_id'] == 'mixed:0')
        checks = {row['name']:row for row in research['analysis']['conditions']}
        assert checks['学历']['status'] == '冲突' and '仅限博士生' in checks['学历']['evidence']
        assert checks['每周出勤']['status'] == '待确认' and '4 天' in checks['每周出勤']['evidence']
        assert research['analysis']['grade'] == '拉完了'
        draft = client.post('/api/discovery/drafts', json={
            'resume_id':rid, 'application_job':research, 'available_from':'2026-12-01',
        }).json()
        assert draft['recipients'] == ['research@example.org']
        assert '博士研究实习' in draft['subject'] and '2026-12-01' in draft['answer']
        assert '每周' not in draft['answer']


def test_shared_email_requires_explicit_scope_and_accepts_multiple_public_addresses():
    from app.offerdao import parse_card
    card = {'posting_id':'shared', 'contact_email':['hire@example.org','hr@example.org','a***@example.org'],
            'description':'以上岗位统一投递邮箱如下。', 'positions':[
                {'role':'A'},
                {'role':'B','contact_email':'role-b@example.org','contact_link':'https://example.org/jobs/b'},
            ]}
    a, b = parse_card(card, 'https://offerdao.ai', set())
    assert a.contact_emails == ['hire@example.org','hr@example.org']
    assert b.contact_emails == ['role-b@example.org']
    assert b.application_url == 'https://example.org/jobs/b'

    card['description'] = '各岗位请分别投递不同邮箱。'
    a, _ = parse_card(card, 'https://offerdao.ai', set())
    assert a.contact_emails == []


def test_offerdao_manual_query_is_forwarded_verbatim(tmp_path, monkeypatch):
    from app.offerdao import OfferDAO
    queries = []

    def endpoint(request):
        queries.append(dict(request.url.params))
        return httpx.Response(200,json={'items':[],'total':0,'limit':12,'offset':0})

    monkeypatch.setattr(OfferDAO,'transport',httpx.MockTransport(endpoint))
    with TestClient(create_app(Settings(data_dir=tmp_path, offerdao_api_key='test-key'))) as client:
        manual = '  大模型应用实习  Agent  '
        assert client.get('/api/discovery/offerdao/search',params={'q':manual}).status_code == 200
        assert client.get('/api/discovery/offerdao/search',params={'q':'   '}).status_code == 422
        assert queries == [{'q':manual,'limit':'12','offset':'0'}]

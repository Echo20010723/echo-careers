import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


PROFILE = """姓名：王测试
邮箱：profile.private@example.org
地址：秘密街道
## 项目经历
梳理用户需求，主持用户访谈，并制定功能优先级。
实现 Python、FastAPI 接口并编写单元测试，验证异常处理。
"""
PRODUCT = {'source':'offerdao','title':'产品经理实习','company':'测试公司',
           'jd':'梳理用户需求和优先级，开展用户访谈。','contact_emails':['hiring@example.org']}
ENGINEERING = {'source':'offerdao','title':'后端开发实习','company':'测试公司',
               'jd':'开发 Python FastAPI 接口，编写单元测试。','contact_emails':['engineer@example.org']}


def upload(client, profile):
    response = client.post('/api/discovery/resume-text', json={'text':profile})
    assert response.status_code == 200
    return response.json()['id']


def draft(client, resume_id, job, **kwargs):
    response = client.post('/api/discovery/drafts', json={
        'resume_id':resume_id, 'application_job':job, **kwargs,
    })
    assert response.status_code == 200
    return response.json()


def test_basic_draft_uses_current_role_and_confirmed_fields(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, use_demo_resume=True))) as client:
        rid = upload(client, PROFILE)
        product = draft(client, rid, PRODUCT, signature='当前候选人')
        engineering = draft(client, rid, ENGINEERING)
        assert product['mode'] == '基础草稿' and product['fallback_reason'] == 'not_configured'
        assert '用户访谈' in product['answer'] and 'FastAPI' not in product['answer']
        assert 'FastAPI' in engineering['answer'] and '用户访谈' not in engineering['answer']
        assert product['recipients'] == ['hiring@example.org']
        assert product['signature'] == '当前候选人' and '当前候选人' in product['attachment_name']
        assert '到岗' not in product['answer'] and '出勤' not in product['answer']
        changed = upload(client, '## 项目经历\n开发 SQL 查询并验证数据质量。\n邮箱：changed@example.org')
        result = draft(client, changed, {**ENGINEERING, 'jd':'开发 SQL 查询，验证数据质量。'})
        assert 'SQL' in result['answer'] and 'FastAPI' not in result['answer']
        assert '当前候选人' not in result['answer'] and 'example.org' not in result['answer']



def configured(path, **kwargs):
    return Settings(data_dir=path, use_demo_resume=True, cloudflare_account_id='a'*32,
                    cloudflare_api_token='test-workers-token', **kwargs)


def test_model_receives_only_canonical_evidence_for_selected_role(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    requests = []

    def worker(request):
        outbound = json.loads(request.content)
        payload = json.loads(outbound['messages'][1]['content'])
        requests.append(payload)
        assert request.url.path.endswith('/ai/v1/chat/completions')
        assert outbound['model'] == '@cf/qwen/qwen3.8-27b'
        assert request.headers['Authorization'] == 'Bearer test-workers-token'
        assert not any(value in request.content.decode() for value in (
            'profile.private@example.org','hiring@example.org','秘密街道','测试公司','王测试','确认姓名',
        ))
        evidence = payload['evidence'][0]
        body = ('我希望结合用户访谈经验来梳理功能取舍与需求。' if payload['role'] == 'product'
                else '我做过 FastAPI 接口开发并通过单元测试验证实现。')
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'body':body, 'evidence_ids':[evidence['id']]})}}]})

    monkeypatch.setattr(WorkersDrafts, 'transport', httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path))) as client:
        rid = upload(client, PROFILE)
        prod = draft(client, rid, PRODUCT, signature='确认姓名')
        eng = draft(client, rid, ENGINEERING)
        assert prod['mode'] == eng['mode'] == 'Workers AI 草稿'
        assert '功能取舍' in prod['answer'] and '验证实现' in eng['answer']
        assert prod['evidence'][0]['line'] != eng['evidence'][0]['line']
        assert requests[0]['role'] == 'product' and requests[1]['role'] == 'engineering'
        assert len(requests) == 2


@pytest.mark.parametrize('response,reason', [
    (402,'upstream_quota'),(429,'upstream_limit'),('timeout','timeout'),('unknown_evidence','invalid_output'),
])
def test_workers_failure_returns_editable_draft(tmp_path, monkeypatch, response, reason):
    from app.workers_drafts import WorkersDrafts

    def worker(request):
        if response == 'timeout':
            raise httpx.ReadTimeout('simulated', request=request)
        if response == 'unknown_evidence':
            return httpx.Response(200, json={'choices':[{'message':{
                'content':'{"body":"我做过 FastAPI 接口开发与单元测试。","evidence_ids":["E99"]}'}}]})
        return httpx.Response(response)

    monkeypatch.setattr(WorkersDrafts, 'transport', httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path))) as client:
        result = draft(client, upload(client, PROFILE), ENGINEERING, weekly_days=3)
        assert result['mode'] == '基础草稿' and result['fallback_reason'] == reason
        assert 'FastAPI' in result['answer'] and '3天' in result['answer']


def test_public_rate_limit_and_shared_daily_budget(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    calls = []
    def worker(request):
        calls.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(WorkersDrafts, 'transport', httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path, public_mode=True, cloudflare_daily_drafts=4, cloudflare_visitor_hourly_drafts=3))) as client:
        one = upload(client, PROFILE)
        for i in range(3):
            assert draft(client, one, {**PRODUCT, 'source_id':str(i)})['fallback_reason'] == 'provider_error'
        assert draft(client, one, {**PRODUCT, 'source_id':'limit'})['fallback_reason'] == 'app_hourly_limit'
        client.cookies.clear()
        assert client.post('/api/discovery/drafts', json={'resume_id':one,'application_job':PRODUCT}).status_code == 404
        two = upload(client, PROFILE)
        assert draft(client, two, PRODUCT)['fallback_reason'] == 'provider_error'
        assert draft(client, two, {**PRODUCT, 'source_id':'daily'})['fallback_reason'] == 'app_daily_limit'
        assert len(calls) == 4


def test_english_fallback_and_manual_confirmation(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, use_demo_resume=True))) as client:
        rid = upload(client, '## Projects\nBuilt Python APIs and wrote unit tests.')
        result = draft(client, rid, {'source':'himalayas','title':'Software Engineer Intern','company':'Example',
            'jd':'Build Python APIs and add unit tests.\nEmail subject: Intern Application'},
            recipient='confirmed@example.org', signature='Candidate', available_from='2026-12-01')
        assert result['subject'] == 'Intern Application'
        assert result['recipients'] == ['confirmed@example.org']
        assert 'unit testing' in result['answer'] and '2026-12-01' in result['answer']
        assert 'Best regards,\nCandidate' in result['answer']



def test_five_distinct_public_jobs_and_cached_inputs(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    calls = []

    def worker(request):
        calls.append(request)
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
            'body':'我做过 FastAPI 接口并编写单元测试来验证。','evidence_ids':['E1'],
        })}}], 'usage':{'prompt_tokens':250,'completion_tokens':80}})

    monkeypatch.setattr(WorkersDrafts,'transport',httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path, public_mode=True))) as client:
        rid = upload(client, PROFILE)
        for index in range(5):
            job = {**ENGINEERING, 'source_id':f'sample-{index}'}
            preview = draft(client, rid, job, preview=True)
            assert preview['mode'] == '基础草稿' and len(calls) == index
            first = draft(client, rid, job)
            assert first['mode'] == 'Workers AI 草稿'
            assert draft(client, rid, job) == first
        assert len(calls) == 5
        status = client.get('/health').json()['draft_usage']
        assert status['attempts'] == 5 and status['failed'] == 0
        assert status['input_tokens'] == 1250 and status['output_tokens'] == 400



def test_concurrent_requests_coalesce_and_regeneration_is_idempotent(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, wait
    from threading import Event
    from uuid import uuid4
    from app.workers_drafts import WorkersDrafts

    started = Event()
    release = Event()
    calls = []

    def worker(request):
        calls.append(request)
        started.set()
        assert release.wait(4)
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
            'body':f'我参与 FastAPI 接口开发并编写单元测试，这是第{len(calls)}次草稿。',
            'evidence_ids':['E1'],
        })}}]})

    monkeypatch.setattr(WorkersDrafts,'transport',httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path))) as client, ThreadPoolExecutor(2) as pool:
        rid = upload(client, PROFILE)
        first = pool.submit(draft, client, rid, ENGINEERING)
        assert started.wait(4)
        duplicate = pool.submit(draft, client, rid, ENGINEERING)
        try:
            assert not wait([duplicate], timeout=0.15).done and len(calls) == 1
        finally:
            release.set()
        original = first.result(timeout=5)
        assert duplicate.result(timeout=5) == original and len(calls) == 1
        key = str(uuid4())
        replaced = draft(client, rid, ENGINEERING, regeneration_id=key)
        assert replaced['answer'] != original['answer'] and len(calls) == 2
        assert draft(client, rid, ENGINEERING, regeneration_id=key) == replaced
        assert draft(client, rid, ENGINEERING) == replaced and len(calls) == 2


def test_twelve_sanitized_product_and_engineering_pairs(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    examples = [
        ('主持用户访谈','用户访谈','实现 FastAPI 接口','FastAPI'),
        ('梳理用户需求','用户需求','开发 Python API','Python'),
        ('制定功能优先级','功能优先级','编写单元测试','单元测试'),
        ('设计产品原型','产品原型','验证异常处理','异常处理'),
        ('分析用户反馈','用户反馈','验证数据质量','数据质量验证'),
        ('分析转化率和漏斗','指标分析','完成 Docker 部署','Docker'),
        ('设计 A/B 测试','实验设计','验证性能测试结果','性能验证'),
        ('编写需求文档','需求文档','搭建自动化测试流程','自动化测试'),
        ('分析产品取舍','需求取舍','编写集成测试','集成测试'),
        ('制定需求优先级','功能优先级','完成数据清洗','数据处理'),
        ('调研用户需求和用户反馈','用户反馈','构建 SQL 查询','SQL'),
        ('设计界面原型','产品原型','实现 React 前端','React'),
    ]
    payloads = []

    def worker(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        payloads.append(payload)
        item = payload['evidence'][0]
        text = '我在项目中实践过'+ '、'.join(item['topics'])+'，也愿意进一步说明经历。'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
            'body':text, 'evidence_ids':[item['id']],
        })}}]})

    monkeypatch.setattr(WorkersDrafts,'transport',httpx.MockTransport(worker))
    with TestClient(create_app(configured(tmp_path))) as client:
        for i, (product, product_topic, engineering, engineering_topic) in enumerate(examples):
            rid = upload(client, f'## 项目经历\n{product}。\n{engineering}。')
            product_result = draft(client, rid, {**PRODUCT,'source_id':f'p{i}','jd':product})
            engineer_result = draft(client, rid, {**ENGINEERING,'source_id':f'e{i}','jd':engineering})
            assert product_result['mode'] == engineer_result['mode'] == 'Workers AI 草稿'
            assert product_result['evidence'][0]['line'] == 2 and engineer_result['evidence'][0]['line'] == 3
            assert product_topic in product_result['answer'] and engineering_topic in engineer_result['answer']
            assert payloads[-2]['role']=='product' and payloads[-1]['role']=='engineering'
        assert len(payloads) == 24



def test_statement_preview_is_bilingual_bounded_and_has_no_mail_fields(tmp_path):
    job = {**ENGINEERING, 'source':'himalayas', 'contact_emails':[],
           'application_url':'https://example.org/apply?job=42#application'}
    with TestClient(create_app(Settings(data_dir=tmp_path, use_demo_resume=True))) as client:
        rid = upload(client, PROFILE)
        english = draft(client,rid,job,statement={'language':'en','limit':16},signature='Never Included')
        chinese = draft(client,rid,job,statement={'language':'zh','limit':26})
        assert 'FastAPI' in english['answer'] and len(english['answer'].split()) <= 16
        assert 'FastAPI' in chinese['answer'] and len(''.join(chinese['answer'].split())) <= 26
        assert english['language']=='en' and chinese['language']=='zh'
        for result in (english, chinese):
            assert result['mode']=='基础草稿' and result['fallback_reason']=='not_configured'
            assert not {'subject','recipients','signature','attachment_name'}.intersection(result)
            assert result['evidence'][0]['line']==6



def test_statement_shares_worker_and_keys_language_question_and_limit(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    requests = []
    def worker(request):
        content = json.loads(json.loads(request.content)['messages'][1]['content'])
        requests.append(content)
        body = ('我实现过 FastAPI 接口并验证功能。' if content['language']=='zh' else
                'I built FastAPI endpoints and wrote unit tests to verify the implementation.')
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
            'body':body,'evidence_ids':['E1'],
        })}}]})

    monkeypatch.setattr(WorkersDrafts,'transport',httpx.MockTransport(worker))
    job = {**ENGINEERING,'source':'himalayas'}
    with TestClient(create_app(configured(tmp_path, model_mode='mock', cloudflare_daily_drafts=4))) as client:
        rid = upload(client, PROFILE)
        options = {'language':'en','question':'Why are you a good fit for this role?'}
        assert draft(client,rid,job,preview=True,statement=options)['mode']=='基础草稿'
        assert not requests
        original = draft(client,rid,job,statement=options)
        assert original['mode']=='Workers AI 草稿'
        assert draft(client,rid,job,statement=options)==original and len(requests)==1
        assert draft(client,rid,job,statement={**options,'language':'zh'})['mode']=='Workers AI 草稿'
        challenge = draft(client,rid,job,statement={**options,'question':'Describe a debugging challenge at Private Company.'})
        assert challenge['mode']=='Workers AI 草稿'
        assert requests[-1]['question_intent']=='challenge'
        assert 'Private Company' not in json.dumps(requests)
        assert 1 <= len(requests[-1]['duties']) <= 2
        short = draft(client,rid,job,statement={**options,'limit':5})
        assert short['fallback_reason']=='invalid_output' and short['length']<=5
        email = draft(client,rid,ENGINEERING)
        assert email['fallback_reason']=='app_daily_limit' and email['subject']
        eligibility = draft(client,rid,job,statement={**options,'question':'Do you require visa sponsorship?'})
        assert eligibility['fallback_reason']=='question_needs_input' and len(requests)==4
        stats=client.get('/health').json()['draft_usage']
        assert stats['attempts']==4 and stats['failed']==1


def test_application_url_preserves_job_query_and_anchor():
    from app.discovery_models import JobCandidate

    url = 'https://jobs.example.org/apply?job=42&utm_source=himalayas#application'
    assert JobCandidate(application_url=url).application_url==url
    assert JobCandidate(application_url='javascript:alert(1)').application_url is None
    assert JobCandidate(application_url='https://not-a-job@example.org/apply').application_url is None



def test_ops_statement_answers_introduction_and_identifies_only_missing_facts(tmp_path, monkeypatch):
    from app.workers_drafts import WorkersDrafts

    requests = []

    def worker(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        requests.append(data)
        body = ('我有 Linux 服务部署和故障排查的经验。' if data['language']=='zh'
                else 'I have hands-on Linux deployment experience and can apply it to this role.')
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
            'body':body,'evidence_ids':['E1'],
        })}}]})

    monkeypatch.setattr(WorkersDrafts, 'transport', httpx.MockTransport(worker))
    job = {'source':'himalayas','title':'Linux Operations Engineer',
           'jd':'部署和维护 Linux 服务器，日常监控与故障排查。'}
    profile = '## 工作经历\n负责 Linux 服务器巡检、故障排查，部署和维护云平台服务。'
    with TestClient(create_app(configured(tmp_path))) as client:
        rid = upload(client, profile)
        intro = {'language':'zh','question':'请你介绍一下你自己'}
        blank = draft(client,rid,job,statement={'language':'zh'})
        chinese = draft(client,rid,job,statement=intro)
        english = draft(client,rid,job,statement={'language':'en','question':'Why are you a good fit for this role?'})
        assert all(item['mode']=='Workers AI 草稿' and item['evidence'] for item in (blank,chinese,english))
        assert [call['question_intent'] for call in requests] == ['fit','introduction','fit']
        assert draft(client,rid,job,statement=intro) == chinese and len(requests)==3
        mixed = draft(client,rid,job,statement={**intro,'question':'请作自我介绍，并说明期望薪资和到岗日期。'})
        assert mixed['mode']=='Workers AI 草稿' and 'Linux' in mixed['answer']
        assert mixed['missing_facts']==['期望薪资','到岗日期']
        pending = draft(client,rid,job,statement={**intro,'question':'你需要签证吗？'})
        assert pending['mode']=='待补充' and pending['missing_facts']==['签证或工作资格'] and len(requests)==4
        confirmed = draft(client,rid,job,statement={**intro,'question':'什么时候能到岗？'},available_from='2027-01-04')
        assert '2027-01-04' in confirmed['answer'] and not confirmed['missing_facts'] and len(requests)==4
        limited = draft(client,rid,job,statement={**intro,'limit':10})
        assert limited['fallback_reason']=='invalid_output' and limited['length']<=10

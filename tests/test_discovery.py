import json
import time

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import create_app
from app.settings import ROOT, Settings

SAMPLE = ROOT / 'examples/resume_demo.md'
TERMS = ['Agent开发实习', '大模型应用开发实习', 'Python后端实习']


@pytest.fixture
def discovery_client(tmp_path):
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False)
    with TestClient(create_app(settings)) as client:
        yield client


def upload(client):
    response = client.post('/api/resumes', files={'file': ('resume.md', SAMPLE.read_bytes(), 'text/markdown')})
    assert response.status_code == 200, response.text
    return response.json()['id']


def start(client, resume_id, **options):
    return client.post('/api/discovery/tasks', json={
        'resume_id':resume_id, 'terms':TERMS, 'mode':'demo', **options,
    })


def result(client, task_id):
    for _ in range(100):
        response = client.get(f'/api/discovery/tasks/{task_id}')
        assert response.status_code == 200
        batch = response.json()
        if batch['stage'] in {'complete','partial','failed'}:
            return batch
        time.sleep(.03)
    pytest.fail('Discovery did not reach a terminal stage')


def test_demo_upload_evidence_dedup_grade_and_export(discovery_client):
    client = discovery_client
    rid = upload(client)
    terms = client.get(f'/api/discovery/resumes/{rid}/terms').json()
    assert len(terms['suggestions']) == 3
    resume_lines = SAMPLE.read_text(encoding='utf-8').strip().splitlines()
    assert all(item['quote'] in resume_lines[item['line']-1] for item in terms['suggestions'])
    response = start(client, rid, city='上海', internship_months=5)
    assert response.status_code == 202
    batch = result(client, response.json()['id'])
    assert batch['stage'] == 'complete'
    assert batch['counts'] == dict(fetched=30, matched=30, unique=27, duplicates=3, selected=27, analyzed=27, failed=0)
    jobs = {job['source_id']:job for job in batch['jobs']}
    assert jobs['SYN-019']['analysis']['rank'] == 4
    mentor = jobs['SYN-003']
    assert mentor['analysis']['rank'] <= 1
    assert mentor['salary_raw'] is None
    assert next(row for row in mentor['analysis']['conditions'] if row['name'] == '正式经验')['evidence'] is None
    assert set(mentor['matched_terms']) == {TERMS[0], TERMS[2]}
    assert next(row for row in jobs['SYN-025']['analysis']['conditions'] if row['name'] == '实习期限')['status'] == '冲突'
    assert {row['analysis']['rank'] for row in jobs.values()} == set(range(5))
    exported = client.get(f"/api/discovery/tasks/{batch['id']}/export")
    assert exported.json()['jobs'] == batch['jobs']
    roundtrip = start(client, rid, mode='import', jobs=exported.json()['jobs'])
    assert roundtrip.status_code == 202
    again = result(client, roundtrip.json()['id'])
    assert len(again['jobs']) == len(jobs)
    assert all(job['data_mode'] == 'import' for job in again['jobs'])


def test_five_keywords_round_robin_and_validation(discovery_client):
    client = discovery_client
    rid = upload(client)
    terms = ['岗位词甲','岗位词乙','岗位词丙','岗位词丁','岗位词戊']
    jobs = [{'source_id':f'{term}-{i}', 'title':term, 'jd':'开发 Python 后端接口', 'search_terms':[term]}
            for term in terms for i in range(4)]
    assert start(client, rid, limit=101).status_code == 422
    assert start(client, rid, terms=terms+['第六关键词']).status_code == 422
    assert start(client, rid, terms=[]).status_code == 422
    assert start(client, rid, mode='live').status_code == 422
    response = start(client, rid, terms=terms+[terms[0]], mode='import', jobs=jobs, limit=5)
    batch = result(client, response.json()['id'])
    assert len(batch['jobs']) == 5
    assert [job['matched_terms'][0] for job in batch['jobs']] == terms
    zero = start(client, rid, terms=['不存在的岗位词'])
    assert result(client, zero.json()['id'])['jobs'] == []


def test_partial_failure_retry_preserves_success(discovery_client, monkeypatch):
    import app.discovery as module

    client = discovery_client
    rid = upload(client)
    original = module.assess
    calls = []

    def unstable(service, job, index, request):
        calls.append(job.source_id)
        if job.source_id == 'SYN-002' and calls.count('SYN-002') == 1:
            raise RuntimeError('synthetic failure')
        return original(service, job, index, request)

    monkeypatch.setattr(module, 'assess', unstable)
    response = start(client, rid, limit=3)
    task_id = response.json()['id']
    batch = result(client, task_id)
    assert batch['stage'] == 'partial'
    assert batch['counts']['analyzed'] == 2
    def evidence(analysis):
        return {field:value for field,value in analysis.items() if field not in {'grade','rank'}}

    finished = {j['local_job_id']:evidence(j['analysis']) for j in batch['jobs'] if j['analysis']}
    assert client.post(f'/api/discovery/tasks/{task_id}/retry').status_code == 202
    repeated = result(client, task_id)
    assert repeated['stage'] == 'complete'
    assert len(repeated['jobs']) == 3
    assert all(evidence(j['analysis']) == finished[j['local_job_id']] for j in repeated['jobs'] if j['local_job_id'] in finished)
    assert calls.count('SYN-001') == 1 and calls.count('SYN-002') == 2


def test_pdf_without_text_and_sparse_pasted_resume(discovery_client):
    from io import BytesIO

    client = discovery_client
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    response = client.post('/api/resumes', files={'file':('scan.pdf',stream.getvalue(),'application/pdf')})
    assert response.status_code == 422
    assert '粘贴' in response.json()['error']['message']
    pasted = client.post('/api/discovery/resume-text', json={'text':'暂无技能资料。'}).json()
    data = client.get(f"/api/discovery/resumes/{pasted['id']}/terms").json()
    assert data['needs_input'] and data['suggestions'] == []


def test_unavailable_model_uses_labeled_local_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    settings = Settings(data_dir=tmp_path, model_mode='live', use_demo_resume=True, automatic_pipeline=False)
    with TestClient(create_app(settings)) as client:
        rid = upload(client)
        assert client.get(f'/api/discovery/resumes/{rid}/terms').json()['mode'] == 'local_fallback'
        response = start(client, rid, limit=1)
        batch = result(client, response.json()['id'])
        assert batch['jobs'][0]['evaluation_mode'] == 'local_fallback'

def test_text_pdf_upload_uses_extracted_text(discovery_client):
    from io import BytesIO
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'):font}),
    })
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 20 110 Td (Python FastAPI backend and Agent workflow project with RAG.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)
    response = discovery_client.post('/api/resumes', files={'file':('demo.pdf',stream.getvalue(),'application/pdf')})
    assert response.status_code == 200
    rid = response.json()['id']
    terms = discovery_client.get(f'/api/discovery/resumes/{rid}/terms').json()['suggestions']
    assert len(terms) == 3
    assert all('FastAPI' in term['quote'] for term in terms)

def test_pdf_uses_visual_order_when_text_stream_trails_with_headings(discovery_client):
    from io import BytesIO
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=240)
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })}),
    })
    lines = [
        (220, 'Education'),
        (202, 'BSc Information Systems'),
        (155, 'Maintained Linux servers and diagnosed operating system faults.'),
        (138, 'Maintained Kubernetes clusters and handled node upgrades.'),
        (121, 'Implemented Jenkins deployment pipelines and automated releases.'),
        (175, 'Professional Experience'),
        (98, 'Skills'),
        (80, 'Familiar with Terraform.'),
    ]
    content = DecodedStreamObject()
    content.set_data('\n'.join(f'BT /F1 10 Tf 25 {y} Td ({line}) Tj ET' for y, line in lines).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)
    client = discovery_client
    response = client.post('/api/discovery/resumes', files={'file':('layout.pdf',stream.getvalue(),'application/pdf')})
    assert response.status_code == 200
    data = client.get(f"/api/discovery/resumes/{response.json()['id']}/terms").json()
    assert not data['needs_input']
    assert {item['role_id'] for item in data['suggestions']} == {'linux', 'containers', 'devops'}
    assert all(item['quote'] in {line for _, line in lines[2:5]} for item in data['suggestions'])


def test_existing_resume_reads_late_project_delivery_but_excludes_tool_lists(discovery_client):
    client = discovery_client
    text = '\n'.join([
        '专业技能', '仅了解基于 FastAPI 的后端服务和 RAG 文档问答。',
        *[f'整理常规培训笔记第 {i} 条。' for i in range(125)],
        '项目背景：提供业务应用的部署环境。',
        '技术栈：仅了解 FastAPI 后端服务、RAG 文档问答。',
        '实施过程：',
        '部署并维护 Linux 服务器，执行故障排查。',
        '负责 Kubernetes 集群运维，升级节点并部署 Kubernetes Dashboard。',
        '搭建 Jenkins 发布流水线，实施自动化发布。',
    ])
    rid = client.post('/api/discovery/resume-text',json={'text':text}).json()['id']
    data = client.get(f'/api/discovery/resumes/{rid}/terms').json()
    assert {item['role_id'] for item in data['suggestions']} == {'linux', 'containers', 'devops'}
    assert all(item['line'] > 120 and item['quote'] in text.splitlines()[item['line']-1]
               for item in data['suggestions'])


def test_match_prepares_current_resume_once_and_returns_when_ready(discovery_client, monkeypatch):
    import app.discovery as module

    original = module.prepare_match_evidence
    prepared = []
    def observe(rows):
        prepared.append(rows)
        return original(rows)
    monkeypatch.setattr(module, 'prepare_match_evidence', observe)
    client = discovery_client
    resume_a = upload(client)
    resume_b = client.post('/api/discovery/resume-text',
                           json={'text':'工作经历：使用 Vue 开发前端组件，实现响应式页面。'}).json()['id']
    jobs = [{'source_id':'fast-api','title':'Python 后端',
             'jd':'使用 Python、FastAPI 开发接口。', 'search_terms':['Python 后端实习']},
            {'source_id':'front','title':'前端',
             'jd':'使用 Vue 开发前端组件。', 'search_terms':['Python 后端实习']}]
    summaries = []
    for rid in (resume_a, resume_a, resume_b):
        created = start(client, rid, mode='import', jobs=jobs)
        assert created.status_code == 202
        response = client.get(f"/api/discovery/tasks/{created.json()['id']}?wait_ms=1000")
        assert response.headers.get('x-request-id') and response.headers.get('server-timing','').startswith('app;dur=')
        snapshot = response.json()
        assert snapshot['stage'] == 'complete'
        assert snapshot['resume']['id'] == rid
        summaries.append([row['analysis'] for row in snapshot['jobs']])
    assert len(prepared) == 2
    assert summaries[0] == summaries[1]
    assert summaries[2] != summaries[1]


def test_paste_matches_without_search_terms_or_source_request(discovery_client, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Pasting JD must not request the external source')

    monkeypatch.setattr('app.discovery_online.read_api', unexpected)
    client = discovery_client
    rid = upload(client)
    jd = '使用 Python、FastAPI 开发接口。'
    response = start(client, rid, mode='paste', terms=[], jobs=[{'title':'粘贴岗位', 'jd':jd}])
    assert response.status_code == 202
    batch = result(client, response.json()['id'])
    assert batch['stage'] == 'complete' and batch['limit'] == 1
    assert batch['counts']['matched'] == 0 and batch['counts']['duplicates'] == 0
    job, = batch['jobs']
    assert job['jd'] == jd and job['source'] == 'manual'
    assert job['analysis']['evidence'] and job['matched_terms'] == []
    assert start(client, rid, mode='paste', jobs=[{'title':'无正文'}]).status_code == 422


def test_actual_frontend_work_and_explicit_weekly_requirement(discovery_client):
    client = discovery_client
    rid = upload(client)
    jobs = [
        {'source_id':'a','title':'Agent 应用实习','jd':'实现 Vue 页面和 CSS 布局。要求熟悉 TypeScript。','search_terms':[TERMS[0]]},
        {'source_id':'b','title':'Python 后端实习','jd':'用 Python、FastAPI 开发接口。每周至少 5 天。','search_terms':[TERMS[2]]},
    ]
    batch = result(client,start(client,rid,mode='import',jobs=jobs,weekly_days=3).json()['id'])
    by_id = {job['source_id']:job['analysis'] for job in batch['jobs']}
    assert by_id['a']['rank'] == 3 and not by_id['a']['evidence']
    assert by_id['b']['rank'] == 4
    conditions = {condition['name']:condition['status'] for condition in by_id['b']['conditions']}
    assert conditions['每周出勤'] == '冲突'
    assert conditions['实习期限'] == '待确认'

def test_himalayas_exact_terms_full_details_and_round_robin(discovery_client, monkeypatch):
    import httpx
    import app.discovery_online as online

    calls = []

    def fetch(url, *, params, **options):
        assert url == online.HIMALAYAS_URL
        calls.append(params)
        n = range(1,7) if 'Agent' in params['q'] else range(5,11)
        return httpx.Response(200,json={'jobs':[
            {'guid':f'https://himalayas.app/companies/sample/jobs/test-{i}',
             'applicationLink':'https://example.invalid/apply','title':f'Test Role {i}',
             'companyName':'Test Co','employmentType':'Intern','locationRestrictions':['Australia'],
             'timezoneRestrictions':[10], 'description':'<p>Develop a Python API with FastAPI.</p>' if i!=1 else None,
             'minSalary':30,'maxSalary':40,'salaryPeriod':'hourly','currency':'AUD'} for i in n
        ]})

    monkeypatch.setattr(online.httpx,'get',fetch)
    client=discovery_client
    assert client.get('/api/discovery/sources').json()['sources'][0]['ready']
    rid=upload(client)
    terms=['Agent 开发实习','Python 后端实习']
    task_id=start(client,rid,mode='online',terms=terms,country='AU',limit=10,job_type='internship').json()['id']
    batch=result(client,task_id)
    assert batch['stage']=='complete'
    assert [c['q'] for c in calls]==['Agent developer','Python backend']
    assert all(c['country']=='AU' and c['employment_type']=='Intern' for c in calls)
    assert batch['counts']['selected']==batch['counts']['analyzed']==10
    assert batch['counts']['duplicates']==2
    shared=next(j for j in batch['jobs'] if j['title']=='Test Role 5')
    assert shared['matched_terms']==terms
    assert shared['application_url']=='https://example.invalid/apply'
    assert shared['analysis']['evidence']
    assert shared['salary_raw']=='AUD 30–40 hourly' and shared['city'] is None
    assert shared['location_restrictions']==['Australia']
    assert shared['detail_status']=='detail' and '<p>' not in shared['jd']
    assert next(j for j in batch['jobs'] if j['title']=='Test Role 1')['detail_status']=='list_only'


def test_retired_online_sources_are_unavailable(discovery_client):
    client=discovery_client
    rid=upload(client)
    assert client.get('/api/discovery/sources').json() == {
        'sources':[{'id':'himalayas','name':'Himalayas','ready':True}]
    }
    for retired in ('greenhouse','careerjet','boss'):
        assert start(client,rid,platform=retired,mode='online').status_code == 503


def test_himalayas_zero_and_only_failed_term_is_retried(discovery_client, monkeypatch):
    import httpx
    import app.discovery_online as online

    client=discovery_client
    rid=upload(client)
    monkeypatch.setattr(online.httpx,'get',lambda *a,**kw: httpx.Response(200,json={'jobs':[]}))
    empty=result(client,start(client,rid,mode='online').json()['id'])
    assert empty['stage']=='complete' and not empty['jobs']
    calls=[]
    def fetch(url, *, params, **kw):
        term='Agent' if 'Agent' in params['q'] else 'Python'
        calls.append((term,params['page']))
        if params['page'] > 1:
            return httpx.Response(200,json={'jobs':[]})
        if term=='Python' and calls.count((term,1))==1:
            raise httpx.ConnectError('offline')
        return httpx.Response(200,json={'jobs':[
            {'guid':'https://himalayas.app/companies/sample/jobs/a', 'title':'Python Intern',
             'description':'Build Python services.','employmentType':'Intern'},
        ]})
    monkeypatch.setattr(online.httpx,'get',fetch)
    terms=[TERMS[0],TERMS[2]]
    task=start(client,rid,mode='online',terms=terms,limit=10).json()['id']
    assert result(client,task)['stage']=='partial'
    assert client.post(f'/api/discovery/tasks/{task}/retry').status_code==202
    done=result(client,task)
    assert done['stage']=='complete' and done['jobs'][0]['matched_terms']==terms
    assert calls==[('Agent',1),('Python',1),('Agent',2),('Python',1),('Python',2)]
    assert start(client,rid,platform='boss',mode='online').status_code==503

@pytest.mark.parametrize(('total','expected'), [(0,[0,0,0,0,0]), (1,[1,0,0,0,0]), (3,[1,0,1,1,0]),
    (5,[1,1,1,1,1]), (10,[1,2,4,2,1]), (50,[5,10,20,10,5]), (100,[10,20,40,20,10])])
def test_relative_distribution_caps_top_and_keeps_all_labels(total, expected):
    from app.discovery_rules import GRADES, rank_batch

    jobs = [{'analysis':{'rank':0, 'grade':GRADES[0], 'match_score':score}}
            for score in range(total)]
    distribution = rank_batch(jobs)
    assert distribution == [dict(grade=grade, count=count) for grade, count in zip(GRADES, expected)]
    assert [job['analysis']['match_score'] for job in jobs] == list(reversed(range(total)))
    assert all(job['analysis']['evidence_rank'] == 0 for job in jobs)


def test_relative_grades_do_not_promote_weak_evidence_or_conflicts():
    from app.discovery_rules import GRADES, rank_batch

    # High skill scores cannot override evidence or hard-condition constraints.
    jobs = [{'analysis':{'rank':rank, 'grade':GRADES[rank], 'match_score':100}}
            for rank in (2,2,2,3,4,4)]
    jobs.append({'analysis':None})
    distribution = rank_batch(jobs)
    assert [row['count'] for row in distribution] == [0,0,3,1,2]
    assert jobs[-1]['analysis'] is None


def test_duty_evidence_outweighs_a_title_or_a_list_of_interests(discovery_client):
    client = discovery_client
    rid = upload(client)
    jobs = [
        {'source_id':'projects','title':'Python 实习', 'jd':'用 Python、FastAPI 开发接口。',
         'search_terms':[TERMS[2]]},
        {'source_id':'gaps','title':'Python 实习', 'jd':'用 Python、FastAPI 开发接口。必须熟练掌握 Kubernetes。',
         'search_terms':[TERMS[2]]},
        {'source_id':'intro','title':'Python Agent 实习','jd':'用 Transformer 开发模型。必须熟练掌握 Transformer。',
         'search_terms':[TERMS[2]]},
    ]
    batch = result(client, start(client, rid, mode='import', jobs=jobs).json()['id'])
    assert [job['source_id'] for job in batch['jobs']] == ['projects','gaps','intro']
    assert 'Transformer' in batch['jobs'][-1]['analysis']['gaps']
    assert 'Kubernetes' in batch['jobs'][1]['analysis']['gaps']


def test_reanalysis_keeps_candidates_offline_and_original_batch(discovery_client, monkeypatch):
    import httpx
    from app import discovery_online

    client = discovery_client
    calls = []

    def fetch(url, *, params, **kwargs):
        calls.append(params.copy())
        return httpx.Response(200, json={'jobs': [
            {'guid':'https://himalayas.app/companies/sample/jobs/python-demo',
             'title':'Python Intern','companyName':'Demo','employmentType':'Intern',
             'description':'Develop a Python API with FastAPI.'},
        ]})

    monkeypatch.setattr(discovery_online.httpx, 'get', fetch)
    old_id = upload(client)
    old_batch = result(client, start(client, old_id, mode='online', terms=[TERMS[2]], limit=1).json()['id'])
    assert old_batch['stage'] == 'complete' and len(old_batch['jobs']) == 1
    before = len(calls)

    bad = client.post('/api/discovery/resumes', files={'file':('empty.txt', b'', 'text/plain')})
    assert bad.status_code == 422
    new = client.post('/api/discovery/resumes', files={'file':('different.md', b'# Projects\nDesign screens in Figma.', 'text/markdown')})
    assert new.status_code == 200
    assert client.app.state.service.resumes.current()['id'] == old_id

    new_id = new.json()['id']
    request = client.post(f"/api/discovery/tasks/{old_batch['id']}/reanalyze", json={'resume_id':new_id})
    assert request.status_code == 202
    snapshot = result(client, request.json()['id'])
    assert snapshot['id'] != old_batch['id'] and snapshot['stage'] == 'complete'
    assert snapshot['reused_candidates'] and snapshot['resume']['id'] == new_id
    assert snapshot['resume']['name'] == new.json()['name'] and snapshot['resume']['version']
    assert len(calls) == before
    old_job, new_job = old_batch['jobs'][0], snapshot['jobs'][0]
    for key in ('local_job_id','matched_terms','source','data_mode','url','jd'):
        assert new_job[key] == old_job[key]
    assert new_job['analysis']['match_score'] < old_job['analysis']['match_score']
    assert client.get(f"/api/discovery/tasks/{old_batch['id']}").json() == old_batch

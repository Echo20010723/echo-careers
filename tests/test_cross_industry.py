import json
import time

import httpx
from fastapi.testclient import TestClient

from app.discovery_models import DiscoveryRequest
from app.main import create_app
from app.occupations import templates
from app.settings import Settings

OPS = """求职意向：运维工程师
工作经历：三年运维工程师
维护 Linux 服务器、负责服务器巡检、处理生产故障和系统监控。
负责云平台交付，部署云主机并管理云资源。
维护 Kubernetes 集群、处理容器编排告警。
使用 Ansible 与 Python 批量部署；使用 Dify 辅助运维。"""
FINANCE = """工作经历：三年财务会计
负责月结、总账与财务报表。使用 Python 自动对账并核对银行流水。
负责发票审核、付款凭证与应付账款。"""


def wait(client, task_id):
    for _ in range(180):
        data = client.get(f'/api/discovery/tasks/{task_id}').json()
        if data['stage'] in {'complete','failed','partial'}:
            return data
        time.sleep(.025)
    raise AssertionError('Search did not finish')


def upload(client, value):
    response = client.post('/api/discovery/resume-text', json={'text':value})
    response.raise_for_status()
    return response.json()['id']


def test_a_then_ops_then_offline_reanalysis(tmp_path, monkeypatch):
    calls = []
    def fetch(url, *, params, **kwargs):
        calls.append(dict(params))
        if 'Agent' in params['q']:
            title, jd, code = 'Agent Intern', 'Build Agent workflow using Python.', 'agent'
        else:
            title, jd, code = 'Cloud Operations Role', '维护 Linux 服务器，负责云平台交付和 Kubernetes 集群维护。', 'ops'
        return httpx.Response(200, json={'jobs':[{'guid':f'https://himalayas.app/companies/demo/jobs/{code}',
                                        'title':title,'description':jd,'employmentType':'Full-time'}]})
    monkeypatch.setattr('app.discovery_online.httpx.get', fetch)
    with TestClient(create_app(Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False))) as client:
        a = client.post('/api/discovery/example').json()['id']
        b = upload(client, OPS)
        terms = client.get(f'/api/discovery/resumes/{b}/terms').json()
        assert terms['resume_id'] == b and len(terms['suggestions']) == 3
        assert {item['role_id'] for item in terms['suggestions']} <= {'linux','cloud','containers','devops'}
        assert all(item['quote'] in OPS.splitlines()[item['line']-1] for item in terms['suggestions'])
        def start(rid, terms):
            task = client.post('/api/discovery/tasks',json={'resume_id':rid,'terms':terms,'limit':1})
            task.raise_for_status()
            return wait(client,task.json()['id'])
        old = start(a, ['Agent 开发实习'])
        new = start(b, ['Kubernetes Operations Engineer','云平台运维工程师'])
        assert calls[-2:] == [{'q':'Kubernetes Operations Engineer','page':1},
                              {'q':'Cloud Operations Engineer','page':1}]
        assert new['id'] != old['id'] and new['resume']['id'] == b
        assert new['jobs'][0]['title'] == 'Cloud Operations Role'
        assert new['jobs'][0]['analysis']['evidence']
        assert new['jobs'][0]['retrieved_by'] == new['terms'] and not new['jobs'][0]['lexical_matches']
        assert new['query_conditions']['job_type'] == 'any'
        exported = client.get(f"/api/discovery/tasks/{new['id']}/export").json()
        assert exported['terms'] == new['terms'] and exported['counts']['queries'] == new['counts']['queries']
        before = len(calls)
        child = client.post(f"/api/discovery/tasks/{old['id']}/reanalyze",json={'resume_id':b}).json()['id']
        revised = wait(client,child)
        assert revised['parent_batch_id'] == old['id'] and len(calls) == before
        assert revised['terms'] == old['terms'] and revised['counts']['queries'] == old['counts']['queries']
        assert revised['jobs'][0]['local_job_id'] == old['jobs'][0]['local_job_id']
        assert client.get(f"/api/discovery/tasks/{old['id']}").json() == old


def test_career_switch_and_non_technical_roles(tmp_path):
    source = tmp_path/'templates.jsonl'
    source.write_text(json.dumps({'id':'template-1','role_title':'财务会计','duties':['核对总账']})+'\n', encoding='utf-8')
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False, occupation_templates=source)
    assert len(templates(source)) == 1
    with TestClient(create_app(settings)) as client:
        switching = upload(client, OPS.replace('运维工程师\n','希望转行 AI 应用开发\n', 1))
        suggestions = client.get(f'/api/discovery/resumes/{switching}/terms').json()['suggestions']
        exploration = next(row for row in suggestions if row['role_id']=='llm')
        assert exploration['kind']=='转行探索' and any('可迁移经验' in item for item in exploration['needs'])
        finance = upload(client, FINANCE)
        offers = client.get(f'/api/discovery/resumes/{finance}/terms').json()['suggestions']
        assert offers[0]['role_id']=='accounting'
        assert all(item['role_id'] in {'accounting','payables','cashier'} for item in offers)
        assert offers[0]['references'][0]['id'] == 'template-1'
        response = client.post('/api/discovery/tasks',json={'resume_id':finance,'mode':'import','terms':['会计'],
            'jobs':[{'id':'finance-demo','job_title':'财务会计','employer':'演示公司','location':'远程',
                     'jd_text':'负责总账和月结，核对财务报表。','source':{}}]})
        assert response.status_code==202
        job = wait(client,response.json()['id'])['jobs'][0]
        assert job['title']=='财务会计' and job['detail_status']=='list_only' and job['jd'] is None
        assert job['analysis']['evidence'] and job['analysis']['rank'] <= 2


def test_legacy_default_and_search_length():
    import json
    data = {'resume_id':'r', 'terms':['Kubernetes Operations Engineer']}
    assert DiscoveryRequest.model_validate(data).job_type == 'any'
    assert DiscoveryRequest.saved(json.dumps(data)).job_type == 'internship'
    data['terms']=['x'*48]
    assert DiscoveryRequest.model_validate(data).terms == ['x'*48]

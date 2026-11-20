import json
import time

import pytest
from fastapi.testclient import TestClient

from app.discovery_agent import Action
from app.discovery_models import JobCandidate
from app.discovery_online import SearchPage
from app.errors import AppError
from app.main import create_app
from app.settings import ROOT, Settings

FIXTURE = json.loads((ROOT/'examples/agent_jobs.json').read_text(encoding='utf-8'))


def wait(client, run_id):
    for _ in range(200):
        response = client.get('/api/discovery/agent/'+run_id)
        assert response.status_code == 200
        state = response.json()
        if state['status'] in {'complete','waiting','failed','interrupted'}:
            return state
        time.sleep(.025)
    raise AssertionError('Agent did not stop')


def launch(client, **options):
    resume = client.post('/api/discovery/example').json()
    response = client.post('/api/discovery/agent', json={
        'resume_id':resume['id'], 'goal':'Find five AI application development internships', **options})
    assert response.status_code == 202, response.text
    return response.json()['run_id'], resume['id']


@pytest.mark.parametrize(('source','searches'), [('drift', 2), ('focused', 1)])
def test_agent_changes_search_only_when_needed(tmp_path, source, searches):
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False)
    with TestClient(create_app(settings)) as client:
        run, resume = launch(client, source=source, degree='bachelor')
        outcome = wait(client, run)
        assert outcome['status'] == 'complete' and outcome['mode'] == 'demo', outcome
        assert outcome['spent']['searches'] == searches
        assert len(outcome['recommendations']) == 5
        assert {job['source_id'] for job in outcome['recommendations']} == set(FIXTURE['buckets']['focused'])
        assert any(job['eligibility']['degree_preferred'] for job in outcome['recommendations'])
        assert all(not any(check['status']=='冲突' for check in job['analysis']['conditions'])
                   for job in outcome['recommendations'])
        lines = client.get('/api/resumes/'+resume).json()['text'].splitlines()
        for job in outcome['recommendations']:
            assert all(quote in job['jd'] for group in ('duties','required','bonus') for quote in job['analysis'][group])
            assert all(e['quote'] in lines[e['line']-1] for e in job['analysis']['evidence'])
        assert wait(client, run)['spent'] == outcome['spent']


def test_answer_resumes_same_run_without_replaying_search(tmp_path):
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False)
    with TestClient(create_app(settings)) as client:
        run, _ = launch(client, source='clarify', degree='bachelor')
        waiting = wait(client, run)
        assert waiting['status'] == 'waiting' and waiting['question']['field'] == 'country'
        assert waiting['spent']['searches'] == 1
        assert client.post('/api/discovery/agent/'+run+'/answer',json={'answer':'AU'}).status_code == 202
        assert client.post('/api/discovery/agent/'+run+'/answer',json={'answer':'AU'}).status_code == 409
        complete = wait(client, run)
        assert complete['status'] == 'complete' and complete['run_id'] == run
        assert complete['spent']['searches'] == 2
        assert 'us' not in {job['source_id'] for job in complete['recommendations']}


def test_error_budget_and_rule_fallback(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AppError('ONLINE_SEARCH_NETWORK')

    monkeypatch.setattr('app.discovery_agent.Himalayas.page', unavailable)
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False, agent_searches=1)
    with TestClient(create_app(settings)) as client:
        run, _ = launch(client, source='online')
        outcome = wait(client, run)
        assert outcome['mode'] == 'rule_fallback' and outcome['status'] == 'complete'
        assert outcome['spent']['searches'] == 1 and not outcome['recommendations']
        assert any(event['action']=='search_error' for event in outcome['events'])


def test_live_decision_sees_observation_before_changing_search(tmp_path, monkeypatch):
    actions = iter([('search','Agent developer'),('read','job-1'),('search','AI backend intern'),
                    ('read','job-2'),('finish',None)])
    prompts = []
    class ScriptedModel:
        simulated = False
        def run(self, stage, prompt, schema, **kwargs):
            if stage != 'agent':
                raise AssertionError(stage)
            prompts.append(prompt)
            action, argument = next(actions)
            return Action(action=action, argument=argument), {}

    def search(self, term, options, offset, context):
        key = 'phd-a' if term == 'Agent developer' else 'api'
        return SearchPage([JobCandidate.model_validate(FIXTURE['jobs'][key])], False, 1, query=term)

    monkeypatch.setattr('app.discovery_agent.Himalayas.page', search)
    settings = Settings(data_dir=tmp_path, use_demo_resume=True, automatic_pipeline=False, agent_calls=5)
    app = create_app(settings)
    app.state.service.provider = ScriptedModel()
    with TestClient(app) as client:
        run, _ = launch(client, source='online', degree='bachelor', target=1)
        outcome = wait(client, run)
        assert outcome['mode'] == 'live' and outcome['status'] == 'complete', outcome
        assert outcome['spent']['model_calls'] == 5
        assert outcome['recommendations'][0]['source_id'] == 'api'
        assert 'Candidates must be enrolled in a PhD program' in prompts[2]



def test_public_workers_agent_observes_jd_and_shares_request_budget(tmp_path, monkeypatch):
    from httpx import MockTransport, Response
    from app.workers_drafts import WorkersDrafts

    actions = iter([
        ('search', 'Agent developer'), ('read', 'job-1'), ('search', 'AI backend intern'),
        ('read', 'job-2'), ('retrieve', 'Python FastAPI RAG'), ('finish', None),
    ])
    contexts = []

    def infer(request):
        assert request.url.path.endswith('/ai/v1/chat/completions')
        assert request.headers['authorization'] == 'Bearer test-token'
        payload = json.loads(request.content)
        assert payload['model'] == '@cf/qwen/qwen3.8-27b'
        assert payload['store'] is False
        contexts.append(json.loads(payload['messages'][1]['content']))
        action, argument = next(actions)
        return Response(200, json={'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps({'action':action,'argument':argument}), 'reasoning_content':'internal'
        }}]})

    def search(self, term, options, offset, context):
        key = {'Agent developer':'phd-a', 'AI backend intern':'api'}[term]
        return SearchPage([JobCandidate.model_validate(FIXTURE['jobs'][key])], False, 1, query=term)

    monkeypatch.setattr(WorkersDrafts, 'transport', MockTransport(infer))
    monkeypatch.setattr('app.discovery_agent.Himalayas.page', search)
    settings = Settings(data_dir=tmp_path, public_mode=True,
                        cloudflare_account_id='a'*32, cloudflare_api_token='test-token',
                        cloudflare_daily_drafts=6)
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').json()['agent_provider'] == 'cloudflare'
        run, _ = launch(client, source='online', target=1, degree='bachelor')
        outcome = wait(client, run)
        assert (outcome['status'], outcome['mode'], outcome['provider']) == ('complete', 'live', 'cloudflare')
        assert outcome['recommendations'][0]['source_id'] == 'api'
        assert contexts[2]['read'][0]['eligibility']['degree'] == '冲突'
        assert all(isinstance(line, int) for line in contexts[-1]['evidence'])
        assert 'internal' not in str(outcome)
        assert [e['tool'] for e in outcome['events'] if e['action'] == 'model_decision'] == [
            'search', 'read', 'search', 'read', 'retrieve', 'finish']
        assert wait(client, run)['spent']['model_calls'] == 6
        blocked, _ = launch(client, source='online', target=1, degree='bachelor', terms=['Agent developer'])
        fallback = wait(client, blocked)
        assert fallback['mode'] == 'rule_fallback'
        assert fallback['spent']['model_calls'] == 0
        assert '本站今日' in fallback['events'][0]['summary']
        usage = client.get('/health').json()['draft_usage']
        assert usage['attempts'] == usage['agent_attempts'] == usage['agent_succeeded'] == 6



def test_workers_credentials_with_embedded_newline_are_rejected_before_request(tmp_path):
    from app.workers_drafts import WorkersDrafts

    settings = Settings(data_dir=tmp_path, public_mode=True,
                        cloudflare_account_id='a'*32, cloudflare_api_token='test'+chr(10)+'token')
    workers = WorkersDrafts(settings)
    try:
        assert workers.usage()['configuration_status'] == 'invalid_token'
        assert not workers.configured and not workers.agent_enabled
        assert workers.choose_action({}, '', Action, scope=tmp_path, public=True, timeout=2) == (
            None, 'not_configured', False)
        assert workers.usage()['attempts'] == 0
    finally:
        workers.close()

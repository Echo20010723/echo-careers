"""Bounded, resumable tool loop. Stored events are observations, never hidden reasoning."""
from datetime import date
import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .discovery_models import JobCandidate, job_identity
from .discovery_online import Himalayas, remote_query
from .audit import measure
from .discovery_rules import assess
from .errors import AppError
from .store import dumps, uid

log = logging.getLogger(__name__)
DEGREES = ('high_school', 'bachelor', 'master', 'phd')
COUNTRIES = {'US':'united states', 'CA':'canada', 'GB':'united kingdom', 'CN':'china', 'AU':'australia',
             'DE':'germany', 'FR':'france', 'IN':'india', 'SG':'singapore', 'JP':'japan'}
ACADEMIC = re.compile(r'\b(?:ph\.?\s*d\.?|doctorate|doctoral)\b|博士', re.I)
PREFERRED = re.compile(r'preferred|nice to have|bonus|optional|优先|加分', re.I)
NEGATED = re.compile(r'not required|not mandatory|非必需|无需|不要求', re.I)
REQUIRED = re.compile(r'must|required|requirements|only|minimum|enrolled|必需|必须|仅限|要求', re.I)


class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resume_id: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=3, max_length=500)
    terms: list[str] = Field(default_factory=list, max_length=5)
    target: int = Field(5, ge=1, le=10)
    source: Literal['online', 'drift', 'focused', 'clarify'] = 'drift'
    country: str | None = Field(None, pattern=r'^[A-Z]{2}$')
    degree: Literal['high_school', 'bachelor', 'master', 'phd'] | None = None
    job_type: Literal['internship','any'] = 'any'
    weekly_days: int | None = Field(None, ge=1, le=7)
    internship_months: int | None = Field(None, ge=1, le=36)
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    available_from: date | None = None


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=40)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['search', 'read', 'retrieve', 'ask', 'finish']
    argument: str | None


class SearchObservation(BaseModel):
    query: str
    jobs: list[JobCandidate] = Field(max_length=25)


class EvidenceObservation(BaseModel):
    line: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=280)


class Eligibility(BaseModel):
    degree: Literal['符合', '冲突', '待确认']
    location: Literal['符合', '冲突', '待确认']
    degree_quote: str | None
    degree_preferred: str | None


def eligibility(job, options):
    text = job.jd or job.jd_summary or ''
    sentences = re.split(r'[。；;\n]+|(?<=[!?])\s+|(?<=[a-z])\.\s+(?=[A-Z])', text)
    mentions = [line.strip()[:280] for line in sentences if ACADEMIC.search(line)]
    preferred = next((line for line in mentions if PREFERRED.search(line)), None)
    required = next((line for line in mentions if REQUIRED.search(line) and not (PREFERRED.search(line) or NEGATED.search(line))), None)
    degree = '待确认'
    if required and options.degree:
        degree = '符合' if options.degree == 'phd' else '冲突'
    location = '待确认'
    restrictions = {value.casefold().strip() for value in job.location_restrictions}
    if options.country and restrictions:
        names = {options.country.casefold(), COUNTRIES.get(options.country, '')} - {''}
        if names & restrictions or {'worldwide', 'anywhere', 'global'} & restrictions:
            location = '符合'
        elif restrictions <= set(COUNTRIES.values()) | {code.casefold() for code in COUNTRIES}:
            location = '冲突'
    return Eligibility(degree=degree, location=location, degree_quote=required, degree_preferred=preferred)


class DiscoveryAgent:
    def __init__(self, service, discovery):
        self.service, self.discovery, self.store = service, discovery, service.store
        self.source = Himalayas()
        with self.store.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY, status TEXT NOT NULL, state TEXT NOT NULL)')
            db.execute("UPDATE agent_runs SET status='interrupted' WHERE status='running'")

    def create(self, options):
        resume = self.discovery.resume(options.resume_id)
        terms = list(dict.fromkeys(term.strip() for term in options.terms))
        if any(not self.valid_term(term) for term in terms):
            raise AppError('INVALID_INPUT', '请使用简短职位词，避免粘贴简历。')
        settings = self.service.settings
        state = {'run_id':uid(), 'resume_version':resume['version'], 'options':options.model_dump(mode='json'),
                 'terms':terms, 'mode':'demo' if options.source != 'online' else
                 'rule_fallback' if self.service.provider.simulated else 'live',
                 'budget':{'searches':settings.agent_searches, 'reads':settings.agent_reads,
                           'model_calls':settings.agent_calls, 'seconds':settings.agent_seconds},
                 'spent':{'searches':0, 'reads':0, 'model_calls':0, 'seconds':0},
                 'events':[], 'queries':[], 'jobs':{}, 'read':{}, 'retrieved':False, 'evidence':[],
                 'question':None, 'asked':[], 'recommendations':[], 'stop_reason':None}
        if options.source == 'online' and self.service.provider.simulated:
            state['events'].append({'action':'fallback','summary':'已使用规则降级。启用真实决策需配置 MODEL_MODE=live 和 OPENAI_API_KEY。'})
        with self.store.tx() as db:
            db.execute('INSERT INTO agent_runs VALUES(?,?,?)', (state['run_id'],'queued',dumps(state)))
        return {'run_id':state['run_id'], 'status':'queued'}

    def current_stage(self, run_id):
        row = self.store.one('SELECT status FROM agent_runs WHERE id=?', (run_id,))
        return row['status'] if row else None

    def status(self, run_id):
        row = self.store.one('SELECT status,state FROM agent_runs WHERE id=?', (run_id,))
        if row is None:
            raise AppError('RESUME_NOT_FOUND', '未找到该 Agent 任务。')
        state = json.loads(row['state'])
        result = {k:state[k] for k in ('run_id','resume_version','mode','budget','spent','events',
                                      'question','recommendations','stop_reason')}
        if row['status'] == 'interrupted':
            result['stop_reason'] = '服务重启中断了本轮，已执行步骤不会自动重放。'
        resume_id = state['options']['resume_id']
        resume = self.store.one('SELECT id,name FROM resumes WHERE id=?', (resume_id,))
        inspected = [{'title':state['jobs'][key].get('title'), 'source_url':state['jobs'][key].get('url'),
                      **item['eligibility']} for key, item in state['read'].items()]
        return {**result, 'inspected':inspected, 'status':row['status'], 'target':state['options']['target'],
                'resume_id':resume_id, 'resume':resume}

    def answer(self, run_id, value):
        with self.store.tx() as db:
            row = db.execute('SELECT status,state FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            if not row or row['status'] != 'waiting':
                raise AppError('CONFLICT')
            state = json.loads(row['state'])
            key = state['question']['field']
            answer = value.strip().lower()
            if answer not in {'跳过', 'unknown'}:
                if key == 'country' and re.fullmatch('[a-z]{2}', answer):
                    state['options'][key] = answer.upper()
                elif key == 'degree' and answer in DEGREES:
                    state['options'][key] = answer
                else:
                    raise AppError('INVALID_INPUT', '请选择学历选项、输入两位国家代码，或选择跳过。')
            state['events'].append({'action':'answer', 'summary':'已记录补充条件，沿用本轮搜索和已读岗位。'})
            state['question'] = None
            resume = db.execute('SELECT text FROM resumes WHERE id=?', (state['options']['resume_id'],)).fetchone()
            options = AgentInput.model_validate(state['options'])
            index = self.discovery.match_evidence(resume['text'])
            for job_id in state['read']:
                candidate = JobCandidate.model_validate(state['jobs'][job_id])
                state['read'][job_id] = self.evaluate(candidate, options, index)
            db.execute("UPDATE agent_runs SET status='queued', state=? WHERE id=?", (dumps(state),run_id))
        return {'run_id':run_id, 'status':'queued'}

    def save(self, state, status):
        with self.store.tx() as db:
            db.execute('UPDATE agent_runs SET status=?,state=? WHERE id=?', (status,dumps(state),state['run_id']))

    @staticmethod
    def valid_term(term):
        return bool(re.fullmatch(r'[\w +#.\-]{2,48}', term))

    def advance(self):
        with self.store.tx() as db:
            row = db.execute("SELECT id,state FROM agent_runs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return False
            state = json.loads(row['state'])
            db.execute("UPDATE agent_runs SET status='running' WHERE id=?", (row['id'],))
        started = time.monotonic()
        status = 'queued'
        try:
            options = AgentInput.model_validate(state['options'])
            resume = self.discovery.resume(options.resume_id)
            index = self.discovery.match_evidence(resume['text'])
            remaining = state['budget']['seconds'] - state['spent']['seconds']
            if remaining < 1:
                self.finish(state, '已达到本轮时间预算。')
                status = 'complete'
            else:
                action = self.decide(state, options, index, remaining)
                remaining -= time.monotonic() - started
                if remaining <= 0:
                    self.finish(state, '已达到本轮时间预算。')
                    status = 'complete'
                else:
                    status = self.execute(state, options, resume, index, action, timeout=remaining)
        except AppError as error:
            state['events'].append({'action':'error', 'summary':error.message})
            self.finish(state, '任务停止，保留已核实的推荐。')
            status = 'failed'
        except Exception:
            log.exception('Agent run step failed [%s]', state['run_id'])
            self.finish(state, '任务未能继续，保留已核实的推荐。')
            status = 'failed'
        state['spent']['seconds'] = round(state['spent']['seconds'] + time.monotonic() - started, 3)
        self.save(state, status)
        return True

    def allowed(self, state, options, action):
        arg, name = action.argument, action.action
        spent, budget = state['spent'], state['budget']
        if name == 'search':
            if not arg or not self.valid_term(arg) or spent['searches'] >= budget['searches']:
                return False
            normalize = remote_query if options.source == 'online' else str
            return normalize(arg).casefold() not in {normalize(term).casefold() for term in state['queries']}
        if name == 'read':
            return arg in state['jobs'] and arg not in state['read'] and spent['reads'] < budget['reads']
        if name == 'retrieve':
            return not state['retrieved'] and arg and len(arg) <= 160
        if name == 'ask':
            return arg in {'degree','country'} and not getattr(options,arg) and arg not in state['asked']
        return name == 'finish'

    def decide(self, state, options, index, remaining):
        if state['mode'] == 'live' and state['spent']['model_calls'] < state['budget']['model_calls']:
            state['spent']['model_calls'] += 1
            self.save(state, 'running')
            observations = [{'id':key, 'title':item.get('title'), 'summary':(item.get('jd_summary') or '')[:160]}
                            for key,item in state['jobs'].items() if key not in state['read']][:20]
            inspected = [{'id':key, 'title':state['jobs'][key].get('title'), 'eligibility':item['eligibility'],
                          'score':item['analysis']['match_score'], 'reason':item['analysis']['reason'],
                          'duties':item['analysis']['duties'][:2]} for key,item in state['read'].items()]
            prompt, _ = self.service.prompt('discovery_agent.txt', state=dumps({
                'goal':options.goal, 'target':options.target, 'country':options.country, 'degree':options.degree,
                'terms':state['terms'], 'queries':state['queries'], 'unread':observations, 'read':inspected,
                'weekly_days':options.weekly_days, 'internship_months':options.internship_months,
                'graduation_year':options.graduation_year, 'available_from':state['options'].get('available_from'),
                'resume_signals':sorted({skill for row in index.rows for skill in row['skills']}),
                'evidence':state['evidence'], 'asked':state['asked'], 'events':state['events'][-8:],
                'remaining':{key:round(value-state['spent'][key], 2) for key,value in state['budget'].items()},
            }))
            try:
                value, _ = self.service.provider.run('agent', prompt, Action, timeout=min(remaining,self.service.settings.timeout))
                action = Action.model_validate(value)
                if self.allowed(state, options, action):
                    return action
                raise AppError('INVALID_INPUT', '模型建议的动作超出预算、重复或参数无效。')
            except (AppError, ValidationError) as error:
                state['mode'] = 'rule_fallback'
                message = error.message if isinstance(error, AppError) else '模型输出格式无效。'
                state['events'].append({'action':'fallback','summary':'规则降级 · '+message})
        elif state['mode'] == 'live':
            state['mode'] = 'rule_fallback'
            state['events'].append({'action':'fallback','summary':'真实模型调用预算已用完，后续按规则处理。'})
        return self.rule_action(state, options, index)

    def rule_action(self, state, options, index):
        make = lambda action, argument=None: Action(action=action, argument=argument)
        if not state['queries']:
            return make('search', state['terms'][0] if options.source == 'online' and state['terms'] else 'Agent developer')
        if len(self.recommendations(state)) >= options.target:
            return make('retrieve', 'Python FastAPI RAG Agent') if not state['retrieved'] else make('finish')
        if state['spent']['reads'] < state['budget']['reads']:
            unread = [job_id for job_id in state['jobs'] if job_id not in state['read']]
            if unread:
                return make('read', unread[0])
        academic = sum(item['eligibility']['degree_quote'] is not None for item in state['read'].values())
        if academic and not options.degree and 'degree' not in state['asked']:
            return make('ask', 'degree')
        restrictive = any(item.get('location_restrictions') for item in state['jobs'].values())
        if options.source == 'clarify' and restrictive and not options.country and 'country' not in state['asked']:
            return make('ask', 'country')
        skills = {skill for row in index.rows for skill in row['skills']}
        alternatives = (['AI backend intern'] if 'FastAPI' in skills else []) + (['RAG engineering intern'] if 'RAG' in skills else [])
        if options.source != 'online' and 'AI backend intern' not in alternatives:
            alternatives.append('AI backend intern')
        if not state['retrieved'] and state['read']:
            return make('retrieve', 'FastAPI RAG Python Agent')
        if state['spent']['reads'] < state['budget']['reads']:
            for term in [*alternatives, *state['terms'][1:]]:
                action = make('search', term)
                if self.allowed(state, options, action):
                    return action
        return make('finish')

    def execute(self, state, options, resume, index, action, *, timeout=14):
        name, arg = action.action, action.argument
        if not self.allowed(state, options, action):
            self.finish(state, '没有剩余可执行动作。')
            return 'complete'
        events = state['events']
        if name == 'finish':
            if state['read'] and not state['retrieved']:
                self.execute(state, options, resume, index, Action(action='retrieve', argument='Python FastAPI RAG Agent'))
            self.finish(state, '已核实足够的岗位。' if len(self.recommendations(state)) >= options.target else
                        '已检查可用结果或达到预算，返回有证据支持的岗位。')
            return 'complete'
        if name == 'search':
            state['spent']['searches'] += 1
            state['queries'].append(arg)
            self.save(state, 'running')
            try:
                result = self.search(options, arg, timeout=timeout)
            except AppError as error:
                events.append({'action':'search_error','summary':arg+' · '+error.message})
                return 'queued'
            existing = {key for item in state['jobs'].values() for key in job_identity(JobCandidate.model_validate(item))}
            new = 0
            for job in result.jobs:
                keys = job_identity(job)
                if not keys or any(key in existing for key in keys):
                    continue
                existing.update(keys)
                new += 1
                state['jobs']['job-'+str(len(state['jobs'])+1)] = job.model_dump(mode='json')
            academic = sum(item['eligibility']['degree_quote'] is not None for item in state['read'].values())
            reason = f'已有 {academic} 条 JD 明确要求博士，补充搜索' if academic else '搜索'
            events.append({'action':'search','summary':f'{reason} {result.query}，新增 {new} / {len(result.jobs)} 条。'})
        elif name == 'read':
            state['spent']['reads'] += 1
            with measure(self.service.audit, 'provided_jd_read', state['run_id']):
                candidate = JobCandidate.model_validate(state['jobs'][arg])
            with measure(self.service.audit, 'match_analysis', state['run_id']):
                result = self.evaluate(candidate, options, index)
            state['read'][arg] = result
            checks = result['eligibility']
            status = '明确写有博士要求' if checks['degree_quote'] else '博士优先，未列为硬条件' if checks['degree_preferred'] else '已核对职责与条件' if candidate.jd else '仅有摘要'
            events.append({'action':'read','summary':f'已读取 {len(state["read"])} 份岗位材料 · {candidate.title} · {status}。'})
        elif name == 'retrieve':
            state['retrieved'] = True
            row = self.store.one('SELECT chunks,name FROM resumes WHERE id=?', (resume['id'],))
            try:
                with measure(self.service.audit, 'resume_retrieval', state['run_id']):
                    found = self.service.rag.search(arg, {**resume,'name':row['name'],'chunks':json.loads(row['chunks'])})
            except AppError as error:
                found = {'facts':[]}
                events.append({'action':'retrieve_error','summary':error.message})
            facts = [fact['text'] for fact in found['facts']]
            state['evidence'] = [EvidenceObservation(line=item['line'], quote=item['quote']).model_dump()
                                 for item in index.rows if any(item['quote'] in chunk for chunk in facts)][:5]
            events.append({'action':'retrieve','summary':f'检索当前简历，定位 {len(state["evidence"])} 条原文证据。'})
        elif name == 'ask':
            state['asked'].append(arg)
            question = '请确认你的学历，便于核对博士硬条件。' if arg == 'degree' else '你在哪个国家或地区工作？请填两位国家代码。'
            state['question'] = {'field':arg,'text':question}
            events.append({'action':'ask','summary':question})
            return 'waiting'
        return 'queued'

    def search(self, options, term, *, timeout=14):
        if options.source == 'online':
            response = self.source.page(term, SimpleNamespace(country=options.country, job_type=options.job_type), 0,
                                        {'timeout':min(14, timeout)})
            return SearchObservation(query=response.query or term, jobs=response.jobs[:25])
        fixture = json.loads((self.service.settings.root/'examples/agent_jobs.json').read_text(encoding='utf-8'))
        bucket = options.source if term.casefold() in {'agent developer','agent 开发实习','agent开发实习'} else 'focused'
        return SearchObservation(query=term, jobs=[JobCandidate.model_validate(fixture['jobs'][key]) for key in fixture['buckets'][bucket]])

    def evaluate(self, job, options, index):
        verdict = eligibility(job, options)
        request = SimpleNamespace(city=None, job_type=options.job_type, available_from=options.available_from, weekly_days=options.weekly_days,
                                  internship_months=options.internship_months, graduation_year=options.graduation_year)
        analysis = assess(self.service, job, index, request, use_model=False)
        checks = [('学历',verdict.degree,verdict.degree_quote),('远程地区',verdict.location,'、'.join(job.location_restrictions) or None)]
        for name, status, evidence in checks:
            analysis['conditions'].append({'name':name,'status':status,'evidence':evidence})
            if status == '待确认':
                analysis['questions'].append('请向 HR 核对'+name+'条件。')
        if any(item['status'] == '冲突' for item in analysis['conditions']):
            analysis.update(rank=4,grade='拉完了',reason='与已明确的岗位硬条件冲突',recommendation='优先看其他岗位')
        return {'analysis':analysis, 'eligibility':verdict.model_dump()}

    @staticmethod
    def recommendations(state):
        eligible = []
        for key, item in state['read'].items():
            analysis = item['analysis']
            job = state['jobs'][key]
            if (job.get('jd') and analysis['rank'] < 3 and analysis['match_score'] >= 55
                    and analysis['evidence'] and not any(row['status'] == '冲突' for row in analysis['conditions'])
                    and not (item['eligibility']['degree_quote'] and item['eligibility']['degree'] == '待确认')):
                eligible.append({**job, **item, 'local_job_id':key})
        eligible.sort(key=lambda item: (item['analysis']['rank'], -item['analysis']['match_score']))
        return eligible[:state['options']['target']]

    def finish(self, state, reason):
        state['recommendations'] = self.recommendations(state)
        state['stop_reason'] = reason
        state['events'].append({'action':'finish', 'summary':reason+f' 共保留 {len(state["recommendations"])} 条。'})

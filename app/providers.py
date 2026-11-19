import json,re,time,os,threading
from types import SimpleNamespace
from pydantic import ValidationError
from .schemas import JobRequirement,MatchAnalysis,Evidence
from .errors import AppError
from .store import uid,now
from .audit import REQUEST_ID
from .resumes import digest
from .policy import unsafe_instruction

class MockProvider:
    simulated=True
    def __init__(self,settings,store,audit): self.settings,self.store,self.audit=settings,store,audit
    def run(self,stage,prompt,schema=None,context=None,request_id=None):
        c=context or {}
        if stage=='extract':
            job=c['job']; text=job['raw_jd']; result={'job_title':job.get('title'),'required_skills':[],'preferred_skills':[],'responsibilities':[]}
            section=None
            # This deterministic extractor is a mock baseline, never reported as model quality.
            segments=re.split(r'(岗位职责|工作职责|职位描述|职责|任职要求|岗位要求|任职资格|加分项|优先条件)[：:]?',text)
            for item in segments:
                if item in {'岗位职责','工作职责','职位描述','职责'}: section='responsibilities'; continue
                if item in {'任职要求','岗位要求','任职资格'}: section='required_skills'; continue
                if item in {'加分项','优先条件'}: section='preferred_skills'; continue
                if not section: continue
                for line in re.split(r'[；;。\n]+',item):
                    line=re.sub(r'^\s*\d+[.、)]\s*','',line.strip()).strip('：:')
                    if not line or unsafe_instruction(line): continue
                    target='preferred_skills' if '优先' in line or section=='preferred_skills' else section
                    if target=='required_skills' and not re.search(r'(或|至少一种|一个或多个|例如|比如)',line):
                        bits=re.split(r'[、，,]',re.sub(r'^(熟悉|熟练掌握|掌握|了解|具备)','',line))
                    else: bits=[line]
                    result[target].extend(x.strip() for x in bits if x.strip())
            value=JobRequirement(**result)
        elif stage=='match':
            resume=c['resume']['text']; req=c['requirement']
            def assess(item):
                tokens=re.findall(r'[A-Za-z][A-Za-z0-9+#.-]*|需求|原型|协作|视觉|文本分类|检索|工具调用|状态管理',item)
                matches=[t for t in tokens if t.lower() in resume.lower()]
                alternative=bool(re.search(r'或|至少一种|一个或多个',item))
                credit=1 if matches and (alternative or len(matches)==len(tokens)) else .5 if matches else 0
                evidence=next((line.strip() for line in resume.splitlines() if any(t.lower() in line.lower() for t in matches)),None)
                if evidence and re.search(r'规划|设计|了解|基础',evidence): credit=min(credit,.5)
                return Evidence(requirement=item,status={1:'demonstrated',.5:'partial',0:'not_evidenced'}[credit],credit=credit,evidence=evidence,reason='模拟词项证据检查；需真实模型及本人复核')
            value=MatchAnalysis(skill_evidence=[assess(x) for x in req.required_skills],project_evidence=[assess(x) for x in req.responsibilities],preferred_skill_notes=req.preferred_skills,eligibility_checks=[],suggested_highlights=[],questions_to_confirm=[])
        else:
            q=c['question']; chunks=[x for x in c['resume']['chunks'] if not x.get('needs_current_confirmation')]
            related=[]
            for x in chunks:
                if any(w.lower() in x['text'].lower() and w.lower() in q.lower() for w in ['RAG','Python','LangChain','项目','YOLO','产品','教育','技能']): related.append(x)
            x=(related or chunks or c['resume']['chunks'])[0]
            lines=[line for line in x['text'].splitlines() if line.strip() and not line.startswith(('姓名','类别','主题'))]
            value='您好，'+('；'.join(lines)[:240] if lines else '这方面的具体情况需要进一步确认。')
            pref=c.get('preferences',{});details=[]
            if '城市' in q or '哪里' in q: details.append('可以考虑'+ '、'.join(pref.get('cities',[]))+'，城市没有强制排名。')
            if '远程' in q: details.append('优先线下实习，接受远程；具体地域条件可以进一步沟通。' if pref.get('remote_acceptable') else '目前希望优先沟通线下实习。')
            if re.search(r'日薪|月薪|薪资|工资|薪酬',q):
                daily=pref.get('salary_daily',[100,600]);monthly=pref.get('salary_monthly',[3000,8000])
                if '月薪' in q: details.append(f'月薪参考{monthly[0]}—{monthly[1]}元，可以结合岗位具体沟通。')
                elif '日薪' in q: details.append(f'日薪参考{daily[0]}—{daily[1]}元，可以结合岗位具体沟通。')
                else:details.append(f'日薪参考{daily[0]}—{daily[1]}元，月薪参考{monthly[0]}—{monthly[1]}元，分别按计薪周期沟通。')
            if details:value=''.join(details)

        return value,{'simulated':True,'model':'mock-deterministic-v1','usage':None}

class OpenAIProvider:
    simulated=False
    def __init__(self,settings,store,audit,client=None):
        self.settings,self.store,self.audit=settings,store,audit
        if client is None:
            if not os.getenv('OPENAI_API_KEY'): raise AppError('SERVICE_NOT_CONFIGURED')
            from openai import OpenAI
            client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],timeout=settings.timeout,max_retries=0)
        self.client=client
    def run(self,stage,prompt,schema=None,context=None,request_id=None,timeout=None):
        request_id=request_id or REQUEST_ID.get()
        from openai import AuthenticationError,PermissionDeniedError,RateLimitError,APITimeoutError,APIConnectionError,APIStatusError
        if len(prompt)>self.settings.max_input_chars: raise AppError('INPUT_TOO_LARGE')
        model=getattr(self.settings,'model_'+stage)
        retries = 0 if stage == 'agent' else self.settings.retries
        client = self.client.with_options(timeout=timeout) if timeout is not None else self.client
        for attempt in range(retries+1):
            with self.store.tx() as db:
                count=db.execute('SELECT count(*) FROM model_calls').fetchone()[0]
                if count>=self.settings.max_calls: raise AppError('BUDGET_EXCEEDED')
                db.execute('INSERT INTO model_calls VALUES(?,?,?)',(uid(),stage,now()))
            started=time.monotonic()
            try:
                kwargs={'model':model,'input':[{'role':'system','content':prompt}],'max_output_tokens':self.settings.max_output_tokens,'store':False}
                if self.settings.reasoning_effort: kwargs['reasoning']={'effort':self.settings.reasoning_effort}
                if schema:
                    response=client.responses.parse(**kwargs,text_format=schema)
                else: response=client.responses.create(**kwargs)
                if response.status=='incomplete': raise AppError('MODEL_TRUNCATED')
                if any(getattr(c,'type',None)=='refusal' for o in response.output for c in getattr(o,'content',[])): raise AppError('MODEL_REFUSED')
                if response.status!='completed': raise AppError('MODEL_UNAVAILABLE')
                value=response.output_parsed if schema else response.output_text
                if value is None or isinstance(value,str) and not value.strip(): raise AppError('MODEL_EMPTY')
                meta={'simulated':False,'model':response.model,'usage':response.usage.model_dump() if response.usage else None}
                self.audit.event('model_call_completed',request_id,stage=stage,prompt_sha256=digest(prompt),model=meta['model'],token_usage=meta['usage'],duration_ms=round((time.monotonic()-started)*1000))
                return value,meta
            except (AuthenticationError,PermissionDeniedError): raise AppError('MODEL_AUTH') from None
            except ValidationError: raise AppError('MODEL_OUTPUT_INVALID') from None
            except AppError: raise
            except (RateLimitError,APITimeoutError,APIConnectionError,APIStatusError) as e:
                code='MODEL_RATE_LIMITED' if isinstance(e,RateLimitError) else 'MODEL_TIMEOUT' if isinstance(e,APITimeoutError) else 'MODEL_UNAVAILABLE'
                retryable=isinstance(e,(RateLimitError,APITimeoutError,APIConnectionError)) or getattr(e,'status_code',0)>=500
                self.audit.event('model_call_failed', request_id, stage=stage, status='error',
                                 error_code=code, retry_count=attempt, duration_ms=round((time.monotonic()-started)*1000))
                if not retryable or attempt==retries: raise AppError(code) from None
                time.sleep(min(.25*2**attempt,2))
            except Exception as e:
                name=type(e).__name__
                if 'LengthFinish' in name: raise AppError('MODEL_TRUNCATED') from None
                raise AppError('MODEL_OUTPUT_INVALID') from None

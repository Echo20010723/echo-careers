import re,json
from .errors import AppError
from .policy import unsafe_instruction

def mean(values): return sum(values)/len(values) if values else 0.0
def preference_checks(job,preferences):
    raw=job['raw_jd']; title=job.get('title') or ''; text=title+' '+raw
    checks=[]
    def add(d,s,reason): checks.append({'dimension':d,'status':s,'credit':{'matched':1,'acceptable':.75,'unknown':.5,'conflict':0}[s],'reason':reason})
    actual_ai=bool(re.search(r'(?i)(AI|Agent|LLM|RAG|人工智能|智能体|大模型|模型|机器学习|视觉|文本分类)',text))
    add('role','matched' if actual_ai else 'unknown','按JD职责及岗位描述核对AI相关性；关键词仅用于初步参考')
    city=job.get('location') or ''
    remote='远程' in (job.get('work_mode') or '') or job.get('work_mode')=='remote' or '远程' in title
    add('location','matched' if city and any(x in city for x in preferences['cities']) else 'unknown' if not city or remote else 'conflict',city or '地点未披露；远程仍需核对地域限制')
    mode=job.get('work_mode') or ''
    add('work_mode','acceptable' if remote and preferences['remote_acceptable'] else 'matched' if mode in {'线下','onsite'} else 'unknown','优先线下，远程可接受；混合办公待确认')
    salary=job.get('salary') or ''
    nums=[float(x) for x in re.findall(r'\d+(?:\.\d+)?',salary)]
    if nums and re.search(r'天|日|/d',salary,re.I): lo,hi=preferences['salary_daily']; period='日薪'
    elif nums and re.search(r'月|[kK]|千|万',salary):
        lo,hi=preferences['salary_monthly']; period='月薪'
        factor=10000 if '万' in salary else 1000 if re.search(r'[kK]|千',salary) else 1
        nums=[n*factor for n in nums]
    else: nums=[]; period='薪资周期待确认'
    add('salary','matched' if nums and min(nums)<=hi and max(nums)>=lo else 'unknown',period+'；弹性参考，不自动接受或拒绝，不换算周期')
    employment=job.get('employment_type') or ''
    add('employment_type','matched' if '实习' in employment or '实习' in title else 'conflict' if '全职' in employment and '实习' not in employment else 'unknown','目标是实习；未知不视为已满足')
    return checks

def qualification_checks(job,resume):
    text=' '.join(str(job.get(k) or '') for k in ['raw_jd','title','education','experience','employment_type'])
    result=[]
    def add(c,s='unknown'): result.append({'condition':c,'status':s,'evidence':None,'reason':'须本人确认当前资格；不以技术分抵消'})
    if re.search(r'仅全职|不接受实习|只招全职',text) or job.get('employment_type')=='全职': add('仅全职/不接受实习','conflict')
    if re.search(r'(?:[2-9]|[1-9]\d)[-—至]?(?:\d+)?\s*年.{0,8}(经验|研发|工作)|[2-9]年以上',text): add('多年正式经验要求')
    edu=job.get('education') or ''
    if re.search(r'已取得博士|博士学位|博士毕业|仅博士',text) or ('博士' in edu and '优先' not in edu): add('博士学历要求','conflict' if '博士' not in resume['text'] else 'unknown')
    if ('硕士' in edu and '优先' not in edu) or re.search(r'已.{0,3}硕士|硕士学位',text): add('已获硕士资格与在读资格不能等同')
    if re.search(r'每周|连续实习|[一二三四五六七八九十\d]+个月|三天内|到岗|实习期限',text): add('到岗/出勤/实习期限缺少当前确认值')
    return result

def calculate(job,requirement,analysis,resume,preferences,weights):
    def validate(rows,requirements):
        if len(rows)!=len(requirements) or [r.requirement for r in rows]!=requirements: raise AppError('MODEL_OUTPUT_INVALID')
        output=[]
        for row in rows:
            d=row.model_dump(); expected={'demonstrated':1,'partial':.5,'not_evidenced':0}[d['status']]
            if d['credit']!=expected: raise AppError('MODEL_OUTPUT_INVALID')
            if d['credit'] and (not d['evidence'] or d['evidence'] not in resume['text']): raise AppError('MODEL_OUTPUT_INVALID')
            # A quote does not license changing planned work into completed work.
            if d['evidence'] and re.search(r'规划|设计方案|基础了解',d['evidence']) and d['credit']==1:
                d.update(status='partial',credit=.5,reason='证据仅支持设计/基础，已下调')
            output.append(d)
        return output
    skill=validate(analysis.skill_evidence,requirement.required_skills)
    project=validate(analysis.project_evidence,requirement.responsibilities)
    checks=preference_checks(job,preferences)
    eligibility=[]
    for item in analysis.eligibility_checks:
        d=item.model_dump()
        if d['status']=='met' and (not d['evidence'] or d['evidence'] not in resume['text']): d.update(status='unknown',reason='模型未提供有效当前证据')
        eligibility.append(d)
    eligibility+=qualification_checks(job,resume)
    status='ineligible' if any(x['status']=='conflict' for x in eligibility) else 'needs_confirmation' if any(x['status']=='unknown' for x in eligibility) else 'no_known_conflict'
    missing=not requirement.required_skills or not requirement.responsibilities
    values={'skill':mean([x['credit'] for x in skill]),'project':mean([x['credit'] for x in project]),'intention':mean([x['credit'] for x in checks])}
    score=round(sum(values[k]*weights[k] for k in weights),2)
    recommendation='信息不足，先补充' if missing else '暂不优先投递' if status=='ineligible' else '先确认条件，再决定投递' if status=='needs_confirmation' else '建议优先沟通投递' if score>=.75 else '可以尝试投递' if score>=.5 else '暂不优先投递'
    if any(x['status']=='conflict' for x in checks): recommendation='先确认条件，再决定投递'
    return {'match_score':score,'score_detail':{**values,'weights':weights,'skill_evidence':skill,'project_evidence':project,'preferred_skill_notes':analysis.preferred_skill_notes,'intention_checks':checks,'eligibility_checks':eligibility,'eligibility_status':status,'missing_essential_input':bool(missing),'recommendation':recommendation,'questions_to_confirm':analysis.questions_to_confirm,'suggested_highlights':analysis.suggested_highlights},'requirement':requirement.model_dump(),'resume_id':resume['id'],'resume_version':resume['version'],'score_note':'材料相关度，不是录用概率'}

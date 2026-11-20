"""Select local evidence, then assemble an editable application email."""
from pathlib import PurePath
import re

from .discovery_rules import evidence_index, skill_hits
from .resumes import digest
from .policy import unsafe_instruction
from .store import dumps
from .workers_drafts import statement_length


PRODUCT = (
    ('用户需求', 'user needs', r'用户需求|需求梳理|需求分析|user needs|requirements gathering'),
    ('用户访谈', 'user interviews', r'用户访谈|用户调研|user interviews?|user research'),
    ('需求取舍', 'product trade-offs', r'取舍|权衡|trade.?offs?'),
    ('功能优先级', 'feature prioritization', r'优先级|prioriti[sz]|roadmap'),
    ('产品原型', 'product prototypes', r'原型|prototype|wireframe'),
    ('用户反馈', 'user feedback', r'用户反馈|user feedback'),
    ('指标分析', 'product metrics', r'产品指标|转化率|漏斗|product metrics|funnel|conversion rate'),
    ('实验设计', 'experiment design', r'A/B\s*(?:测试|test)|实验设计|experiment design'),
    ('需求文档', 'product requirements documents', r'PRD|需求文档|requirements document'),
)
ENGINEERING = (
    ('接口开发', 'API development', r'接口|API endpoints?|backend APIs?'),
    ('问题调试', 'debugging', r'调试|故障排查|debug|troubleshoot'),
    ('单元测试', 'unit testing', r'单元测试|unit test'),
    ('异常处理', 'error handling', r'异常处理|错误处理|error handling'),
    ('数据质量验证', 'data quality checks', r'数据质量|data quality'),
    ('服务部署', 'service deployment', r'部署|deploy'),
    ('性能验证', 'performance testing', r'性能测试|性能验证|benchmark|load test'),
    ('自动化测试', 'automated testing', r'自动化测试|automated test'),
    ('集成测试', 'integration testing', r'集成测试|integration test'),
    ('数据处理', 'data processing', r'数据处理|数据清洗|data processing|data cleaning'),
)
ACTION = re.compile(
    r'梳理|调研|主持|制定|分析|设计|实现|开发|构建|搭建|编写|验证|测试|部署|负责|参与|完成|巡检|维护|排查|监控|处理|'
    r'\b(?:built|developed|implemented|designed|tested|wrote|analy[sz]ed|conducted|prioriti[sz]ed|'
    r'deployed|validated|contributed|participated|maintained|monitored|operated|troubleshot)\b', re.I,
)
NEGATION = re.compile(r'未曾|尚未|没有|未参与|未完成|未实现|未做过|未使用|不熟悉|\b(?:not yet|never|did not|no experience)\b', re.I)



def topics(text, language):
    found = [skill for skill in skill_hits(text) if not re.search(r'\d{2,}', skill)]
    for zh, en, pattern in PRODUCT + ENGINEERING:
        if re.search(pattern, text, re.I):
            found.append(zh if language == 'zh' else en)
    return list(dict.fromkeys(found))


def select_evidence(job, resume, *, language=None, question_topics=(), allow_transferable=False):
    jd = job.jd or job.jd_summary or job.description or ''
    title = job.title or ''
    title_script = re.sub(r'\s', '', title)
    zh = len(re.findall(r'[\u3400-\u9fff]', title_script))
    body_zh = len(re.findall(r'[\u3400-\u9fff]', jd[:1500]))
    language = language or ('zh' if zh >= 1 or body_zh >= 12 else 'en')
    product_title = re.search(r'产品(?:经理|实习|运营|专员|助理)|\bproduct (?:manager|intern|owner|analyst|management)\b', title, re.I)
    target = topics(title + '\n' + jd, language)
    role_topics = lambda table: {row[0 if language == 'zh' else 1] for row in table}
    product, engineering = role_topics(PRODUCT), role_topics(ENGINEERING)
    role = 'product' if product_title or len(product.intersection(target)) >= 2 and not engineering.intersection(target) else 'engineering'
    preferred = product if role == 'product' else engineering | set(skill_hits(title + jd))
    scored, transferable = [], []
    for item in evidence_index(resume['text']):
        line = item['quote']
        if item['category'] in {'求职方向','教育','技能','认证'} or not ACTION.search(line) or NEGATION.search(line):
            continue
        terms = topics(line, language)
        overlap = set(terms).intersection(target)
        emphasis = set(terms).intersection(preferred)
        if not (overlap or (allow_transferable and emphasis)) or role == 'product' and not emphasis:
            continue
        # Send only canonical actions and topics, never the original resume line.
        terms = sorted((term for term in terms if term in overlap or term in emphasis),
                       key=lambda term: (term not in question_topics, term not in overlap))[:6]
        verbs = []
        if re.search(r'访谈|调研|梳理|分析|conducted|research|analy[sz]ed', line, re.I):
            verbs.append('research_analysis')
        if re.search(r'设计|制定|优先级|designed|prioriti[sz]ed', line, re.I):
            verbs.append('design_prioritization')
        if re.search(r'实现|开发|构建|编写|built|implemented|developed|wrote', line, re.I):
            verbs.append('implementation')
        if re.search(r'验证|测试|tested|validated', line, re.I):
            verbs.append('verification')
        score = 4*len(overlap) + 3*len(emphasis) + int(item['category'] != '其他')
        score += 5*len(set(terms).intersection(question_topics))
        (scored if overlap else transferable).append((score, item['line'], terms, verbs or ['participation']))
    selected = []
    seen = set()
    for _, number, terms, verbs in sorted(scored or transferable, key=lambda row: (-row[0],row[1])):
        if set(terms) <= seen:
            continue
        seen.update(terms)
        selected.append({'id':f'E{len(selected)+1}','line':number,'topics':terms,'actions':verbs,
                         **({'transferable':True} if not scored else {})})
        if len(selected) == 2:
            break
    return language, role, target[:12], selected


def assemble_application(job, data, resume, workers, settings):
    jd = job.jd or job.jd_summary or ''
    language, role, target, evidence = select_evidence(job, resume)
    chinese = language == 'zh'
    title = ' '.join((job.title or ('原帖岗位' if chinese else 'this role')).split())
    company = ' '.join((job.company or '').split())
    signature = (data.signature or '').strip()
    subject = ('应聘｜' if chinese else 'Application: ') + title + (('｜' if chinese else ' — ') + company if company else '')
    specified = re.search(
        r'(?:邮件|投递邮件|投递)\s*(?:主题|标题)\s*(?:为|格式|请注明)?\s*[:：]\s*([^\r\n]{1,180})'
        r'|(?:email subject|subject line)\s*(?:format)?\s*[:：]\s*([^\r\n]{1,180})', jd, re.I,
    )
    if specified:
        subject = ' '.join((specified[1] or specified[2]).strip(' “”，「」').split()) or subject
    guidance = [line.strip()[:250] for line in jd.splitlines()
                if re.search(r'附件|简历.{0,8}命名|文件名|attachment|filename|name (?:your|the) (?:cv|resume)', line, re.I)][:3]
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', ' - '.join(filter(None,[signature,title,'简历' if chinese else 'Resume'])))
    extension = PurePath(resume['name']).suffix.lower()
    attachment = ' '.join(safe_name.split())[:100].strip(' .') + (extension if extension in {'.pdf','.txt','.md'} else '.pdf')
    payload = draft_payload(language, role, target, evidence)
    narrative, reason = generate_narrative(job, data, resume, workers, settings, payload)
    if chinese:
        body = [(company+'招聘团队' if company else '招聘团队')+'您好，', '我希望应聘'+title+'。']
    else:
        body = [f'Dear {company} hiring team,' if company else 'Dear hiring team,',
                f'I am writing to apply for {title}.']
    if narrative:
        body.append(narrative.body)
        evidence = [item for item in evidence if item['id'] in narrative.evidence_ids]
    elif evidence:
        separator = '、' if chinese else ', '
        entries = [separator.join(item['topics']) for item in evidence]
        body.append(('我做过与以下方向相关的工作：' if chinese else 'My relevant work has covered ') +
                    ('；' if chinese else '; ').join(entries) + ('。' if chinese else '.'))
    timing = []
    if data.available_from:
        timing.append(('最早可于' + data.available_from.isoformat() + '到岗') if chinese else
                      ('can start on or after ' + data.available_from.isoformat()))
    if data.weekly_days:
        timing.append((f'每周可出勤{data.weekly_days}天') if chinese else f'can work {data.weekly_days} days per week')
    if data.internship_months:
        timing.append((f'可实习{data.internship_months}个月') if chinese else f'can intern for {data.internship_months} months')
    if timing:
        body.append('，'.join(timing)+'。' if chinese else 'I '+', and '.join(timing)+'.')
    body.append('感谢您审阅我的申请。' if chinese else 'Thank you for considering my application.')
    if signature:
        body.append(('此致\n' if chinese else 'Best regards,\n')+signature)
    return {'mode':'Workers AI 草稿' if narrative else '基础草稿', 'fallback_reason':reason,
            'subject':subject,'answer':'\n\n'.join(body),'recipients':job.contact_emails,
            'guidance':guidance,'signature':signature,'attachment_name':attachment,'evidence':evidence}



def draft_payload(language, role, target, evidence):
    return {'language':language,'role':role,'jd_topics':target,
            'evidence':[{key:value for key,value in item.items() if key != 'line'} for item in evidence]}


def generate_narrative(job, data, resume, workers, settings, payload):
    if data.preview:
        return None, None
    filename = 'application_statement.txt' if data.statement else 'application_draft.txt'
    instructions = (settings.root/'prompts'/filename).read_text(encoding='utf-8')
    cache_key = digest(dumps([
        resume['id'], resume['version'], job.model_dump(mode='json'),
        data.model_dump(mode='json', exclude={'preview','regeneration_id','application_job'}),
        payload, instructions, workers.model,
    ]))
    return workers.generate(payload, instructions, scope=settings.data_dir,
        public=settings.public_mode, cache_key=cache_key, regeneration_id=data.regeneration_id)


QUESTION_INTENTS = (
    ('introduction', r'自我介绍|(?:介绍|说说|谈谈).{0,12}(?:你?自己|你的背景|个人背景)|introduce yourself|(?:tell|talk to) (?:us|me) about yourself|your background'),
    ('challenge', r'挑战|调试|故障|如何解决|challeng|debug|troubleshoot|how did you solve'),
    ('collaboration', r'协作|合作|团队|collaborat|teamwork|worked with'),
    ('motivation', r'兴趣|为什么想|动机|motiv|interest|attract|why (?:this|do you want)'),
    ('fit', r'匹配|适合|胜任|优势|good fit|why .* fit|qualification|strength|suitable'),
    ('experience', r'经验|经历|项目|举例|example|experience|project'),
)

QUESTION_FACTS = (
    ('salary', '期望薪资', 'salary expectations', r'薪资|薪酬|工资|待遇|salary|compensation|pay expectations'),
    ('visa', '签证或工作资格', 'visa or work authorization', r'签证|工作资格|工作许可|work authori[sz]ation|visa|sponsor|eligible to work'),
    ('start', '到岗日期', 'start date', r'到岗|入职日期|何时入职|什么时候入职|when can you start|start date|available to start|availability'),
    ('attendance', '每周出勤天数', 'days per week', r'出勤|每周.{0,8}天|days per week'),
    ('duration', '实习时长', 'internship duration', r'实习.{0,8}(?:多久|时长|期限|几个月)|internship duration|how long can you intern'),
)


def question_focus(question, language):
    # The free-text question stays local. Only canonical intent and topic labels leave the server.
    if unsafe_instruction(question):
        return 'manual', [], []
    facts = [row for row in QUESTION_FACTS if re.search(row[3], question, re.I)]
    # Remove fact phrases before looking for evidence questions; a visa question alone is not a request for experience.
    evidence_question = question
    for _, _, _, pattern in facts:
        evidence_question = re.sub(pattern, '', evidence_question, flags=re.I)
    phrases = topics(evidence_question, language)[:6]
    intent = next((name for name, pattern in QUESTION_INTENTS if re.search(pattern, evidence_question, re.I)), None)
    if intent is None and not facts and re.search(r'tell (?:us|me) about|describe', evidence_question, re.I):
        intent = 'experience'
    return intent or ('experience' if phrases else 'eligibility' if facts else 'manual' if question else 'fit'), phrases, facts


def statement_facts(data, facts, language):
    confirmed = {
        'start':data.available_from.isoformat() if data.available_from else None,
        'attendance':data.weekly_days,
        'duration':data.internship_months,
    }
    known, missing = [], []
    for field, zh, en, _ in facts:
        label = zh if language == 'zh' else en
        if value := confirmed.get(field):
            known.append(f'{label}：{value}' if language == 'zh' else f'{label}: {value}')
        else:
            missing.append(label)
    return known, missing


def statement_duties(job, language, role, question_topics):
    jd = job.jd or job.jd_summary or job.description or ''
    table = PRODUCT if role == 'product' else ENGINEERING
    preferred = {row[0 if language == 'zh' else 1] for row in table}
    duties = []
    for order, clause in enumerate(re.split(r'[。；;\n]+|(?<=[.!?])\s+', jd)[:80]):
        terms = topics(clause,language)[:5]
        if not terms or re.search(r'优先|加分|bonus|preferred',clause,re.I):
            continue
        score = 3*len(set(terms)&preferred) + 5*len(set(terms)&set(question_topics))
        score += 2*bool(re.search(r'负责|实现|验证|调试|取舍|反馈|build|develop|test|debug|design|prioriti[sz]',clause,re.I))
        duties.append((-score,order,terms))
    unique = []
    for _, _, terms in sorted(duties):
        if terms not in unique:
            unique.append(terms)
        if len(unique) == 2:
            break
    return unique


def statement_fallback(language, intent, duties, evidence, limit):
    zh = language == 'zh'
    if intent in {'manual','eligibility'} or not evidence:
        message = ('请补充经确认的信息后作答。' if zh else 'Add verified details before answering.') if evidence or intent in {'manual','eligibility'} else (
            '请补充与岗位相关的经历。' if zh else 'Add a relevant example from your experience.')
        if limit is None or statement_length(message,language) <= limit:
            return message
        return message[:limit] if zh else ' '.join(message.split()[:limit])

    examples = []
    wanted = {term for duty in duties for term in duty}
    for item in evidence:
        original = item['topics']
        chosen = [term for term in original if term in wanted] or original
        if limit is not None and len(chosen) > 1:
            chosen = sorted(chosen, key=lambda term: len(term)) if limit < (20 if zh else 8) else chosen
        conjunction = '和' if zh else ' and '
        linked = conjunction.join(chosen[:2])
        use = ('我曾参与'+linked+'，这段经历可用于岗位相关工作。' if zh else
               f'My work on {linked} is relevant to the responsibilities of this role.')
        if intent == 'introduction':
            use = ('我有'+linked+'的实践经历，希望把这些经验用于这个岗位。' if zh else
                   f'My background includes hands-on work in {linked}, which I would bring to this role.')
        elif intent == 'motivation' and zh:
            use = '岗位中的'+linked+'吸引我，因为我做过相关实践。'
        elif intent == 'motivation':
            use = f'I am drawn to work on {linked} because it builds on my previous experience.'
        elif intent in {'challenge','collaboration','experience'}:
            use = ('相关项目实践包括'+linked+'。' if zh else
                   f'One example from my project work is {linked}.')
        examples.append(use)
    full = '\n\n'.join(examples)
    if limit is None or statement_length(full,language) <= limit:
        return full
    if statement_length(examples[0],language) <= limit:
        return examples[0]
    short_terms = ('和' if zh else ' and ').join(evidence[0]['topics'][:2])
    short = ('我做过'+short_terms+'项目。' if zh else f'My project work included {short_terms}.')
    if statement_length(short,language) <= limit:
        return short
    for term in evidence[0]['topics']:
        brief = ('我有'+term+'项目经验。' if zh else f'My project work included {term}.')
        if statement_length(brief,language) <= limit:
            return brief
    return ('待补充。'[:limit] if zh else 'Review.')


def assemble_statement(job, data, resume, workers, settings):
    options = data.statement
    intent, question_topics, requested_facts = question_focus(options.question.strip(),options.language)
    confirmed, missing = statement_facts(data, requested_facts, options.language)
    language, role, target, evidence = select_evidence(
        job, resume, language=options.language, question_topics=question_topics, allow_transferable=True,
    )
    duties = statement_duties(job, language, role, question_topics)
    payload = draft_payload(language, role, target, evidence)
    payload.update(kind='statement', duties=duties, question_intent=intent, question_topics=question_topics,
                   limit=options.limit)
    if intent in {'manual','eligibility'}:
        narrative, reason = None, 'question_needs_input' if missing or intent == 'manual' else None
    else:
        narrative, reason = generate_narrative(job, data, resume, workers, settings, payload)
    answer = narrative.body if narrative else statement_fallback(language,intent,duties,evidence,options.limit)
    if missing and intent == 'eligibility':
        answer = ('请补充'+'、'.join(missing)+'。' if language == 'zh'
                  else 'Please confirm your '+', '.join(missing)+'.')
        if options.limit is not None and statement_length(answer, language) > options.limit:
            answer = statement_fallback(language, intent, duties, evidence, options.limit)
    if confirmed:
        facts_text = ('；' if language == 'zh' else '; ').join(confirmed)+('。' if language == 'zh' else '.')
        combined = facts_text if intent == 'eligibility' and not missing else answer+'\n\n'+facts_text
        if options.limit is None or statement_length(combined, language) <= options.limit:
            answer = combined
    if narrative:
        evidence = [item for item in evidence if item['id'] in narrative.evidence_ids]
    else:
        evidence = [item for item in evidence if any(term.casefold() in answer.casefold() for term in item['topics'])]
    return {'mode':'Workers AI 草稿' if narrative else '待补充' if reason == 'question_needs_input' else '基础草稿',
            'fallback_reason':reason, 'missing_facts':missing, 'confirmed_facts':confirmed,
            'answer':answer,'language':language,'limit':options.limit,
            'length':statement_length(answer,language),'evidence':evidence,'duties':duties}

import re
from dataclasses import dataclass
from datetime import date
from itertools import zip_longest

from .discovery_models import MatchExtraction, SearchSuggestions, job_identity, normalized, term_matches
from .policy import unsafe_instruction
from .occupations import TECH, directions, duty_roles

RULE_VERSION = 'discovery-7'
GRADES = ['夯', '顶级', '人上人', 'npc', '拉完了']
SKILLS = (
    'Python', 'FastAPI', 'Flask', 'SQL', 'RAG', 'Agent', '工具调用', '工作流', '检索', '模型 API',
    'REST API', 'Docker', 'Kubernetes', 'CUDA', 'C++', 'PyTorch', 'Transformer', 'CLIP',
    'YOLO', 'OpenCV', 'Vue', 'TypeScript', 'CSS', 'Excel', 'Figma', 'Photoshop', 'RTOS', 'OCR',
    'JavaScript', 'React', 'Node.js', 'Java', 'Go', 'C#', 'PostgreSQL', 'Redis', 'AWS', 'Linux',
)
def skill_hits(text):
    return [skill for skill in SKILLS if re.search(
        r'(?<![A-Za-z0-9_])' + re.escape(skill) + r'(?![A-Za-z0-9_])'
        if skill.isascii() else re.escape(skill), text, re.I,
    )]


def evidence_index(text):
    category = '其他'
    headings = (
        ('求职方向', r'求职(?:意向|目标|方向)|职业目标|目标岗位|target role|career objective|objective'),
        ('教育', r'教育背景|教育|学历|education|academic background'),
        ('认证', r'证书|资格证|认证|certification[s]?|licen[cs]es?'),
        ('工作', r'工作经历|职业经历|工作经验|工作|professional experience|work experience|employment'),
        ('实习', r'实习经历|实习|[^｜|：:\n]{2,25}实习|临床轮转|clinical placements?|internship[s]?'),
        ('项目', r'项目经历|项目经验|项目介绍|项目|校内项目|课程项目|(?:selected )?projects?|project experience|capstone'),
        ('技能', r'专业技能|技术栈|技能|technical skills|skills'),
    )
    project_label = re.compile(r'^项目(?:名称|名|背景|描述|概述|周期|职责|成果|总结)[：:]')
    implementation_label = re.compile(r'^(?:项目实施|实施(?:过程|步骤|内容|成果)|项目工作内容|implementation)(?:$|\s*[:：])', re.I)
    stack_label = re.compile(r'^(?:项目技术栈|技术栈|tech(?:nology)? stack)(?:$|\s*[:：])', re.I)
    evidence = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip(' \t#-*')
        if not line or re.search(r'@|\b1[3-9]\d{9}\b|联系方式|住址', line) or unsafe_instruction(line):
            continue
        heading = next(((label, pattern) for label,pattern in headings
                        if re.match(r'^(?:'+pattern+r')(?:$|\s*[:：｜|]|\s+[–—-]\s+)', line, re.I)), None)
        if heading:
            category = heading[0]
            if re.fullmatch(heading[1]+r'\s*[:：]?', line,re.I):
                continue
        if project_label.match(line) or implementation_label.match(line):
            category = '项目'
        if not heading and (re.match(r'^[^｜|：:]{2,25}(?:助理|兼职|专员|工程师)[｜|：:]', line)
                            or re.match(r'(?i)^\d{4}.{0,12}(?:现在|至今|present)[｜|：:, ]', line)):
            category = '工作'
        if len(line) >= 4:
            evidence.append({'line':number,'quote':line[:280],
                             'category':'技能' if stack_label.match(line) else category,'skills':skill_hits(line)})
    return evidence


def suggest(service, index, template_path=None):
    local = directions(index, template_path)
    options = local
    mode = 'local_rules'
    if service.settings.model_mode == 'live':
        try:
            if service.provider.simulated:
                raise ValueError('Model not configured')
            candidates = [{k:item[k] for k in ('term','line','quote','kind')} for item in local]
            prompt, _ = service.prompt('search_terms_prompt.txt', evidence=index[:30], candidates=candidates)
            response, _ = service.provider.run('extract', prompt, SearchSuggestions)
            selected = []
            selected_terms = set()
            for item in response.suggestions[:3]:
                candidate = next((row for row in local if row['term'] == item.term and row['line'] == item.line
                                  and len(item.quote.strip()) >= 4 and item.quote in row['quote']), None)
                if candidate and item.term not in selected_terms:
                    selected.append({**candidate,'quote':item.quote})
                    selected_terms.add(item.term)
            if selected:
                options = [*selected, *(item for item in local if item['term'] not in selected_terms)][:3]
                mode = 'model' if len(selected)==len(options) else 'model_with_rules'
            else:
                mode = 'local_fallback'
        except Exception:
            mode = 'local_fallback'
    return {'suggestions':options, 'mode':mode, 'needs_input':len(options)<3,
            'summary': {name:[row for row in index if row['category']==name][:8]
                        for name in ('求职方向','工作','实习','项目','技能','教育','认证')}}


def select_candidates(rows, terms, limit, *, include_unmatched=False):
    groups = []
    lookup = {}
    matched = 0
    for row in rows:
        hits = [term for term in terms if term_matches(term, row)]
        lexical = {term for term in hits if term_matches(term,row,include_tags=False)}
        tags = {normalized(tag) for tag in row.search_terms}
        retrieval = {term for term in terms if normalized(term) in tags}
        if not hits and not include_unmatched:
            continue
        matched += bool(hits)
        keys = job_identity(row)
        existing = {lookup[key] for key in keys if key in lookup}
        if existing:
            group_id = min(existing)
            group = groups[group_id]
            for other_id in existing - {group_id}:
                other = groups[other_id]
                group['hits'].update(other['hits'])
                group['lexical'].update(other['lexical'])
                group['retrieval'].update(other['retrieval'])
                for key in other['keys']:
                    lookup[key] = group_id
                group['keys'].update(other['keys'])
                if len(other['job'].jd or '') > len(group['job'].jd or ''):
                    group['job'] = other['job']
                groups[other_id] = None
            group['hits'].update(hits)
            group['lexical'].update(lexical)
            group['retrieval'].update(retrieval)
            group['keys'].update(keys)
            if len(row.jd or '') > len(group['job'].jd or ''):
                group['job'] = row
        else:
            group_id = len(groups)
            groups.append({'job':row,'hits':set(hits),'keys':set(keys),'lexical':lexical,'retrieval':retrieval})
        for key in keys:
            lookup[key] = group_id
    groups = [group for group in groups if group is not None]
    queues = [[i for i, group in enumerate(groups) if term in group['hits']] for term in terms]
    if include_unmatched:
        queues.append(list(range(len(groups))))
    selected = {}
    cursors = [0] * len(queues)
    while len(selected) < min(limit, len(groups)):
        for index, queue in enumerate(queues):
            while cursors[index] < len(queue) and queue[cursors[index]] in selected:
                cursors[index] += 1
            if cursors[index] < len(queue):
                group_id = queue[cursors[index]]
                selected[group_id] = groups[group_id]
            if len(selected) >= limit:
                break
    return list(selected.values()), matched, len(groups)


ACADEMIC = re.compile(r'\b(?:ph\.?\s*d\.?|doctorate|doctoral)\b|博士', re.I)
PREFERRED = re.compile(r'preferred|nice to have|bonus|optional|优先|加分', re.I)
NEGATED = re.compile(r'not required|not mandatory|非必需|无需|不要求', re.I)
REQUIRED = re.compile(r'must|required|requirements|only|minimum|enrolled|必需|必须|仅限|只招|要求|候选人须', re.I)


def doctoral_requirements(text):
    sentences = re.split(r'[。；;\n]+|(?<=[!?])\s+|(?<=[a-z])\.\s+(?=[A-Z])', text)
    mentions = [line.strip()[:280] for line in sentences if ACADEMIC.search(line)]
    preferred = next((line for line in mentions if PREFERRED.search(line)), None)
    required = next((line for line in mentions if REQUIRED.search(line) and not (
        PREFERRED.search(line) or NEGATED.search(line) or re.search(r'硕士|master\b|指导|导师|mentor', line, re.I)
    )), None)
    return required, preferred


def hard_conditions(job, request):
    jd = job.jd or job.jd_summary or ''
    full_time = re.search(r'仅.{0,4}全职|不接受实习|只招全职', jd) or (job.employment_type or '').casefold() in {'全职', 'full_time', 'full-time', 'full time'}
    wants_internship = getattr(request, 'job_type', 'internship') == 'internship'
    is_internship = (job.employment_type or '').casefold() in {'实习', 'intern', 'internship'} or '实习' in (job.title or '')
    # Only explicit candidate requirements count, never a mentor description.
    senior = re.search(r'(?:要求|需|至少|具备)[^。；，]{0,12}(?:[2-9]|两|三|五)\s*年[^。；，]{0,12}(?:经验|研发|经历)', jd)
    duration = re.search(r'(?:至少|不少于|最少|最低|不低于)\s*([1-9]\d?|一|二|两|三|四|五|六|七|八|九)\s*个?月', jd)
    months = None
    if duration:
        val = duration[1]
        months = int(val) if val.isascii() else '零一二三四五六七八九'.index(val.replace('两', '二'))
    start = re.search(r'(20\d\d-\d\d-\d\d)(?:前)?(?:到岗|入职)', jd)
    deadline = None
    if start:
        try:
            deadline = date.fromisoformat(start[1])
        except ValueError:
            pass
    weeks = re.search(r'每周\s*(?:(?:须|需|要求|必须)\s*)?(?:出勤|到岗)?\s*(?:至少|不少于|不低于|最少)?\s*(?:出勤|到岗)?\s*([1-7一二三四五六七])\s*天', jd)
    days = None
    if weeks and re.search(r'至少|不少于|须出勤|出勤|到岗', weeks.group()):
        days = int(weeks[1]) if weeks[1].isascii() else '零一二三四五六七'.index(weeks[1])
    credential = next((part.strip() for part in re.split(r'[。；;\n]+', jd) if re.search(
        r'(?:必须|须持|方可|持有).{0,24}(?:资格证|执业|注册证)|(?:must|required).{0,36}(?:licen[cs]e|registration)',part,re.I)
        and not re.search(r'优先|加分|preferred|optional',part,re.I)), None)
    cohort = re.search(r'(?:仅限|只招|限招|仅招)\s*(20\d{2})\s*届(?![、至/或和-])', jd)
    degree = getattr(request, 'degree', None)
    doctoral, preferred = doctoral_requirements(jd)
    education = [{'name':'学历', 'status':('符合' if degree == 'phd' else '冲突')
                  if doctoral and degree else '待确认', 'evidence':doctoral or preferred,
                  'question':'是否仅接受符合原文要求的博士申请者？'}] if doctoral else []
    return education + ([{'name':'执业资质','status':'待确认','evidence':credential,'question':'是否满足适用地区的执业资质？'}]
            if credential else []) + [
        {'name':'每周出勤', 'status':('冲突' if request.weekly_days < days else '符合')
         if days and request.weekly_days else '待确认', 'evidence':weeks.group() if weeks else None,
         'question':'每周须出勤几天？'},
        {'name':'毕业届别', 'status':('冲突' if request.graduation_year != int(cohort[1]) else '符合')
         if cohort and request.graduation_year else '待确认', 'evidence':cohort.group() if cohort else None,
         'question':'是否只接受特定届别？'},
        {'name':'岗位类型', 'status':'冲突' if full_time and wants_internship else '符合' if (is_internship or full_time) else '待确认',
         'evidence': full_time.group() if isinstance(full_time, re.Match) else job.employment_type,
         'question':'是否接受实习申请？'},
        {'name':'正式经验', 'status':'待确认',
         'evidence': senior.group() if senior else None, 'question':'是否要求正式工作年限？项目经验可以替代吗？'},
        {'name':'实习期限', 'status': ('冲突' if request.internship_months < months else '符合')
         if months and request.internship_months else '待确认',
         'evidence':duration.group() if duration else None, 'question':'最低实习时长及每周出勤要求是什么？'},
        {'name':'到岗日期', 'status': ('冲突' if request.available_from > deadline else '符合')
         if deadline and request.available_from else '待确认',
         'evidence':start.group() if deadline else None, 'question':'最迟何时到岗？'},
        {'name':'办公地点', 'status':'符合' if job.city and request.city and request.city.casefold() in job.city.casefold() else '待确认',
         'evidence':job.city, 'question':'办公地点和远程安排是否可以协商？'},
    ]


def rank_batch(jobs, *, sort=True):
    def by_rank_and_score(job):
        analysis = job['analysis'] or {}
        return analysis.get('rank', 9), -analysis.get('match_score', 0)

    counts = [0] * len(GRADES)
    for job in jobs:
        if (analysis := job['analysis']) is not None:
            rank = analysis['rank']
            job['analysis'] = {**analysis, 'evidence_rank':rank, 'evidence_grade':GRADES[rank]}
            counts[rank] += 1
    if sort:
        jobs.sort(key=by_rank_and_score)
    return [{'grade': grade, 'count': count} for grade, count in zip(GRADES, counts)]


@dataclass(frozen=True)
class MatchEvidence:
    rows: list
    attributed: list
    technical: bool


def prepare_match_evidence(index):
    attributed = [(row, duty_roles(row['quote'])) for row in index
                  if row['category'] not in {'求职方向', '技能', '教育', '认证'}]
    return MatchEvidence(index, attributed, any(roles & TECH for _, roles in attributed))


JD_HEADINGS = (
    ('background', r'公司(?:介绍|简介|背景)|团队(?:介绍|简介|背景)|关于(?:我们|公司|团队)|关于公司|about (?:us|the company|the team)|company (?:overview|background|profile)|who we are|our (?:mission|company|team)|what we offer|benefits|福利待遇'),
    ('duties', r"岗位职责|核心职责|主要职责|职责|工作内容|岗位描述|responsibilities|key responsibilities|role responsibilities|duties|what you(?:[’']ll| will) do|about the role|the role"),
    ('required', r'任职要求|岗位要求|基本要求|必备条件|招聘要求|要求|requirements|(?:minimum|required|basic) qualifications|qualifications|what you bring'),
    ('bonus', r'加分项|优先条件|优先考虑|(?:preferred|desired) qualifications|nice to have|bonus'),
)


def split_job_text(jd):
    sections = {key: [] for key in ('duties', 'required', 'bonus', 'background')}
    current = 'duties'
    parts = (part.strip() for part in re.split(r'[。；;\n]+|[.!?](?=\s)', jd) if part.strip())
    for number, part in enumerate(parts):
        if number == 120:
            break
        plain = re.sub(r'^[\s#>*\-•\d.、]+', '', part).strip('* ')
        if re.match(r'^(?:job (?:title|location|contract type|seniority level)|location type|岗位名称|工作地点)\s*[:：]', plain, re.I):
            continue
        heading, content = next(
            ((key, found[1]) for key, pattern in JD_HEADINGS
             if (found := re.fullmatch(r'(?:'+pattern+r')\s*\**(?:\s*[:：]\s*(.*))?', plain, re.I))),
            (None, None),
        )
        if heading:
            current = heading
            if content and content in part:
                sections[heading].append(content)
            continue
        if current == 'background' or re.match(
            r'^(?:我们(?:是|专注|致力|拥有|成立)|本公司|公司成立|we (?:are|were founded|design and deliver|design, test, and deploy)|'
            r'our (?:mission|leadership|company|founder|studio)|.{1,55} is (?:an? |the )(?:fast-growing|leading|global|award-winning|SBA))', plain, re.I
        ):
            key = 'background'
        elif re.search(r'加分|优先|nice to have|bonus|preferred', plain, re.I):
            key = 'bonus'
        elif re.search(r'必须|需要具备|需具备|熟练掌握|\b(?:required|must have|proficiency in)\b', plain, re.I):
            key = 'required'
        else:
            key = current
        sections[key].append(part)
    return sections


def assess(service, job, index, request, *, use_model=True):
    evidence = index if isinstance(index, MatchEvidence) else prepare_match_evidence(index)
    jd = job.jd or job.jd_summary or ''
    parts = split_job_text(jd)
    duties, required, bonus = (parts[key][:30] for key in ('duties','required','bonus'))
    extract = {'duties': duties, 'required': required, 'bonus': bonus, 'explanation': ''}
    duty_skills = set(skill_hits(' '.join(duties)))
    required_skills = set(skill_hits(' '.join(required)))
    skills = duty_skills | required_skills
    jd_roles = duty_roles(' '.join(duties))
    responsibility_lines = {row['line'] for row, roles in evidence.attributed if roles & jd_roles}
    relevant = sorted(
        (row for row in evidence.rows if row['line'] in responsibility_lines or set(row['skills']).intersection(skills)),
        key=lambda row: (row['line'] in responsibility_lines, row['category'] in ('项目', '工作', '实习'),
                         len(set(row['skills']).intersection(skills))),
        reverse=True,
    )[:6]
    display_evidence = relevant
    mode = 'local_rules'
    if use_model and service.settings.model_mode == 'live':
        try:
            if service.provider.simulated:
                raise ValueError('Model not configured')
            prompt, _ = service.prompt('discovery_match_prompt.txt', jd=jd, evidence=relevant)
            value, _ = service.provider.run('match', prompt, MatchExtraction)
            model = value.model_dump()
            for key in ('duties', 'required', 'bonus'):
                if len(model[key]) > 5 or any(not item.strip() or item not in jd for item in model[key]):
                    raise ValueError('Unverified JD quotation')
            cited = []
            for item in model['resume_evidence'][:5]:
                row = next((row for row in relevant if row['line'] == item['line']
                            and item['quote'].strip() and item['quote'] in row['quote']), None)
                if row is None:
                    raise ValueError('Unverified resume quotation')
                if row not in cited:
                    cited.append(row)
            display_evidence = cited + [row for row in relevant if row not in cited]
            extract = model
            mode = 'model_with_rules'
        except Exception:
            mode = 'local_fallback'
    conditions = hard_conditions(job, request)
    conflicts = [item for item in conditions if item['status'] == '冲突']
    non_technical = re.search(r'销售指标|催收|电话.{0,8}客户|整理发票|不需要编程|无软件开发职责|品牌海报', jd)
    technical = bool(re.search(r'开发|实现|编写|训练|调试|检索|测试|接口|部署|工具调用|\b(?:build|develop|implement|deploy|debug|test|software|API)\w*\b', jd, re.I))
    covered = {skill for row in relevant if row['category'] != '求职方向' for skill in row['skills']}
    weak_prefix = r'(?:了解|入门|初步接触|基础认识|learning|basic knowledge of|familiar with)\s*'
    practice_skills = {
        skill for row in relevant if row['category'] != '求职方向' for skill in row['skills']
        if not re.search(weak_prefix + re.escape(skill), row['quote'], re.I)
    }
    project_skills = {
        skill for row in relevant if row['category'] in ('项目', '工作', '实习') for skill in row['skills']
        if not re.search(weak_prefix + re.escape(skill), row['quote'], re.I)
    }
    needs_practice = {skill for part in required if re.search(r'熟练|实践|项目经验|proficiency|hands-on', part, re.I)
                      for skill in skill_hits(part)}

    missing = sorted((required_skills - covered) | (needs_practice - practice_skills))
    duty_coverage = len(duty_skills & practice_skills) / max(1, len(duty_skills))
    project_coverage = len(duty_skills & project_skills) / max(1, len(duty_skills))
    required_coverage = len(required_skills - set(missing)) / len(required_skills) if required_skills else .5
    score = round(45 * duty_coverage + 30 * project_coverage + 25 * required_coverage)

    if responsibility_lines and not skills:
        score = max(score, min(78, 56+7*len(responsibility_lines)))
    if conflicts or non_technical and evidence.technical and not responsibility_lines:
        grade, reason = 4, '存在明确硬条件冲突' if conflicts else '实际职责与简历的技术经历方向不符'
    elif not jd or (not responsibility_lines and (not technical or not covered & skills)):
        grade, reason = 3, '完整 JD 或简历相关证据不足，需人工核对'
    elif not job.jd or job.detail_status == 'list_only':
        grade, reason = 2, '公开摘要有相关证据，完整职责与必需条件待核对'
    elif missing or not (project_coverage or responsibility_lines) or any(row['name'] == '正式经验' and row['evidence'] for row in conditions):
        grade, reason = 2, '有相关证据，但核心职责、技能或经验仍需核对'
    elif score >= 85 and len(duty_skills & project_skills) >= 2:
        grade, reason = 0, '核心职责有多项项目证据支持，未发现明确硬条件冲突'
    elif score >= 55:
        grade, reason = 1, '部分核心职责有工作或项目证据，剩余条件需核对'
    else:
        grade, reason = 2, '部分技能相关，核心职责的项目证据有限'
    return {
        'grade': GRADES[grade], 'match_score': score, 'rank': grade, 'reason': reason, 'evaluation_mode': mode,
        'duties': extract['duties'][:5], 'required': extract['required'][:5], 'bonus': extract['bonus'][:5],
        'evidence': display_evidence, 'gaps': missing or (['现有文字未显示明确技能缺口，按 JD 人工核对'] if relevant else ['简历缺少可定位的相关经历']),
        'conditions': conditions, 'questions': [row['question'] for row in conditions if row['status']=='待确认'],
        'recommendation': ['优先复核后投递', '核对关键条件后考虑投递', '补充相关经历后选择性投递',
                           '先向 HR 核对岗位信息和证据', '优先看其他岗位'][grade],
        'explanation': extract['explanation'][:500],
    }

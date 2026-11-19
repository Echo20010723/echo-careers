"""Offline occupation directions. Templates provide context; only a resume or actual JD supplies evidence."""
import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
import re

from .discovery_models import normalized

CATALOG = Path(__file__).resolve().parents[1] / 'config/occupations.tsv'
log = logging.getLogger(__name__)
EXPERIENCE = {'工作', '项目', '实习'}
WORK = re.compile(r'(?:\d+\s*年(?!毕业)|全职|在职|full.time|professional experience|(?:19|20)\d{2}.{0,12}(?:至今|现在|present)|历任|\byears? (?:in|of experience)\b)', re.I)
ENTRY = re.compile(r'实习|internship|应届|new graduate|recent graduate|学生|毕业|expected|degree.{0,18}(?:19|20)\d{2}|\b(?:bsc|bachelor|bs nursing)\b.{0,25}(?:19|20)\d{2}', re.I)
ACTION = re.compile(r'负责|使用|开发|维护|交付|部署|制作|跟进|处理|开展|主办|完成|改进|协调|参与|核对|设计|实现|建立|审阅|经办|管理|跟踪|实习|项目|work|project|managed|built|develop|deploy|support|coordinat|deliver|maintain|conduct|prepar|led|experience|implement|install|test|scheduled|practi[cs]|rotat|collect|assess|check|organis|organiz|reconcil|train|triag|track|edit|resolved|design|produc', re.I)
INTEREST = re.compile(r'^(?:仅?了解|学习中|兴趣|familiar with|interested in)', re.I)
CREDENTIALS = {'nursing':'核对执业资格及注册地', 'teacher':'核对教师资格与任教学段',
               'lab':'核对检验资质与适用地区', 'pharma':'核对药师注册与执业资格'}
TECH = frozenset({'agent','llm','backend','frontend','qa','vision','nlp','linux','cloud','containers','devops','helpdesk','network'})
TRANSFER = {'linux','cloud','containers','devops','data','bi'}


def _phrases(value):
    return tuple(dict.fromkeys(piece.strip() for piece in value.split(',') if piece.strip()))


@lru_cache(maxsize=2048)
def _pattern(value):
    escaped = re.escape(value.replace(' ', '').casefold()) if not value.isascii() else re.escape(value.casefold())
    return re.compile(r'(?<![a-z0-9])'+escaped+r'(?![a-z0-9])' if value.isascii() else escaped)


def phrase_in(value, text):
    if value.isascii():
        return bool(_pattern(value).search(text.casefold()))
    return bool(_pattern(value).search(normalized(text)))


@lru_cache(maxsize=1)
def catalog():
    roles = []
    for row in CATALOG.read_text(encoding='utf-8-sig').splitlines():
        if not row or row.startswith('#'):
            continue
        key, family, zh, en, aliases, signs = row.split('|')
        roles.append({'id':key, 'family':family, 'title':zh, 'query':en,
                      'aliases':(zh,en,*_phrases(aliases)), 'signals':_phrases(signs)})
    return tuple(roles)


def version(path=None):
    signature = hashlib.sha256(CATALOG.read_bytes())
    if path and path.is_file():
        stat = path.stat()
        signature.update(f'{path}:{stat.st_mtime_ns}:{stat.st_size}'.encode())
    return signature.hexdigest()[:18]


@lru_cache(maxsize=2)
def _templates(path, revision):
    rows = []
    try:
        with path.open(encoding='utf-8-sig') as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if row.get('id') and row.get('role_title') and isinstance(row.get('duties'), list):
                        rows.append(row)
                except (ValueError, AttributeError, TypeError):
                    continue
    except OSError:
        log.warning('Optional occupation template file could not be read.')
    return tuple(rows)


def templates(path):
    return _templates(path, version(path)) if path and path.is_file() else ()


def _hits(role, text):
    return sum(phrase_in(phrase, text) for phrase in role['signals'])


def duty_roles(text):
    """Use actual responsibilities only; technical tools without a delivery signal do not define a role."""
    output = set()
    for role in catalog():
        hits = _hits(role, text)
        if hits and (role['id'] not in {'agent','llm','backend'} or role_evidence(role, text)):
            output.add(role['id'])
    return output


def role_evidence(role, text):
    key = role['id']
    if key == 'backend':
        return bool(re.search(r'开发接口|后端项目|后端服务|服务端|(?:fastapi|flask).{0,28}(?:api|backend|开发|接口|服务)|(?:develop|build|built|implement).{0,40}(?:api|backend)', text,re.I))
    if key == 'llm':
        return bool(re.search(r'\brag\b|文档问答|向量检索|大模型应用|模型\s*api|引用溯源', text,re.I)
                    and not re.search(r'辅助运维|仅用于运维|assist.*operations',text,re.I))
    if key == 'agent':
        return bool(re.search(r'\bagent\b|智能体|工具调用',text,re.I)
                    and re.search(r'开发|实现|编排|工作流|tooling|workflow|具备|experience|project',text,re.I))
    return bool(_hits(role,text))


def matched_query(term):
    key = normalized(term)
    for role in catalog():
        if any(normalized(alias) == key for alias in role['aliases']):
            return role['query']
    return None


def _references(role, rows, evidence):
    aliases = {normalized(alias) for alias in role['aliases'] if not alias.isascii()}
    keywords = {word for word in role['signals'] if phrase_in(word, evidence)}
    found = []
    for item in rows:
        title = normalized(item['role_title'])
        if title not in aliases:
            continue
        duties = ' '.join(str(piece) for piece in item['duties'][:10])
        retrieval = str(item.get('retrieval_text') or '')
        weight = sum(2*phrase_in(word, duties) + phrase_in(word, retrieval) for word in keywords)
        found.append((weight, item))
    return [_template_ref(item) for _, item in sorted(found,key=lambda entry:-entry[0])[:3]]


def _template_ref(item):
    source = item.get('source')
    return {'id':item['id'], 'title':item['role_title'], 'category':item.get('category'),
            'document':item.get('document'),
            'archive_member':source.get('archive_member') if isinstance(source, dict) else None}


def directions(index, path=None):
    context = '\n'.join(row['quote'] for row in index)
    experienced = bool(WORK.search(context))
    entry = not experienced and bool(ENTRY.search(context))
    intent = ' '.join(row['quote'] for row in index if row['category']=='求职方向')
    english = len(re.findall(r'[A-Za-z]',context)) > 3*len(re.findall(r'[\u4e00-\u9fff]',context))
    found = []
    for position, role in enumerate(catalog()):
        key = role['id']
        intent_match = any(phrase_in(alias,intent) for alias in role['aliases'])
        scores = []
        for row in index:
            quote = row['quote']
            if row['category'] in {'求职方向','教育','认证','技能'} or INTEREST.search(quote):
                continue
            work = row['category'] in EXPERIENCE or ACTION.search(quote)
            if work and role_evidence(role, quote):
                hits = _hits(role, quote) or 1
                scores.append((hits*3+(2 if row['category'] in EXPERIENCE else 0),row))
        if scores:
            weight, best = max(scores,key=lambda item:item[0])
            kind = '直接匹配'
            weight += min(len(scores),4) + (7 if intent_match else 0)
        elif intent_match:
            best = next(row for row in index if row['category']=='求职方向'
                        and any(phrase_in(alias,row['quote']) for alias in role['aliases']))
            kind, weight = '转行探索', 24
        else:
            continue
        en = role['query']
        term = en if english else role['title']
        if entry and key in TECH and len(term) < 32:
            if not english:
                term = term.removesuffix('工程师')+'实习'
            else:
                term = term.removesuffix(' Developer').removesuffix(' Engineer')+' Intern'
        needs = [CREDENTIALS[key]] if key in CREDENTIALS else []
        if kind == '转行探索':
            needs += ['补充目标职责的实际交付证据']
            if key in {'agent','llm','backend'} and duty_roles(context) & TRANSFER:
                needs += ['可迁移经验：部署、排障或自动化；核对应用开发项目']
        found.append((-weight, position, role, best, kind, term, en, needs))

    rows = templates(path)
    if not found:
        # Long-tail exact role recognition. Do not infer stronger seniority from a title substring.
        by_title = {}
        for template in rows:
            title = template['role_title']
            by_title.setdefault(title, []).append(template)
        for title in sorted(by_title, key=len, reverse=True):
            if not 2 <= len(title) <= 48 or not re.fullmatch(r'[\w +#.-]+',title):
                continue
            for row in index:
                if row['category'] in EXPERIENCE and phrase_in(title,row['quote']):
                    refs = by_title[title][:3]
                    return [{'role_id':refs[0]['id'],'direction':title,'kind':'相邻方向','term':title,
                             'en_query':None,'line':row['line'],'quote':row['quote'],'needs':['核对实际职责与任职范围'],
                             'references':[_template_ref(ref) for ref in refs]}]
        if intent:
            for row in index:
                if row['category'] != '求职方向':
                    continue
                title = re.sub(r'^(?:求职(?:意向|目标|方向)|职业目标|目标岗位|target role|objective)\s*[:：]?\s*','',row['quote'],flags=re.I).strip()
                if 2 <= len(title) <= 45 and re.fullmatch(r'[\w +#.-]+',title) and (
                        re.search(r'工程师|专员|分析师|助理|顾问|技师|协调员|coordinator|engineer|analyst|specialist',title,re.I)):
                    return [{'role_id':None,'direction':title,'kind':'暂定方向','term':title,'en_query':None,
                             'line':row['line'],'quote':row['quote'],'needs':['补充目标岗位实际职责与交付证据'],'references':[]}]
    return [{'role_id':role['id'], 'direction':role['title'], 'kind':kind, 'term':term,
             'en_query':en, 'line':best['line'], 'quote':best['quote'],
             'needs':needs, 'references':_references(role, rows, best['quote'])}
            for _,_,role,best,kind,term,en,needs in sorted(found)[:3]]

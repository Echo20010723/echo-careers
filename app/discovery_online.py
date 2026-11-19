"""Read-only adapters for the public Himalayas jobs API."""
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx
from pydantic import ValidationError

from .discovery_models import JobCandidate
from .errors import AppError
from .occupations import matched_query

HIMALAYAS_URL = 'https://himalayas.app/jobs/api/search'
MAX_PAGES = 10
PLATFORM_NAMES = {'himalayas':'Himalayas'}


@dataclass(frozen=True)
class SearchPage:
    jobs: list[JobCandidate]
    more: bool
    fetched: int
    warning: str | None = None
    query: str | None = None


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}:
            self.ignored += 1
        if tag in {'p','br','li','div','ul','ol','h1','h2','h3','h4'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script','style'}:
            self.ignored = max(0,self.ignored-1)
        if tag in {'p','li','div','ul','ol','h1','h2','h3','h4'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def plain(value, limit=18000):
    if not isinstance(value, str):
        return None
    parser = TextParser()
    parser.feed(html.unescape(html.unescape(value)))
    parser.close()
    lines = (' '.join(line.split()) for line in ''.join(parser.parts).splitlines())
    return '\n'.join(line for line in lines if line)[:limit] or None


def identifier(value, limit=180):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()[:limit] or None
    return None


def job(**fields):
    try:
        candidate = JobCandidate(**fields)
    except ValidationError:
        return None
    return candidate if candidate.title and (candidate.source_id or candidate.url) else None


def read_api(url, params, *, timeout=14, **options):
    try:
        response = httpx.get(url, params=params, timeout=timeout, **options)
    except httpx.RequestError:
        raise AppError('ONLINE_SEARCH_NETWORK') from None
    if response.status_code in {401,403}:
        raise AppError('ONLINE_SEARCH_AUTH')
    if response.status_code == 429:
        raise AppError('ONLINE_SEARCH_LIMIT')
    if response.status_code == 404:
        raise AppError('ONLINE_SOURCE_NOT_FOUND')
    if not response.is_success:
        raise AppError('ONLINE_SEARCH_PROVIDER')
    try:
        data = response.json()
    except ValueError:
        raise AppError('ONLINE_SEARCH_PROVIDER') from None
    if not isinstance(data, dict):
        raise AppError('ONLINE_SEARCH_PROVIDER')
    return data


def rows_in(data):
    rows = data.get('jobs')
    if not isinstance(rows, list):
        raise AppError('ONLINE_SEARCH_PROVIDER')
    return rows


def remote_query(term):
    # Preserve the final user-entered keyword as provenance and show the API query separately.
    if not re.search(r'[\u4e00-\u9fff]', term):
        return term.strip()
    text = re.sub(r'实习生?|校招|应届', '', term, flags=re.I)
    legacy = {'Agent 开发':'Agent developer', '大模型应用':'LLM', 'Python 后端':'Python backend'}
    if text != term and text.strip() in legacy:
        return legacy[text.strip()]
    exact = matched_query(text)
    if exact:
        return exact
    for original, translated in (
        ('云平台运维','cloud operations'), ('容器运维','Kubernetes operations'),
        ('自动化运维','DevOps'), ('系统运维','systems operations'), ('运维','operations'),
        ('分析师',' analyst '), ('专员',' specialist '), ('人工智能','AI'), ('智能体','AI agent'), ('大模型应用','LLM'),
        ('大模型','LLM'), ('后端','backend'), ('前端','frontend'),
        ('全栈','full stack'), ('计算机视觉','computer vision'),
        ('机器学习','machine learning'), ('算法','algorithm'),
        ('测试','QA'), ('产品经理','product manager'), ('开发',' developer '),
        ('研发',' engineering '), ('工程师',' engineer '), ('应用',' application '),
    ):
        text = text.replace(original, f' {translated} ')
    return ' '.join(text.split()) or term.strip()


def salary_range(row):
    lo, hi = row.get('minSalary'), row.get('maxSalary')
    numbers = [str(n) for n in (lo,hi) if isinstance(n,(int,float)) and not isinstance(n,bool)]
    if not numbers:
        return None
    return ' '.join(part for part in (
        plain(row.get('currency'),20), '–'.join(dict.fromkeys(numbers)), plain(row.get('salaryPeriod'),20),
    ) if part)


class Himalayas:
    ready = True

    def page(self, term, request, offset, context):
        query = remote_query(term)
        params = {'q':query,'page':offset+1}
        if request.country:
            params['country'] = request.country
        if request.job_type == 'internship':
            params['employment_type'] = 'Intern'
        rows = rows_in(read_api(HIMALAYAS_URL, params, timeout=context.get('timeout', 14)))
        candidates = []
        for row in rows:
            if not isinstance(row,dict):
                continue
            restrictions = [plain(v,100) for v in row.get('locationRestrictions') or []] if isinstance(row.get('locationRestrictions'),list) else []
            timezones = row.get('timezoneRestrictions')
            jd = plain(row.get('description'))
            candidate = job(
                source='himalayas', source_id=identifier(row.get('guid')),
                title=plain(row.get('title'),300), company=plain(row.get('companyName'),300),
                url=row.get('guid'), application_url=row.get('applicationLink'),
                salary_raw=salary_range(row), employment_type=plain(row.get('employmentType'),100),
                jd=jd, jd_summary=plain(row.get('excerpt')), search_terms=[term],
                detail_status='detail' if jd else 'list_only', retrieval_source='himalayas_api',
                remote=True, location_restrictions=[x for x in restrictions if x][:100],
                timezone_restrictions=[str(x)[:30] for x in timezones if isinstance(x,(str,int,float))][:100] if isinstance(timezones,list) else [],
            )
            if candidate and (request.job_type != 'internship' or candidate.employment_type == 'Intern'):
                candidates.append(candidate)
        return SearchPage(candidates,bool(rows) and offset+1<MAX_PAGES,len(rows),query=query)

import re
import json
import unicodedata
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .applications import job_link


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()


def clean_terms(values):
    return list({normalized(term): term.strip() for term in values if term.strip()}.values())


class SearchEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    term: str
    line: int
    quote: str


class SearchSuggestions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suggestions: list[SearchEvidence]


class EvidenceExcerpt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    line: int
    quote: str


class MatchExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    duties: list[str]
    required: list[str]
    bonus: list[str]
    explanation: str
    resume_evidence: list[EvidenceExcerpt]


class JobCandidate(BaseModel):
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)
    source: str | None = Field(None, max_length=100)
    source_id: str | None = Field(None, max_length=180)
    title: str | None = Field(None, max_length=300)
    company: str | None = Field(None, max_length=300)
    city: str | None = Field(None, max_length=300)
    salary_raw: str | None = Field(None, max_length=300)
    application_url: str | None = Field(None, max_length=1500)
    remote: bool | None = None
    location_restrictions: list[str] = Field(default_factory=list, max_length=100)
    timezone_restrictions: list[str] = Field(default_factory=list, max_length=100)
    source_page_url: str | None = Field(None, max_length=1500)
    url: str | None = Field(None, max_length=1500)
    jd: str | None = Field(None, max_length=18000)
    jd_summary: str | None = Field(None, max_length=18000)
    detail_status: Literal['list_only', 'detail'] | None = None
    retrieval_source: str | None = Field(None, max_length=100)
    employment_type: str | None = Field(None, max_length=100)
    search_terms: list[str] = Field(default_factory=list, max_length=50)
    imported_at: str | None = Field(None, max_length=60)

    @model_validator(mode='before')
    @classmethod
    def aliases(cls, value):
        if not isinstance(value, dict):
            return value
        fields = {
            'source_id': ('platform_id', 'job_id', 'id'), 'city': ('location',),
            'salary_raw': ('salary',), 'url': ('source_url', 'link'),
            'jd': ('raw_jd', 'description'), 'search_terms': ('matched_terms',),
        }
        result = dict(value)
        if 'job_title' in result:
            source = result.get('source')
            result.update(source='imported_summary', title=result.get('job_title'), company=result.get('employer'),
                          jd_summary=result.get('jd_text'), detail_status='list_only',
                          url=source.get('url') if isinstance(source, dict) else result.get('url'))
        for key, aliases in fields.items():
            if result.get(key) is None:
                result[key] = next((value[k] for k in aliases if value.get(k) is not None), None)
                if key == 'search_terms' and result[key] is None:
                    result[key] = []
        return result

    @field_validator('url', 'source_page_url', 'application_url')
    @classmethod
    def normalize_url(cls, value):
        return job_link(value)


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resume_id: str = Field(min_length=1, max_length=100)
    platform: Literal['himalayas','greenhouse','careerjet','boss'] = 'himalayas'
    mode: Literal['online', 'demo', 'import', 'paste'] = 'online'
    limit: int = Field(50, ge=1, le=100, strict=True)
    terms: list[str] = Field(max_length=100)
    jobs: list[JobCandidate] = Field(default_factory=list, max_length=1000)
    city: str | None = Field(None, max_length=40)
    country: str | None = Field(None, pattern=r'^[A-Z]{2}$')
    job_type: Literal['internship','any'] = 'any'
    greenhouse_boards: list[str] = Field(default_factory=lambda:['cloudflare'], max_length=5)

    @field_validator('greenhouse_boards')
    @classmethod
    def board_tokens(cls, tokens):
        if not tokens or any(not re.fullmatch(r'[a-z0-9_-]{1,80}',token) for token in tokens):
            raise ValueError('企业招聘板需填写 1–5 个有效标识')
        return list(dict.fromkeys(tokens))
    internship_months: int | None = Field(None, ge=1, le=36)
    available_from: date | None = None
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    weekly_days: int | None = Field(None, ge=1, le=7)

    @field_validator('terms')
    @classmethod
    def validate_terms(cls, values):
        values = clean_terms(values)
        if len(values) > 5 or any(not 2 <= len(v) <= 48 or re.search(r'[\n\r。！？；;]', v) for v in values):
            raise ValueError('请输入 1–5 个不同的短职位词，每个 2–48 字')
        return values

    @classmethod
    def saved(cls, record):
        value = json.loads(record)
        value.setdefault('job_type', 'internship')
        return cls.model_validate(value)

    @model_validator(mode='after')
    def validate_mode(self):
        if self.mode == 'paste':
            if len(self.jobs) != 1 or not self.jobs[0].jd:
                raise ValueError('请粘贴一份岗位正文')
            self.limit = 1
            return self
        if not self.terms:
            raise ValueError('请填写至少一个职位词')
        if self.mode == 'online' and any(not re.fullmatch(r'[\w +#.-]+', value) for value in [*self.terms, self.city or '城市']):
            raise ValueError('在线搜索词和城市只能填写地点、职位及技能名称')
        if (self.mode == 'import') != bool(self.jobs):
            raise ValueError('导入模式需要岗位文件，演示模式使用内置脱敏数据')
        return self


class ReanalysisRequest(BaseModel):
    resume_id: str = Field(min_length=1, max_length=100)


class PasteResume(BaseModel):
    text: str = Field(min_length=1, max_length=40000)


class LocalSource:
    """Local imports and synthetic data share the same discovery pipeline."""

    def __init__(self, demo_path):
        self.demo_path = demo_path

    def fetch(self, request):
        if request.mode in {'import','paste'}:
            return request.jobs
        import json
        return [JobCandidate.model_validate(row) for row in json.loads(self.demo_path.read_text(encoding='utf-8-sig'))]


def job_identity(job):
    origin = normalized(job.source or 'boss')
    keys = []
    if job.source_id:
        keys.append(('id', origin, job.source_id))
    if job.url and urlsplit(job.url).path not in {'', '/', '/web/geek/jobs'}:
        keys.append(('url', job.url))
    if not keys and all((job.company, job.title, job.city)):
        keys.append(('text', *(normalized(value) for value in (job.company, job.title, job.city))))
    return keys


def term_matches(term, job, *, include_tags=True):
    def key(text):
        text = normalized(text)
        return text.replace('大模型应用开发', '大模型应用').replace('实习生', '实习')
    needle = key(term)
    if include_tags and any(key(tag) == needle for tag in job.search_terms):
        return True
    return needle in key(job.title or '') or needle in key(job.jd or job.jd_summary or '')

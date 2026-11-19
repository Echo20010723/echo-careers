from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class JobRequirement(Strict):
    job_title: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    @field_validator('required_skills','preferred_skills','responsibilities')
    @classmethod
    def clean(cls,v): return list(dict.fromkeys(x.strip() for x in v if x.strip()))
class Evidence(Strict):
    requirement: str
    status: Literal['demonstrated','partial','not_evidenced']
    credit: float
    evidence: str | None
    reason: str
class Eligibility(Strict):
    condition: str
    status: Literal['met','conflict','unknown']
    evidence: str | None
    reason: str
class MatchAnalysis(Strict):
    skill_evidence: list[Evidence]
    project_evidence: list[Evidence]
    preferred_skill_notes: list[str]
    eligibility_checks: list[Eligibility]
    suggested_highlights: list[str]
    questions_to_confirm: list[str]
class JobInput(BaseModel):
    model_config=ConfigDict(extra='ignore')
    id: str | None = Field(None,max_length=180)
    title: str | None = Field(None,max_length=300)
    company: str | None = Field(None,max_length=300)
    location: str | None = Field(None,max_length=300)
    salary: str | None = Field(None,max_length=300)
    education: str | None = Field(None,max_length=300)
    experience: str | None = Field(None,max_length=300)
    employment_type: str | None = Field(None,max_length=300)
    work_mode: str | None = Field(None,max_length=300)
    raw_jd: str = Field(min_length=1,max_length=18000)

    @field_validator('raw_jd')
    @classmethod
    def nonempty_description(cls, value):
        if not value.strip():
            raise ValueError('empty description')
        return value.strip()

    @model_validator(mode='before')
    @classmethod
    def import_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for field, aliases in {
            'raw_jd': ('jd', 'description'), 'location': ('city',),
            'source_url': ('url', 'link'),
        }.items():
            if field not in value:
                for alias in aliases:
                    if value.get(alias):
                        value[field] = value[alias]
                        break
        return value

    source: str = Field('manual',max_length=100)
    source_url: str | None = Field(None,max_length=1500)
class JobBatch(Strict):
    jobs: list[JobInput] = Field(min_length=1,max_length=100)
    @field_validator('jobs',mode='before')
    @classmethod
    def text_inputs(cls,value):
        return [{'raw_jd':x} if isinstance(x,str) else x for x in value] if isinstance(value,list) else value

class ConversationInput(Strict):
    job_id: str
    recruiter: str = Field(min_length=1,max_length=100)
    channel: Literal['mock','boss'] = 'mock'
    account: str = Field('local',min_length=1,max_length=100)
class MessageInput(Strict):
    platform_id: str | None = Field(None,max_length=200)
    text: str = Field(min_length=1,max_length=4000)
    observed_at: str | None = None
class ModeInput(Strict):
    mode: Literal['draft','auto']
    authorized: bool = False
    channel: Literal['mock','boss'] = 'mock'
class TaskInput(Strict):
    action: Literal['start','pause','resume']
class Preferences(Strict):
    cities: list[str] = Field(min_length=1,max_length=30)
    keywords: list[str] = Field(min_length=1,max_length=30)
    remote_acceptable: bool = True
    salary_daily: tuple[int,int] = (100,600)
    salary_monthly: tuple[int,int] = (3000,8000)
    @field_validator('salary_daily','salary_monthly')
    @classmethod
    def bounds(cls,v):
        if v[0]<0 or v[0]>v[1]: raise ValueError('invalid salary range')
        return v

class AssistantStartInput(Strict):
    channel: Literal['boss','mock'] = 'boss'

class BrowserPairInput(Strict):
    code: str = Field(pattern=r'^\d{6}$')
class BrowserImportInput(Strict):
    job: JobInput

class BossCandidate(Strict):
    href: str = Field(max_length=3000)
    title: str = Field('',max_length=200)
class BossCard(Strict):
    key: str = Field(max_length=1200)
    title: str = Field('',max_length=300)
    active: bool = False
class BossPageSnapshot(BaseModel):
    model_config=ConfigDict(extra='ignore')
    authenticated: bool = False
    safety: bool = False
    loading: bool = False
    job: JobInput | None = None
    job_links: list[BossCandidate] = Field(default_factory=list,max_length=12)
    cards: list[BossCard] = Field(default_factory=list,max_length=12)
    empty: bool = False
    search_available: bool = False
    navigate_jobs: bool = False
    view: Literal['home','jobs','job_detail','chat'] = 'home'
    diagnostic: dict | None = None
class BossNativeEvent(Strict):
    kind: Literal['hello','snapshot','ack','browser']
    pages: list[str] = Field(default_factory=list,max_length=30)
    snapshot: BossPageSnapshot | None = None
    command_id: str | None = Field(None,max_length=50)
    success: bool = False
    error: str | None = Field(None,max_length=80)

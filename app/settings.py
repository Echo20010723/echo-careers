from pathlib import Path
import os, yaml
from pydantic import BaseModel, Field, SecretStr, model_validator

ROOT = Path(__file__).resolve().parents[1]
class Settings(BaseModel):
    root: Path = ROOT
    data_dir: Path = ROOT / 'data'
    llm_provider: str = 'openai'
    model_mode: str = 'mock'
    vector_backend: str = 'memory'
    boss_browser: str = 'chrome'
    boss_cdp_enabled: bool = False
    managed_boss_enabled: bool = False
    boss_auto_search: bool = True
    boss_auto_contact: bool = False
    boss_platform_authorized: bool = False
    boss_search_query: str = 'AI实习'
    boss_batch_size: int = Field(20,ge=1,le=50)
    use_demo_resume: bool = False
    public_mode: bool = False
    occupation_templates: Path | None = None
    public_host: str = ''
    offerdao_base: str = 'https://offerdao.ai'
    offerdao_api_key: SecretStr | None = None
    cloudflare_account_id: str = ''
    cloudflare_api_token: SecretStr | None = None
    cloudflare_agent_enabled: bool | None = None
    cloudflare_model: str = '@cf/qwen/qwen3.8-27b'
    cloudflare_timeout: float = Field(16, ge=1, le=20)
    cloudflare_daily_drafts: int = Field(100, ge=1, le=200)
    cloudflare_visitor_hourly_drafts: int = Field(12, ge=1, le=100)
    cloudflare_local_hourly_drafts: int = Field(30, ge=1, le=200)
    public_sessions: int = Field(32, ge=2, le=256)
    agent_searches: int = Field(3, ge=1, le=5)
    agent_reads: int = Field(10, ge=1, le=25)
    agent_calls: int = Field(18, ge=1, le=40)
    agent_seconds: float = Field(90, gt=0, le=300)
    model_agent: str = 'gpt-5.4-mini'
    default_mode: str = 'auto'
    automatic_pipeline: bool = True
    model_extract: str = 'gpt-5.4-mini'
    model_match: str = 'gpt-5.4-mini'
    model_reply: str = 'gpt-5.4-mini'
    embedding_model: str = 'text-embedding-3-small'
    embedding_dimensions: int = 1536
    timeout: float = 45
    retries: int = Field(1, ge=0, le=3)
    max_calls: int = Field(100, ge=1)
    max_output_tokens: int = Field(4096, ge=128)
    max_input_chars: int = Field(60000, ge=1000)
    reasoning_effort: str | None = None
    weights: dict[str,float] = {'skill':.6,'project':.3,'intention':.1}
    @model_validator(mode='after')
    def validate_config(self):
        import math
        if set(self.weights) != {'skill','project','intention'} or any(not math.isfinite(v) or v<0 for v in self.weights.values()) or abs(sum(self.weights.values())-1)>1e-6:
            raise ValueError('INVALID_WEIGHTS')
        if self.boss_browser not in {'chrome','msedge'}: raise ValueError('Unsupported BOSS browser')
        if self.default_mode not in {'draft','auto'}: raise ValueError('Unsupported default mode')
        if self.llm_provider != 'openai' or self.model_mode not in {'mock','live'} or self.vector_backend not in {'memory','chroma'}:
            raise ValueError('Unsupported provider/mode/backend')
        return self
    @classmethod
    def load(cls):
        cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
        mapping={'BOSS_BROWSER':'boss_browser','MODEL_MODE':'model_mode','VECTOR_BACKEND':'vector_backend','LLM_PROVIDER':'llm_provider','LLM_MODEL_EXTRACT':'model_extract','LLM_MODEL_MATCH':'model_match','LLM_MODEL_REPLY':'model_reply','EMBEDDING_MODEL':'embedding_model','EMBEDDING_DIMENSIONS':'embedding_dimensions','MODEL_TIMEOUT':'timeout','MODEL_MAX_CALLS':'max_calls','REASONING_EFFORT':'reasoning_effort'}
        for env,key in mapping.items():
            if os.getenv(env): cfg[key]=os.environ[env]
        for env, key in {'PUBLIC_MODE':'public_mode','PUBLIC_MAX_SESSIONS':'public_sessions', 'AGENT_MAX_SEARCHES':'agent_searches',
                         'AGENT_MAX_READS':'agent_reads','AGENT_MAX_CALLS':'agent_calls','AGENT_TIMEOUT':'agent_seconds',
                         'LLM_MODEL_AGENT':'model_agent'}.items():
            if os.getenv(env): cfg[key] = os.environ[env]
        cfg['public_host'] = os.getenv('PUBLIC_HOST') or os.getenv('RENDER_EXTERNAL_HOSTNAME', '')
        if os.getenv('OCCUPATION_TEMPLATES'):
            cfg['occupation_templates'] = Path(os.environ['OCCUPATION_TEMPLATES'])
        cfg['offerdao_base'] = os.getenv('OFFERDAO_BASE') or 'https://offerdao.ai'
        cfg['offerdao_api_key'] = os.getenv('OFFERDAO_API_KEY') or None
        for env, key in {
            'CLOUDFLARE_ACCOUNT_ID':'cloudflare_account_id',
            'CLOUDFLARE_API_TOKEN':'cloudflare_api_token',
            'CLOUDFLARE_MODEL':'cloudflare_model',
            'CLOUDFLARE_AGENT_ENABLED':'cloudflare_agent_enabled',
        }.items():
            if os.getenv(env):
                cfg[key] = os.environ[env]
        for env, key, convert, lower, upper in (
            ('CLOUDFLARE_TIMEOUT','cloudflare_timeout',float,1,20),
            ('CLOUDFLARE_DAILY_DRAFTS','cloudflare_daily_drafts',int,1,200),
            ('CLOUDFLARE_VISITOR_HOURLY_DRAFTS','cloudflare_visitor_hourly_drafts',int,1,100),
            ('CLOUDFLARE_LOCAL_HOURLY_DRAFTS','cloudflare_local_hourly_drafts',int,1,200),
        ):
            if raw := os.getenv(env):
                try:
                    candidate = convert(raw)
                    if lower <= candidate <= upper:
                        cfg[key] = candidate
                except ValueError:
                    pass
        if os.getenv('APP_DATA_DIR'): cfg['data_dir']=Path(os.environ['APP_DATA_DIR'])
        return cls(**cfg)

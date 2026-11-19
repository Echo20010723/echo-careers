import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from .store import now

REQUEST_ID = ContextVar("request_id", default="local")


@contextmanager
def measure(audit, stage, request_id=None, **fields):
    if audit is None:
        yield
        return
    started = time.perf_counter()
    status = 'ok'
    try:
        yield
    except Exception:
        status = 'error'
        raise
    finally:
        audit.event('performance_stage', request_id or REQUEST_ID.get(), stage=stage, status=status,
                    duration_ms=round((time.perf_counter()-started)*1000, 2), **fields)

class Audit:
    def __init__(self,root,data):
        cfg=json.loads((root/'config/logging_rules.json').read_text(encoding='utf-8'))
        self.allowed=set(cfg['required_fields']+cfg['optional_fields'])
        self.directory=data/'logs'; self.directory.mkdir(parents=True,exist_ok=True)
        limit=time.time()-cfg.get('retention_days',14)*86400
        for p in self.directory.glob('audit-*.jsonl'):
            if p.stat().st_mtime<limit: p.unlink()
    def event(self,event,request_id,**kwargs):
        record={'timestamp':now(),'level':'INFO','event':event,'request_id':request_id,'stage':'http','duration_ms':0,'status':'ok'}
        record.update({k:v for k,v in kwargs.items() if k in self.allowed})
        p=self.directory/('audit-'+datetime.now(timezone.utc).date().isoformat()+'.jsonl')
        with p.open('a',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')

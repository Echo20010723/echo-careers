import sqlite3,json,threading
from contextlib import contextmanager
from datetime import datetime,timezone
from uuid import uuid4

def uid(): return uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def dumps(v): return json.dumps(v,ensure_ascii=False)
class Store:
    def __init__(self,path,recover=True):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        self.lock=threading.RLock()
        with self.tx() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resumes(id TEXT PRIMARY KEY,name TEXT,version TEXT,text TEXT,chunks TEXT,path TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,source_key TEXT UNIQUE,payload TEXT,analysis TEXT);
            CREATE TABLE IF NOT EXISTS applications(
                job_id TEXT PRIMARY KEY REFERENCES jobs(id),
                status TEXT NOT NULL, due_on TEXT, note TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,job_id TEXT,recruiter TEXT,channel TEXT,account TEXT,state TEXT,resume_id TEXT,created TEXT,UNIQUE(job_id,recruiter,channel,account,resume_id));
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,conversation_id TEXT,dedupe_key TEXT,text TEXT,observed TEXT,status TEXT,UNIQUE(conversation_id,dedupe_key));
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,conversation_id TEXT,message_id TEXT UNIQUE,resume_id TEXT,version TEXT,kind TEXT,text TEXT,status TEXT,result TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,conversation_id TEXT,reason TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS model_calls(id TEXT PRIMARY KEY,stage TEXT,created TEXT);
            ''')
            versions=[r[0] for r in db.execute('SELECT version FROM schema_version')]
            if versions != [1]: raise RuntimeError('Unsupported database schema; back up and use a compatible version')
            # A crash after click cannot safely be retried. Preserve uncertainty.
            if recover:
                db.execute("UPDATE outbox SET status='unknown' WHERE status='sending'")
                db.execute("UPDATE messages SET status='failed' WHERE status='processing'")
            for k,v in {'mode':'draft','task':'paused','authorized_channel':None}.items():
                db.execute('INSERT OR IGNORE INTO state VALUES(?,?)',(k,dumps(v)))
    @contextmanager
    def tx(self):
        with self.lock:
            db=sqlite3.connect(self.path,timeout=20)
            db.row_factory=sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON'); db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback(); raise
            finally: db.close()
    def all(self,sql,args=()):
        with self.tx() as db: return [dict(r) for r in db.execute(sql,args).fetchall()]
    def one(self,sql,args=()):
        rows=self.all(sql,args); return rows[0] if rows else None
    def get(self,key,default=None):
        row=self.one('SELECT value FROM state WHERE key=?',(key,))
        return json.loads(row['value']) if row else default
    def set(self,key,value):
        with self.tx() as db: db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,dumps(value)))

"""Deterministic persisted orchestration: import -> match -> screen -> greeting.

Real BOSS drafts stay local until the adapter and send permission are verified.
"""
import json
import threading
from .errors import AppError
from .resumes import digest
from .schemas import ConversationInput
from .store import now
from .boss_outreach import inquiry_candidate,is_demo_payload

class Automation:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.lock = threading.Lock()
        with self.store.tx() as db:
            rows = db.execute("SELECT key,value FROM state WHERE key LIKE 'pipeline:%'").fetchall()
            for row in rows:
                value = json.loads(row['value'])
                if value['status'] == 'processing':
                    value['status'] = 'pending'
                    db.execute('UPDATE state SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), row['key']))

    def signature(self):
        resume = self.service.resumes.current()
        return digest('initial-inquiry-v2:' + resume['id'] + resume['version'] + json.dumps(self.store.get('preferences'), sort_keys=True))

    def enroll(self, job):
        key = 'pipeline:' + job['id']
        old = self.store.get(key)
        signature = self.signature()
        if old and old['signature'] == signature:
            return old
        payload = json.loads(job['payload'])
        value = {'job_id': job['id'], 'signature': signature, 'status': 'pending',
                 'channel': 'mock' if is_demo_payload(payload) else 'boss',
                 'updated_at': now(), 'error_code': None}
        self.store.set(key, value)
        return value

    def list(self):
        return [json.loads(x['value']) for x in self.store.all("SELECT value FROM state WHERE key LIKE 'pipeline:%'")]

    def retry(self, job_id):
        key = 'pipeline:' + job_id
        old = self.store.get(key)
        if not old or old['status'] != 'failed': raise AppError('CONFLICT')
        old.update(status='pending', error_code=None, updated_at=now())
        self.store.set(key, old)
        return old

    def advance_one(self):
        if not self.service.settings.automatic_pipeline or self.store.get('task') not in {'running', 'waiting_connection'}:
            return None
        if not self.lock.acquire(blocking=False):
            return None
        key = value = None
        try:
            for item in self.list():
                if item['status'] == 'pending':
                    value = item
                    break
            if value is None:
                return None
            key = 'pipeline:' + value['job_id']
            value.update(status='processing', updated_at=now())
            self.store.set(key, value)
            signature = self.signature()
            result = self.service.match(value['job_id'])
            if self.store.get('task') == 'paused' or signature != self.signature():
                value.update(status='pending', signature=self.signature())
                self.store.set(key, value)
                return value
            job_payload=json.loads(self.store.one('SELECT payload FROM jobs WHERE id=?',(value['job_id'],))['payload'])
            inquiry_ok=value['channel']=='boss' and inquiry_candidate(job_payload,self.store.get('preferences'))[0]
            if not inquiry_ok and (result['score_detail']['recommendation'] == '暂不优先投递' or result['score_detail']['missing_essential_input']):
                value.update(status='screened_out', recommendation=result['score_detail']['recommendation'])
            else:
                row = self.store.one('SELECT payload FROM jobs WHERE id=?', (value['job_id'],))
                job = json.loads(row['payload'])
                conversation = self.service.create_conversation(ConversationInput(
                    job_id=value['job_id'], recruiter=job.get('company') or ('演示招聘方' if value['channel']=='mock' else '招聘方待核实'), channel=value['channel']))
                if conversation['state'] == 'handoff':
                    value.update(status='handoff', conversation_id=conversation['id'])
                else:
                    draft = self.service.greeting(conversation['id'])
                    value.update(status='prepared', conversation_id=conversation['id'], outbox_id=draft['id'])
            value.update(signature=signature, updated_at=now(), error_code=None)
            self.store.set(key, value)
            return value
        except Exception as error:
            if key and value:
                value.update(status='failed', error_code=error.code if isinstance(error, AppError) else 'INTERNAL_ERROR', updated_at=now())
                self.store.set(key, value)
            return value
        finally:
            self.lock.release()

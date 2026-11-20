"""Opt-in question history, scoped to the current workbench database."""
from datetime import date
from uuid import UUID
from typing import Literal
import json
import re

from pydantic import BaseModel, Field

from .application_draft import assemble_application, assemble_statement
from .discovery_models import JobCandidate, valid_recruiting_email
from .errors import AppError
from .policy import handoff_reason, safe_example, unknown_fact, unsafe_instruction, validate_reply
from .store import dumps, uid


class StatementInput(BaseModel):
    language: Literal['en','zh'] = 'en'
    question: str = Field('', max_length=800)
    limit: int | None = Field(None, ge=1, le=500, strict=True)


class DraftInput(BaseModel):
    resume_id: str = Field(min_length=1, max_length=100)
    question: str | None = Field(None, min_length=1, max_length=1500)
    application_job: JobCandidate | None = None
    statement: StatementInput | None = None
    preview: bool = False
    regeneration_id: UUID | None = None
    available_from: date | None = None
    weekly_days: int | None = Field(None, ge=1, le=7)
    internship_months: int | None = Field(None, ge=1, le=36)
    signature: str | None = Field(None, max_length=80, pattern=r'^[^\x00-\x1f\x7f]*$')
    recipient: str | None = Field(None, max_length=254)
    job_title: str = Field(default='', max_length=300)


class DiscoveryDrafts:
    def __init__(self, service, workers):
        self.service, self.store, self.workers = service, service.store, workers
        with self.store.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS discovery_drafts(
                id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0)''')

    def create(self, data):
        resume = self.store.one('SELECT id,name,version,text,chunks FROM resumes WHERE id=?', (data.resume_id,))
        if not resume:
            raise AppError('RESUME_NOT_FOUND')
        if data.statement:
            if not data.application_job or data.application_job.source != 'himalayas':
                raise AppError('INVALID_INPUT', '请选择 Himalayas 岗位后生成申请陈述。')
            return assemble_statement(data.application_job, data, resume, self.workers, self.service.settings)
        resume['chunks'] = json.loads(resume['chunks'])
        if data.application_job:
            job = data.application_job
            if data.recipient:
                address = data.recipient.strip()
                if not valid_recruiting_email(address) or (job.contact_emails and address not in job.contact_emails):
                    raise AppError('INVALID_INPUT', '请核对收件人地址。')
                job = job.model_copy(update={'contact_emails':[address]})
            return assemble_application(job, data, resume, self.workers, self.service.settings)
        if not data.question:
            raise AppError('INVALID_INPUT', '请填写招聘方的问题。')
        found = self.service.rag.search(data.question, resume)
        facts = found['facts']
        terms = set(re.findall(r'[a-z][a-z0-9+#]+|[\u4e00-\u9fff]{2}', data.question.lower()))
        history = self.store.all('SELECT id,question,answer FROM discovery_drafts WHERE approved=1 ORDER BY rowid DESC LIMIT 40')
        scored = [(sum(term in row['question'].lower() for term in terms), row) for row in history if safe_example(row)]
        examples = [row for count,row in sorted(scored, key=lambda pair:-pair[0]) if count][:2]
        answer = '您好，谢谢您的问题。我的现有资料还不足以确认这部分，核对后我再补充。'
        mode = '待确认'
        if facts and not (unknown_fact(data.question) or unsafe_instruction(data.question) or handoff_reason(data.question)):
            mode = '演示草稿'
            answer = '您好，感谢您的提问。我可以结合以下简历经历进一步说明：'+facts[0]['text'][:220]
            if not self.service.provider.simulated:
                prompt, _ = self.service.prompt('discovery_draft.txt',
                    facts=dumps([item['text'][:650] for item in facts]), examples=dumps(examples),
                    question=data.question, title=data.job_title)
                mode = '规则降级草稿'
                try:
                    proposed, _ = self.service.provider.run('reply', prompt)
                    if validate_reply(proposed, resume['text'], data.question):
                        mode, answer = '真实模型草稿', proposed
                except AppError:
                    mode = '规则降级草稿'
            elif self.service.settings.model_mode == 'live':
                mode = '规则降级草稿'
        draft_id = uid()
        with self.store.tx() as db:
            db.execute('INSERT INTO discovery_drafts(id,question,answer) VALUES(?,?,?)', (draft_id,data.question,answer))
        return {'id':draft_id,'answer':answer,'mode':mode,
                'facts':[{'source':fact.get('source_file') or resume['name'],'quote':fact['text'][:280]} for fact in facts],
                'examples':[{'id':example['id'],'question':example['question']} for example in examples],
                'can_approve':bool(facts and mode != '待确认' and safe_example({'question':data.question,'answer':answer}))}

    def approve(self, draft_id):
        with self.store.tx() as db:
            row = db.execute('SELECT question,answer FROM discovery_drafts WHERE id=?', (draft_id,)).fetchone()
            if not row or not safe_example(dict(row)):
                raise AppError('INVALID_INPUT', '未找到可保存的历史问答。')
            db.execute('UPDATE discovery_drafts SET approved=1 WHERE id=?', (draft_id,))
        return {'saved':True}

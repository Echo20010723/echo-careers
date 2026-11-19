from datetime import date
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field
from typing import Literal

from .boss_outreach import is_demo_payload
from .errors import AppError
from .schemas import Strict
from .store import now

STATUS_NAMES = {
    'review': '待复核', 'shortlisted': '准备联系', 'applied': '本人已投递',
    'follow_up': '待跟进', 'interview': '面试', 'closed': '已结束',
}

class ApplicationUpdate(Strict):
    status: Literal['review', 'shortlisted', 'applied', 'follow_up', 'interview', 'closed']
    due_on: date | None = None
    note: str = Field('', max_length=2000)


def job_link(value):
    """Accept user-supplied HTTP links without fetching them."""
    if not value or any(ord(ch) <= 32 or ch == '\\' for ch in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None:
            return None
        netloc = parsed.netloc.lower()
        if parsed.port == (443 if parsed.scheme == 'https' else 80):
            netloc = netloc.rsplit(':', 1)[0]
    except ValueError:
        return None
    params = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith('utm_')
    ]
    return urlunsplit((parsed.scheme, netloc, parsed.path or '/', urlencode(params), ''))


def save_application(store, job_id, update):
    with store.tx() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or is_demo_payload(json.loads(row['payload'])):
            raise AppError('INVALID_INPUT')
        entry = update.model_dump(mode='json')
        entry.update(job_id=job_id, updated_at=now())
        db.execute(
            '''INSERT INTO applications(job_id,status,due_on,note,updated_at)
               VALUES(:job_id,:status,:due_on,:note,:updated_at)
               ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
                   due_on=excluded.due_on,note=excluded.note,updated_at=excluded.updated_at''',
            entry,
        )
        return entry


def review_report(store):
    def plain(value):
        return re.sub(r'([\\`*_{}\[\]()<>#!|~])', r'\\\1', ' '.join(str(value or '').split()))

    rows = store.all('''SELECT j.payload, j.analysis, a.status, a.due_on, a.note
                        FROM jobs j LEFT JOIN applications a ON j.id=a.job_id
                        WHERE COALESCE(a.status,'review') <> 'closed'
                        ORDER BY a.due_on IS NULL, a.due_on, j.rowid DESC''')
    lines = ['# 求职复核与跟进清单', '', '进度由本人填写。', '']
    for row in rows:
        job = json.loads(row['payload'])
        if is_demo_payload(job):
            continue
        analysis = json.loads(row['analysis']) if row['analysis'] else {}
        status = row['status'] or 'review'
        lines.append(f"## {plain(job.get('title') or '未命名岗位')}")
        lines.append(f"- 进度：{STATUS_NAMES[status]}")
        for label, value in (
            ('公司', job.get('company')), ('地点', job.get('location')),
            ('建议', analysis.get('score_detail', {}).get('recommendation')),
            ('截止', row['due_on']), ('备注', row['note']),
            ('岗位链接', job_link(job.get('source_url'))),
        ):
            if value:
                lines.append(f'- {label}：{plain(value)}')
        lines.append('')
    return '\n'.join(lines)

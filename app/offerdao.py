"""One-page, user-initiated search against OfferDAO's official API."""
import asyncio
import json
import re
import time
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .audit import REQUEST_ID
from .discovery_models import JobCandidate, valid_recruiting_email
from .discovery_online import plain
from .errors import AppError

LABEL = '国内 AI 岗位 · Offer岛'
TOTAL_TIMEOUT = 8
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class OfferSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    q: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=100,
                                        pattern=r'^[^\x00-\x1f\x7f]+$')]
    location: str | None = Field(None, max_length=40)
    employment_type: str | None = Field(None, max_length=60)
    limit: int = Field(12, ge=1, le=30)
    offset: int = Field(0, ge=0)
    published_within_days: int | None = Field(None, ge=1)

    @field_validator('q')
    @classmethod
    def reject_blank(cls, value):
        if not value.strip():
            raise ValueError('请输入职位搜索词')
        return value


class OfferLimit(AppError):
    def __init__(self, daily, seconds):
        code = 'OFFERDAO_DAILY_LIMIT' if daily else 'OFFERDAO_RATE_LIMIT'
        super().__init__(code)
        self.seconds = seconds
        if seconds is not None and not daily:
            self.message += f' 请在 {seconds} 秒后手动重试。'


def recruiting_emails(raw):
    values = raw if isinstance(raw, list) else re.split(r'[;,，；、\s]+', raw) if isinstance(raw, str) else []
    return list(dict.fromkeys(value.strip() for value in values
                              if isinstance(value, str) and valid_recruiting_email(value.strip())))[:8]


def parse_card(card, base, types):
    if not isinstance(card, dict):
        return []
    posting_id = card.get('posting_id')
    if not isinstance(posting_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', posting_id):
        return []

    positions = card.get('positions')
    if isinstance(positions, str):
        try:
            positions = json.loads(positions) if positions.strip() else None
        except (ValueError, RecursionError):
            positions = {}
    if positions is None or positions == []:
        entries = [(None, card)]
    elif isinstance(positions, list) and any(isinstance(row, dict) for row in positions):
        entries = [(index, row) for index, row in enumerate(positions) if isinstance(row, dict)]
    else:
        # A malformed positions field does not establish which role owns the top-level JD.
        entries = [(None, {key:card.get(key) for key in ('role','employment_type')})]

    parent_emails = recruiting_emails(card.get('contact_email'))
    shared = any(
        re.search(r'(?:所有岗位|各岗位|以上岗位|上述岗位|以下岗位|各职位|两个岗位|多个岗位).{0,28}(?:统一|共用|同一|均可?投递至|均发送至)|统一.{0,8}(?:投递邮箱|招聘邮箱|收件邮箱)', line)
        and not re.search(r'分别|不同|各自|仅限|专属', line)
        for field in ('intro', 'description', 'requirements')
        for line in (plain(card.get(field), 12000) or '').splitlines()
    )
    result = []
    for index, row in entries:
        raw_types = row.get('employment_type')
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        labels = list(dict.fromkeys(clean for value in raw_types if (clean := plain(value, 30)))) \
            if isinstance(raw_types, list) else []
        if types and not types.intersection(labels):
            continue
        description = plain(row.get('description'), 12000)
        requirements = plain(row.get('requirements'), 4500)
        jd = '\n'.join(filter(None, [description, *(
            ['任职要求：'+line for line in requirements.splitlines()] if requirements else []
        )])) or None
        result.append(JobCandidate(
            source='offerdao', source_id=posting_id if index is None else f'{posting_id}:{index}',
            posting_id=posting_id, url=f'{base}/j/{posting_id}',
            original_source_url=source_url(card.get('source_url')),
            contact_emails=recruiting_emails(row.get('contact_email')) or (parent_emails if index is not None and shared else []),
            application_url=application_link(row.get('contact_link')),
            title=plain(row.get('role'), 300), company=plain(card.get('organization'), 300),
            team=plain(card.get('team'), 300), city=plain(card.get('location'), 300),
            employment_type=' / '.join(labels)[:100] or None,
            description=description, requirements=requirements, jd=jd[:18000] if jd else None,
            published_at=plain(card.get('published_at'), 60),
            retrieval_source='offerdao_api', detail_status='detail' if jd else 'list_only',
        ))
    return result


def application_link(value):
    value = source_url(value)
    if value:
        try:
            parsed = urlsplit(value)
            has_job_query = any(key.casefold() in {'job','job_id','jobid','gh_jid','jid','requisitionid'} and val
                                for key, val in parse_qsl(parsed.query))
            return value if parsed.path.strip('/') or has_job_query else None
        except ValueError:
            pass
    return None


def source_url(value):
    return value if isinstance(value, str) and len(value) <= 1500 else None


def pagination_number(value, fallback=None, *, minimum=0, maximum=None):
    if value is None and fallback is not None:
        return fallback
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise AppError('OFFERDAO_UPSTREAM')
    return value


class OfferDAO:
    transport = None

    def __init__(self, base, key):
        self.base = base.strip().rstrip('/')
        self.key = key.get_secret_value().strip() if key else ''
        parts = urlsplit(self.base)
        if (not parts.hostname or parts.username is not None or parts.path or parts.query or parts.fragment
                or not (parts.scheme == 'https' or parts.scheme == 'http'
                        and parts.hostname in {'localhost','127.0.0.1','::1'})):
            raise ValueError('OFFERDAO_BASE must be an HTTPS origin or a loopback HTTP origin')
        self.client = None

    @property
    def ready(self):
        return bool(self.key)

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def _map_jobs(self, items, types):
        return [job for card in items for job in parse_card(card, self.base, types)]

    async def search(self, query, audit):
        if not self.ready:
            raise AppError('OFFERDAO_NOT_CONFIGURED')
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(5, connect=2, pool=2),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                transport=self.transport, follow_redirects=False,
            )
        started = time.perf_counter()
        code, returned, status = None, 0, 'error'
        params = {k:v for k,v in query.model_dump(exclude_none=True).items() if v != ''}
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT):
                async with self.client.stream(
                    'GET', self.base+'/api/v1/postings/search', params=params,
                    headers={'Authorization':f'Bearer {self.key}', 'Accept':'application/json'},
                ) as response:
                    code = response.status_code
                    if code in {401,403}:
                        raise AppError('OFFERDAO_AUTH' if code == 401 else 'OFFERDAO_FORBIDDEN')
                    if code not in {200, 429}:
                        raise AppError('OFFERDAO_FILTER' if code == 400 else 'OFFERDAO_UPSTREAM')
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body)+len(chunk) > MAX_RESPONSE_BYTES:
                            raise AppError('OFFERDAO_UPSTREAM')
                        body.extend(chunk)
                    try:
                        data = json.loads(body)
                    except (ValueError, RecursionError):
                        data = None
                    if code == 429:
                        retry = response.headers.get('Retry-After', '').strip()
                        info = data if isinstance(data, dict) else {}
                        fallback = info.get('retry_after')
                        seconds = int(retry) if re.fullmatch(r'[0-9]{1,9}', retry) else (
                            fallback if type(fallback) is int and 0 <= fallback < 10**9 else None)
                        raise OfferLimit(info.get('code') == 'rate_limited', seconds)
                if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                    raise AppError('OFFERDAO_UPSTREAM')
                items = data['items']
                returned = len(items)
                limit = pagination_number(data.get('limit'), query.limit, minimum=1, maximum=30)
                offset = pagination_number(data.get('offset'), query.offset)
                total = pagination_number(data.get('total'))
                if returned > limit:
                    raise AppError('OFFERDAO_UPSTREAM')
                types = {value.strip() for value in (query.employment_type or '').split(',') if value.strip()}
                jobs = await asyncio.to_thread(self._map_jobs, items, types)
            status = 'ok'
            return {'jobs':jobs, 'limit':limit, 'offset':offset, 'total':total, 'returned':returned}
        except (TimeoutError, httpx.TimeoutException):
            status = 'timeout'
            raise AppError('OFFERDAO_TIMEOUT') from None
        except httpx.RequestError:
            status = 'network_error'
            raise AppError('OFFERDAO_NETWORK') from None
        finally:
            audit.event('offerdao_search', REQUEST_ID.get(), stage='source_request', source='offerdao',
                        status=status, http_status=code, returned_count=returned, retry_count=0,
                        duration_ms=round((time.perf_counter()-started)*1000,2))

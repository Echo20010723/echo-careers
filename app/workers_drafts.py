"""Cloudflare Workers AI requests with shared budgets and draft coalescing."""
from collections import Counter, OrderedDict, deque
from concurrent.futures import Future
from datetime import datetime, timezone
import json
import re
from threading import Lock
from time import monotonic

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .policy import unsafe_instruction


AGENT_FALLBACK = {
    'not_configured': 'Cloudflare Workers AI 尚未配置。',
    'app_daily_limit': 'Workers AI 本站今日调用额度已用完。',
    'app_hourly_limit': '你的 Workers AI 小时调用额度已用完。',
    'busy': 'Workers AI 正忙。',
    'upstream_quota': 'Cloudflare Workers AI 额度已用完。',
    'upstream_limit': 'Cloudflare Workers AI 暂时限流。',
    'timeout': 'Cloudflare Workers AI 响应超时。',
    'provider_error': 'Cloudflare Workers AI 请求失败，请检查凭据与服务状态。',
    'invalid_output': 'Cloudflare Workers AI 返回了无效动作。',
}


class Narrative(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    body: str = Field(min_length=12, max_length=900)
    evidence_ids: list[str] = Field(min_length=1, max_length=2)


class WorkersDrafts:
    transport = None

    def __init__(self, settings):
        self.account = settings.cloudflare_account_id.strip()
        self.token = settings.cloudflare_api_token
        self.model = settings.cloudflare_model.strip()
        self.daily_limit = settings.cloudflare_daily_drafts
        self.public_hourly = settings.cloudflare_visitor_hourly_drafts
        self.local_hourly = settings.cloudflare_local_hourly_drafts
        timeout = settings.cloudflare_timeout
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=min(2.0, timeout)),
                                   transport=self.transport, follow_redirects=False)
        self.lock = Lock()
        self.day = None
        self.stats = Counter()
        self.recent = {}
        self.pending = {}
        self.active = 0
        self.cache = OrderedDict()
        self.cooldown = 0.0
        self.cooldown_reason = None

    def close(self):
        self.client.close()

    @property
    def configured(self):
        return bool(re.fullmatch(r'[0-9a-fA-F]{32}', self.account)
                    and self.token and self.token.get_secret_value().strip()
                    and re.fullmatch(r'@cf/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+', self.model))

    def _roll_day(self):
        today = datetime.now(timezone.utc).date()
        if self.day != today:
            self.day = today
            self.stats.clear()
        return today

    def usage(self):
        with self.lock:
            self._roll_day()
            fields = ('attempts', 'succeeded', 'failed', 'input_tokens', 'output_tokens',
                      'usage_reported', 'upstream_ms', 'agent_attempts', 'agent_succeeded')
            return {'day_utc':self.day.isoformat(), 'configured':self.configured,
                    'public_hourly_limit':self.public_hourly, 'daily_limit':self.daily_limit,
                    **{name:self.stats[name] for name in fields}}

    def _reserve(self, scope, public):
        now = monotonic()
        if now < self.cooldown:
            return self.cooldown_reason
        if self.stats['attempts'] >= self.daily_limit:
            return 'app_daily_limit'
        if self.active >= 4:
            return 'busy'
        self.recent = {key: times for key, times in self.recent.items()
                       if times and now-times[-1] < 3600}
        recent = self.recent.setdefault(scope, deque())
        while recent and now-recent[0] >= 3600:
            recent.popleft()
        if len(recent) >= (self.public_hourly if public else self.local_hourly):
            return 'app_hourly_limit'
        recent.append(now)
        self.stats['attempts'] += 1
        self.active += 1
        return None

    def _record(self, day, started, result, usage, *, agent=False):
        self.active -= 1
        if self.day != day:
            return
        success = result[0] is not None
        self.stats['succeeded' if success else 'failed'] += 1
        self.stats['upstream_ms'] += round((monotonic()-started)*1000)
        if agent and success:
            self.stats['agent_succeeded'] += 1
        if usage:
            self.stats['usage_reported'] += 1
            self.stats['input_tokens'] += usage[0]
            self.stats['output_tokens'] += usage[1]

    def choose_action(self, payload, instructions, schema, *, scope, public, timeout):
        if not self.configured:
            return None, 'not_configured', False
        with self.lock:
            day = self._roll_day()
            if reason := self._reserve(scope, public):
                return None, reason, False
            self.stats['agent_attempts'] += 1
        started = monotonic()
        result, usage = (None, 'provider_error'), None
        try:
            result, usage = self._request(payload, instructions, schema=schema,
                                          timeout=timeout, max_tokens=256)
        finally:
            with self.lock:
                self._record(day, started, result, usage, agent=True)
        return *result, True

    def generate(self, payload, instructions, *, scope, public, cache_key, regeneration_id=None):
        if not self.configured:
            return None, 'not_configured'
        if not payload['evidence']:
            return None, 'no_evidence'
        key = (scope, cache_key)
        lookup = (key, regeneration_id)
        with self.lock:
            if lookup in self.cache:
                self.cache.move_to_end(lookup)
                return self.cache[lookup]
            existing = self.pending.get(key)
            if existing is None:
                day = self._roll_day()
                if reason := self._reserve(scope, public):
                    return None, reason
                future = self.pending[key] = Future()

        if existing is not None:
            result = existing.result()
            if regeneration_id:
                with self.lock:
                    self._remember(lookup, result)
            return result

        started = monotonic()
        # The default is also used to release concurrent waiters if an unexpected error propagates.
        result, usage = (None, 'provider_error'), None
        try:
            result, usage = self._request(payload, instructions)
        finally:
            with self.lock:
                self._record(day, started, result, usage)
                self._remember((key, None), result)
                if regeneration_id:
                    self._remember(lookup, result)
                future.set_result(result)
                self.pending.pop(key)
        return result

    def _remember(self, key, result):
        self.cache[key] = result
        self.cache.move_to_end(key)
        if len(self.cache) > 2048:
            self.cache.popitem(last=False)

    def _request(self, payload, instructions, *, schema=Narrative, timeout=None, max_tokens=384):
        usage = None
        try:
            endpoint = f'https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/v1/chat/completions'
            request_options = {}
            if timeout is not None:
                wait = min(timeout, self.client.timeout.read)
                request_options['timeout'] = httpx.Timeout(wait, connect=min(2.0, wait))
            response = self.client.post(endpoint,
                **request_options,
                headers={'Authorization':'Bearer '+self.token.get_secret_value().strip()},
                json={'model':self.model, 'messages':[
                    {'role':'system','content':instructions},
                    {'role':'user','content':json.dumps(payload, ensure_ascii=False)},
                ], 'max_completion_tokens':max_tokens, 'reasoning_effort':'low', 'temperature':0.2,
                    'response_format':{'type':'json_object'}, 'stream':False, 'store':False})
            if response.status_code in {402, 429}:
                hint = response.headers.get('Retry-After', '')
                delay = min(900, int(hint)) if hint.isascii() and hint.isdigit() and len(hint) <= 6 else 60
                reason = 'upstream_quota' if response.status_code == 402 else 'upstream_limit'
                with self.lock:
                    self.cooldown = monotonic() + delay
                    self.cooldown_reason = reason
                return (None, reason), usage
            response.raise_for_status()
            envelope = response.json()
            metadata = envelope.get('usage')
            if isinstance(metadata, dict):
                counts = (metadata.get('prompt_tokens'), metadata.get('completion_tokens'))
                if all(type(n) is int and n >= 0 for n in counts):
                    usage = counts
            choice = envelope['choices'][0]
            if choice.get('finish_reason') not in {None, 'stop'}:
                return (None, 'invalid_output'), usage
            result = schema.model_validate_json(choice['message']['content'])
            if schema is Narrative and not supported(result, payload):
                return (None, 'invalid_output'), usage
            return (result, None), usage
        except httpx.TimeoutException:
            return (None, 'timeout'), usage
        except httpx.HTTPError:
            return (None, 'provider_error'), usage
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
            return (None, 'invalid_output'), usage


def statement_length(body, language):
    return len(''.join(body.split())) if language == 'zh' else len(body.split())


def supported(narrative, payload):
    available = {item['id']:item['topics'] for item in payload['evidence']}
    ids = narrative.evidence_ids
    if len(ids) != len(set(ids)) or any(identifier not in available for identifier in ids):
        return False
    body = narrative.body
    if unsafe_instruction(body) or re.search(
        r'[@<>]|\d{2,}|https?://|\d\s*[%％天月年]|到岗|出勤|附件|可实习|署名|'
        r'\b(?:availability|available to start|days per week|attached|sincerely)\b', body, re.I
    ):
        return False
    if payload.get('kind') == 'statement':
        if re.search(r'^(?:Dear\b|Hi\b|Hello\b|您好|尊敬|Best regards|Regards\b|此致|敬礼)|谢谢您的考虑|感谢您审阅', body, re.I | re.M):
            return False
        if len(re.split(r'\n\s*\n', body)) > 2:
            return False
        limit = payload.get('limit')
        if limit is not None and statement_length(body,payload['language']) > limit:
            return False
    is_chinese = bool(re.search(r'[\u3400-\u9fff]',body))
    return is_chinese == (payload['language'] == 'zh') and all(
        any(topic.casefold() in body.casefold() for topic in available[identifier]) for identifier in ids
    )

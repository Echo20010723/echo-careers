"""Cloudflare Workers AI requests with shared budgets and draft coalescing."""
from collections import Counter, OrderedDict, deque
from concurrent.futures import Future
from datetime import datetime, timezone
import json
import re
import ssl
from threading import Lock
from time import monotonic

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .policy import unsafe_instruction
from .discovery_rules import skill_hits


AGENT_FALLBACK = {
    'not_configured': 'Cloudflare Workers AI 尚未配置。',
    'app_daily_limit': 'Workers AI 本站今日调用额度已用完。',
    'app_hourly_limit': '你的 Workers AI 小时调用额度已用完。',
    'busy': 'Workers AI 正忙。',
    'upstream_quota': 'Cloudflare Workers AI 额度已用完。',
    'upstream_limit': 'Cloudflare Workers AI 暂时限流。',
    'timeout': 'Cloudflare Workers AI 响应超时。',
    'provider_auth': 'Cloudflare 拒绝认证，请检查账户 ID 和 Workers AI 推理权限。',
    'provider_error': 'Cloudflare Workers AI 请求失败，请检查模型与服务状态。',
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
        self.agent_enabled = (settings.cloudflare_agent_enabled if settings.cloudflare_agent_enabled is not None
                              else settings.public_mode and self.configured)
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
        self.last_http_error = None

    def close(self):
        self.client.close()

    @property
    def configuration_status(self):
        if not self.account or not self.token:
            return 'missing_credentials'
        if not re.fullmatch(r'[0-9a-fA-F]{32}', self.account):
            return 'invalid_account'
        # An embedded line break or a pasted cURL command is not an HTTP bearer token.
        if not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', self.token.get_secret_value().strip()):
            return 'invalid_token'
        if not re.fullmatch(r'@cf/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+', self.model):
            return 'invalid_model'
        return 'ready'

    @property
    def configured(self):
        return self.configuration_status == 'ready'

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
            return {'day_utc':self.day.isoformat(), 'configured':self.configured, 'configuration_status':self.configuration_status,
                    'public_hourly_limit':self.public_hourly, 'daily_limit':self.daily_limit,
                    'last_http_error':self.last_http_error,
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
                                          timeout=timeout, max_tokens=384)
        finally:
            with self.lock:
                self._record(day, started, result, usage, agent=True)
        return *result, True

    def generate(self, payload, instructions, *, scope, public, cache_key, regeneration_id=None, trace):
        if not self.configured:
            return None, 'not_configured'
        if not payload['evidence']:
            return None, 'no_evidence'
        key = (scope, cache_key)
        lookup = (key, regeneration_id)
        with self.lock:
            if lookup in self.cache:
                self.cache.move_to_end(lookup)
                result, upstream = self.cache[lookup]
                trace.update(upstream, source='cache_hit')
                return result
            existing = self.pending.get(key)
            if existing is None:
                day = self._roll_day()
                if reason := self._reserve(scope, public):
                    return None, reason
                future = self.pending[key] = Future()

        if existing is not None:
            result, upstream = existing.result()
            trace.update(upstream, source='coalesced')
            if regeneration_id:
                with self.lock:
                    self._remember(lookup, (result, upstream))
            return result

        started = monotonic()
        # The default is also used to release concurrent waiters if an unexpected error propagates.
        result, usage = (None, 'provider_error'), None
        upstream = {}
        try:
            result, usage = self._request(payload, instructions, max_tokens=620, upstream=upstream)
        finally:
            with self.lock:
                self._record(day, started, result, usage)
                saved = (result, upstream)
                self._remember((key, None), saved)
                if regeneration_id:
                    self._remember(lookup, saved)
                future.set_result(saved)
                self.pending.pop(key)
        trace.update(upstream, source='fresh' if result[0] else 'fallback', attempted=True)
        return result

    def _remember(self, key, result):
        self.cache[key] = result
        self.cache.move_to_end(key)
        if len(self.cache) > 2048:
            self.cache.popitem(last=False)

    def _request(self, payload, instructions, *, schema=Narrative, timeout=None, max_tokens=384, upstream=None):
        usage = None
        try:
            endpoint = f'https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/v1/chat/completions'
            request_options = {}
            if timeout is not None:
                wait = min(timeout, self.client.timeout.read)
                request_options['timeout'] = httpx.Timeout(wait, connect=min(2.0, wait))
            body = {'model':self.model, 'messages':[
                    {'role':'system','content':instructions},
                    {'role':'user','content':json.dumps(payload, ensure_ascii=False)},
            ], 'max_completion_tokens':max_tokens, 'temperature':0.2,
                    'response_format':{'type':'json_object'}, 'stream':False, 'store':False}
            if self.model == '@cf/qwen/qwen3.8-27b':
                body['reasoning_effort'] = 'low'
                body['chat_template_kwargs'] = {'enable_thinking':False}
            response = self.client.post(endpoint, **request_options, json=body,
                headers={'Authorization':'Bearer '+self.token.get_secret_value().strip()})
            if upstream is not None:
                upstream['http_status'] = response.status_code
            if response.status_code in {402, 429}:
                hint = response.headers.get('Retry-After', '')
                delay = min(900, int(hint)) if hint.isascii() and hint.isdigit() and len(hint) <= 6 else 60
                reason = 'upstream_quota' if response.status_code == 402 else 'upstream_limit'
                with self.lock:
                    self.cooldown = monotonic() + delay
                    self.cooldown_reason = reason
                return (None, reason), usage
            if not response.is_success:
                upstream_code = None
                try:
                    candidate = response.json()['errors'][0]['code']
                    if type(candidate) is int:
                        upstream_code = candidate
                except (ValueError, KeyError, TypeError, IndexError):
                    pass
                with self.lock:
                    self.last_http_error = {'status':response.status_code, 'code':upstream_code}
                reason = 'provider_auth' if response.status_code in {401, 403} else 'provider_error'
                return (None, reason), usage
            envelope = response.json()
            reported_model = envelope.get('model')
            if upstream is not None and isinstance(reported_model, str) and re.fullmatch(r'[A-Za-z0-9@/_.:-]{1,120}', reported_model):
                upstream['response_model'] = reported_model
            metadata = envelope.get('usage')
            if isinstance(metadata, dict):
                counts = (metadata.get('prompt_tokens'), metadata.get('completion_tokens'))
                if all(type(n) is int and n >= 0 for n in counts):
                    usage = counts
            choice = envelope['choices'][0]
            if choice.get('finish_reason') not in {None, 'stop', 'length'}:
                return (None, 'invalid_output'), usage
            result = schema.model_validate_json(choice['message']['content'])
            if schema is Narrative and not supported(result, payload):
                return (None, 'invalid_output'), usage
            return (result, None), usage
        except httpx.TimeoutException:
            return (None, 'timeout'), usage
        except httpx.HTTPError as error:
            causes = []
            current = error
            for _ in range(4):
                if current is None:
                    break
                if isinstance(current, ssl.SSLCertVerificationError):
                    causes.append('TLSCertificateVerification')
                    break
                causes.append(type(current).__name__)
                current = current.__cause__
            with self.lock:
                self.last_http_error = {'transport':causes}
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
        r'[@<>]|https?://|到岗|出勤|附件|可实习|署名|'
        r'\b(?:availability|available to start|days per week|attached|sincerely)\b', body, re.I
    ):
        return False
    # Numeric claims are allowed only when their complete literal occurs in cited evidence.
    cited_quotes = ' '.join(item.get('quote', '') for item in payload['evidence'] if item['id'] in ids)
    numeric_claim = r'(?<![\d.])\d+(?:\.\d+)?\s*(?:[%％]|年|个月|天|次|hours?\b|years?\b|months?\b)|\d{2,}(?:\.\d+)?'
    canon = lambda value: re.sub(r'\s+', '', value).replace('％','%').casefold()
    known_numbers = {canon(match.group()) for match in re.finditer(numeric_claim,cited_quotes,re.I)}
    if any(canon(match.group()) not in known_numbers for match in re.finditer(numeric_claim,body,re.I)):
        return False
    # A detail present only in a vacancy can describe the role, not past experience.
    prior_work = re.compile(r'做过|负责过|维护过|曾|过往|此前|我(?:使用|用|实现|开发|维护|参与|管理)|'
                            r'\bI (?:have |previously )?(?:built|developed|used|maintained|deployed|managed|worked|implemented)\b', re.I)
    for sentence in re.split(r'[。；;\n]+|[.!?](?=\s)', body):
        if not prior_work.search(sentence):
            continue
        tools = skill_hits(sentence) + re.findall(r'\b(?:Jenkins|Ansible|Terraform|Kubernetes|Shell|Bash|CI/CD|Dockerfile|Harbor|Prometheus|Grafana|Loki|PromQL|Filebeat|Zabbix)\b', sentence,re.I)
        if any(tool.casefold() not in cited_quotes.casefold() for tool in tools):
            return False
    if re.search(r'^(?:Dear\b|Hi\b|Hello\b|您好|尊敬|Best regards|Regards\b|此致|敬礼)|'
                 r'\b(?:I am authorized to work|I do not require sponsorship)\b|无需签证|有工作许可', body, re.I | re.M):
        return False
    if payload.get('kind') == 'statement':
        if re.search(r'谢谢您的考虑|感谢您审阅', body):
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

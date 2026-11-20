import json
import logging
from functools import lru_cache

from .audit import measure
from .discovery_models import DiscoveryRequest, JobCandidate, LocalSource, job_identity
from .discovery_online import Himalayas, PLATFORM_NAMES, remote_query
from .discovery_rules import RULE_VERSION, assess, evidence_index, prepare_match_evidence, rank_batch, select_candidates, suggest
from .errors import AppError
from .resumes import digest
from .occupations import version as occupation_version
from .schemas import JobInput
from .store import dumps, now, uid

logger = logging.getLogger(__name__)


class Discovery:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        settings = service.settings
        self.occupation_path = None if settings.public_mode else (
            settings.occupation_templates or settings.root/'Career_Workbench_Resume_JD_Benchmark'/'jobs.jsonl')
        # One bounded cache per workbench, including each isolated public visitor.
        self.match_evidence = lru_cache(maxsize=8)(self._match_evidence)
        self.sources = {'himalayas':Himalayas()}
        self.local_source = LocalSource(service.settings.root / 'examples/jobs_demo.json')
        with self.store.tx() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS discovery_batches (
                    id TEXT PRIMARY KEY, stage TEXT NOT NULL, request TEXT NOT NULL,
                    counts TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS discovery_items (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES discovery_batches(id),
                    position INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
                    payload TEXT NOT NULL, result TEXT, error TEXT, UNIQUE(batch_id, position)
                );
            ''')
            db.execute("""UPDATE discovery_batches SET stage='queued'
                          WHERE stage IN ('fetching','deduplicating','analyzing')""")

    def resume(self, resume_id):
        row = self.store.one('SELECT id,version,text FROM resumes WHERE id=?', (resume_id,))
        if not row:
            raise AppError('RESUME_NOT_FOUND')
        return row

    @staticmethod
    def _match_evidence(text):
        return prepare_match_evidence(evidence_index(text))

    def summary(self, resume_id):
        resume = self.resume(resume_id)
        settings = self.service.settings
        prompt = digest((settings.root/'prompts/search_terms_prompt.txt').read_text(encoding='utf-8'))
        version = occupation_version(self.occupation_path)
        key = f"discovery-summary:{RULE_VERSION}:{resume_id}:{resume['version']}:{version}:{prompt}:{settings.model_mode}:{settings.model_extract}"
        cached = self.store.get(key)
        if cached is None:
            index = evidence_index(resume['text'])
            cached = suggest(self.service, index, self.occupation_path)
            if cached['mode'] != 'local_fallback':
                self.store.set(key, cached)
        preferences = self.store.get('preferences') or {}
        return {'resume_id': resume_id, 'resume_version':resume['version'], 'occupation_version':version,
                'preferred_terms':preferences.get('keywords', []), **cached}

    def source_status(self):
        return {'sources':[{'id':key,'name':PLATFORM_NAMES[key],'ready':source.ready}
                           for key,source in self.sources.items()]}

    def create(self, request):
        self.resume(request.resume_id)
        batch_id = uid()
        if request.mode == 'online':
            if request.platform not in self.sources:
                raise AppError('ONLINE_SEARCH_NOT_CONFIGURED')
        with self.store.tx() as db:
            db.execute('INSERT INTO discovery_batches VALUES(?,?,?,?,?,NULL)',
                       (batch_id, 'queued', request.model_dump_json(), '{}', now()))
        return {'id': batch_id, 'stage': 'queued'}

    def reanalyze(self, batch_id, resume_id):
        self.resume(resume_id)
        new_id = uid()
        with self.store.tx() as db:
            batch = db.execute('SELECT stage,request,counts FROM discovery_batches WHERE id=?', (batch_id,)).fetchone()
            if not batch or batch['stage'] not in ('complete', 'partial', 'failed'):
                raise AppError('CONFLICT')
            items = db.execute('SELECT position,job_id,payload FROM discovery_items WHERE batch_id=?', (batch_id,)).fetchall()
            if not items:
                raise AppError('CONFLICT')
            request = DiscoveryRequest.saved(batch['request']).model_copy(update={'resume_id': resume_id})
            counts = json.loads(batch['counts'])
            counts.pop('current_term', None)
            counts.pop('rerun_job', None)
            counts['reuse_candidates'] = True
            counts['parent_batch_id'] = batch_id
            db.execute('INSERT INTO discovery_batches VALUES(?,?,?,?,?,NULL)',
                       (new_id, 'queued', request.model_dump_json(), dumps(counts), now()))
            db.executemany('INSERT INTO discovery_items VALUES(?,?,?,?,?,NULL,NULL)',
                           [(uid(), new_id, item['position'], item['job_id'], item['payload']) for item in items])
        return {'id': new_id, 'stage': 'queued'}

    def retry(self, batch_id, job_id=None):
        with self.store.tx() as db:

            batch = db.execute('SELECT stage,request,counts FROM discovery_batches WHERE id=?', (batch_id,)).fetchone()
            allowed = ('complete', 'partial', 'failed') if job_id else ('partial', 'failed')
            if not batch or batch['stage'] not in allowed:
                raise AppError('CONFLICT')
            counts = json.loads(batch['counts'])
            if job_id:
                if DiscoveryRequest.saved(batch['request']).mode != 'source_page' or not db.execute(
                    'UPDATE discovery_items SET error=NULL,result=NULL WHERE batch_id=? AND job_id=?',
                    (batch_id, job_id),
                ).rowcount:
                    raise AppError('INVALID_INPUT')
                counts['rerun_job'] = job_id
            else:
                counts.pop('rerun_job', None)
                db.execute('UPDATE discovery_items SET error=NULL WHERE batch_id=? AND result IS NULL', (batch_id,))
            db.execute("UPDATE discovery_batches SET stage='queued', error=NULL, counts=? WHERE id=?", (dumps(counts),batch_id))
        return {'id': batch_id, 'stage': 'queued'}

    def current_stage(self, batch_id):
        row = self.store.one('SELECT stage FROM discovery_batches WHERE id=?', (batch_id,))
        return row['stage'] if row else None

    def status(self, batch_id):
        row = self.store.one('SELECT * FROM discovery_batches WHERE id=?', (batch_id,))
        if not row:
            raise AppError('INVALID_INPUT')
        request = DiscoveryRequest.saved(row.pop('request'))
        row.update(mode=request.mode, platform=request.platform, terms=request.terms, limit=request.limit,
                   counts=json.loads(row['counts']))
        row['reused_candidates'] = row['counts'].pop('reuse_candidates', False)
        row['parent_batch_id'] = row['counts'].pop('parent_batch_id', None)
        row['counts'].pop('rerun_job', None)
        row['query_conditions'] = request.model_dump(mode='json', exclude={'jobs','resume_id'})
        row['resume'] = self.store.one('SELECT id,name,version FROM resumes WHERE id=?', (request.resume_id,))
        items = self.store.all('SELECT * FROM discovery_items WHERE batch_id=? ORDER BY position', (batch_id,))
        row['counts']['analyzed'] = sum(item['result'] is not None for item in items)
        row['counts']['failed'] = sum(item['error'] is not None for item in items)
        row['jobs'] = [
            {**json.loads(item['payload']), 'local_job_id': item['job_id'],
             'analysis':json.loads(item['result']) if item['result'] else None,
             'evaluation_mode':json.loads(item['result'])['evaluation_mode'] if item['result'] else None,
             'error':item['error']} for item in items
        ]
        row['grade_distribution'] = rank_batch(row['jobs'], sort=request.mode != 'source_page' or row['stage'] in {'complete','partial','failed'})
        return row

    def stage(self, batch_id, stage, counts=None, error=None):
        with self.store.tx() as db:
            db.execute('UPDATE discovery_batches SET stage=?, counts=COALESCE(?,counts), error=? WHERE id=?',
                       (stage, dumps(counts) if counts is not None else None, error, batch_id))

    def advance(self):
        with self.store.tx() as db:
            row = db.execute("SELECT * FROM discovery_batches WHERE stage='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return False
            batch_id = row['id']
            db.execute("UPDATE discovery_batches SET stage='fetching' WHERE id=?", (batch_id,))
        try:
            request = DiscoveryRequest.saved(row['request'])
            resume = self.resume(request.resume_id)
            with measure(self.service.audit, 'resume_evidence', batch_id):
                index = self.match_evidence(resume['text'])
            source_errors = []
            if not json.loads(row['counts']).get('reuse_candidates'):
                if request.mode == 'online':
                    source_errors = self.fetch_online(batch_id, request, resume, index)
                elif not self.store.one('SELECT id FROM discovery_items WHERE batch_id=? LIMIT 1', (batch_id,)):
                    self.fetch(batch_id, request)
            self.stage(batch_id, 'analyzing')
            self.analyze(batch_id, request, resume, index, rerun_job=json.loads(row['counts']).get('rerun_job'))
            failed = self.store.one(
                'SELECT COUNT(*) AS n FROM discovery_items WHERE batch_id=? AND error IS NOT NULL', (batch_id,),
            )['n']
            analyzed = self.store.one(
                'SELECT COUNT(*) AS n FROM discovery_items WHERE batch_id=? AND result IS NOT NULL', (batch_id,),
            )['n']
            has_errors = bool(failed or source_errors)
            self.stage(batch_id, 'partial' if has_errors and analyzed else 'failed' if has_errors else 'complete',
                       error='；'.join(source_errors) or None)
        except AppError as error:
            self.stage(batch_id, 'failed', error=error.message)
        except Exception:
            logger.exception('Discovery batch failed [%s]', batch_id)
            self.stage(batch_id, 'failed', error='任务未能完成，可重试。已有结果已保留。')
        return True

    def fetch(self, batch_id, request):
        with measure(self.service.audit, 'provided_jd_read', batch_id):
            rows = self.local_source.fetch(request)
        self.stage(batch_id, 'deduplicating', {'fetched':len(rows)})
        direct = request.mode in {'paste', 'source_page'}
        selected, matched, unique = select_candidates(rows, request.terms, request.limit, include_unmatched=direct)
        if request.mode == 'source_page':
            positions = {job.source_id: index for index, job in enumerate(rows)}
            selected.sort(key=lambda group: positions[group['job'].source_id])
        counts = {'fetched':len(rows), 'matched':matched, 'unique':unique,
                  'duplicates':(len(rows) if direct else matched)-unique, 'selected':len(selected)}
        self.save_candidates(batch_id, request, selected, counts)

    def save_candidates(self, batch_id, request, groups, counts):
        existing = self.store.all('SELECT id, payload FROM discovery_items WHERE batch_id=? ORDER BY position', (batch_id,))
        saved = {}
        if request.mode == 'online':
            for row in existing:
                for key in job_identity(JobCandidate.model_validate_json(row['payload'])):
                    saved[key] = row
        prepared, updates = [], []
        for group in groups:
            candidate = group['job']
            hits = [term for term in request.terms if term in group['hits']]
            retrieved = [term for term in request.terms if term in group['retrieval']] if request.mode == 'online' else []
            lexical = [term for term in request.terms if term in group['lexical']]
            previous = next((saved[key] for key in job_identity(candidate) if key in saved),None)
            if previous:
                payload = json.loads(previous['payload'])
                payload['matched_terms'] = hits
                payload['retrieved_by'] = retrieved
                payload['lexical_matches'] = lexical
                updates.append((dumps(payload),previous['id']))
                continue
            if len(existing) + len(prepared) >= request.limit:
                continue
            payload = candidate.model_dump(exclude={'search_terms'})
            payload.update(source=candidate.source or ('synthetic' if request.mode == 'demo' else ('manual' if request.mode in {'import','paste'} else request.platform)),
                           data_mode=request.mode, matched_terms=hits,
                           retrieved_by=retrieved, lexical_matches=lexical,
                           detail_status=candidate.detail_status or ('detail' if candidate.jd else 'list_only'),
                           imported_at=candidate.imported_at or now())
            source = 'mock_discovery' if request.mode == 'demo' or candidate.source == 'synthetic' else ('discovery_'+request.platform if request.mode == 'online' else 'discovery_import')
            legacy = self.service.import_job(JobInput(
                id=candidate.source_id, source=source, title=candidate.title, company=candidate.company,
                location=candidate.city, salary=candidate.salary_raw, source_url=candidate.url,
                employment_type=candidate.employment_type,
                raw_jd=candidate.jd or candidate.jd_summary or '（未提供岗位描述）',
            ))
            position = len(existing) + len(prepared)
            prepared.append((uid(), batch_id, position, legacy['id'], dumps(payload)))
        counts.update(selected=len(existing)+len(prepared))
        with self.store.tx() as db:
            db.executemany('INSERT INTO discovery_items VALUES(?,?,?,?,?,NULL,NULL)', prepared)
            db.executemany('UPDATE discovery_items SET payload=? WHERE id=?', updates)
            db.execute("UPDATE discovery_batches SET stage='analyzing', counts=? WHERE id=?", (dumps(counts),batch_id))

    def fetch_online(self, batch_id, request, resume, index):
        adapter = self.sources.get(request.platform)
        if adapter is None:
            raise AppError('ONLINE_SEARCH_NOT_CONFIGURED')
        context = {'audit':self.service.audit, 'trace_id':batch_id}
        key = 'discovery-online:'+batch_id
        saved = self.store.get(key) or {'terms':{},'rows':[],'fetched':0}
        pages = saved['terms']
        rows = [JobCandidate.model_validate(job) for job in saved['rows']]
        counts = {'fetched':saved['fetched'], 'queries':saved.setdefault('queries',{})}
        source_errors = {}
        previously_failed = {term for term, page in pages.items() if page.get('failed')}
        old_count = self.store.one('SELECT COUNT(*) AS n FROM discovery_items WHERE batch_id=?', (batch_id,))['n']
        active = [term for term in request.terms if term in previously_failed or
                  (old_count < request.limit and not pages.get(term, {}).get('done'))]
        if not active and rows:
            groups, matched, unique = select_candidates(rows, request.terms, len(rows))
            counts.update(matched=matched, unique=unique, duplicates=matched-unique)
            self.save_candidates(batch_id, request, groups, counts)
        if active and not adapter.ready:
            code = 'ONLINE_SEARCH_NOT_CONFIGURED'
            if not old_count:
                raise AppError(code)
            return [AppError(code).message]
        while active:
            for term in active:
                page = pages.setdefault(term, {'offset':0,'done':False})
                counts['queries'].setdefault(term, remote_query(term))
                self.stage(batch_id, 'fetching', {**counts, 'current_term':term})
                try:
                    result = adapter.page(term, request, page['offset'], context)
                except AppError as error:
                    page['failed'] = True
                    source_errors[term] = term+'：'+error.message
                    self.store.set(key,saved)
                    if error.code in {'ONLINE_SEARCH_AUTH','ONLINE_SEARCH_LIMIT','ONLINE_SEARCH_NOT_CONFIGURED'}:
                        for skipped in active[active.index(term)+1:]:
                            pages.setdefault(skipped, {'offset':0,'done':False})['failed'] = True
                        self.store.set(key,saved)
                        break
                    continue
                page.update(offset=page['offset']+1,done=not result.more,failed=False)
                counts['queries'][term] = result.query or term
                if result.warning:
                    source_errors[term] = result.warning
                    page['board_warning'] = True
                else:
                    page.pop('board_warning',None)
                rows.extend(result.jobs)
                saved['rows'].extend(job.model_dump() for job in result.jobs)
                saved['fetched'] += result.fetched
                counts['fetched'] = saved['fetched']
                self.store.set(key,saved)
            # Commit and analyze one balanced round before requesting further pages.
            self.stage(batch_id, 'deduplicating', counts)
            groups, matched, unique = select_candidates(rows, request.terms, len(rows))
            counts.update(matched=matched, unique=unique, duplicates=matched-unique)
            self.save_candidates(batch_id, request, groups, counts)
            self.analyze(batch_id, request, resume, index)
            if counts['selected'] >= request.limit:
                break
            active = [term for term in active if not pages[term]['done'] and not pages[term].get('failed')]
        for term, page in pages.items():
            if page.pop('board_warning',False):
                page.update(offset=0,failed=True)
        self.store.set(key,saved)
        return list(dict.fromkeys(source_errors.values()))

    def analyze(self, batch_id, request, resume, index, rerun_job=None):
        constraints = request.model_dump(mode='json', include={'city','job_type','internship_months','available_from','graduation_year','weekly_days','degree'})
        config = {
            'version':RULE_VERSION, 'mode':self.service.settings.model_mode,
            'model':self.service.settings.model_match,
            'prompt':digest((self.service.settings.root/'prompts/discovery_match_prompt.txt').read_text(encoding='utf-8')),
        }
        items = self.store.all('SELECT * FROM discovery_items WHERE batch_id=? AND result IS NULL AND error IS NULL', (batch_id,))
        for item in items:
            if rerun_job and item['job_id'] != rerun_job:
                continue
            try:
                candidate = JobCandidate.model_validate_json(item['payload'])
                job_content = candidate.model_dump(exclude={'source','source_id','url','source_page_url','search_terms','imported_at','contact_emails','application_url'})
                key = 'discovery-match:' + digest(dumps([resume['version'], config, constraints, job_content]))
                result = None if rerun_job else self.store.get(key)
                cache_hit = result is not None
                if not cache_hit:
                    with measure(self.service.audit, 'match_analysis', batch_id):
                        result = assess(self.service, candidate, index, request)
                else:
                    self.service.audit.event('performance_stage', batch_id, stage='match_cache', status='hit')
                if result['evaluation_mode'] != 'local_fallback' and not cache_hit:
                    self.store.set(key, result)
                with self.store.tx() as db:
                    db.execute('UPDATE discovery_items SET result=?, error=NULL WHERE id=?',
                               (dumps(result), item['id']))
            except Exception:
                logger.exception('Discovery item failed [%s]', item['id'])
                with self.store.tx() as db:
                    db.execute('UPDATE discovery_items SET error=? WHERE id=?',
                               ('岗位分析失败，可重试。', item['id']))

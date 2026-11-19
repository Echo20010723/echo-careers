"""Reproduce the offline labeled comparison, using a disposable workbench."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.discovery_models import JobCandidate
from app.discovery_rules import assess, evidence_index, rank_batch
from app.main import create_app
from app.settings import ROOT, Settings

fixture = json.loads((ROOT/'examples/agent_jobs.json').read_text(encoding='utf-8'))
text = (ROOT/'examples/resume_demo.md').read_text(encoding='utf-8').strip()
options = SimpleNamespace(country='AU', degree='bachelor', city=None, weekly_days=None,
                          available_from=None, graduation_year=None, internship_months=None)


def measure(results, searches, reads, retrievals, model_calls=0):
    ids = [job['source_id'] for job in results]
    conflict = {'phd-a','phd-b','phd-c','us'}
    lines = text.splitlines()
    citations = [(row['quote'] in lines[row['line']-1])
                 for job in results for row in job['analysis']['evidence']]
    jd_citations = [quote in job['jd'] for job in results
                   for group in ('duties','required','bonus') for quote in job['analysis'][group]]
    return {'ids':ids, 'worth_applying':sum(fixture['jobs'][key]['human_label']=='worth_applying' for key in ids),
            'hard_conflicts':len(set(ids)&conflict), 'locatable_citations':sum(citations+jd_citations),
            'citations':len(citations+jd_citations), 'searches':searches, 'reads':reads,
            'retrievals':retrievals, 'model_calls':model_calls}


def main():
    output = {'mode':'controlled synthetic', 'labeled_jobs':len(fixture['jobs'])}
    with TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=Path(tmp), use_demo_resume=True, automatic_pipeline=False))
        service = app.state.service
        with TestClient(app) as client:
            resume = client.post('/api/discovery/example').json()
            index = evidence_index(text)
            for key, ids in [('rule_single_search',fixture['buckets']['drift']), ('rule_full_corpus',fixture['jobs'])]:
                candidates = []
                for job_id in ids:
                    job = JobCandidate.model_validate(fixture['jobs'][job_id])
                    candidates.append({**job.model_dump(), 'analysis':assess(service,job,index,options,use_model=False)})
                rank_batch(candidates)
                output[key] = measure(candidates[:5], 1 if key=='rule_single_search' else 0, len(candidates), 0)
            for mode in ('drift','focused'):
                response = client.post('/api/discovery/agent', json={'resume_id':resume['id'], 'goal':'Find 5 AI internships',
                    'source':mode, 'country':'AU', 'degree':'bachelor'})
                response.raise_for_status()
                url='/api/discovery/agent/'+response.json()['run_id']
                for _ in range(200):
                    run = client.get(url).json()
                    if run['status']=='complete':
                        break
                    if run['status'] in {'waiting','failed','interrupted'}:
                        raise RuntimeError(run['status'])
                    time.sleep(.025)
                else:
                    raise RuntimeError('Agent timed out')
                tool_reads = sum(event['action']=='retrieve' for event in run['events'])
                output['agent_'+mode] = measure(run['recommendations'], run['spent']['searches'],
                        run['spent']['reads'], tool_reads, run['spent']['model_calls'])
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()

"""Explicit offline evaluation. Runtime code never loads the two answer keys."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

from app.discovery_rules import evidence_index, suggest
from app.discovery_models import normalized
from app.occupations import catalog, duty_roles
from app.settings import ROOT

RESUME_HOLDOUT = {'CV-005','CV-010','CV-015','CV-020','CV-025','CV-030'}
JD_HOLDOUT = {'Q-05','Q-10','Q-15'}
SENIORITY = re.compile(r'实习生?|应届|助理|初级|\b(?:entry[- ]level|junior|graduate|internship|intern|trainee|associate)\b', re.I)
QUALIFIER = re.compile(r'\s+(?:conditional on|subject to|where eligible)\b.*$|[（(]按资质核验[）)]$', re.I)


def role_key(text):
    return normalized(SENIORITY.sub('', QUALIFIER.sub('', text))).strip(' -()（）')


def aliases(prediction, expected):
    term = role_key(expected)
    if not term:
        return False
    role = next((row for row in catalog() if row['id']==prediction.get('role_id')), None)
    representations = [prediction.get('direction'),prediction['term'],*(role['aliases'] if role else [])]
    return any(term == role_key(value) for value in representations if value)


def resume_report(data, path):
    service = SimpleNamespace(settings=SimpleNamespace(model_mode='mock'))
    outcomes = []
    # Generate and freeze predictions before loading the answer key for scoring.
    predictions = [suggest(service, evidence_index(item['resume_text']), path)['suggestions'] for item in data['records']]
    answers = data['answer_key']
    for item, prediction in zip(data['records'], predictions):
        key = item['id']
        expected = answers[key]
        lines = item['resume_text'].splitlines()
        outcomes.append({'id':key, 'industry':item['industry'], 'stage':item['career_stage'],
            'holdout':key in RESUME_HOLDOUT, 'directions':[row['direction'] for row in prediction],
            'queries':[row['term'] for row in prediction], 'evidence_count':len(prediction),
            'citation_matches':sum(row['quote'] in lines[row['line']-1] for row in prediction),
            'reasonable_hit':any(aliases(p, role) for p in prediction for role in expected['reasonable_roles']),
            'poor_fit_in_top3':any(aliases(p, role) for p in prediction for role in expected['poor_fit_roles']),
            'credential_reminder':any('资质' in check or '资格' in check for p in prediction for check in p['needs'])})
    return outcomes


def tokens(text):
    latin = re.findall(r'[a-z][a-z0-9+#.]{1,29}',text.casefold())
    chinese = re.findall(r'[\u4e00-\u9fff]+',text)
    return Counter([*latin, *(part[i:i+2] for part in chinese for i in range(len(part)-1))])


class JDIndex:
    def __init__(self, records):
        self.rows = []
        frequencies = Counter()
        for row in records:
            title = tokens(row['job_title'])
            duties = ' '.join(row.get('responsibilities') or [])
            required = ' '.join(row.get('required_qualifications') or [])
            preferred = ' '.join(row.get('preferred_qualifications') or [])
            document = tokens(' '.join([row.get('jd_text',''), duties, required, preferred]))
            frequencies.update(set(title) | set(document))
            self.rows.append((row['id'],title,document,duty_roles(duties)))
        self.idf = {word: math.log((len(records)+1)/(count+1))+1 for word,count in frequencies.items()}

    def rank(self, text):
        query = tokens(text)
        areas = duty_roles(text)
        scored = []
        for key,title,body,roles in self.rows:
            score = sum(self.idf.get(word,0)*(2.8*bool(title[word])+math.sqrt(min(body[word],4)))
                        for word in query)
            # Prefer responsibility alignment. Dense descriptions should not win on length alone.
            score /= max(1.0, len(body)**.23)
            score += 2*len(areas & roles)
            scored.append((-score,key))
        return [key for _,key in sorted(scored)]


def jd_report(data):
    index = JDIndex(data['records'])
    ranked = [index.rank(query['query']) for query in data['search_queries']]
    answers = data['answer_key']
    results = []
    for query, ranking in zip(data['search_queries'],ranked):
        key = query['id']
        relevant = answers[key]['relevant_jd_ids']
        negative = answers[key]['hard_negative_jd_ids']
        positions = {item:i+1 for i,item in enumerate(ranking)}
        judged_pairs = [(positions[good],positions[bad]) for good in relevant for bad in negative]
        results.append({'id':key,'holdout':key in JD_HOLDOUT,'top5':ranking[:5],
            'recall_at_5':len(set(relevant)&set(ranking[:5]))/len(relevant) if relevant else None,
            'positive_positions':{item:positions[item] for item in relevant},
            'hard_negative_positions':{item:positions[item] for item in negative},
            'preferred_pairs':sum(a<b for a,b in judged_pairs),'labeled_pairs':len(judged_pairs)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path, nargs='?', default=ROOT/'Career_Workbench_Resume_JD_Benchmark')
    parser.add_argument('--output', type=Path, default=ROOT/'data/cross-industry-evaluation.json')
    args = parser.parse_args()
    def read(file): return json.loads((args.package/file).read_text(encoding='utf-8-sig'))
    resumes = resume_report(read('resumes_30.json'), args.package/'jobs.jsonl')
    jobs = jd_report(read('job_descriptions_35.json'))
    recall = [row['recall_at_5'] for row in jobs if row['recall_at_5'] is not None]
    summary = {'mode':'offline_local_rules', 'resume_count':len(resumes),
        'resume_hits':sum(row['reasonable_hit'] for row in resumes),
        'poor_fit_top3':sum(row['poor_fit_in_top3'] for row in resumes),
        'citations':sum(row['evidence_count'] for row in resumes),
        'locatable_citations':sum(row['citation_matches'] for row in resumes),
        'credential_reminders':sum(row['credential_reminder'] for row in resumes),
        'jd_queries':len(jobs), 'macro_recall_at_5':round(sum(recall)/len(recall),4),
        'preferred_pairs':sum(row['preferred_pairs'] for row in jobs),
        'labeled_pairs':sum(row['labeled_pairs'] for row in jobs)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'summary':summary,'resumes':resumes,'jd_queries':jobs},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()

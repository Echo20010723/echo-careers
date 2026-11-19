"""Run against a Docker or Render public URL. Only synthetic inputs are sent."""
import argparse
from http.cookiejar import CookieJar
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('base', nargs='?', default='http://127.0.0.1:8766')
    parser.add_argument('--online', action='store_true', help='Also make a public Himalayas job search')
    args = parser.parse_args()
    base = args.base.rstrip('/')
    browser = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(path, data=None, headers=None, client=browser):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers = {'Content-Type':'application/json'}
        with client.open(Request(base+path,data=data,headers=headers or {}), timeout=15) as response:
            body = response.read()
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type','') else body

    assert request('/health')['public']
    assert b'Echo Careers' in request('/')
    assert b'renderAgent' in request('/static/discovery.js')
    boundary = 'echo-demo-smoke'
    content = ('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="synthetic.txt"\r\n'
               'Content-Type: text/plain\r\n\r\n# Projects\r\nPython FastAPI and RAG development\r\n--'+boundary+'--\r\n').encode()
    uploaded = request('/api/discovery/resumes', content, {'Content-Type':'multipart/form-data; boundary='+boundary})
    assert request('/api/discovery/resumes/'+uploaded['id']+'/terms')['resume_id']==uploaded['id']
    example = request('/api/discovery/example', {})
    task = request('/api/discovery/tasks', {'resume_id':example['id'], 'mode':'demo',
                   'terms':['Agent 开发实习','Python 后端实习'], 'limit':5})
    path = '/api/discovery/tasks/'+task['id']
    for _ in range(200):
        result = request(path)
        if result['stage'] in {'complete','failed','partial'}:
            break
        time.sleep(.1)
    assert result['stage']=='complete' and len(result['jobs'])==5
    assert result['jobs'][0]['jd'] and result['jobs'][0]['analysis']
    assert request(path+'/export')['jobs']==result['jobs']
    other = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        request(path+'/export', client=other)
    except HTTPError as error:
        assert error.code in {404,422}
    else:
        raise AssertionError('a second client accessed the export')
    launched = request('/api/discovery/agent', {'resume_id':example['id'], 'goal':'Find 5 AI internships',
                        'source':'drift','country':'AU','degree':'bachelor'})
    agent_path = '/api/discovery/agent/'+launched['run_id']
    for _ in range(200):
        agent = request(agent_path)
        if agent['status'] in {'complete','failed','interrupted'}:
            break
        time.sleep(.1)
    assert agent['status']=='complete' and agent['spent']['searches']==2
    assert len(request(agent_path+'/export')['recommendations'])==5
    print('Public home, CSS/JS shell, health, upload, search, JD evidence, isolation and both exports passed.')
    if args.online:
        real = request('/api/discovery/tasks', {'resume_id':example['id'], 'mode':'online',
                       'terms':['Python'], 'job_type':'any', 'limit':3})
        real_path = '/api/discovery/tasks/'+real['id']
        for _ in range(300):
            real = request(real_path)
            if real['stage'] in {'complete','failed','partial'}:
                break
            time.sleep(.1)
        print(json.dumps({'online_stage':real['stage'], 'online_jobs':len(real['jobs']),
                          'error':real.get('error')}, ensure_ascii=False))
        assert real['stage']=='complete' and real['jobs']



if __name__ == '__main__':
    main()

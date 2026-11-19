"""Double-click local launcher. Reuses a healthy server; never terminates other apps."""
import json,subprocess,sys,time,webbrowser,os
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8766'
def healthy():
    try:
        with build_opener(ProxyHandler({})).open(URL+'/health',timeout=2) as response:
            data=json.load(response)
            valid=data.get('app') == 'career-workbench'
            if valid and data.get('pid'):
                (ROOT/'data/server.pid').write_text(str(data['pid']),encoding='ascii')
            return valid
    except Exception:return False

def main():
    already_running=healthy()
    if not already_running:
        data=ROOT/'data';data.mkdir(exist_ok=True)
        with (data/'server.log').open('ab') as output:
            child=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','8766','--workers','1','--no-access-log'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        (data/'server.pid').write_text(str(child.pid),encoding='ascii')
        for _ in range(30):
            if healthy():break
            if child.poll() is not None:break
            time.sleep(.5)
        if not healthy():
            print('Could not start. Check data/server.log. The port may already be used.')
            return 1
    print(f'Workbench: {URL}')
    if '--no-ui' not in sys.argv:
        try:
            if os.name == 'nt':
                os.startfile(URL)
            elif not webbrowser.open(URL):
                raise OSError('No browser accepted the URL')
        except OSError as error:
            print(f'Could not open the default browser: {error}')
            print(f'Open this address in your browser: {URL}')
            return 1
    return 0
if __name__=='__main__':raise SystemExit(main())

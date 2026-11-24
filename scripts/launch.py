"""Start or reuse the local workbench without stopping unrelated applications."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.runtime import project_identity, runtime_revision
from app.settings import Settings

HOST = '127.0.0.1'
PORT = 8766
URL = f'http://{HOST}:{PORT}'
SERVER_ARGS = ['-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', HOST,
               '--port', str(PORT), '--workers', '1', '--no-access-log']


def running_health():
    try:
        with build_opener(ProxyHandler({})).open(URL + '/health', timeout=2) as response:
            health = json.load(response)
    except (URLError, OSError, ValueError):
        return None
    if (isinstance(health, dict) and health.get('app') == 'career-workbench'
            and health.get('status') == 'ok' and type(health.get('pid')) is int
            and health['pid'] > 0):
        return health
    return None


def listening():
    try:
        with socket.create_connection((HOST, PORT), timeout=.5):
            return True
    except OSError:
        return False


def same_project_process(pid: int) -> bool:
    if os.name != 'nt':
        return False
    # Windows venv Python can launch a base interpreter that owns the socket.
    query = (
        f"$server = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
        "if ($server) { "
        "$parent = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $server.ParentProcessId); "
        "@($server, $parent) | Select-Object ExecutablePath, CommandLine | ConvertTo-Json -Compress }"
    )
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             '[Console]::OutputEncoding = [Text.Encoding]::UTF8; ' + query],
            capture_output=True, encoding='utf-8', timeout=5, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        servers = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    if not isinstance(servers, list) or not servers:
        return False

    suffix = ' ' + ' '.join(SERVER_ARGS)
    venv = os.path.normcase(str(ROOT / '.venv' / 'Scripts' / 'python.exe'))
    return (str(servers[0].get('CommandLine')).rstrip().endswith(suffix)
            and any(os.path.normcase(str(item.get('ExecutablePath'))) == venv
                    and str(item.get('CommandLine')).rstrip().endswith(suffix)
                    for item in servers if item))


def stop_owned_server(current):
    if os.name != 'nt':
        return False
    import _winapi

    # Keep the specific process open while performing the ownership checks.
    terminate_and_wait = 0x0001 | 0x00100000
    try:
        handle = _winapi.OpenProcess(terminate_and_wait, False, current['pid'])
        try:
            if not same_project_process(current['pid']):
                return False
            latest = running_health()
            if not latest or any(latest.get(key) != current.get(key) for key in (
                    'pid', 'runtime_revision', 'project_identity', 'launcher_instance')):
                return False
            _winapi.TerminateProcess(handle, 0)
            return _winapi.WaitForSingleObject(handle, 5000) == 0
        finally:
            _winapi.CloseHandle(handle)
    except OSError:
        return False


def config_signature(instance: str, base: str, key: str) -> str:
    message = json.dumps([base, key], ensure_ascii=True).encode('ascii')
    return hmac.new(instance.encode('ascii'), message, hashlib.sha256).hexdigest()


def offer_config_matches(health, data, base, key):
    if health.get('offerdao_ready') != bool(key) or health.get('offerdao_base', base) != base:
        return False
    instance = health.get('launcher_instance')
    if not instance:
        return True
    try:
        record = json.loads((data / 'server.launch.json').read_text(encoding='ascii'))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get('instance') == instance
            and record.get('offerdao_signature') == config_signature(instance, base, key))


def start_server(data, identity, revision, base, key):
    instance = secrets.token_hex(16)
    env = {**os.environ, 'ECHO_WORKBENCH_LAUNCHER_INSTANCE': instance}
    data.mkdir(exist_ok=True)
    with (data / 'server.log').open('ab') as output:
        child = subprocess.Popen(
            [sys.executable, *SERVER_ARGS], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    for _ in range(30):
        health = running_health()
        if health and (health.get('project_identity'), health.get('launcher_instance'),
                       health.get('runtime_revision'), health.get('offerdao_ready'),
                       health.get('offerdao_base')) == (identity, instance, revision, bool(key), base):
            record = {'instance': instance, 'offerdao_signature': config_signature(instance, base, key)}
            (data / 'server.launch.json').write_text(json.dumps(record), encoding='ascii')
            (data / 'server.pid').write_text(str(health['pid']), encoding='ascii')
            return True
        if child.poll() is not None:
            break
        time.sleep(.5)
    return False


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    settings = Settings.load()
    base = settings.offerdao_base.strip().rstrip('/')
    key = settings.offerdao_api_key.get_secret_value().strip() if settings.offerdao_api_key else ''
    identity = project_identity(ROOT)
    revision = runtime_revision(ROOT)
    data = ROOT / 'data'
    current = running_health()
    if current and current.get('project_identity') not in (None, identity):
        print(f'{PORT} 端口属于另一个工作台目录，当前后台未被停止。')
        return 1
    if current and (current.get('runtime_revision') != revision
                    or not offer_config_matches(current, data, base, key)):
        if current.get('offerdao_ready') and not key:
            print('当前后台已配置 Offer 岛。请从保有原 Offer 岛配置的环境启动，以保留现有配置。')
            return 1
        if not stop_owned_server(current):
            print(f'无法确认并停止 {PORT} 端口上的旧后台。请从原项目目录关闭后再打开。')
            return 1
        print('已停止本项目的旧后台，正在启动新版。')
        current = None
    if not current:
        if listening():
            print(f'{PORT} 端口已被占用。请检查占用进程及 data/server.log。')
            return 1
        if not start_server(data, identity, revision, base, key):
            print('工作台启动后未通过校验。请检查 data/server.log。')
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


if __name__ == '__main__':
    raise SystemExit(main())

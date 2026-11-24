import pytest

from app.runtime import project_identity, runtime_revision
from app.settings import Settings
from scripts import launch


def test_static_edits_do_not_make_running_backend_stale(tmp_path):
    static = tmp_path / 'app' / 'static'
    static.mkdir(parents=True)
    backend = tmp_path / 'app' / 'main.py'
    backend.write_text('first')
    revision = runtime_revision(tmp_path)
    (static / 'hero-terrain.js').write_text('new frame')
    assert runtime_revision(tmp_path) == revision
    backend.write_text('second')
    assert runtime_revision(tmp_path) != revision


@pytest.fixture
def local_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, 'ROOT', tmp_path)
    monkeypatch.setattr(launch.sys, 'argv', ['launch.py', '--no-ui'])
    monkeypatch.setattr(Settings, 'load', classmethod(lambda cls: Settings(offerdao_api_key='test-key')))
    data = tmp_path / 'data'
    data.mkdir()
    return data, {
        'pid': 101, 'runtime_revision': runtime_revision(tmp_path),
        'project_identity': project_identity(tmp_path), 'offerdao_ready': True,
        'offerdao_base': 'https://offerdao.ai',
    }


def test_cold_and_repeated_open_keep_the_same_instance(monkeypatch, local_launcher):
    _, health = local_launcher
    current = []
    monkeypatch.setattr(launch, 'running_health', lambda: current[0] if current else None)
    monkeypatch.setattr(launch, 'listening', lambda: False)

    def start(*args):
        current.append(health)
        return True

    monkeypatch.setattr(launch, 'start_server', start)
    assert launch.main() == launch.main() == 0
    assert current == [health]


def test_foreign_instance_is_not_stopped(monkeypatch, local_launcher):
    _, health = local_launcher
    monkeypatch.setattr(launch, 'running_health', lambda: {
        **health, 'runtime_revision': 'outdated', 'project_identity': 'other-directory',
    })

    def forbidden(*args):
        pytest.fail('Should neither stop the foreign server nor create a second server')

    monkeypatch.setattr(launch, 'stop_owned_server', forbidden)
    monkeypatch.setattr(launch, 'start_server', forbidden)
    assert launch.main() == 1


def test_key_change_is_detected_even_when_both_are_configured(local_launcher):
    import json

    data, health = local_launcher
    instance = 'instance-id'
    health['launcher_instance'] = instance
    base = health['offerdao_base']
    record = {'instance': instance, 'offerdao_signature': launch.config_signature(instance, base, 'first')}
    (data / 'server.launch.json').write_text(json.dumps(record), encoding='ascii')
    assert launch.offer_config_matches(health, data, base, 'first')
    assert not launch.offer_config_matches(health, data, base, 'second')

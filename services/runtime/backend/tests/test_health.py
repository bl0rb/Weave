"""GET /health -- unauthenticated on purpose (see app/main.py's docstring
comment), so every test here calls it with no Authorization header at all.
"""

from app.core.config import settings
from tests.conftest import REPO_BOTS_DIR, client


def test_health_is_healthy_and_reports_the_real_bot_roster():
    # No Authorization header sent, and it must still succeed.
    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'healthy'
    # bots/general-assistant.yaml + bots/legal-support.yaml -- both must
    # validate against BotConfig for this to be 2, not merely "the directory
    # has 2 files in it".
    assert body['bots'] == 2
    assert body['detail'] is None


def test_health_is_degraded_when_bots_dir_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path / 'does-not-exist'))
    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'degraded'
    assert body['bots'] == 0
    assert 'not readable' in body['detail']


def test_health_is_degraded_when_a_bot_yaml_is_malformed(monkeypatch, tmp_path):
    # Unclosed flow-sequence bracket -- genuinely invalid YAML syntax, not
    # merely a schema violation, so this exercises the yaml.YAMLError branch
    # of app/services/botconfig.py's _load_bot_file().
    (tmp_path / 'broken.yaml').write_text('name: [unclosed\n', encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))

    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'degraded'
    assert body['bots'] == 0
    assert 'broken.yaml' in body['detail']


def test_health_is_degraded_when_a_bot_yaml_fails_schema_validation(monkeypatch, tmp_path):
    # Syntactically valid YAML, but missing every required BotConfig field.
    (tmp_path / 'incomplete.yaml').write_text('id: incomplete\n', encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))

    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'degraded'
    assert 'incomplete.yaml' in body['detail']


def test_health_reflects_only_the_real_bots_dir_again_after_override(monkeypatch):
    # Regression guard for the monkeypatch fixtures above: they must not
    # leak into other tests (monkeypatch auto-reverts at teardown, but
    # confirming it here catches a future test that swaps monkeypatch for a
    # plain `settings.bots_dir = ...` assignment).
    assert settings.bots_dir == str(REPO_BOTS_DIR)
    resp = client.get('/health')
    assert resp.json()['bots'] == 2

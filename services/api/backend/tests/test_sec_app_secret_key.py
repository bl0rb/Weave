"""app/main.py refuses to start with the public default SECRET_KEY (or a
short one) unless ALLOW_INSECURE_DEV_SECRET_KEY opts in -- the key signs
the OIDC login state cookie (app/core/security.py)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import DEFAULT_SECRET_KEY, Settings, insecure_secret_key_reason

BACKEND_DIR = Path(__file__).resolve().parents[1]


STRONG_KEY = 'q7F2k9Lm4Xz8Rt1Vb6Nw3Hy5Pc0Sd2Ge'


@pytest.fixture(autouse=True)
def _no_key_from_environment(monkeypatch):
    # _env_file=None skips .env but not os.environ: a SECRET_KEY exported in
    # the shell/CI job running pytest would otherwise decide these tests.
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.delenv('ALLOW_INSECURE_DEV_SECRET_KEY', raising=False)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_default_key_is_refused():
    assert insecure_secret_key_reason(_settings()) == 'SECRET_KEY is the public built-in default'


def test_short_key_is_refused():
    assert 'shorter than 32' in insecure_secret_key_reason(_settings(secret_key='x' * 31))


def test_strong_key_is_accepted():
    assert insecure_secret_key_reason(_settings(secret_key=STRONG_KEY)) is None


@pytest.mark.parametrize(
    'key, reason',
    [
        (DEFAULT_SECRET_KEY + ' ', 'public built-in default'),
        (DEFAULT_SECRET_KEY + '\n', 'public built-in default'),
        (' ' * 40, 'shorter than 32'),
        ('x' * 31 + ' ' * 9, 'shorter than 32'),
        ('a' * 32, 'distinct characters'),
        ('ab' * 32, 'distinct characters'),
    ],
)
def test_effectively_public_or_trivial_key_is_refused(key, reason):
    assert reason in (insecure_secret_key_reason(_settings(secret_key=key)) or '')


def test_dev_opt_in_accepts_default():
    assert insecure_secret_key_reason(_settings(allow_insecure_dev_secret_key=True)) is None


def _import_app(tmp_path, **env) -> subprocess.CompletedProcess:
    child_env = {
        k: v for k, v in os.environ.items() if k.upper() not in ('SECRET_KEY', 'ALLOW_INSECURE_DEV_SECRET_KEY')
    }
    child_env.update(PYTHONPATH=str(BACKEND_DIR), DATABASE_URL=f'sqlite:///{tmp_path}/app.db', **env)
    # cwd=tmp_path: no stray .env file is picked up.
    return subprocess.run(
        [sys.executable, '-c', 'import app.main'],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize('env', [{}, {'SECRET_KEY': DEFAULT_SECRET_KEY}, {'SECRET_KEY': 'too-short'}])
def test_app_refuses_to_start_with_insecure_key(tmp_path, env):
    result = _import_app(tmp_path, **env)
    assert result.returncode != 0
    assert 'refusing to start' in result.stderr


@pytest.mark.parametrize('env', [{'SECRET_KEY': STRONG_KEY}, {'ALLOW_INSECURE_DEV_SECRET_KEY': 'true'}])
def test_app_starts_with_strong_key_or_dev_opt_in(tmp_path, env):
    result = _import_app(tmp_path, **env)
    assert result.returncode == 0, result.stderr

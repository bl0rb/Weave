"""The portable bot document (contracts/bot-document.md): the committed
JSON Schema must match the live BotConfig, and the two rules that make an
exported file safe -- secret placeholder rejected, unknown schema_version
rejected -- hold on BotConfig itself."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.bot import SECRET_PLACEHOLDER, BotConfig
from app.schemas.bot_document_schema import export_schema

CONTRACT = Path(__file__).resolve().parents[4] / 'contracts' / 'bot-document.schema.json'

_N8N = {
    'id': 'service-bot',
    'name': 'Service',
    'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
    'system_prompt': 'Du führst Anfragen über den konfigurierten n8n-Workflow aus.',
    'n8n': {'webhook_url': 'https://n8n.example.test/webhook/service'},
}


def test_committed_schema_matches_bot_config():
    committed = json.loads(CONTRACT.read_text(encoding='utf-8'))
    assert export_schema() == committed, (
        'contracts/bot-document.schema.json is stale -- regenerate with '
        '`python -m app.schemas.bot_document_schema > ../../../contracts/bot-document.schema.json`'
    )


def test_schema_version_defaults_to_one_and_is_part_of_the_schema():
    assert BotConfig.model_validate(_N8N).schema_version == 1
    assert export_schema()['properties']['schema_version'] == {
        'const': 1, 'default': 1, 'title': 'Schema Version', 'type': 'integer',
    }


def test_secret_placeholder_is_rejected_by_name():
    with pytest.raises(ValidationError) as excinfo:
        BotConfig.model_validate({**_N8N, 'n8n': {**_N8N['n8n'], 'auth_token': SECRET_PLACEHOLDER}})
    assert SECRET_PLACEHOLDER in str(excinfo.value)
    assert 'Weave-Ingest' in str(excinfo.value)


def test_unknown_schema_version_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        BotConfig.model_validate({**_N8N, 'schema_version': 2})
    assert 'schema_version' in str(excinfo.value)

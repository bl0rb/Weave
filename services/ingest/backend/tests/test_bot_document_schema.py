"""Weave-Ingest's mirror of the portable bot document (app/schemas/
bot_document.py) must produce exactly the committed contract, which
Weave-Runtime generates from its own BotConfig -- drift on either side
fails both suites (contracts/bot-document.md)."""

import json
from pathlib import Path

from app.schemas.bot_document import BotDocument

CONTRACT = Path(__file__).resolve().parents[4] / 'contracts' / 'bot-document.schema.json'


def test_mirror_schema_matches_committed_contract():
    committed = json.loads(CONTRACT.read_text(encoding='utf-8'))
    assert BotDocument.model_json_schema() == committed, (
        'app/schemas/bot_document.py drifted from contracts/bot-document.schema.json -- repeat the Runtime '
        'change (services/runtime/backend/app/schemas/bot.py) here, or regenerate the contract there'
    )

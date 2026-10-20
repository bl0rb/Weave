"""JSON Schema of the portable bot document (contracts/bot-document.md).

`export_schema()` is `BotConfig.model_json_schema()` (app/schemas/bot.py)
with every `description` removed: the prose lives next to the fields in
bot.py and in contracts/bot-document.md, so the committed schema stays a
pure shape contract that Weave-Ingest's mirror model
(services/ingest/backend/app/schemas/bot_document.py) can reproduce
field-for-field without copying docstrings.

Regenerate the committed file after any change to app/schemas/bot.py::

    cd services/runtime/backend
    python -m app.schemas.bot_document_schema > ../../../contracts/bot-document.schema.json

tests/test_bot_document_schema.py (here) and Weave-Ingest's own
tests/test_bot_document_schema.py both fail on drift.
"""

import json
import sys

from app.schemas.bot import BotConfig


def _strip_descriptions(node, *, in_properties: bool = False):
    """Drop the `description` keyword everywhere -- but never a property
    that happens to be NAMED `description` (BotConfig.description)."""
    if isinstance(node, dict):
        return {
            key: _strip_descriptions(value, in_properties=key == 'properties')
            for key, value in node.items()
            if in_properties or key != 'description'
        }
    if isinstance(node, list):
        return [_strip_descriptions(item) for item in node]
    return node


def export_schema() -> dict:
    return _strip_descriptions(BotConfig.model_json_schema())


def main() -> None:
    sys.stdout.write(json.dumps(export_schema(), indent=2, ensure_ascii=False, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()

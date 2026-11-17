"""Tests for the answer evaluation (app/services/answer_eval.py) and its CLI
(app/eval_cli.py).

The end-to-end cases run the REAL `handle_chat` pipeline against
bots/legal-support.yaml (retrieval, require_sources, collection-bound to
'vertraege', teams legal/management) with Weave-Retrieval mocked at
`httpx.post`/`httpx.get` and `FakeLLM.chat` scripted per question -- the
same seams tests/test_chat_e2e.py uses.
"""

import json

import pytest

from app import eval_cli
from app.schemas.chat import ChatResponse, ChatTrace, ChatUser, GuardTrace, Source
from app.services import answer_eval
from app.services import llm as llm_service
from app.services import retrieval_client
from app.services.answer_eval import AnswerSpec, GoldenItem, LLMJudge
from app.services.botconfig import BotNotFoundError
from app.services.llm import LLMResult, LLMUsage
from app.services.retrieval_client import RetrievedChunk, RetrievedChunkScores
from tests.conftest import REPO_BOTS_DIR  # noqa: F401 -- bots_dir set for the real pipeline


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = ''

    def json(self):
        return self._json_data


QUESTION_TERMINATION = 'Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?'
QUESTION_CAR = 'Welche Regelung gilt laut unserem Vertrag für Dienstwagen?'

_CHUNKS = {
    QUESTION_TERMINATION: [
        {'chunk_id': 1, 'document_id': 'doc-1', 'source_job_id': 'job-vertrag', 'chunk_index': 3,
         'text': 'Die Kündigungsfrist beträgt drei Monate zum Quartalsende.'},
        {'chunk_id': 2, 'document_id': 'doc-2', 'source_job_id': 'job-other', 'chunk_index': 0,
         'text': 'Urlaubsanträge werden im Portal gestellt.'},
    ],
    QUESTION_CAR: [],
}
_ANSWERS = {
    QUESTION_TERMINATION: 'Die Kündigungsfrist beträgt drei Monate zum Quartalsende [1]. Bitte beachten Sie die Form.',
}


def _result(chunk: dict, rank: int) -> dict:
    return {
        'heading_path': ['Arbeitsvertrag', 'Kündigung'], 'page_start': 1, 'page_end': 1, 'document_version': 1,
        'source': 'confluence', 'original_filename': 'vertrag.pdf', 'collection': 'vertraege', 'rank': rank,
        'scores': {'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.9}, **chunk,
    }


@pytest.fixture
def pipeline(monkeypatch):
    """Mock Weave-Retrieval and script FakeLLM; returns the list of search queries seen."""
    searched = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, [{'slug': 'vertraege', 'name': 'Verträge', 'description': None, 'public': False}])

    def _fake_post(url, headers=None, json=None, timeout=None):
        searched.append(json['query'])
        chunks = _CHUNKS.get(json['query'], [])
        return _FakeResponse(200, {'results': [_result(c, i) for i, c in enumerate(chunks, 1)], 'trace': {}})

    def _scripted_chat(self, messages, model=None, temperature=None):
        question = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        reply = _ANSWERS.get(question, 'Keine Ahnung.')
        return LLMResult(content=reply, model=model or 'fake-chat', usage=LLMUsage(prompt_tokens=100, completion_tokens=7))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)
    monkeypatch.setattr(llm_service.FakeLLM, 'chat', _scripted_chat)
    return searched


LEGAL_USER = ChatUser(team='legal', teams=['legal'])
GOLDEN = [
    GoldenItem(
        query=QUESTION_TERMINATION, relevant=['job-vertrag'],
        answer=AnswerSpec(
            reference='Die Kündigungsfrist beträgt drei Monate zum Quartalsende.',
            must_include=[['drei Monate', '3 Monate'], ['Quartalsende'], ['schriftlich']],
            must_not_include=['sechs Wochen'],
        ),
    ),
    GoldenItem(query=QUESTION_CAR, answer=AnswerSpec(answerable=False)),
]


# --- golden loader ---------------------------------------------------------------


def _write(tmp_path, text: str):
    path = tmp_path / 'golden.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_load_golden_reads_query_relevant_and_answer_and_ignores_unknown_keys(tmp_path):
    path = _write(tmp_path, """
- query: "Wie lange ist die Kündigungsfrist?"
  k: 5
  relevant:
    - source_job_id: job-1
      contains: "drei Monate"
    - source_job_id: job-1
    - contains: "ohne job id"
  answer:
    reference: "Drei Monate."
    must_include: ["drei Monate", ["Formular A", "Formblatt A"]]
    must_not_include: "sechs Wochen"
    must_cite: [job-9]
- query: "Gibt es einen Dienstwagen?"
  answer: {answerable: false}
- query: "Nur Retrieval"
  relevant: [{source_job_id: job-2}]
""")
    first, second, third = answer_eval.load_golden(path)

    assert first.relevant == ['job-1']  # deduplicated, entry without id skipped
    assert first.answer == AnswerSpec(
        reference='Drei Monate.', must_include=[['drei Monate'], ['Formular A', 'Formblatt A']],
        must_not_include=['sechs Wochen'], must_cite=['job-9'],
    )
    assert first.expected_documents == ['job-9']  # must_cite overrides relevant
    assert second.relevant == [] and second.answer.answerable is False
    assert third.answer is None and third.expected_documents == ['job-2']


@pytest.mark.parametrize('text, message', [
    ('{query: x}', 'expected a YAML list'),
    ('[]', 'empty'),
    ('- query: [unclosed', 'not valid YAML'),
    ('- just a string', 'not a mapping'),
    ('- relevant: []', 'missing/invalid query'),
    ('- query: q\n  answer: [1]', 'not a mapping'),
    ('- query: q\n  answer: {answerable: "no"}', 'non-boolean'),
    ('- query: q\n  answer: {must_include: [1]}', 'must_include item'),
    ('- query: q\n  answer: {must_include: "x"}', 'must be a list'),
    ('- query: q\n  answer: {reference: 3}', 'reference'),
])
def test_load_golden_rejects_structurally_invalid_files(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        answer_eval.load_golden(_write(tmp_path, text))


def test_load_golden_reads_the_shared_retrieval_example():
    path = REPO_BOTS_DIR.parents[1] / 'retrieval' / 'backend' / 'eval' / 'golden.example.yaml'
    if not path.exists():  # runtime checked out / built on its own
        pytest.skip('Weave-Retrieval golden example not present')
    items = answer_eval.load_golden(path)
    assert items and all(item.query for item in items)


# --- metric helpers ----------------------------------------------------------------


def test_key_fact_recall_matches_alternatives_case_insensitively_and_on_word_boundaries():
    item = GoldenItem(query='q', answer=AnswerSpec(must_include=[['3 Monate', 'drei Monate'], ['Formular-A'], ['30']]))
    metrics = answer_eval.correctness_metrics(item, 'Es gelten DREI Monate; nutzen Sie Formular A. Frist: 3 Tage [1].')
    # "30" must not match the "3" in "3 Tage" and not the citation "[1]".
    assert metrics['key_fact_recall'] == pytest.approx(2 / 3)


def test_forbidden_rate_and_token_f1():
    item = GoldenItem(query='q', answer=AnswerSpec(
        reference='Die Frist beträgt drei Monate.', must_not_include=['sechs Wochen', 'zwei Jahre'],
    ))
    metrics = answer_eval.correctness_metrics(item, 'Die Frist beträgt sechs Wochen [2].')
    assert metrics['forbidden_rate'] == 0.5
    # tokens: frist beträgt sechs wochen vs frist beträgt drei monate -> 2 common of 4/4
    assert metrics['token_f1'] == pytest.approx(0.5)
    assert metrics['key_fact_recall'] is None


def test_correctness_metrics_are_none_without_an_answer_block():
    assert answer_eval.correctness_metrics(GoldenItem(query='q'), 'x') == {
        'key_fact_recall': None, 'forbidden_rate': None, 'token_f1': None,
    }


def test_token_f1_edge_cases():
    assert answer_eval.token_f1('', '') == 1.0
    assert answer_eval.token_f1('Die', 'drei Monate') == 0.0
    assert answer_eval.token_f1('drei Monate', 'Drei Monate!') == 1.0


def test_split_sentences_keeps_trailing_citations_with_their_sentence():
    sentences = answer_eval.split_sentences('Erster Satz ist hier. [1] Zweiter Satz folgt jetzt.\n- Dritter Punkt mit Text [2]\nOk.')
    assert sentences == ['Erster Satz ist hier [1].', 'Zweiter Satz folgt jetzt.', 'Dritter Punkt mit Text [2]']


def _source(job_id: str | None, ref: int | None = None, memory_kind=None) -> Source:
    return Source(source='', document_id=f'doc-{job_id}', chunk_id=1, source_job_id=job_id, ref=ref,
                  memory_kind=memory_kind)


def test_citation_metrics_by_position_and_by_ref():
    item = GoldenItem(query='q', relevant=['job-a'])
    answer = 'Satz eins ist belegt [1]. Satz zwei ist belegt [2]. Satz drei ohne Beleg hier. Satz vier steht hier [7].'
    by_position = answer_eval.citation_metrics(item, answer, [_source('job-a'), _source('job-b')])
    assert by_position['citation_coverage'] == 0.75
    assert by_position['citation_precision'] == pytest.approx(1 / 3)  # [7] resolves to nothing

    by_ref = answer_eval.citation_metrics(item, answer, [_source('job-b', ref=1), _source('job-a', ref=2)])
    assert by_ref['citation_precision'] == pytest.approx(1 / 3)


def test_citation_precision_is_none_without_citations_or_expected_documents():
    sources = [_source('job-a')]
    assert answer_eval.citation_metrics(GoldenItem(query='q', relevant=['job-a']), 'Ganz ohne Zitat hier.', sources)[
        'citation_precision'] is None
    assert answer_eval.citation_metrics(GoldenItem(query='q'), 'Mit Zitat hier [1].', sources)[
        'citation_precision'] is None
    assert answer_eval.citation_metrics(GoldenItem(query='q'), 'ok', sources)['citation_coverage'] is None


def _hit(job_id: str | None, chunk_id: int = 1, text: str = 'x') -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, document_id=f'doc-{chunk_id}', text=text, source=None, original_filename=None,
        page_start=None, page_end=None, document_version=1, heading_path=[], scores=RetrievedChunkScores(),
        source_job_id=job_id,
    )


def test_retrieval_metrics():
    item = GoldenItem(query='q', relevant=['job-a', 'job-c'])
    hits = [_hit('job-x', 1), _hit(None, 2), _hit('job-a', 3)]
    sources = [_source('job-a'), _source('job-x'), _source(None, memory_kind='collection')]
    metrics = answer_eval.retrieval_metrics(item, hits, sources)
    assert metrics == {'context_recall': 0.5, 'context_precision': 0.5, 'mrr': pytest.approx(1 / 3)}


def test_retrieval_metrics_without_hits_or_expectations():
    assert answer_eval.retrieval_metrics(GoldenItem(query='q', relevant=['job-a']), [], []) == {
        'context_recall': 0.0, 'context_precision': None, 'mrr': 0.0,
    }
    assert set(answer_eval.retrieval_metrics(GoldenItem(query='q'), [_hit('a')], []).values()) == {None}


def test_lexical_faithfulness():
    contexts = ['Die Kündigungsfrist beträgt drei Monate zum Quartalsende.']
    answer = 'Die Kündigungsfrist beträgt drei Monate [1]. Der Mond besteht aus grünem Käse.'
    assert answer_eval.lexical_faithfulness(answer, contexts) == 0.5
    assert answer_eval.lexical_faithfulness(answer, []) is None
    assert answer_eval.lexical_faithfulness('ok', contexts) is None


def test_is_abstention():
    trace = ChatTrace(intent='knowledge', confidence=0.9, needs_retrieval=True, needs_tool=False, router_mode='rules')
    with_source = ChatResponse(answer='a', sources=[_source('j')], trace=trace)
    assert answer_eval.is_abstention(with_source) is False
    assert answer_eval.is_abstention(with_source.model_copy(update={'sources': []})) is True
    guarded = with_source.model_copy(update={'trace': trace.model_copy(update={
        'guard': GuardTrace(triggered=True, reason='no_context')})})
    assert answer_eval.is_abstention(guarded) is True


# --- judge -------------------------------------------------------------------------------


class _ScriptedProvider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, model=None, temperature=None):
        self.calls.append({'messages': messages, 'model': model, 'temperature': temperature})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(content=reply, model='judge-model')


@pytest.mark.parametrize('text, expected', [
    ('{"score": 0.5}', {'score': 0.5}),
    ('```json\n{"score": 1}\n```', {'score': 1}),
    ('Ergebnis: {"score": 0} fertig', {'score': 0}),
    ('kein json', None),
    ('[1, 2]', None),
    ('{"score": }', None),
])
def test_parse_judge_json(text, expected):
    assert answer_eval.parse_judge_json(text) == expected


def test_judge_correctness_parses_strict_json_and_rejects_invalid_scores():
    spec = AnswerSpec(reference='Drei Monate.', must_include=[['drei Monate', '3 Monate']])
    provider = _ScriptedProvider('{"score": 0.8, "reason": "ok"}', '{"score": 1.5}', '{"score": true}', 'nope',
                                 llm_service.LLMError('down'))
    judge = LLMJudge(provider, model='m')
    assert judge.correctness('Frage?', 'Antwort', spec) == 0.8
    assert judge.correctness('Frage?', 'Antwort', spec) is None
    assert judge.correctness('Frage?', 'Antwort', spec) is None
    assert judge.correctness('Frage?', 'Antwort', spec) is None
    assert judge.correctness('Frage?', 'Antwort', spec) is None
    first = provider.calls[0]
    assert first['model'] == 'm' and first['temperature'] == 0.0
    assert 'drei Monate / 3 Monate' in first['messages'][1]['content']
    assert 'Drei Monate.' in first['messages'][1]['content']


def test_judge_faithfulness_is_the_claim_support_ratio():
    provider = _ScriptedProvider(
        '{"claims": [{"claim": "a", "supported": true}, {"claim": "b", "supported": false}]}',
        '{"claims": []}',
        '{"claims": [{"claim": "a", "supported": "yes"}]}',
    )
    judge = LLMJudge(provider)
    assert judge.faithfulness('Antwort', ['Kontext eins']) == 0.5
    assert judge.faithfulness('Antwort', ['Kontext']) is None
    assert judge.faithfulness('Antwort', ['Kontext']) is None
    assert judge.faithfulness('Antwort', []) is None
    assert '[1] Kontext eins' in provider.calls[0]['messages'][1]['content']


# --- end to end through handle_chat --------------------------------------------------------


def test_evaluate_answers_runs_the_real_pipeline_and_scores_every_group(pipeline):
    evaluation = answer_eval.evaluate_answers(GOLDEN, bot_id='legal-support', user=LEGAL_USER)

    assert evaluation.models == ['default']
    answered, abstained = evaluation.results
    assert answered.error is None
    assert answered.answer == _ANSWERS[QUESTION_TERMINATION]
    assert answered.retrieved == ['job-vertrag', 'job-other'] and answered.searches == 1
    assert answered.sources == ['job-vertrag']  # only the cited source is returned
    assert (answered.llm_calls, answered.prompt_tokens, answered.completion_tokens) == (1, 100, 7)
    assert answered.abstained is False and answered.faithfulness_method == 'lexical'
    m = answered.metrics
    assert (m['context_recall'], m['context_precision'], m['mrr']) == (1.0, 1.0, 1.0)
    assert m['key_fact_recall'] == pytest.approx(2 / 3)  # "schriftlich" missing
    assert m['forbidden_rate'] == 0.0
    assert 0 < m['token_f1'] < 1
    assert m['judge_correctness'] is None
    assert m['citation_coverage'] == 0.5 and m['citation_precision'] == 1.0
    assert m['faithfulness'] == 0.5  # "Bitte beachten Sie die Form." is not in any chunk
    assert m['abstention_accuracy'] == 1.0

    assert abstained.guard_reason == 'no_context' and abstained.abstained is True
    assert abstained.metrics['abstention_accuracy'] == 1.0
    assert abstained.metrics['citation_coverage'] is None and abstained.metrics['faithfulness'] is None
    assert abstained.metrics['context_recall'] is None
    assert abstained.prompt_tokens is None and abstained.llm_calls == 0  # guard reply, no LLM call

    summary = evaluation.summary_for('default')
    assert summary.queries == 2 and summary.errors == 0
    assert summary.metrics['abstention_accuracy'] == 1.0 and summary.counts['abstention_accuracy'] == 2
    assert summary.metrics['key_fact_recall'] == pytest.approx(2 / 3) and summary.counts['key_fact_recall'] == 1
    assert summary.metrics['judge_correctness'] is None and summary.counts['judge_correctness'] == 0
    assert summary.prompt_tokens_mean == 100


def test_evaluate_answers_flags_an_answer_where_abstention_was_expected(pipeline):
    item = GoldenItem(query=QUESTION_TERMINATION, answer=AnswerSpec(answerable=False))
    result = answer_eval.evaluate_answers([item], bot_id='legal-support', user=LEGAL_USER).results[0]
    assert result.metrics['abstention_accuracy'] == 0.0


def test_evaluate_answers_with_judge_uses_it_for_correctness_and_faithfulness(pipeline):
    provider = _ScriptedProvider(
        '{"score": 0.9}',
        '{"claims": [{"claim": "drei Monate", "supported": true}]}',
    )
    evaluation = answer_eval.evaluate_answers(
        GOLDEN[:1], bot_id='legal-support', user=LEGAL_USER, judge=LLMJudge(provider, label='judge-ep'),
    )
    result = evaluation.results[0]
    assert result.metrics['judge_correctness'] == 0.9
    assert result.metrics['faithfulness'] == 1.0 and result.faithfulness_method == 'judge'
    assert evaluation.judge == 'judge-ep'
    # The judge's own calls are never counted as the turn's token usage.
    assert result.llm_calls == 1


def test_evaluate_answers_compares_endpoints_and_passes_them_to_the_pipeline():
    seen = []
    trace = ChatTrace(intent='knowledge', confidence=0.9, needs_retrieval=True, needs_tool=False, router_mode='rules')

    def _chat(request):
        seen.append((request.llm_endpoint, request.user.teams, request.bot_id))
        answer = 'Es gelten drei Monate.' if request.llm_endpoint == 'gross' else 'Keine Angabe.'
        return ChatResponse(answer=answer, sources=[_source('job-vertrag')], trace=trace)

    evaluation = answer_eval.evaluate_answers(
        GOLDEN[:1], bot_id='b', endpoints=['klein', 'gross', 'klein'], user=LEGAL_USER, chat_fn=_chat,
    )

    assert seen == [('klein', ['legal'], 'b'), ('gross', ['legal'], 'b')]
    assert evaluation.models == ['klein', 'gross']
    assert evaluation.summary_for('klein').metrics['key_fact_recall'] == 0.0
    assert evaluation.summary_for('gross').metrics['key_fact_recall'] == pytest.approx(1 / 3)
    report = answer_eval.format_text_report(evaluation)
    assert 'klein' in report and 'gross' in report
    assert report.index('Suche (Retrieval)') < report.index('Fachliche Richtigkeit') < report.index(
        'Quellenbezug (Grounding)') < report.index('Abstention') < report.index('Betrieb')


def test_evaluate_answers_records_a_failing_turn_and_keeps_going():
    calls = []

    def _chat(request):
        calls.append(request.message)
        raise retrieval_client.RetrievalUnavailable('down')

    evaluation = answer_eval.evaluate_answers(GOLDEN, bot_id='b', chat_fn=_chat)
    assert len(calls) == 2
    assert all(r.error == 'RetrievalUnavailable: down' for r in evaluation.results)
    # A failed turn is a failed answer, not a skipped one ...
    failed, failed_unanswerable = evaluation.results
    assert failed.metrics['key_fact_recall'] == 0.0 and failed.metrics['token_f1'] == 0.0
    assert failed.metrics['abstention_accuracy'] == 0.0
    assert failed_unanswerable.metrics['abstention_accuracy'] == 0.0
    # ... but nothing was retrieved or cited, so those (and forbidden_rate) stay unscored.
    for name in ('context_recall', 'context_precision', 'mrr', 'forbidden_rate', 'judge_correctness',
                 'citation_coverage', 'citation_precision', 'faithfulness'):
        assert failed.metrics[name] is None and failed_unanswerable.metrics[name] is None
    assert failed_unanswerable.metrics['key_fact_recall'] is None
    summary = evaluation.summaries[0]
    assert summary.errors == 2 and summary.latency_ms_mean is None
    assert summary.metrics['key_fact_recall'] == 0.0 and summary.counts['abstention_accuracy'] == 2
    assert '## Fehler' in answer_eval.format_markdown_report(evaluation)
    assert 'Fehler (2):' in answer_eval.format_text_report(evaluation)


def test_evaluate_answers_aborts_on_configuration_errors(pipeline):
    with pytest.raises(BotNotFoundError):
        answer_eval.evaluate_answers(GOLDEN, bot_id='no-such-bot', user=LEGAL_USER)
    with pytest.raises(FileNotFoundError):
        answer_eval.evaluate_answers(GOLDEN, bot_id='x', chat_fn=lambda request: (_ for _ in ()).throw(
            FileNotFoundError('bots')))
    # legal-support offers no extra endpoints: LlmEndpointNotAllowed is fatal too.
    with pytest.raises(answer_eval.BotPermissionDenied):
        answer_eval.evaluate_answers(GOLDEN, bot_id='legal-support', endpoints=['other'], user=LEGAL_USER)


def test_recorders_are_removed_afterwards_and_inert_outside_a_turn(pipeline):
    original_search = retrieval_client.search
    original_chat = llm_service.FakeLLM.chat
    answer_eval.evaluate_answers(GOLDEN[:1], bot_id='legal-support', user=LEGAL_USER)
    assert retrieval_client.search is original_search
    assert llm_service.FakeLLM.chat is original_chat

    with answer_eval._recorders_installed():
        assert retrieval_client.search is not original_search
        # No active recording: plain pass-through.
        assert llm_service.FakeLLM().chat([{'role': 'user', 'content': QUESTION_TERMINATION}]).content
        recording = answer_eval.TurnRecording()
        token = answer_eval._ACTIVE.set(recording)
        try:
            llm_service.FakeLLM().chat_with_tools([{'role': 'user', 'content': 'x'}], tools=[])
        finally:
            answer_eval._ACTIVE.reset(token)
    assert recording.llm_calls == 1  # chat_with_tools -> chat counted once
    assert retrieval_client.search is original_search


def test_json_and_markdown_reports(pipeline):
    evaluation = answer_eval.evaluate_answers(GOLDEN, bot_id='legal-support', user=LEGAL_USER)
    data = json.loads(json.dumps(answer_eval.to_json(evaluation)))
    assert data['models'] == ['default'] and data['bot_id'] == 'legal-support'
    assert data['metric_groups']['grounding'] == ['citation_coverage', 'citation_precision', 'faithfulness']
    assert data['aggregate']['default']['metrics']['abstention_accuracy'] == 1.0
    assert len(data['queries']) == 2 and data['queries'][0]['retrieved'] == ['job-vertrag', 'job-other']
    markdown = answer_eval.format_markdown_report(evaluation)
    assert '| Metrik | `default` |' in markdown and '| **Abstention** |' in markdown
    assert '| key_fact_recall | 0.667 (n=1) |' in markdown


# --- CLI ------------------------------------------------------------------------------------


_GOLDEN_YAML = f"""
- query: "{QUESTION_TERMINATION}"
  relevant: [{{source_job_id: job-vertrag}}]
  answer:
    must_include: ["drei Monate", "Quartalsende", "schriftlich"]
- query: "{QUESTION_CAR}"
  answer: {{answerable: false}}
"""


def _cli(tmp_path, *extra):
    golden = _write(tmp_path, _GOLDEN_YAML)
    return eval_cli.main(['answers', '--golden', str(golden), '--bot', 'legal-support', '--team', 'legal', *extra])


def test_cli_prints_the_table_and_writes_json_and_markdown(pipeline, tmp_path, capsys):
    out_json, out_md = tmp_path / 'r.json', tmp_path / 'r.md'
    assert _cli(tmp_path, '--json', str(out_json), '--markdown', str(out_md), '--min-correctness', '0.5') == 0
    stdout = capsys.readouterr().out
    assert 'Suche (Retrieval)' in stdout and 'key_fact_recall' in stdout and 'default' in stdout
    assert json.loads(out_json.read_text())['aggregate']['default']['metrics']['key_fact_recall'] == pytest.approx(2 / 3)
    assert out_md.read_text().startswith('# Antwort-Evaluation')


def test_cli_exits_1_below_min_correctness(pipeline, tmp_path, capsys):
    assert _cli(tmp_path, '--min-correctness', '0.9') == 1
    assert 'below --min-correctness' in capsys.readouterr().err


@pytest.mark.parametrize('extra, message', [
    (('--min-correctness', '1.5'), 'between 0 and 1'),
    (('--judge-endpoint', 'default'), 'judge unavailable'),
    (('--endpoint', 'not-offered'), 'LlmEndpointNotAllowed'),
])
def test_cli_exits_2_for_config_errors(pipeline, tmp_path, capsys, extra, message):
    assert _cli(tmp_path, *extra) == 2
    assert message in capsys.readouterr().err


def test_cli_exits_2_for_an_unknown_bot_or_bad_golden_set(pipeline, tmp_path, capsys):
    golden = _write(tmp_path, _GOLDEN_YAML)
    assert eval_cli.main(['answers', '--golden', str(golden), '--bot', 'nope']) == 2
    assert eval_cli.main(['answers', '--golden', str(tmp_path / 'missing.yaml'), '--bot', 'legal-support']) == 2
    no_facts = tmp_path / 'nofacts.yaml'
    no_facts.write_text('- query: q\n  relevant: [{source_job_id: a}]\n', encoding='utf-8')
    assert eval_cli.main(['answers', '--golden', str(no_facts), '--bot', 'legal-support', '--min-correctness', '0.5']) == 2
    err = capsys.readouterr().err
    assert 'BotNotFoundError' in err and 'could not load golden set' in err and 'needs golden entries' in err


def test_cli_builds_the_judge_from_the_central_endpoint(monkeypatch):
    from app.services.chat_config_client import ChatProviderSnapshot

    requested = []

    def _fetch(endpoint):
        requested.append(endpoint)
        return ChatProviderSnapshot(enabled=True, base_url='http://llm', model='judge-1', api_key='k')

    monkeypatch.setattr(eval_cli, 'fetch_chat_provider', _fetch)
    judge = eval_cli.build_judge('pruefer')
    assert requested == ['pruefer'] and judge.label == 'pruefer'
    assert isinstance(judge._provider, llm_service.OpenAICompatibleLLM)

    monkeypatch.setattr(eval_cli, 'fetch_chat_provider', lambda endpoint: ChatProviderSnapshot(enabled=False))
    with pytest.raises(eval_cli.ChatConfigUnavailable, match='deaktiviert'):
        eval_cli.build_judge('pruefer')


# --- review fixes: failed turns, preflight, contexts, memory citations -------------------------


def test_failed_turns_count_as_wrong_answers_for_judge_and_means():
    trace = ChatTrace(intent='knowledge', confidence=0.9, needs_retrieval=True, needs_tool=False, router_mode='rules')
    items = [GOLDEN[0]] * 10

    def _chat(request, calls=[]):  # noqa: B006 -- call counter
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError('LLM timeout')
        return ChatResponse(answer='Drei Monate zum Quartalsende, schriftlich [1].', sources=[_source('job-vertrag')],
                            trace=trace)

    judge = LLMJudge(_ScriptedProvider('{"score": 1.0}', '{"claims": [{"claim": "x", "supported": true}]}'))
    summary = answer_eval.evaluate_answers(items, bot_id='b', chat_fn=_chat, judge=judge).summaries[0]
    assert summary.errors == 9
    assert summary.counts['key_fact_recall'] == 10 and summary.metrics['key_fact_recall'] == pytest.approx(0.1)
    assert summary.counts['judge_correctness'] == 10 and summary.metrics['judge_correctness'] == pytest.approx(0.1)
    assert summary.metrics['abstention_accuracy'] == pytest.approx(0.1)
    # Grounding is only scored for the one real answer.
    assert summary.counts['citation_coverage'] == 1


def test_cli_min_correctness_gate_fails_when_most_turns_error(pipeline, tmp_path, monkeypatch, capsys):
    from app.services import chat as chat_service

    real = chat_service.handle_chat

    def _flaky(request):
        if request.message == QUESTION_TERMINATION:
            raise RuntimeError('LLM timeout')
        return real(request)

    monkeypatch.setattr(chat_service, 'handle_chat', _flaky)
    assert _cli(tmp_path, '--min-correctness', '0.1') == 1
    captured = capsys.readouterr()
    assert 'below --min-correctness' in captured.err and 'Fehler (1):' in captured.out


def test_transient_chat_config_errors_mid_run_are_recorded_and_the_report_is_still_written(
        pipeline, tmp_path, monkeypatch, capsys):
    from app.services import chat as chat_service
    from app.services.chat_config_client import ChatConfigUnavailable

    calls = []

    def _fetch(endpoint):
        calls.append(endpoint)
        if len(calls) == 3:  # preflight, turn 1 ok, turn 2 hits a 503
            raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist vorübergehend nicht verfügbar.')
        return None

    monkeypatch.setattr(chat_service, 'fetch_chat_provider', _fetch)
    out_json = tmp_path / 'r.json'
    assert _cli(tmp_path, '--json', str(out_json)) == 0
    data = json.loads(out_json.read_text())
    assert data['aggregate']['default']['errors'] == 1
    assert data['queries'][1]['error'].startswith('ChatConfigUnavailable')
    assert data['queries'][0]['error'] is None
    assert 'Fehler (1):' in capsys.readouterr().out


def test_preflight_failure_of_the_control_plane_is_fatal(pipeline, monkeypatch):
    from app.services import chat as chat_service
    from app.services.chat_config_client import ChatConfigUnavailable

    def _down(endpoint):
        raise ChatConfigUnavailable('Zentrale Chat-Konfiguration ist vorübergehend nicht erreichbar.')

    monkeypatch.setattr(chat_service, 'fetch_chat_provider', _down)
    with pytest.raises(answer_eval.EvalConfigError, match='nicht erreichbar'):
        answer_eval.evaluate_answers(GOLDEN, bot_id='legal-support', user=LEGAL_USER)


def test_explicit_endpoints_in_standalone_mode_are_a_config_error(pipeline, tmp_path, capsys):
    # general-assistant offers every endpoint ('*'), but without CHAT_CONFIG_BASE_URL none takes effect.
    with pytest.raises(answer_eval.EvalConfigError, match='CHAT_CONFIG_BASE_URL'):
        answer_eval.evaluate_answers(GOLDEN, bot_id='general-assistant', endpoints=['small', 'large'])
    golden = _write(tmp_path, _GOLDEN_YAML)
    assert eval_cli.main(['answers', '--golden', str(golden), '--bot', 'general-assistant',
                          '--endpoint', 'small', '--endpoint', 'large']) == 2
    assert 'EvalConfigError' in capsys.readouterr().err
    # Without --endpoint the bot's own model runs; standalone is fine then.
    assert answer_eval.preflight('general-assistant', None, ChatUser()) == {'default': None}


def test_preflight_rejects_disabled_endpoints_and_warns_about_duplicate_models(monkeypatch, caplog):
    from app.services import chat as chat_service
    from app.services.chat_config_client import ChatProviderSnapshot

    models = {'small': 'm-1', 'large': 'm-2', 'alias': 'm-1'}

    def _fetch(endpoint):
        if endpoint == 'off':
            return ChatProviderSnapshot(enabled=False)
        return ChatProviderSnapshot(enabled=True, base_url='http://llm', model=models.get(endpoint, 'm-0'), api_key='k')

    monkeypatch.setattr(chat_service, 'fetch_chat_provider', _fetch)
    with pytest.raises(answer_eval.EvalConfigError, match='deaktiviert'):
        answer_eval.preflight('general-assistant', ['small', 'off'], ChatUser())
    with caplog.at_level('WARNING', logger='app.services.answer_eval'):
        resolved = answer_eval.preflight('general-assistant', ['small', 'large', 'alias'], ChatUser())
    assert resolved == {'small': 'm-1', 'large': 'm-2', 'alias': 'm-1'}
    assert "small, alias all resolve to model 'm-1'" in caplog.text


def test_preflight_rejects_endpoints_for_an_n8n_bot(monkeypatch):
    from app.services import chat as chat_service

    bot = chat_service.load_bot('general-assistant')
    n8n_bot = bot.model_copy(update={'model': bot.model.model_copy(update={'provider': 'n8n'})})
    monkeypatch.setattr(chat_service, 'load_bot', lambda bot_id: n8n_bot)
    with pytest.raises(answer_eval.EvalConfigError, match='n8n'):
        answer_eval.preflight('general-assistant', ['small'], ChatUser())
    assert answer_eval.preflight('general-assistant', None, ChatUser()) == {'default': None}


def test_preflight_checks_permissions(pipeline):
    with pytest.raises(answer_eval.BotPermissionDenied):
        answer_eval.evaluate_answers(GOLDEN, bot_id='legal-support', user=ChatUser(team='sales', teams=['sales']))


def test_faithfulness_contexts_use_only_chunks_the_llm_received_cited_first():
    from app.schemas.chat import RetrievalHit, RetrievalTrace

    hits = [_hit('job-a', 1, 'eins'), _hit('job-b', 2, 'zwei'), _hit('job-c', 3, 'drei')]
    trace = ChatTrace(intent='knowledge', confidence=0.9, needs_retrieval=True, needs_tool=False, router_mode='rules',
                      retrieval=RetrievalTrace(candidates=3, used=2, hits=[
                          RetrievalHit(rank=1, document_id='doc-1', chunk_id=1, used=True),
                          RetrievalHit(rank=2, document_id='doc-2', chunk_id=2, used=False),  # min_score drop
                          RetrievalHit(rank=3, document_id='doc-3', chunk_id=3, used=True),
                      ]))
    cited_third = Source(source='', document_id='doc-3', chunk_id=3, source_job_id='job-c')
    response = ChatResponse(answer='a', sources=[cited_third], trace=trace)
    assert answer_eval.faithfulness_contexts(response, hits) == ['drei', 'eins']

    # Agent mode: no hit trace -> every recorded hit, cited ones first (so the judge cap keeps them).
    agent = response.model_copy(update={'trace': trace.model_copy(update={'retrieval': None})})
    many = [_hit('job-x', i, f't{i}') for i in range(1, 11)]
    cited = [Source(source='', document_id='doc-9', chunk_id=9), Source(source='', document_id='doc-10', chunk_id=10),
             _source(None, memory_kind='document')]
    contexts = answer_eval.faithfulness_contexts(agent.model_copy(update={'sources': cited}), many)
    assert contexts[:2] == ['t9', 't10'] and len(contexts) == 10


def test_lexical_faithfulness_ignores_chunks_dropped_by_min_score(pipeline, monkeypatch):
    from app.services import chat as chat_service

    # Only the first chunk survives min_score; the answer's second sentence is in the dropped one.
    monkeypatch.setattr(chat_service, '_effective_min_score', lambda bot: 0.5)
    chunks = [
        {**_CHUNKS[QUESTION_TERMINATION][0], 'scores': {'rerank': 0.9}},
        {**_CHUNKS[QUESTION_TERMINATION][1], 'scores': {'rerank': 0.1}},
    ]
    monkeypatch.setitem(_CHUNKS, QUESTION_TERMINATION, chunks)
    monkeypatch.setitem(_ANSWERS, QUESTION_TERMINATION,
                        'Die Kündigungsfrist beträgt drei Monate zum Quartalsende [1]. '
                        'Urlaubsanträge werden im Portal gestellt.')
    result = answer_eval.evaluate_answers(GOLDEN[:1], bot_id='legal-support', user=LEGAL_USER).results[0]
    assert result.retrieved == ['job-vertrag', 'job-other']  # both recorded ...
    assert result.metrics['faithfulness'] == 0.5  # ... but only the used one supports sentences


def test_citation_precision_ignores_knowledge_memory_sources():
    item = GoldenItem(query='q', relevant=['job-a'])
    sources = [_source(None, ref=1, memory_kind='document'), _source('job-a', ref=2), _source('job-b', ref=3)]
    metrics = answer_eval.citation_metrics(item, 'Überblick über das Dokument [1]. Details stehen hier [2]. Mehr dazu [3].',
                                           sources)
    assert metrics['citation_precision'] == 0.5
    only_memory = answer_eval.citation_metrics(item, 'Das Dokument behandelt Fristen [1].', sources)
    assert only_memory['citation_precision'] is None

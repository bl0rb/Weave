"""app/services/ingest_identity.py's `ingest_subject()` -- the
`INGEST_SUBJECT_PREFIX` stripping helper that recovers a `User.oidc_subject`
value's underlying Weave-Ingest user id (see tests/test_ingest_login.py for
the HTTP-level flow that actually writes that namespaced form)."""

from app.services.ingest_identity import INGEST_SUBJECT_PREFIX, ingest_subject


def test_ingest_subject_strips_the_prefix_off_an_ingest_provisioned_value() -> None:
    assert ingest_subject(f'{INGEST_SUBJECT_PREFIX}ingest-user-id-1') == 'ingest-user-id-1'


def test_ingest_subject_returns_none_for_an_unprefixed_value() -> None:
    # e.g. a raw OIDC `sub` from a directly-configured provider -- there is
    # no known Weave-Ingest id for that account, so this must never guess.
    assert ingest_subject('some-oidc-sub') is None


def test_ingest_subject_returns_none_for_no_subject_at_all() -> None:
    assert ingest_subject(None) is None

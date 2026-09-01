"""app/core/security.py's `sign_value`/`unsign_value` -- the plain
HMAC-signed-cookie-value primitive app/api/auth.py's OIDC state cookie is
built on. Pure unit tests, no HTTP client, no database: these two functions
take and return plain strings.

The bottom section covers the issued-at binding specifically: `unsign_value`
must reject a value older than its `max_age_seconds` budget, and that budget
must be enforced from the SIGNED payload itself, not from anything a caller
could influence -- a forged/rolled-back timestamp has to fail the signature
check, not sneak past the age check.
"""

from app.core import security as security_module
from app.core.security import sign_value, unsign_value

# --- round trip / tamper detection (pre-existing behaviour) -------------------


def test_sign_then_unsign_roundtrips_the_original_value():
    signed = sign_value('hello world')
    assert unsign_value(signed) == 'hello world'


def test_unsign_value_rejects_a_value_with_no_signature_separator_at_all():
    assert unsign_value('not-signed-at-all') is None


def test_unsign_value_rejects_an_empty_string():
    assert unsign_value('') is None


def test_unsign_value_rejects_a_value_whose_payload_was_tampered_with():
    signed = sign_value('original-value')
    payload, _, mac = signed.rpartition('.')
    tampered = f'{payload}-tampered.{mac}'
    assert unsign_value(tampered) is None


def test_unsign_value_rejects_a_tampered_mac():
    signed = sign_value('original-value')
    payload, _, mac = signed.rpartition('.')
    flipped_mac = ('0' if mac[0] != '0' else '1') + mac[1:]
    assert unsign_value(f'{payload}.{flipped_mac}') is None


# --- issued-at binding / max-age enforcement (FIX 3) --------------------------


def test_unsign_value_accepts_a_freshly_signed_value_within_the_default_budget():
    signed = sign_value('fresh-value')
    assert unsign_value(signed) == 'fresh-value'


def test_unsign_value_accepts_a_fresh_value_within_an_explicit_max_age():
    signed = sign_value('fresh-value')
    assert unsign_value(signed, max_age_seconds=5) == 'fresh-value'


def test_unsign_value_rejects_a_value_older_than_the_explicit_max_age(monkeypatch):
    """Ages the value by moving the CLOCK forward at verify time, not by
    editing the signed string -- proving expiry is enforced against
    wall-clock time at verification, exactly as a real 10-minutes-later
    replay attempt would be, rather than against anything derivable from
    the value alone."""
    signed = sign_value('going-stale')
    real_time = security_module.time.time
    monkeypatch.setattr(security_module.time, 'time', lambda: real_time() + 3600)
    assert unsign_value(signed, max_age_seconds=600) is None


def test_unsign_value_rejects_a_value_older_than_the_default_budget(monkeypatch):
    signed = sign_value('going-stale-default-budget')
    real_time = security_module.time.time
    stale_time = real_time() + security_module._DEFAULT_MAX_AGE_SECONDS + 1
    monkeypatch.setattr(security_module.time, 'time', lambda: stale_time)
    assert unsign_value(signed) is None


def test_unsign_value_rejects_a_value_one_second_past_the_boundary(monkeypatch):
    signed = sign_value('right-at-the-edge')
    real_time = security_module.time.time
    monkeypatch.setattr(security_module.time, 'time', lambda: real_time() + 601)
    assert unsign_value(signed, max_age_seconds=600) is None


def test_unsign_value_accepts_a_value_still_inside_the_boundary(monkeypatch):
    signed = sign_value('right-at-the-edge')
    real_time = security_module.time.time
    monkeypatch.setattr(security_module.time, 'time', lambda: real_time() + 599)
    assert unsign_value(signed, max_age_seconds=600) == 'right-at-the-edge'


def test_unsign_value_rejects_a_forged_timestamp_despite_an_otherwise_well_formed_mac():
    """Rolling the embedded issued-at back (to dodge the max-age check)
    without recomputing the HMAC over the new payload must fail signature
    verification -- the timestamp is signed data, not a side channel an
    attacker who merely captured the cookie value could rewrite freely."""
    signed = sign_value('payload-value')
    payload, _, mac = signed.rpartition('.')
    issued_at_raw, _, value = payload.partition(':')
    rolled_back_payload = f'{int(issued_at_raw) - 100_000}:{value}'
    forged = f'{rolled_back_payload}.{mac}'
    assert unsign_value(forged, max_age_seconds=600) is None


def test_unsign_value_rejects_a_non_numeric_issued_at():
    """A payload with no parseable timestamp at all (e.g. a value signed
    by some hypothetical older/other format) is rejected outright rather
    than crashing or silently skipping the age check."""
    value = 'some-value'
    forged_payload = f'not-a-number:{value}'
    import hashlib
    import hmac

    from app.core.config import settings

    mac = hmac.new(settings.secret_key.encode('utf-8'), forged_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    assert unsign_value(f'{forged_payload}.{mac}') is None

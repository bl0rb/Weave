"""Redeems a Weave-Ingest login-handoff code for the identity behind it.

The second half of the federated login this gateway offers instead of
configuring an identity provider of its own (app/api/auth.py's
`/v1/auth/ingest/login` + `/callback`): the browser arrives carrying a
one-time code, and this module trades it -- server to server, holding the
shared handoff secret -- for the user it was issued to.

Plain httpx with a short timeout, same reasoning as app/services/oidc.py's
module docstring: `INGEST_API_URL` is an operator-set deployment constant,
never a URL taken from a request, so it is trusted-internal-caller
territory rather than the safe_fetch-hardened kind.

`_client()` is factored out purely so tests can monkeypatch it, exactly
like oidc.py and runtime_client.py already do.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings

_HTTP_TIMEOUT_SECONDS = 10.0
_HANDOFF_SECRET_HEADER = 'X-Weave-Handoff-Secret'


class IngestIdentityError(Exception):
    """Raised when a handoff code cannot be redeemed: Weave-Ingest
    unreachable, an unexpected status, or a malformed response body."""


@dataclass(frozen=True)
class IngestIdentity:
    """One redeemed identity.

    `subject` is Weave-Ingest's own user id and the ONLY field this gateway
    may key a local account on -- `username` and `email` are both things an
    admin can change over there without meaning to hand the account to
    somebody else.
    """

    subject: str
    username: str
    email: str
    team: str | None
    is_admin: bool


def _client() -> httpx.Client:
    return httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS)


def exchange_handoff_code(code: str) -> IngestIdentity:
    url = settings.ingest_api_url.rstrip('/') + '/api/v1/auth/handoff/exchange'
    try:
        with _client() as client:
            response = client.post(
                url,
                json={'code': code},
                headers={_HANDOFF_SECRET_HEADER: settings.ingest_handoff_secret},
            )
    except httpx.RequestError as exc:
        raise IngestIdentityError(f'Weave-Ingest handoff exchange failed: {exc}') from exc

    if response.status_code != 200:
        # Deliberately no distinction between 401 (unknown/expired/replayed
        # code, or a wrong secret) and anything else: the caller turns every
        # one of these into the same generic login failure, and a message
        # that told them apart would be a probing oracle for a code the
        # attacker only half-knows.
        raise IngestIdentityError(f'Weave-Ingest handoff exchange returned HTTP {response.status_code}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise IngestIdentityError('Weave-Ingest handoff exchange returned a malformed body') from exc
    if not isinstance(payload, dict):
        raise IngestIdentityError('Weave-Ingest handoff exchange returned a malformed body')

    subject = payload.get('subject')
    username = payload.get('username')
    email = payload.get('email')
    team = payload.get('team')
    is_admin = payload.get('is_admin')
    if not isinstance(subject, str) or not subject or not isinstance(username, str) or not username:
        raise IngestIdentityError('Weave-Ingest handoff exchange returned no usable identity')

    return IngestIdentity(
        subject=subject,
        username=username,
        email=email if isinstance(email, str) else '',
        team=team if isinstance(team, str) and team else None,
        is_admin=bool(is_admin),
    )

"""SQLAlchemy models for Weave-API's own `weave_api` database (ADR-0004:
one database per service, no shared tables).

Six tables, in three groups:

- `User` / `ApiToken` follow Weave-Ingest's own API-token authentication
  pattern (that service's app/models/models.py User/ApiToken, see
  app/api/auth.py there) but slimmed down for this gateway's current stage:
  no password hash, no Team table -- `team` is a plain denormalized string,
  not a foreign key to a Team table Weave-API doesn't have yet, existing
  purely to carry the propagated team-membership context ADR-0002 describes
  the gateway attaching to a request, not to model team administration.
- `Session` is this gateway's OWN browser-session counterpart to `ApiToken`
  above -- see that model's own docstring and app/api/auth.py (the OIDC
  login/callback/logout endpoints that create/consume it) and
  app/core/auth.py (the `get_current_user` dependency that accepts EITHER
  credential now). `User.oidc_subject` is the other half of ADR-0002's OIDC
  login: the (nullable, unique) link to this user's identity at the
  configured provider, populated on first OIDC login (app/api/auth.py) and
  never by the CLI (app/cli.py), which only ever creates Bearer-token-only
  users. `SessionExchangeCode` is the one-time code behind the cross-origin
  post-login handoff (`return_to`/POST /v1/auth/session/exchange, both in
  app/api/auth.py) a chat UI on a different origin than this gateway uses
  to obtain its own session token -- see that model's own docstring.
- `Conversation` / `Message` are this service's own conversational state
  (README's "Konversations-State-Verwaltung") -- the one thing here no
  other Weave service ever writes to.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    """A Weave-API identity that can hold API tokens and own conversations.

    `disabled` (rather than Weave-Ingest's `is_active`) mirrors the exact
    field name the task spec and app/core/auth.py's docstring use --
    checked on every authenticated request so deactivating an account takes
    effect immediately, without needing to revoke each of its tokens
    individually.
    """

    __tablename__ = 'users'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    team: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    teams: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    @property
    def effective_teams(self) -> list[str]:
        return list(self.teams) if self.teams is not None else ([self.team] if self.team else [])

    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Surfaced by POST /internal/tokens/introspect (app/api/internal.py) so a
    # later service-token-authenticated caller (a future MCP service, per
    # that endpoint's own docstring) can tell an admin identity apart from a
    # regular one without sharing this database. Deliberately no
    # Weave-Ingest-style `UserRole` enum/Team table here yet -- this gateway
    # has exactly one privilege bit to carry today, not a role hierarchy;
    # widen this into a real enum if/when a second privilege level appears.
    # No CLI flag sets this yet (app/cli.py's create-user has none) --
    # flip it directly in the database until an admin-management surface
    # exists.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # This user's subject identifier at the configured OIDC provider
    # (app/services/oidc.py's validated ID-token `sub` claim) -- `None` for
    # a user created only via app/cli.py, which never sets this. `unique`
    # so two logins can never race their way onto the same local account
    # (app/api/auth.py's oidc_callback looks this up as the sole key
    # deciding which User an OIDC login resolves to, never by username/
    # email/any other claim). No foreign key to a providers table --
    # there is only ever the one, statically-configured provider
    # (`settings.oidc_issuer`), unlike Weave-Ingest's own per-provider
    # `oidc_provider_id` + `oidc_subject` pair.
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    api_tokens: Mapped[list['ApiToken']] = relationship(back_populates='user', cascade='all, delete-orphan')
    sessions: Mapped[list['Session']] = relationship(back_populates='user', cascade='all, delete-orphan')
    session_exchange_codes: Mapped[list['SessionExchangeCode']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )
    conversations: Mapped[list['Conversation']] = relationship(back_populates='user', cascade='all, delete-orphan')


class ApiToken(Base):
    """Personal bearer token for programmatic API access.

    Same never-store-the-raw-value discipline as Weave-Ingest's own
    ApiToken: only sha256(token) is persisted, in `token_sha256` (see
    app/core/auth.py's lookup and app/cli.py's issuance). Unlike
    Weave-Ingest's version there is no `token_prefix` column -- app/cli.py
    is the only issuer today and there is no "list my tokens" UI yet that
    would need a recognizable-without-the-secret prefix; add one if/when
    that UI exists.
    """

    __tablename__ = 'api_tokens'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Touched at most once/60s by app/core/auth.py's current_user
    # dependency, to bound write volume for a token used on every request
    # of a hot integration (same threshold and reasoning as Weave-Ingest's
    # own API_TOKEN_TOUCH_THRESHOLD).
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates='api_tokens')


class Session(Base):
    """A browser session created by a successful OIDC login
    (app/api/auth.py's `oidc_callback`) and consumed by
    app/core/auth.py's `get_current_user` (the cookie-fallback path, tried
    when the request carries no Bearer token). Opaque, DB-backed, exactly
    like `ApiToken` above -- the raw token lives only in the client's
    httpOnly cookie and this row's own `token_hash` (sha256, never the raw
    value) is all that's ever persisted, so a leaked database row alone
    cannot be replayed as a cookie, and logout (`POST /v1/auth/logout`)
    revokes instantly by deleting the row rather than waiting out an
    expiry.

    Fixed lifetime (`expires_at`, set once at creation -- see
    app/api/auth.py's `_SESSION_LIFETIME`), unlike Weave-Ingest's own
    sliding-window session: this gateway's session model doesn't need that
    refinement yet, just a hard cap past which the cookie stops working and
    the row is lazily deleted the next time it's looked up
    (`get_current_user`'s expired-session branch) or on logout.
    """

    __tablename__ = 'sessions'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates='sessions')


class SessionExchangeCode(Base):
    """A one-time code minted by `oidc_callback` (app/api/auth.py) when the
    login carries an allowlisted `return_to` -- the cross-origin handoff a
    chat UI on a DIFFERENT origin than this gateway uses to obtain its OWN
    session token via POST /v1/auth/session/exchange, since the gateway's
    session cookie set on the callback response (still set unconditionally,
    for the same-origin case) is scoped to THIS gateway's own origin and
    never reaches that UI's browser storage on its own.

    Deliberately a SEPARATE table from `Session` above, not extra columns
    on it, and deliberately keyed to `user_id` rather than to a specific
    `Session` row: this row's only job is "prove, once, that this browser
    just completed a real OIDC login as this user", not to carry any
    already-created session's own secret forward. Redeeming one (the
    exchange endpoint) mints a BRAND NEW `Session` the exact same way
    `_create_session` does for the cookie -- so nothing here ever needs to
    hold a second copy (encrypted or otherwise) of a raw session token
    at rest; the only secret this table ever stores is this code's own
    hash, exactly like `Session.token_hash`/`ApiToken.token_sha256`.

    Short-lived (60 seconds, `app/api/auth.py`'s `_EXCHANGE_CODE_TTL`) and
    single-use (`used_at`, set atomically by the exchange endpoint's own
    UPDATE ... WHERE used_at IS NULL AND expires_at > now() -- see that
    endpoint's docstring for why this closes the double-redemption race a
    plain read-then-write would leave open). Never cleaned up by a
    background job, same as `Session`'s own lazy-expiry discipline: a row
    past its `expires_at` (or already `used_at`) simply can never satisfy
    that WHERE clause again, so it just sits inert until `User` deletion
    cascades it away.
    """

    __tablename__ = 'session_exchange_codes'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates='session_exchange_codes')


class MessageRole(str, enum.Enum):
    USER = 'user'
    ASSISTANT = 'assistant'


class Conversation(Base):
    """One chat thread against a specific bot.

    `bot_id` is an opaque string, not a foreign key -- Weave-Runtime owns
    bot configuration (README's Nicht-Ziele: "Kein Bot-Management"), so
    Weave-API has no local `bots` table to reference. Every lookup
    (GET /v1/conversations/{id}, app/api/conversations.py) scopes on
    `Conversation.user_id == current_user.id` in the query itself, so one
    user can never read another's conversation by guessing an id (404, not
    403 -- same IDOR discipline as Weave-Ingest's job-authz surface).
    """

    __tablename__ = 'conversations'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    bot_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[User] = relationship(back_populates='conversations')
    messages: Mapped[list['Message']] = relationship(
        back_populates='conversation', cascade='all, delete-orphan', order_by='Message.id'
    )


class Message(Base):
    """One turn of a Conversation.

    `sources`/`trace` are opaque JSON blobs carried verbatim from
    Weave-Runtime's chat response (README's Output contract: `{ answer,
    sources[], trace, ... }`) -- retrieval citations and pipeline trace
    respectively. Weave-API never interprets their contents, only persists
    and replays them via GET /v1/conversations/{id}.
    """

    __tablename__ = 'messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name='message_role', native_enum=False, validate_strings=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation: Mapped[Conversation] = relationship(back_populates='messages')

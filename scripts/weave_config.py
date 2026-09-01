#!/usr/bin/env python3
"""Render and validate the Weave platform's declarative configuration.

Reads weave.yaml (see that file's own header for the full schema) and
supports two subcommands:

    weave_config.py render
        Writes deploy/.env for the docker-compose stack. Every value marked
        `secret: true` in weave.yaml is read from an OS environment variable
        at render time -- never written into weave.yaml itself. Every value
        shared across services is computed exactly once and mirrored into
        every distinct variable name that needs it.

    weave_config.py check
        Validates an existing rendered configuration (deploy/.env by
        default) for the contradictions docs/betrieb.md section 4-7
        describes: shared values that have drifted, required values that
        are missing, SEARCH_TOP_K exceeding RERANKER_MAX_DOCUMENTS, an
        EMBEDDING_DIMENSION that does not match a known embedding model, a
        CORS_ORIGINS that names a different port than the one the Ingest
        frontend is actually published on, and a half-configured federated
        login (the four settings across three services that have to agree
        before anyone can sign in to the chat with their Ingest account).
        Exits non-zero on any finding. Never prints a secret's actual value
        -- only a short fingerprint, so the check's output itself is safe to
        paste into a chat or a ticket.

Only the standard library plus PyYAML -- no dependency on any of the nine
services this configures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "weave.yaml"
DEFAULT_ENV_FILE = REPO_ROOT / "deploy" / ".env"

# Embedding models with a publicly documented, fixed vector width. Used only
# by `check`'s EMBEDDING_DIMENSION validation -- a model absent from this map
# (including the 'fake-embed' default, whose dimension is arbitrary by
# design) is simply not checked, never flagged as wrong.
KNOWN_EMBEDDING_DIMENSIONS: dict[str, int] = {
    "intfloat/multilingual-e5-small": 384,
    "intfloat/multilingual-e5-base": 768,
    "intfloat/multilingual-e5-large": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


# Hosts for which `check` treats a CORS origin as "this machine" -- see the
# CORS_ORIGINS/FRONTEND_PORT check below.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _split_origin(origin: str) -> tuple[str, str]:
    """Split an origin into (host, port); port is '' when none is given."""
    netloc = origin.split("://", 1)[-1].split("/", 1)[0]
    host, sep, port = netloc.rpartition(":")
    if not sep or (host.startswith("[") and not host.endswith("]")):
        return netloc, ""
    return host, port


class ConfigError(Exception):
    """Raised for a problem in weave.yaml itself (not a check finding)."""


@dataclass
class Item:
    """One resolvable setting: either a shared value (with `targets`) or a
    single service's own setting (`targets` has exactly one entry, filled in
    by the caller from the service/key it was read from)."""

    key: str
    secret: bool
    required: bool
    value_type: str
    targets: list[tuple[str, str]]  # (service, rendered_var_name)
    literal: Any = None  # only for secret: false
    env_var: str | None = None  # only for secret: true
    comment: str = ""
    manual_targets: list[str] = field(default_factory=list)

    @property
    def is_shared(self) -> bool:
        return len(self.targets) > 1 or len({t[1] for t in self.targets}) > 1


def _parse_item(key: str, raw: dict, default_target: tuple[str, str] | None) -> Item:
    secret = bool(raw.get("secret", False))
    required = bool(raw.get("required", False))
    value_type = raw.get("type", "string")
    comment = raw.get("comment", "") or ""
    manual_targets = raw.get("manual_targets", []) or []

    if "targets" in raw:
        targets = [(t["service"], t["var"]) for t in raw["targets"]]
    elif default_target is not None:
        targets = [default_target]
    else:
        raise ConfigError(f"{key}: neither 'targets' nor a default target given")

    if secret:
        env_var = raw.get("env_var")
        if not env_var:
            raise ConfigError(f"{key}: secret entries need 'env_var'")
        return Item(
            key=key, secret=True, required=required, value_type=value_type,
            targets=targets, env_var=env_var, comment=comment,
            manual_targets=manual_targets,
        )

    if "value" not in raw:
        raise ConfigError(f"{key}: non-secret entries need 'value'")
    return Item(
        key=key, secret=False, required=required, value_type=value_type,
        targets=targets, literal=raw["value"], comment=comment,
        manual_targets=manual_targets,
    )


def load_items(config_path: Path) -> list[Item]:
    """Parse weave.yaml into a flat list of Items (shared + per-service)."""
    with config_path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}

    items: list[Item] = []
    for key, raw in (doc.get("shared") or {}).items():
        items.append(_parse_item(key, raw, default_target=None))

    for service, block in (doc.get("services") or {}).items():
        for key, raw in (block.get("settings") or {}).items():
            items.append(_parse_item(key, raw, default_target=(service, key)))

    return items


def serialize(value: Any, value_type: str) -> str:
    """Turn a resolved Python value into the exact string an .env file (and
    therefore docker compose's variable substitution) should carry."""
    if value_type == "json_list":
        if isinstance(value, str):
            # A raw override string, e.g. from os.environ. Validate it: a
            # shell that sourced a previous .env strips the quotes, turning
            # ["http://x"] into [http://x] -- which is not JSON, and which
            # pydantic-settings rejects at container start with a stack
            # trace that names the field but not the cause. Failing here
            # says what actually happened.
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f'Override ist kein gueltiges JSON: {value!r}. '
                    f'Eine Liste muss als JSON-Array mit Anfuehrungszeichen '
                    f'geschrieben werden, z.B. \'["http://localhost:3000"]\'. '
                    f'(Tipp: beim Uebernehmen aus einer .env entfernt die '
                    f'Shell die Anfuehrungszeichen.)'
                ) from exc
            return value
        return json.dumps(value, separators=(",", ":"))
    if value_type == "bool":
        if isinstance(value, str):
            return value
        return "true" if value else "false"
    return str(value)


def fingerprint(value: str) -> str:
    if not value:
        return "(leer)"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def resolve_for_render(item: Item, environ: dict[str, str]) -> tuple[str, list[str]]:
    """Resolve one Item's value for `render`.

    Returns (serialized_value, missing_required_env_vars).
    """
    if item.secret:
        raw = environ.get(item.env_var, "")
        if not raw and item.required:
            return "", [item.env_var]
        return raw, []

    # Non-secret: the literal default from weave.yaml, unless an operator
    # exported an environment variable matching the RENDERED variable name
    # (the first target's var name) to override it for this machine.
    override_name = item.targets[0][1]
    if override_name in environ:
        return environ[override_name], []
    return serialize(item.literal, item.value_type), []


def render(config_path: Path, out_path: Path, environ: dict[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    try:
        items = load_items(config_path)
    except ConfigError as exc:
        print(f"Fehler in {config_path}: {exc}", file=sys.stderr)
        return 2

    rendered: dict[str, str] = {}
    missing: list[str] = []
    collisions: list[str] = []

    for item in items:
        value, item_missing = resolve_for_render(item, environ)
        missing.extend(item_missing)
        for _service, var in item.targets:
            if var in rendered and rendered[var] != value:
                collisions.append(
                    f"{var}: '{item.key}' ergibt widerspruechliche Werte "
                    f"fuer denselben .env-Schluessel"
                )
            rendered[var] = value

    if missing:
        print(
            "Fehlende Pflicht-Geheimnisse -- vor dem Rendern in der Umgebung setzen:",
            file=sys.stderr,
        )
        for name in sorted(set(missing)):
            print(f"  export {name}=...", file=sys.stderr)
        return 1

    if collisions:
        print("Widerspruechliche Werte in weave.yaml selbst:", file=sys.stderr)
        for c in collisions:
            print(f"  {c}", file=sys.stderr)
        return 2

    lines = [
        "# Generated by scripts/weave_config.py render from weave.yaml.",
        "# DO NOT EDIT BY HAND -- edit weave.yaml and re-render instead.",
        "# DO NOT COMMIT -- this file carries real secrets (see .gitignore).",
        "",
    ]
    for var in sorted(rendered):
        lines.append(f"{var}={rendered[var]}")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    manual_notes = [
        (item.key, note)
        for item in items
        if item.manual_targets
        for note in item.manual_targets
    ]
    print(f"Geschrieben: {out_path} ({len(rendered)} Variablen).")
    if manual_notes:
        print("\nManuelle Nacharbeit noetig (nicht per .env abbildbar):")
        for key, note in manual_notes:
            print(f"  {key}: {note.strip()}")
    return 0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass
class Finding:
    message: str


def check(
    config_path: Path,
    default_env_file: Path,
    service_env_overrides: dict[str, Path] | None = None,
) -> list[Finding]:
    service_env_overrides = service_env_overrides or {}
    items = load_items(config_path)

    file_cache: dict[Path, dict[str, str]] = {}

    def values_for(path: Path) -> dict[str, str]:
        if path not in file_cache:
            file_cache[path] = parse_env_file(path)
        return file_cache[path]

    def lookup(service: str, var: str) -> str | None:
        path = service_env_overrides.get(service, default_env_file)
        return values_for(path).get(var)

    def source_label(service: str, var: str) -> str:
        path = service_env_overrides.get(service, default_env_file)
        return f"{service}.{var} ({path})"

    findings: list[Finding] = []

    # --- 1) shared values that have drifted -------------------------------
    for item in items:
        if not item.is_shared:
            continue
        resolved: list[tuple[str, str, str | None]] = [
            (service, var, lookup(service, var)) for service, var in item.targets
        ]
        distinct = {v for _s, _var, v in resolved if v is not None}
        if len(distinct) > 1:
            if item.secret:
                shown = [
                    f"{source_label(s, v)} = {fingerprint(val or '')}"
                    for s, v, val in resolved
                ]
            else:
                shown = [
                    f"{source_label(s, v)} = {val!r}"
                    for s, v, val in resolved
                ]
            findings.append(Finding(
                f"Geteilter Wert '{item.key}' laeuft auseinander:\n    "
                + "\n    ".join(shown)
            ))

    # --- 2) required values missing ----------------------------------------
    for item in items:
        if not item.required:
            continue
        for service, var in item.targets:
            val = lookup(service, var)
            if not val:
                findings.append(Finding(
                    f"Pflichtwert fehlt: {source_label(service, var)} ist nicht gesetzt."
                ))

    # --- 3) SEARCH_TOP_K vs RERANKER_MAX_DOCUMENTS --------------------------
    rerank_provider = lookup("retrieval", "RERANK_PROVIDER")
    if rerank_provider == "api":
        top_k_raw = lookup("retrieval", "SEARCH_TOP_K")
        max_docs_raw = lookup("reranker", "RERANKER_MAX_DOCUMENTS")
        if top_k_raw and max_docs_raw:
            try:
                top_k = int(top_k_raw)
                max_docs = int(max_docs_raw)
            except ValueError:
                findings.append(Finding(
                    "SEARCH_TOP_K oder RERANKER_MAX_DOCUMENTS ist keine Zahl -- "
                    "kann nicht geprueft werden."
                ))
            else:
                if top_k > max_docs:
                    findings.append(Finding(
                        f"retrieval.SEARCH_TOP_K ({top_k}) ist groesser als "
                        f"reranker.RERANKER_MAX_DOCUMENTS ({max_docs}) bei "
                        f"RERANK_PROVIDER=api: der Reranker antwortet mit 413, "
                        f"jede Suche faellt still auf die unrerankte Reihenfolge "
                        f"zurueck (docs/betrieb.md Abschnitt 7)."
                    ))

    # --- 4) EMBEDDING_DIMENSION vs known model ------------------------------
    for service in ("knowledge", "retrieval"):
        model = lookup(service, "EMBEDDING_MODEL")
        dimension_raw = lookup(service, "EMBEDDING_DIMENSION")
        if not model or not dimension_raw:
            continue
        expected = KNOWN_EMBEDDING_DIMENSIONS.get(model)
        if expected is None:
            continue
        try:
            actual = int(dimension_raw)
        except ValueError:
            findings.append(Finding(
                f"{service}.EMBEDDING_DIMENSION ist keine Zahl ({dimension_raw!r})."
            ))
            continue
        if actual != expected:
            findings.append(Finding(
                f"{service}.EMBEDDING_DIMENSION ({actual}) passt nicht zum "
                f"konfigurierten Modell {model!r} (erwartet: {expected}). "
                f"Eine falsche Dimension braucht eine Migration und einen "
                f"vollstaendigen Reindex (docs/betrieb.md Abschnitt 7.4/7.5), "
                f"kein reiner Env-Var-Flip."
            ))

    # --- 5) CORS_ORIGINS vs the port the frontend is published on -----------
    # CORS_ORIGINS is Ingest's CSRF origin check as well, so it has to name
    # the exact origin the browser loaded the frontend from -- and that
    # origin's port is FRONTEND_PORT, which operators routinely override on
    # a machine where 3000 is already taken, without thinking of CORS.
    # Only checked for an all-localhost origin list: as soon as one origin
    # names a real host, the frontend sits behind a reverse proxy and
    # FRONTEND_PORT says nothing about the port the browser sees.
    frontend_port = lookup("ingest", "FRONTEND_PORT")
    origins_raw = lookup("ingest", "CORS_ORIGINS")
    if frontend_port and origins_raw:
        try:
            origins = json.loads(origins_raw)
        except json.JSONDecodeError:
            origins = None
            findings.append(Finding(
                f"ingest.CORS_ORIGINS ist kein gueltiges JSON-Array "
                f"({origins_raw}) -- pydantic-settings verweigert den Start."
            ))
        if isinstance(origins, list) and origins:
            parsed = [_split_origin(str(o)) for o in origins]
            all_local = all(host in LOCAL_HOSTS for host, _port in parsed)
            hits_port = any(port == str(frontend_port) for _host, port in parsed)
            if all_local and not hits_port:
                findings.append(Finding(
                    f"ingest.CORS_ORIGINS ({origins_raw}) nennt nicht den Port, "
                    f"auf dem das Frontend veroeffentlicht wird "
                    f"(ingest.FRONTEND_PORT = {frontend_port}). Der Browser "
                    f"bekommt dann auf jede Anfrage 'Disallowed CORS origin' "
                    f"(Preflight 400, danach ein 401 ohne CORS-Header); "
                    f"Einrichtung und Login sind unmoeglich "
                    f"(docs/betrieb.md Abschnitt 6)."
                ))

    # --- 6) the federated-login chain -------------------------------------
    # Four settings across three services have to agree before a person can
    # sign in to the chat with their Weave-Ingest account. Every way of
    # getting it half-right fails quietly: a 404 on click, a 503 on the
    # exchange, or -- the nastiest -- a login that succeeds and then drops
    # the user on the gateway instead of back in the chat.
    handoff_callback = lookup("ingest", "HANDOFF_CALLBACK_URL")
    ingest_login_url = lookup("api", "INGEST_LOGIN_URL")
    ingest_api_url = lookup("api", "INGEST_API_URL")
    handoff_secret = lookup("ingest", "HANDOFF_SECRET")
    if handoff_callback or ingest_login_url:
        if not handoff_callback:
            findings.append(Finding(
                "api.INGEST_LOGIN_URL ist gesetzt, ingest.HANDOFF_CALLBACK_URL aber "
                "nicht: Weave-Ingest weiss dann nicht, wohin es den Anmeldecode "
                "zurueckgeben soll, und antwortet auf den ganzen Handoff mit 404."
            ))
        if not ingest_login_url:
            findings.append(Finding(
                "ingest.HANDOFF_CALLBACK_URL ist gesetzt, api.INGEST_LOGIN_URL aber "
                "nicht: die Anmeldung ueber Weave-Ingest wird nie gestartet, der "
                "Knopf im Chat fuehrt ins 404."
            ))
        if not ingest_api_url:
            findings.append(Finding(
                "api.INGEST_API_URL ist leer: Weave-API kann den Anmeldecode nicht "
                "einloesen, die Anmeldung endet mit 404."
            ))
        if not handoff_secret:
            findings.append(Finding(
                "WEAVE_HANDOFF_SECRET ist nicht gesetzt, obwohl der Login-Handoff "
                "konfiguriert ist: Weave-Ingest lehnt jede Einloesung mit 503 ab "
                "(fail-closed -- ein leeres Secret waere ein offenes "
                "Identitaets-Orakel)."
            ))
        if (lookup("chat", "WEAVE_API_INGEST_LOGIN_ENABLED") or "").strip().lower() not in ("true", "1"):
            findings.append(Finding(
                "chat.WEAVE_API_INGEST_LOGIN_ENABLED steht nicht auf 'true': der "
                "Knopf 'Mit Weave anmelden' erscheint nicht, obwohl die Anmeldung "
                "dahinter funktioniert."
            ))

        app_base = (lookup("chat", "CHAT_APP_BASE_URL") or "").rstrip("/")
        allowed_raw = lookup("api", "OIDC_POST_LOGIN_ALLOWED_URLS")
        if app_base and allowed_raw:
            expected_callback = f"{app_base}/api/auth/sso/callback"
            try:
                allowed = json.loads(allowed_raw)
            except json.JSONDecodeError:
                findings.append(Finding(
                    f"api.OIDC_POST_LOGIN_ALLOWED_URLS ist kein gueltiges JSON-Array "
                    f"({allowed_raw})."
                ))
            else:
                if isinstance(allowed, list) and expected_callback not in allowed:
                    findings.append(Finding(
                        f"api.OIDC_POST_LOGIN_ALLOWED_URLS enthaelt {expected_callback!r} "
                        f"nicht. Die Anmeldung gelingt dann und laesst den Nutzer "
                        f"trotzdem auf dem Gateway statt zurueck im Chat -- ohne "
                        f"Fehlermeldung, weil ein nicht erlaubtes return_to "
                        f"absichtlich still ignoriert wird."
                    ))

    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_service_env_args(pairs: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(
                f"--service-env erwartet DIENST=PFAD, bekommen: {pair!r}"
            )
        service, _, path_str = pair.partition("=")
        result[service.strip()] = Path(path_str.strip())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render/validate the Weave platform's weave.yaml configuration."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Pfad zu weave.yaml (Default: {DEFAULT_CONFIG_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    render_p = sub.add_parser("render", help="deploy/.env aus weave.yaml erzeugen")
    render_p.add_argument(
        "--out", type=Path, default=DEFAULT_ENV_FILE,
        help=f"Zieldatei (Default: {DEFAULT_ENV_FILE})",
    )

    check_p = sub.add_parser("check", help="eine bestehende Konfiguration pruefen")
    check_p.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE,
        help=f"Zu pruefende .env-Datei (Default: {DEFAULT_ENV_FILE})",
    )
    check_p.add_argument(
        "--service-env", action="append", default=[],
        metavar="DIENST=PFAD",
        help=(
            "Fuer einen einzelnen Dienst eine ANDERE .env-Datei lesen als "
            "--env-file (z.B. beim Betrieb ohne docker compose, wo jeder "
            "Dienst sein eigenes .env hat). Mehrfach angebbar."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "render":
        return render(args.config, args.out)

    if args.command == "check":
        overrides = _parse_service_env_args(args.service_env)
        findings = check(args.config, args.env_file, overrides)
        if not findings:
            print("OK -- keine Widersprueche gefunden.")
            return 0
        print(f"{len(findings)} Problem(e) gefunden:\n")
        for i, finding in enumerate(findings, 1):
            print(f"{i}. {finding.message}\n")
        return 1

    parser.error(f"unbekannter Befehl: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

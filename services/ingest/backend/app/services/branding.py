"""Branding (Administration › Betrieb › Erscheinungsbild, app/api/branding.py):
colour normalisation, the derived light/dark palette, the content-based
validation of an uploaded logo or busy animation, and the per-process cache
of the public projection the portal and Weave-API read before login.

Uploaded SVGs are served inline (as `<img>` source) on the API origin, so
they are checked here with the standard library only: no DOCTYPE/ENTITY
(checked on the raw bytes, before any parser runs), no processing
instructions, no scripting elements or event-handler attributes, no
external references. The serving route adds a sandboxing CSP on top.
"""

from __future__ import annotations

import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import BrandingAsset, BrandingConfig
from app.schemas.branding import BrandingAdmin, BrandingAssetInfo, BrandingPublic

DEFAULT_PRIMARY = '#1d6a53'
ASSET_KINDS = ('logo', 'busy')
MAX_ASSET_BYTES = 1024 * 1024
ASSET_EXTENSIONS = {'image/svg+xml': 'svg', 'image/gif': 'gif', 'image/png': 'png'}
COLOR_ERROR = 'Farbe als Hex-Code angeben, z. B. #1d6a53.'

_HEX_COLOR = re.compile(r'#([0-9a-f]{3}|[0-9a-f]{6})', re.IGNORECASE)


def normalize_color(value: str | None) -> str | None:
    """`#rgb` or `#rrggbb` (any case) -> lowercase `#rrggbb`; None stays
    None (= the built-in colour). Anything else raises ValueError."""
    if value is None:
        return None
    match = _HEX_COLOR.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(COLOR_ERROR)
    digits = match.group(1).lower()
    if len(digits) == 3:
        digits = ''.join(ch * 2 for ch in digits)
    return f'#{digits}'


# --- Palette -----------------------------------------------------------------

def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a `#rrggbb` colour."""
    red, green, blue = (_channel(value) for value in _rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def mix(color: str, target: str, amount: float) -> str:
    """Per-channel sRGB blend `c + (target - c) * amount`, rounded half up."""
    return '#' + ''.join(
        f'{math.floor(c + (t - c) * amount + 0.5):02x}' for c, t in zip(_rgb(color), _rgb(target))
    )


_MIX_STEPS = tuple(step / 20 for step in range(21))  # 0, 0.05, ..., 1.0


def _readable_mix(color: str, target: str, background: str) -> str:
    """`color` moved towards `target` by the smallest step that reaches a
    contrast of 4.5 against `background` (black/white always do at 1.0)."""
    for amount in _MIX_STEPS:
        candidate = mix(color, target, amount)
        if contrast_ratio(candidate, background) >= 4.5:
            return candidate
    return mix(color, target, 1.0)


def derive_palette(primary_color: str | None, accent_color: str | None) -> dict | None:
    """The CSS tokens both frontends set from the two admin colours
    (normalised `#rrggbb` or None); None when neither is set, so the
    built-in look stays untouched."""
    if primary_color is None and accent_color is None:
        return None
    primary = primary_color or DEFAULT_PRIMARY
    accent = accent_color or primary

    light_soft = mix(accent, '#ffffff', 0.88)
    light = {
        'accent': primary,
        'accent_hover': mix(primary, '#000000', 0.15),
        'accent_ink': _readable_mix(accent, '#000000', light_soft),
        'accent_soft': light_soft,
        'on_accent': '#ffffff' if contrast_ratio('#ffffff', primary) >= contrast_ratio('#14221d', primary) else '#14221d',
    }
    dark_accent = _readable_mix(primary, '#ffffff', '#1f2024')
    dark_soft = mix(accent, '#1f2024', 0.80)
    dark = {
        'accent': dark_accent,
        'accent_hover': mix(dark_accent, '#ffffff', 0.12),
        'accent_ink': _readable_mix(accent, '#ffffff', dark_soft),
        'accent_soft': dark_soft,
        'on_accent': '#0c1511' if contrast_ratio('#0c1511', dark_accent) >= contrast_ratio('#ffffff', dark_accent) else '#ffffff',
    }
    return {'light': light, 'dark': dark}


# --- Upload validation -------------------------------------------------------

_SVG_NS = 'http://www.w3.org/2000/svg'
_FORBIDDEN_ELEMENTS = frozenset({
    'script', 'foreignobject', 'iframe', 'embed', 'object', 'audio', 'video', 'handler', 'listener',
})
# Every element that takes an attributeName, not only set/animate.
_ANIMATION_ELEMENTS = frozenset({'set', 'animate', 'animatecolor', 'animatetransform'})
_XML_BASE = '{http://www.w3.org/XML/1998/namespace}base'
_ALLOWED_HREF_PREFIXES = ('#', 'data:image/png;base64,', 'data:image/gif;base64,', 'data:image/jpeg;base64,')
_MAX_SVG_ELEMENTS = 10_000
_MAX_SVG_DEPTH = 64
_FEED_CHARS = 64 * 1024
_XML_DECLARATION_ENCODING = re.compile(r'<\?xml[^>]*?\sencoding\s*=\s*["\']([^"\']*)["\']')
# CSS escapes (`\75 rl(`) are resolved before the checks below.
_CSS_ESCAPE = re.compile(r'\\(?:([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(.))', re.DOTALL)
# Possessive, so backtracking cannot skip the quote and dodge the `#` check.
# image-set() loads its plain strings as URLs, so it is refused outright.
_EXTERNAL_CSS_URL = re.compile(r'url\(\s*+[\'"]?+\s*+(?!#)|image-set\(')

UNSUPPORTED_TYPE = 'Nur SVG, GIF oder PNG.'


def _unsupported() -> HTTPException:
    return HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=UNSUPPORTED_TYPE)


def _rejected(reason: str) -> HTTPException:
    return HTTPException(status_code=422, detail=reason)


def _local(name: str) -> str:
    return name.rsplit('}', 1)[-1].lower()


def _shown(name: str) -> str:
    return name.rsplit('}', 1)[-1][:40]


def _css_unescape(match: re.Match) -> str:
    if match.group(1) is None:
        return match.group(2)
    code = int(match.group(1), 16)
    return chr(code) if 0 < code <= 0x10FFFF else '�'


def _unsafe_css(value: str, *, full: bool) -> bool:
    """`full` (style attribute, <style> text): @import, javascript:,
    expression( and any url() not pointing into the document. Otherwise
    (presentation attributes such as fill/filter/mask) only the url()."""
    normalized = _CSS_ESCAPE.sub(_css_unescape, value).lower()
    if _EXTERNAL_CSS_URL.search(normalized):
        return True
    return full and ('@import' in normalized or 'javascript:' in normalized or 'expression(' in normalized)


def _check_svg_element(element: ET.Element) -> None:
    local = _local(element.tag)
    if local in _FORBIDDEN_ELEMENTS:
        raise _rejected(f'Die SVG-Datei enthält das unzulässige Element <{_shown(element.tag)}>.')
    if local in _ANIMATION_ELEMENTS:
        target = (element.get('attributeName') or '').strip().lower()
        # Any prefix bound to the XLink namespace names the same attribute.
        local_target = target.rsplit(':', 1)[-1]
        if local_target == 'href' or local_target.startswith('on'):
            raise _rejected(f'Die SVG-Datei animiert das unzulässige Attribut „{target[:40]}“.')
    for name, value in element.attrib.items():
        attribute = _local(name)
        if attribute.startswith('on'):
            raise _rejected(f'Die SVG-Datei enthält das unzulässige Attribut „{_shown(name)}“ (Ereignis-Handler).')
        if attribute == 'href' or name == _XML_BASE:
            # xml:base would turn even a `#…` reference into an external one.
            if name == _XML_BASE or not value.strip().lower().startswith(_ALLOWED_HREF_PREFIXES):
                raise _rejected(
                    'Die SVG-Datei enthält einen externen Verweis (href). Erlaubt sind nur Verweise innerhalb '
                    'der Datei (#…) und eingebettete PNG-, GIF- oder JPEG-Bilder.'
                )
        elif _unsafe_css(value, full=attribute == 'style'):
            raise _rejected(
                'Die SVG-Datei enthält unzulässiges CSS (@import, javascript:, expression( oder eine externe url()).'
            )


def _check_svg(data: bytes) -> None:
    """Accepts a UTF-8 SVG document without active or external content;
    raises 415 for something that is not an SVG at all, 422 naming the
    reason for an SVG that is refused."""
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        if b'<svg' in data.lower():
            raise _rejected('Die SVG-Datei muss UTF-8-kodiert sein.') from None
        raise _unsupported() from None
    text = text.removeprefix('\ufeff')
    if not text.lstrip().startswith('<'):
        raise _unsupported()
    # Before any parser sees the document, so no entity can be expanded.
    lowered = data.lower()
    if b'<!doctype' in lowered or b'<!entity' in lowered:
        raise _rejected('Die SVG-Datei darf keine DOCTYPE- oder ENTITY-Deklaration enthalten.')
    declaration = _XML_DECLARATION_ENCODING.match(text)
    if declaration is not None and declaration.group(1).strip().lower() != 'utf-8':
        raise _rejected('Die SVG-Datei muss UTF-8-kodiert sein.')

    parser = ET.XMLPullParser(events=('start', 'end', 'pi'))
    depth = elements = 0
    # Fed in slices so a depth/size violation stops the parse early.
    for offset in range(0, len(text), _FEED_CHARS):
        try:
            parser.feed(text[offset:offset + _FEED_CHARS])
            if offset + _FEED_CHARS >= len(text):
                parser.close()
        except ET.ParseError:
            raise _rejected('Die SVG-Datei ist kein gültiges XML.') from None
        for event, element in parser.read_events():
            if event == 'pi':
                raise _rejected('Die SVG-Datei darf keine Verarbeitungsanweisungen (z. B. xml-stylesheet) enthalten.')
            if event == 'end':
                depth -= 1
                if _local(element.tag) == 'style':
                    # Browsers build the stylesheet from the direct text only,
                    # so a child element can split a token like @im<g/>port.
                    css = (element.text or '') + ''.join(child.tail or '' for child in element)
                    if _unsafe_css(css, full=True):
                        raise _rejected(
                            'Die SVG-Datei enthält unzulässiges CSS (@import, javascript:, expression( oder eine '
                            'externe url()).'
                        )
                    if len(element):
                        raise _rejected(
                            'Die SVG-Datei enthält ein <style>-Element mit Kindelementen; erlaubt ist dort nur '
                            'CSS-Text.'
                        )
                continue
            depth += 1
            elements += 1
            if elements == 1 and element.tag != f'{{{_SVG_NS}}}svg':
                if _local(element.tag) == 'svg':
                    raise _rejected('Das Wurzelelement muss <svg> im SVG-Namensraum (xmlns) sein.')
                raise _unsupported()
            if elements > _MAX_SVG_ELEMENTS:
                raise _rejected('Die SVG-Datei hat mehr als 10 000 Elemente.')
            if depth > _MAX_SVG_DEPTH:
                raise _rejected('Die SVG-Datei ist tiefer als 64 Ebenen verschachtelt.')
            _check_svg_element(element)


def validate_asset(data: bytes) -> str:
    """The content type of an uploaded logo/busy animation, decided by its
    content alone (never the filename or the declared type). `data` is at
    most MAX_ASSET_BYTES + 1 bytes read from the upload."""
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail='Die Datei ist größer als 1 MiB.')
    if not data:
        raise _rejected('Die Datei ist leer.')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    _check_svg(data)
    return 'image/svg+xml'


# --- Projection and cache ----------------------------------------------------

_CACHE_SECONDS = 10
_cache_lock = threading.Lock()
_cache: tuple[float, BrandingPublic] | None = None
_cache_generation = 0


def invalidate_cache() -> None:
    """Called after every admin write (after the commit); other processes
    pick the change up within _CACHE_SECONDS."""
    global _cache, _cache_generation
    with _cache_lock:
        _cache = None
        _cache_generation += 1


def _aware(value: datetime) -> datetime:
    # sqlite returns DateTime(timezone=True) naive; every write is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _asset_info(asset: BrandingAsset) -> BrandingAssetInfo:
    return BrandingAssetInfo(
        content_type=asset.content_type, sha256=asset.sha256, size_bytes=asset.size_bytes,
        url=f'/api/v1/branding/assets/{asset.kind}?v={asset.sha256[:16]}',
    )


def admin_branding(db: Session) -> BrandingAdmin:
    """Read fresh from the database (the deferred asset bytes stay unread)."""
    config = db.get(BrandingConfig, 'default')
    assets = {asset.kind: asset for asset in db.scalars(select(BrandingAsset)).all() if asset.kind in ASSET_KINDS}
    primary = config.primary_color if config is not None else None
    accent = config.accent_color if config is not None else None
    stamps = [_aware(row.updated_at) for row in (config, *assets.values()) if row is not None and row.updated_at]
    return BrandingAdmin(
        primary_color=primary,
        accent_color=accent,
        palette=derive_palette(primary, accent),
        logo=_asset_info(assets['logo']) if 'logo' in assets else None,
        busy=_asset_info(assets['busy']) if 'busy' in assets else None,
        updated_at=max(stamps) if stamps else None,
        managed=config is not None or bool(assets),
    )


def public_branding(db: Session) -> BrandingPublic:
    """The public projection, reused for _CACHE_SECONDS per process."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        cached, generation = _cache, _cache_generation
    if cached is not None and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    payload = BrandingPublic(**admin_branding(db).model_dump(exclude={'managed'}))
    with _cache_lock:
        # A write that landed while this was read must not be overwritten
        # by the older value.
        if _cache_generation == generation:
            _cache = (now, payload)
    return payload

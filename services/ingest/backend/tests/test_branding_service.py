"""Branding logic without HTTP (app/services/branding.py): hex colours, the
derived palette, and the content-based upload validation incl. the SVG
safety checks."""

import re

import pytest
from fastapi import HTTPException

from app.services.branding import (
    COLOR_ERROR,
    MAX_ASSET_BYTES,
    contrast_ratio,
    derive_palette,
    mix,
    normalize_color,
    relative_luminance,
    validate_asset,
)

HEX = re.compile(r'^#[0-9a-f]{6}$')
STEPS = [step / 20 for step in range(21)]


# --- Colours -------------------------------------------------------------------

@pytest.mark.parametrize(('raw', 'expected'), [
    ('#1d6a53', '#1d6a53'),
    ('#1D6A53', '#1d6a53'),
    ('#abc', '#aabbcc'),
    ('#ABC', '#aabbcc'),
    (None, None),
])
def test_colours_are_normalised(raw, expected):
    assert normalize_color(raw) == expected


@pytest.mark.parametrize('raw', ['1d6a53', '#12345', '#1234567', '#ggg', ' #fff', '#fff ', 'red', '', '#', 'rgb(0,0,0)'])
def test_other_colour_inputs_are_refused(raw):
    with pytest.raises(ValueError, match=re.escape(COLOR_ERROR)):
        normalize_color(raw)


def test_wcag_helpers():
    assert relative_luminance('#ffffff') == pytest.approx(1.0)
    assert relative_luminance('#000000') == pytest.approx(0.0)
    assert contrast_ratio('#000000', '#ffffff') == pytest.approx(21.0)
    assert contrast_ratio('#ffffff', '#000000') == pytest.approx(21.0)
    assert contrast_ratio('#777777', '#777777') == pytest.approx(1.0)
    assert mix('#1d6a53', '#000000', 0.15) == '#195a47'  # 24.65 / 90.1 / 70.55, rounded
    assert mix('#000000', '#ffffff', 0.5) == '#808080'  # 127.5 rounds half up
    assert mix('#123456', '#ffffff', 0) == '#123456'
    assert mix('#123456', '#ffffff', 1) == '#ffffff'


# --- Palette -------------------------------------------------------------------

def test_no_colours_means_no_palette():
    assert derive_palette(None, None) is None


def test_palette_of_the_built_in_green():
    assert derive_palette('#1d6a53', None) == {
        'light': {
            'accent': '#1d6a53', 'accent_hover': '#195a47', 'accent_ink': '#1d6a53',
            'accent_soft': '#e4edea', 'on_accent': '#ffffff',
        },
        'dark': {
            'accent': '#619787', 'accent_hover': '#74a395', 'accent_ink': '#6c9e8f',
            'accent_soft': '#1f2f2d', 'on_accent': '#0c1511',
        },
    }


def test_accent_alone_keeps_the_default_primary():
    palette = derive_palette(None, '#ff8800')
    assert palette['light']['accent'] == '#1d6a53'
    assert palette['light']['accent_soft'] == mix('#ff8800', '#ffffff', 0.88)
    assert palette['dark']['accent_soft'] == mix('#ff8800', '#1f2024', 0.80)
    # Primary alone: the accent follows it.
    assert derive_palette('#ff8800', None)['light']['accent_soft'] == mix('#ff8800', '#ffffff', 0.88)


def test_text_on_the_accent_picks_the_better_contrast():
    assert derive_palette('#ffff00', None)['light']['on_accent'] == '#14221d'
    assert derive_palette('#0a1f8f', None)['light']['on_accent'] == '#ffffff'
    assert derive_palette('#0a1f8f', None)['dark']['on_accent'] == '#0c1511'
    assert derive_palette('#ffff00', None)['dark']['accent'] == '#ffff00'


def _smallest(color: str, target: str, background: str) -> str:
    for amount in STEPS:
        if contrast_ratio(mix(color, target, amount), background) >= 4.5:
            return mix(color, target, amount)
    raise AssertionError('no step reaches 4.5')


@pytest.mark.parametrize(('primary', 'accent'), [
    ('#1d6a53', '#1d6a53'), ('#000000', '#ffffff'), ('#ffffff', '#000000'), ('#ffff00', '#00ffff'),
    ('#7f7f7f', '#7f7f7f'), ('#e11d48', '#2563eb'), ('#0b6f52', None), (None, '#bfe7d4'),
])
def test_palette_meets_the_contrast_rules(primary, accent):
    palette = derive_palette(primary, accent)
    effective_primary = primary or '#1d6a53'
    effective_accent = accent or effective_primary
    light, dark = palette['light'], palette['dark']
    for value in (*light.values(), *dark.values()):
        assert HEX.match(value), value
    assert light['accent'] == effective_primary
    assert light['accent_hover'] == mix(effective_primary, '#000000', 0.15)
    assert light['accent_soft'] == mix(effective_accent, '#ffffff', 0.88)
    assert light['accent_ink'] == _smallest(effective_accent, '#000000', light['accent_soft'])
    assert contrast_ratio(light['accent_ink'], light['accent_soft']) >= 4.5
    assert dark['accent'] == _smallest(effective_primary, '#ffffff', '#1f2024')
    assert contrast_ratio(dark['accent'], '#1f2024') >= 4.5
    assert dark['accent_hover'] == mix(dark['accent'], '#ffffff', 0.12)
    assert dark['accent_soft'] == mix(effective_accent, '#1f2024', 0.80)
    assert dark['accent_ink'] == _smallest(effective_accent, '#ffffff', dark['accent_soft'])
    assert contrast_ratio(dark['accent_ink'], dark['accent_soft']) >= 4.5


# --- Upload validation ---------------------------------------------------------

NS = 'xmlns="http://www.w3.org/2000/svg"'
XLINK = 'xmlns:xlink="http://www.w3.org/1999/xlink"'
PNG_PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC'


def _status(data: bytes) -> tuple[int, str]:
    with pytest.raises(HTTPException) as caught:
        validate_asset(data)
    return caught.value.status_code, caught.value.detail


def test_png_and_gif_are_recognised_by_their_magic_bytes():
    assert validate_asset(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32) == 'image/png'
    assert validate_asset(b'GIF89a' + b'\x00' * 32) == 'image/gif'
    assert validate_asset(b'GIF87a' + b'\x00' * 32) == 'image/gif'
    # Exactly 1 MiB is still fine.
    assert validate_asset(b'GIF89a' + b'\x00' * (MAX_ASSET_BYTES - 6)) == 'image/gif'


def test_size_and_empty_files():
    assert _status(b'GIF89a' + b'\x00' * (MAX_ASSET_BYTES - 5)) == (413, 'Die Datei ist größer als 1 MiB.')
    assert _status(b'')[0] == 422


@pytest.mark.parametrize('data', [
    b'\xff\xd8\xff\xe0\x00\x10JFIF\x00',  # JPEG
    b'RIFF\x00\x00\x00\x00WEBPVP8 ',  # WebP
    b'hello world',
    b'\x00\x01\x02\x03',
    b'%PDF-1.7\n',
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
    b'<?xml version="1.0"?><note>x</note>',
])
def test_anything_else_is_an_unsupported_type(data):
    assert _status(data) == (415, 'Nur SVG, GIF oder PNG.')


ACCEPTED_SVGS = {
    'minimal': f'<svg {NS} viewBox="0 0 10 10"><rect width="10" height="10" fill="#1d6a53"/></svg>',
    'declaration and BOM': f'\ufeff<?xml version="1.0" encoding="UTF-8"?>\n<svg {NS}><circle r="4"/></svg>',
    'smil animation': (
        f'<svg {NS}><circle r="4"><animate attributeName="opacity" values="1;0;1" dur="1s" '
        'repeatCount="indefinite"/></circle><set attributeName="fill" to="red"/></svg>'
    ),
    'css animation and internal urls': (
        f'<svg {NS}><defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
        '<style>@keyframes spin { to { transform: rotate(360deg) } } .a { fill: url(#g); '
        'animation: spin 1s linear infinite }</style>'
        '<rect class="a" style="fill: url( \'#g\' )" fill="url(#g)" width="4" height="4"/></svg>'
    ),
    'internal references': f'<svg {NS} {XLINK}><g id="a"/><use href="#a"/><use xlink:href="#a"/></svg>',
    'embedded raster image': f'<svg {NS}><image href="data:image/png;base64,{PNG_PIXEL}" width="1" height="1"/></svg>',
    'comments': f'<svg {NS}><!-- made by hand --><g/></svg>',
}


@pytest.mark.parametrize('svg', ACCEPTED_SVGS.values(), ids=ACCEPTED_SVGS.keys())
def test_safe_svgs_are_accepted(svg):
    assert validate_asset(svg.encode('utf-8')) == 'image/svg+xml'


REJECTED_SVGS = {
    'script': (f'<svg {NS}><script>alert(1)</script></svg>', '<script>'),
    'script in another namespace': (
        f'<svg {NS}><h:script xmlns:h="http://www.w3.org/1999/xhtml">alert(1)</h:script></svg>', '<script>'),
    'onload': (f'<svg {NS} onload="alert(1)"/>', 'onload'),
    'onclick upper case': (f'<svg {NS}><rect ONCLICK="alert(1)"/></svg>', 'ONCLICK'),
    'foreignObject': (f'<svg {NS}><foreignObject><div/></foreignObject></svg>', '<foreignObject>'),
    'iframe': (f'<svg {NS}><iframe/></svg>', '<iframe>'),
    'embed': (f'<svg {NS}><embed/></svg>', '<embed>'),
    'object': (f'<svg {NS}><object/></svg>', '<object>'),
    'audio': (f'<svg {NS}><audio/></svg>', '<audio>'),
    'video': (f'<svg {NS}><video/></svg>', '<video>'),
    'handler': (f'<svg {NS}><handler/></svg>', '<handler>'),
    'listener': (f'<svg {NS}><listener/></svg>', '<listener>'),
    'external href': (f'<svg {NS}><a href="https://example.com"><g/></a></svg>', 'externen Verweis'),
    'javascript xlink:href': (f'<svg {NS} {XLINK}><a xlink:href="javascript:alert(1)"/></svg>', 'externen Verweis'),
    'svg data uri': (f'<svg {NS}><image href="data:image/svg+xml;base64,PHN2Zz4="/></svg>', 'externen Verweis'),
    'external image': (f'<svg {NS}><image href="logo.png"/></svg>', 'externen Verweis'),
    'set href': (f'<svg {NS}><a><set attributeName="href" to="javascript:alert(1)"/></a></svg>', 'href'),
    'animate xlink:href': (f'<svg {NS}><a><animate attributeName="xlink:href" values="#a"/></a></svg>', 'xlink:href'),
    'animate onclick': (f'<svg {NS}><animate attributeName="onclick" to="x"/></svg>', 'onclick'),
    'set href under another xlink prefix': (
        f'<svg {NS} xmlns:l="http://www.w3.org/1999/xlink"><a><set attributeName="l:href" to="javascript:alert(1)"/>'
        '</a></svg>', 'l:href'),
    'animateTransform href': (f'<svg {NS}><a><animateTransform attributeName="href" to="#b"/></a></svg>', 'href'),
    'xml:base': (f'<svg {NS} xml:base="https://example.com/"><use href="#a"/></svg>', 'externen Verweis'),
    'doctype': (f'<!DOCTYPE svg><svg {NS}/>', 'DOCTYPE'),
    'doctype lower case': (f'<!doctype svg><svg {NS}/>', 'DOCTYPE'),
    'entity expansion': (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        f'<svg {NS}><text>&b;</text></svg>', 'DOCTYPE'),
    'entity only': (f'<svg {NS}><!ENTITY x "y"></svg>', 'ENTITY'),
    'xml-stylesheet': (f'<?xml version="1.0"?><?xml-stylesheet href="https://example.com/x.css"?><svg {NS}/>',
                       'Verarbeitungsanweisungen'),
    'processing instruction inside': (f'<svg {NS}><?php echo 1 ?></svg>', 'Verarbeitungsanweisungen'),
    'style attribute external url': (f'<svg {NS}><rect style="fill: url(https://example.com/a.svg#x)"/></svg>', 'CSS'),
    'style attribute quoted external url': (f'<svg {NS}><rect style="fill:url( \'//example.com/x\' )"/></svg>', 'CSS'),
    'style element import': (f'<svg {NS}><style>@import "https://example.com/x.css";</style></svg>', 'CSS'),
    'style element javascript': (f'<svg {NS}><style>a {{ b: javascript:alert(1) }}</style></svg>', 'CSS'),
    'style element expression': (f'<svg {NS}><style>a {{ width: expression(alert(1)) }}</style></svg>', 'CSS'),
    'style element data url': (f'<svg {NS}><style>a {{ fill: url(data:image/png;base64,AAAA) }}</style></svg>', 'CSS'),
    'style element in cdata': (f'<svg {NS}><style><![CDATA[a {{ fill: URL(http://x/y) }}]]></style></svg>', 'CSS'),
    'css escape': (f'<svg {NS}><style>a {{ fill: \\75 rl(http://x/y) }}</style></svg>', 'CSS'),
    'style attribute image-set': (f'<svg {NS}><rect style="fill: image-set(\'https://x/y.png\' 1x)"/></svg>', 'CSS'),
    'css escaped import': (f'<svg {NS}><style>@\\69mport "x.css";</style></svg>', 'CSS'),
    # Browsers build the stylesheet from the direct text only, so a child element splits no token.
    'style element import split by child': (
        f'<svg {NS}><style>@im<g>zzz</g>port "https://evil.example/x.css";</style></svg>', 'CSS'),
    'style element url split by child': (
        f'<svg {NS}><style>rect{{fill:u<g>z</g>rl(https://evil.example/a#x)}}</style><rect/></svg>', 'CSS'),
    'style element javascript split by child': (f'<svg {NS}><style>a{{b:java<g>z</g>script:x}}</style></svg>', 'CSS'),
    'style element with child element': (f'<svg {NS}><style>a {{ fill: red }}<g/></style></svg>', 'Kindelement'),
    'presentation attribute url': (f'<svg {NS}><rect filter="url(https://example.com/f.svg#f)"/></svg>', 'CSS'),
    'not xml': (f'<svg {NS}><g></svg>', 'kein gültiges XML'),
    'undefined entity': (f'<svg {NS}>&ext;</svg>', 'kein gültiges XML'),
    'no namespace': ('<svg><g/></svg>', 'SVG-Namensraum'),
    'latin-1 bytes': (None, 'UTF-8'),
    'declared latin-1': (f'<?xml version="1.0" encoding="ISO-8859-1"?><svg {NS}/>', 'UTF-8'),
    'too many elements': (f'<svg {NS}>' + '<g/>' * 10_000 + '</svg>', '10 000'),
    'too deep': (f'<svg {NS}>' + '<g>' * 64 + '</g>' * 64 + '</svg>', '64 Ebenen'),
}


@pytest.mark.parametrize(('svg', 'reason'), REJECTED_SVGS.values(), ids=REJECTED_SVGS.keys())
def test_unsafe_svgs_are_refused_with_a_reason(svg, reason):
    data = f'<svg {NS}><text>Gr\xfc\xdfe</text></svg>'.encode('latin-1') if svg is None else svg.encode('utf-8')
    status_code, detail = _status(data)
    assert status_code == 422
    assert reason in detail


def test_limits_are_inclusive():
    assert validate_asset((f'<svg {NS}>' + '<g/>' * 9_999 + '</svg>').encode()) == 'image/svg+xml'
    assert validate_asset((f'<svg {NS}>' + '<g>' * 63 + '</g>' * 63 + '</svg>').encode()) == 'image/svg+xml'

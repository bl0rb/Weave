"""Small HTTP helpers shared by several routers."""

from urllib.parse import quote


def _content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266/5987 Content-Disposition: ASCII-safe quoted fallback plus a
    UTF-8 filename* parameter for everything else."""
    fallback = ''.join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else '_' for ch in filename) or 'download'
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'

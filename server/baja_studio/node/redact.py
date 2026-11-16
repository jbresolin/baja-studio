"""Keeping the node's secrets out of what it shows and logs (ADR 0023).

A camera's output URL is a Feeds URL, which carries the feed's publish
password (`srt://host:8890?streamid=publish:cam1:user:password`, ADR 0022).
The node shows where a camera sends its picture without it
(`redacted_url`), and runs every reason that came from a pipeline or OBS
through `scrub` before it reaches the state, the API or the log, in case a
library repeated the URL or the password in an error message.
"""

from urllib.parse import parse_qsl, urlsplit

HIDDEN = "***"
# Shorter values are not scrubbed one by one (a 1-character "secret" would
# blank every such letter); the whole URL still is.
MIN_SECRET_LENGTH = 4


def redacted_url(url: str) -> str:
    """`scheme://host:port` and the parameter names, never their values ("" for no URL)."""
    if not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
        where = f"{parts.scheme}://{parts.hostname or '?'}"
        if parts.port is not None:
            where += f":{parts.port}"
    except ValueError:
        return "(an address that cannot be read)"
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    return where + (f"?{'&'.join(f'{name}={HIDDEN}' for name in names)}" if names else "")


def secret_parts(url: str) -> list[str]:
    """The pieces of a URL that must never be shown: itself, its password and query values."""
    if not url.strip():
        return []
    parts = [url.strip()]
    try:
        split = urlsplit(url.strip())
    except ValueError:
        return parts
    if split.password:
        parts.append(split.password)
    for _, value in parse_qsl(split.query, keep_blank_values=True):
        parts.append(value)
        # A streamid is "publish:path:user:password": hide its parts too.
        parts.extend(value.split(":")[2:])
    return [part for part in parts if len(part) >= MIN_SECRET_LENGTH]


def scrub(text: str, secrets: list[str]) -> str:
    """`text` with every secret replaced by ***, longest first (a URL before its password)."""
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= MIN_SECRET_LENGTH:
            text = text.replace(secret, HIDDEN)
    return text

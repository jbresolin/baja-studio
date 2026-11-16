"""Keeping the node's secrets out of what it shows and logs (ADR 0023).

A camera's output URL is a Feeds URL, which can carry the feed's publish
password (`srt://host:8890?streamid=publish:cam1:user:password`, or
`rtmp://host/cam1?user=cam1&pass=password`, ADR 0022). The node shows where
a camera sends its picture with only the secrets hidden (`redacted_url`), so
the address, the feed and settings such as the SRT latency stay readable.
Every reason that came from a pipeline or OBS goes through `scrub` before it
reaches the state, the API or the log, in case a library repeated the URL or
the password in an error message.
"""

from urllib.parse import parse_qsl, urlsplit

HIDDEN = "***"
# Shorter values are not scrubbed one by one (a 1-character "secret" would
# blank every such letter); the whole URL still is.
MIN_SECRET_LENGTH = 4
# Query parameters whose value is a secret (RTMP's `pass`, SRT's `passphrase`).
SECRET_PARAMETERS = frozenset({"pass", "password", "passphrase", "token", "key"})
STREAMID = "streamid"
# A MediaMTX streamid is `action:path[:user:password]`: the first two are shown.
STREAMID_SHOWN_FIELDS = 2


def redacted_url(url: str) -> str:
    """The URL with its secrets replaced by *** ("" for no URL).

    Shown as they are: the scheme, host, port, path and every parameter that
    is not a secret (`pkt_size`, `latency`...). Hidden: a password in the
    address (`user:password@host`), the values of `SECRET_PARAMETERS`, and the
    user and password inside an SRT `streamid`.
    """
    if not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
        where = f"{parts.scheme}://"
        if parts.username:
            where += f"{parts.username}:{HIDDEN}@" if parts.password else f"{parts.username}@"
        where += parts.hostname or "?"
        if parts.port is not None:
            where += f":{parts.port}"
    except ValueError:
        return "(an address that cannot be read)"
    where += parts.path
    shown = [
        f"{name}={_shown_value(name, value)}"
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return where + (f"?{'&'.join(shown)}" if shown else "")


def _shown_value(name: str, value: str) -> str:
    """A query parameter's value as shown: itself, *** if secret, a streamid without its login."""
    if name.lower() in SECRET_PARAMETERS:
        return HIDDEN
    if name.lower() == STREAMID:
        fields = value.split(":")
        if len(fields) > STREAMID_SHOWN_FIELDS:
            return ":".join([*fields[:STREAMID_SHOWN_FIELDS], HIDDEN])
    return value


def secret_parts(url: str) -> list[str]:
    """The pieces of a URL that must never be shown: itself, and its passwords."""
    if not url.strip():
        return []
    parts = [url.strip()]
    try:
        split = urlsplit(url.strip())
    except ValueError:
        return parts
    if split.password:
        parts.append(split.password)
    for name, value in parse_qsl(split.query, keep_blank_values=True):
        if name.lower() in SECRET_PARAMETERS:
            parts.append(value)
        elif name.lower() == STREAMID:
            # Everything after `action:path` is the login: the user (often the
            # feed's own name, so only scrubbed as part of the whole) and the password.
            login = value.split(":")[STREAMID_SHOWN_FIELDS:]
            if login:
                parts.append(":".join(login))
                parts.append(login[-1])
    return [part for part in parts if len(part) >= MIN_SECRET_LENGTH]


def scrub(text: str, secrets: list[str]) -> str:
    """`text` with every secret replaced by ***, longest first (a URL before its password)."""
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= MIN_SECRET_LENGTH:
            text = text.replace(secret, HIDDEN)
    return text

"""Publish passwords as MediaMTX checks them: an argon2id hash, never the password.

MediaMTX reads its users from `mediamtx.yml`. A plain password there would
put a secret in a file, which the studio never does (docs/conventions.md).
MediaMTX also accepts `argon2:<encoded hash>` and verifies the sender's
password against it, so the file holds only the hash: what a server stores
for a password anyway.

The hash must come out the same every time the config is written for the
same password, or every write would change the file and make MediaMTX
reload for nothing. So the salt is not random per hash: it is derived from
a random value made once per install (`<data>/mediamtx/password-salt`) and
the feed's name. Two installs, or two feeds with the same password, still
get different hashes.
"""

import hashlib
import secrets
from pathlib import Path

from argon2.low_level import Type, hash_secret

SALT_FILE_NAME = "password-salt"
# OWASP's argon2id minimum (19 MiB, 2 passes, 1 lane): MediaMTX checks the hash on every
# publish attempt, and a sender reconnecting in a loop must not eat the PC's memory.
TIME_COST = 2
MEMORY_COST_KIB = 19456
PARALLELISM = 1
HASH_LENGTH = 32
SALT_LENGTH = 16
# What MediaMTX expects in front of an argon2 hash in `authInternalUsers[].pass`.
MEDIAMTX_ARGON2_PREFIX = "argon2:"


def install_salt(directory: Path) -> bytes:
    """This install's random salt, made (and saved in `directory`) when first asked for."""
    path = directory / SALT_FILE_NAME
    if path.is_file():
        text = path.read_text(encoding="ascii").strip()
        if len(text) == SALT_LENGTH * 2:
            return bytes.fromhex(text)
    directory.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(SALT_LENGTH)
    path.write_text(salt.hex(), encoding="ascii")
    return salt


def mediamtx_password_hash(password: str, feed_name: str, salt: bytes) -> str:
    """The `pass:` value for MediaMTX: `argon2:$argon2id$v=19$...`, the same for the same input."""
    feed_salt = hashlib.sha256(salt + feed_name.encode("utf-8")).digest()[:SALT_LENGTH]
    encoded = hash_secret(
        password.encode("utf-8"),
        feed_salt,
        time_cost=TIME_COST,
        memory_cost=MEMORY_COST_KIB,
        parallelism=PARALLELISM,
        hash_len=HASH_LENGTH,
        type=Type.ID,
    )
    return MEDIAMTX_ARGON2_PREFIX + encoded.decode("ascii")

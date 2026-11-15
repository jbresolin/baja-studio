"""Tests for publish-password hashing (modules/feeds/passwords.py).

MediaMTX gets an argon2id hash of each feed password, never the password.
The hash must come out the same for the same password (or every config
write would make MediaMTX reload for nothing), yet differ between feeds
and between installs.
"""

from pathlib import Path

from argon2.low_level import Type, verify_secret

from baja_studio.modules.feeds.passwords import (
    MEDIAMTX_ARGON2_PREFIX,
    SALT_FILE_NAME,
    install_salt,
    mediamtx_password_hash,
)

SALT = bytes(16)


def test_the_hash_is_one_mediamtx_reads_and_it_checks_the_password() -> None:
    stored = mediamtx_password_hash("drone-pw", "drone", SALT)
    # MediaMTX recognises "argon2:" and then a standard encoded argon2id hash.
    assert stored.startswith(MEDIAMTX_ARGON2_PREFIX + "$argon2id$v=19$m=19456,t=2,p=1$")
    encoded = stored.removeprefix(MEDIAMTX_ARGON2_PREFIX).encode()
    assert verify_secret(encoded, b"drone-pw", Type.ID)


def test_the_same_password_gives_the_same_hash_so_the_file_does_not_change() -> None:
    assert mediamtx_password_hash("pw-1234", "drone", SALT) == mediamtx_password_hash(
        "pw-1234", "drone", SALT
    )


def test_the_same_password_on_two_feeds_or_two_installs_gives_different_hashes() -> None:
    # The salt mixes the install's own random value with the feed's name.
    drone = mediamtx_password_hash("pw-1234", "drone", SALT)
    laptop = mediamtx_password_hash("pw-1234", "laptop1", SALT)
    other_install = mediamtx_password_hash("pw-1234", "drone", bytes([1]) * 16)
    assert len({drone, laptop, other_install}) == 3


def test_the_install_salt_is_made_once_and_then_reused(tmp_path: Path) -> None:
    first = install_salt(tmp_path / "mediamtx")  # the folder does not exist yet
    second = install_salt(tmp_path / "mediamtx")
    assert first == second
    assert len(first) == 16
    assert (tmp_path / "mediamtx" / SALT_FILE_NAME).read_text(encoding="ascii") == first.hex()


def test_a_damaged_salt_file_is_replaced(tmp_path: Path) -> None:
    # A truncated file (a disk problem) would give a short salt: make a new one.
    (tmp_path / SALT_FILE_NAME).write_text("abc", encoding="ascii")
    assert len(install_salt(tmp_path)) == 16

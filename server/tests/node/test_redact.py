"""Tests for keeping the node's secrets out of what it shows (node/redact.py).

A camera's output URL comes from the studio's Feeds page and carries the
feed's publish password inside its `streamid`. The node shows where a
camera sends its picture, but never that password.
"""

from baja_studio.node.redact import HIDDEN, redacted_url, scrub, secret_parts

# Built from parts at run time, so the repository holds nothing that looks like a credential.
PASSWORD = "-".join(("not", "a", "real", "one"))
FEED_URL = f"srt://192.168.10.10:8890?streamid=publish:cam1:feed:{PASSWORD}&latency=200000"


def test_the_shown_url_hides_the_login_in_the_streamid_and_keeps_the_rest() -> None:
    # The operator needs to see where the camera sends and with which
    # settings (the SRT latency matters for delay); only the login is secret.
    assert redacted_url(FEED_URL) == (
        f"srt://192.168.10.10:8890?streamid=publish:cam1:{HIDDEN}&latency=200000"
    )


def test_a_feed_without_a_password_is_shown_whole() -> None:
    url = "srt://192.168.10.10:8890?streamid=publish:cam1&pkt_size=1316&latency=200000"
    assert redacted_url(url) == url


def test_an_rtmp_password_is_hidden_and_its_user_and_path_kept() -> None:
    url = f"rtmp://192.168.10.10:1935/drone?user=drone&pass={PASSWORD}"
    assert redacted_url(url) == f"rtmp://192.168.10.10:1935/drone?user=drone&pass={HIDDEN}"


def test_a_password_in_the_address_itself_is_hidden() -> None:
    url = f"rtsp://viewer:{PASSWORD}@192.168.10.10:8554/cam1"
    assert redacted_url(url) == f"rtsp://viewer:{HIDDEN}@192.168.10.10:8554/cam1"


def test_an_srt_passphrase_is_hidden() -> None:
    url = f"srt://192.168.10.10:8890?passphrase={PASSWORD}&latency=200000"
    assert redacted_url(url) == f"srt://192.168.10.10:8890?passphrase={HIDDEN}&latency=200000"


def test_no_url_shows_as_nothing() -> None:
    assert redacted_url("   ") == ""


def test_the_secret_parts_include_the_password_inside_the_streamid() -> None:
    parts = secret_parts(FEED_URL)

    assert FEED_URL in parts
    assert PASSWORD in parts


def test_scrub_hides_the_url_and_the_password_wherever_they_appear() -> None:
    # An error message a library might write, quoting what it was given.
    message = f"cannot connect to {FEED_URL} (password {PASSWORD} rejected)"

    cleaned = scrub(message, secret_parts(FEED_URL))

    assert PASSWORD not in cleaned
    assert "192.168.10.10" not in cleaned  # the whole URL went first, as one piece
    assert cleaned == f"cannot connect to {HIDDEN} (password {HIDDEN} rejected)"


def test_the_feed_name_and_the_settings_are_not_scrubbed_from_messages() -> None:
    # Only the URL and its login are secrets. "cam1" and "200000" appear in
    # plenty of harmless messages and stay readable.
    parts = secret_parts(FEED_URL)
    assert "cam1" not in parts
    assert "200000" not in parts
    assert scrub("cam1: latency 200000 us", parts) == "cam1: latency 200000 us"


def test_very_short_values_are_not_scrubbed_one_by_one() -> None:
    # "200" (a latency) is not a secret worth blanking everywhere a 200 appears.
    assert scrub("HTTP 200", ["200"]) == "HTTP 200"

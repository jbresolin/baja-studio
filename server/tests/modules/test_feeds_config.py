"""Tests for the feeds settings (modules/feeds/config.py).

Every value in these settings ends up in MediaMTX's config file, and
MediaMTX *exits* when it reloads a config it cannot use. So these tests
check that the model refuses, with a message an operator understands,
anything that would stop MediaMTX or break a sender's URL.
"""

import pytest
from pydantic import ValidationError

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings


def test_the_defaults_are_mediamtx_ports_except_that_nothing_uses_8888() -> None:
    # Out of the box: RTMP 1935 for the drone, SRT 8890 for laptops, RTSP 8554
    # for OBS, the control API on 9997. No feeds yet, accept-any off.
    settings = FeedsSettings()
    assert (settings.rtmp_port, settings.srt_port, settings.rtsp_port, settings.api_port) == (
        1935,
        8890,
        8554,
        9997,
    )
    assert settings.feeds == []
    assert settings.accept_any_name is False
    assert settings.rtsp_on_lan is False


@pytest.mark.parametrize("name", ["drone", "laptop1", "cam-2", "a_b", "9"])
def test_simple_lowercase_names_are_accepted(name: str) -> None:
    # A feed's name is what the operator types on the drone's controller: keep it simple.
    assert Feed(name=name).name == name


@pytest.mark.parametrize(
    "name", ["Drone", "drone 1", "", "-drone", "a" * 33, "drone/1", "~regex", "dróne"]
)
def test_names_with_capitals_spaces_or_symbols_are_refused(name: str) -> None:
    # Capitals would make "Drone" and "drone" two different feeds; spaces and
    # slashes break URLs; "~" starts a regular expression in MediaMTX.
    with pytest.raises(ValidationError, match="lowercase letters"):
        Feed(name=name)


@pytest.mark.parametrize("name", ["all", "all_others"])
def test_names_mediamtx_reserves_are_refused(name: str) -> None:
    # "all_others" is MediaMTX's catch-all path; a feed with that name would
    # accept every sender.
    with pytest.raises(ValidationError, match="special meaning"):
        Feed(name=name)


@pytest.mark.parametrize("password", ["pass:word", "a&b=c", "with space", "50%", "q?x", "abc"])
def test_passwords_that_would_break_a_url_or_are_too_short_are_refused(password: str) -> None:
    # The password goes into the sender's URL (`?pass=` for RTMP,
    # `streamid=publish:name:user:pass` for SRT), so ':' '&' '?' '%' and
    # spaces would cut it short. Three characters is too short.
    with pytest.raises(ValidationError, match="4 to 64"):
        Feed(name="drone", password=password)


def test_an_empty_password_means_no_password() -> None:
    assert Feed(name="drone").password == ""


def test_a_new_feed_gets_a_stable_id_and_its_obs_name_falls_back_to_its_name() -> None:
    # The id keeps a password with its feed when the feed is renamed (the
    # settings API matches list items by id). The OBS source is named after
    # the label, or the name when there is no label.
    first, second = Feed(name="drone"), Feed(name="drone2", label="Drone 2")
    assert first.id.startswith("feed-") and first.id != second.id
    assert first.obs_source_name == "drone"
    assert second.obs_source_name == "Drone 2"
    assert first.kind == FeedKind.OTHER


def test_two_feeds_with_the_same_name_are_refused() -> None:
    with pytest.raises(ValidationError, match="same name: drone"):
        FeedsSettings(feeds=[Feed(name="drone"), Feed(name="drone")])


def test_two_feeds_that_would_make_the_same_obs_source_are_refused() -> None:
    # Add to OBS refuses a name that exists, so two feeds labelled alike could
    # never both be added.
    with pytest.raises(ValidationError, match="OBS sources named Drone"):
        FeedsSettings(feeds=[Feed(name="a", label="Drone"), Feed(name="b", label="Drone")])


@pytest.mark.parametrize("port", [8888, 80, 443, 2019, 4455])
def test_a_port_the_studio_or_obs_already_uses_is_refused(port: int) -> None:
    # MediaMTX's own HLS default is 8888, the studio's port: this is the
    # mistake the brief warns about, refused for every feed port.
    with pytest.raises(ValidationError, match=f"{port} is used by"):
        FeedsSettings(rtmp_port=port)


def test_two_feed_ports_that_are_the_same_are_refused() -> None:
    with pytest.raises(ValidationError, match="must all be different"):
        FeedsSettings(rtsp_port=9997, api_port=9997)


def test_accept_any_name_cannot_be_on_with_a_password() -> None:
    # "Accept any name" lets anyone publish on every name, the protected ones
    # included, so the two together would give a false sense of protection.
    with pytest.raises(ValidationError, match="turn it off to use feed passwords"):
        FeedsSettings(accept_any_name=True, feeds=[Feed(name="drone", password="secret1")])


@pytest.mark.parametrize("folder", ['D:\\Rec"ordings', "D:\\100%", "D:\\a\nb"])
def test_a_recordings_folder_mediamtx_would_misread_is_refused(folder: str) -> None:
    # '%' starts a MediaMTX placeholder (%path, %Y) in the recording path.
    with pytest.raises(ValidationError, match="folder name cannot contain"):
        FeedsSettings(recordings_folder=folder)


def test_the_lan_address_must_look_like_an_address() -> None:
    assert FeedsSettings(lan_address=" 192.168.10.20 ").lan_address == "192.168.10.20"
    with pytest.raises(ValidationError, match="IPv4 address or a host name"):
        FeedsSettings(lan_address="http://192.168.10.20/")


def test_feed_named_finds_a_feed_by_its_path_name() -> None:
    settings = FeedsSettings(feeds=[Feed(name="drone"), Feed(name="laptop1")])
    found = settings.feed_named("laptop1")
    assert found is not None and found.name == "laptop1"
    assert settings.feed_named("nothing") is None


def test_the_feed_list_is_left_out_of_the_generic_settings_form() -> None:
    # Feeds are edited on the Feeds page; the generic form (which shows the
    # ports) skips fields marked `managed_elsewhere`. The password is a secret.
    schema = FeedsSettings.model_json_schema()
    assert schema["properties"]["feeds"]["managed_elsewhere"] is True
    assert schema["$defs"]["Feed"]["properties"]["password"]["secret"] is True

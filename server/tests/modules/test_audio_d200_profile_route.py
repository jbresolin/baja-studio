"""Tests for Audio setup → D200 → "Download the D200 profile" (`GET /api/audio/d200-profile`).

The profile is built from the saved settings (audio/d200_profile.py has the
layout's own tests); here: the download itself, and that every button of
the downloaded profile is answered by the service it calls.
"""

from urllib.parse import urlsplit

from tests.audio.ulanzi_profile import Profile
from tests.modules.audio_studio import AudioStudio

STUDIO_PC_SEATS = [
    {"seat_id": 1, "channel": "5", "iem_bus": "bus1"},
    {"seat_id": 2, "channel": "6", "iem_bus": "bus2"},
    {"seat_id": 3, "channel": "7", "iem_bus": "bus3"},
    {"seat_id": 4, "channel": "8", "iem_bus": "bus4"},
]


def test_the_profile_downloads_as_a_file_built_from_the_saved_settings(audio: AudioStudio) -> None:
    audio.configure(seats=STUDIO_PC_SEATS, production_channels=["9", "10"])

    response = audio.http.get("/api/audio/d200-profile", params={"base": "http://10.0.0.5:8888/"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == (
        'attachment; filename="Baja Studio.ulanziDeckProfile"'
    )
    hosts = Profile(response.content).buttons("http://10.0.0.5:8888")[1]
    assert hosts["0_0"]["request"] == "/action/mute/5"


def test_without_an_address_the_buttons_call_the_studio_pc_itself(audio: AudioStudio) -> None:
    response = audio.http.get("/api/audio/d200-profile")

    hosts = Profile(response.content).buttons("http://localhost:8888")[1]
    assert hosts["0_0"]["request"] == "/action/mute/1"


def test_an_address_that_is_not_one_is_refused(audio: AudioStudio) -> None:
    response = audio.http.get("/api/audio/d200-profile", params={"base": "localhost:8888"})

    assert response.status_code == 400
    assert "D200's address" in response.json()["detail"]


def test_every_button_of_the_profile_is_answered_by_the_service(audio: AudioStudio) -> None:
    # Pressing each button and polling each state, as the deck would; every
    # macro twice, so it ends where it started.
    audio.connect_mixer()
    audio.configure(seats=STUDIO_PC_SEATS, production_channels=["9", "10"])
    profile = Profile(audio.http.get("/api/audio/d200-profile").content)

    paths = [
        urlsplit(action["ActionParam"][key]).path
        for page in profile.actions
        for action in page.values()
        for key in ("request_url", "poll_status_url")
        if action["ActionParam"].get(key)
    ]
    assert len(paths) > 60
    for path in paths:
        for _press in range(2 if "/action/macro/" in path else 1):
            response = audio.http.get(path)
            audio.settle()
            # A JSON answer from the D200 contract, not the page catch-all's 200.
            assert response.status_code == 200, f"{path}: {response.text}"
            assert response.headers["content-type"].startswith("application/json"), path
            assert "error" not in response.json(), path

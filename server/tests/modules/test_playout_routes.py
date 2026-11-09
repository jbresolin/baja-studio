"""Tests for playout's HTTP routes (modules/playout/routes.py): clip media, thumbnails, browse.

The preview card's `<video>` scrubs a clip by asking for byte ranges, so the
media route must answer `Range` requests with 206. Only files the library
knows are served.
"""

from pathlib import Path

from tests.modules.playout_studio import PlayoutStudio, ready_studio


def test_a_clip_file_is_served_whole_and_by_byte_range(playout: PlayoutStudio) -> None:
    playout.connect_obs()
    playout.add_file("a.mp4", b"0123456789")
    playout.watch_folder()
    clip_id = playout.wait_for_clip("a.mp4")["id"]

    whole = playout.http.get(f"/api/playout/clips/{clip_id}/media")
    part = playout.http.get(f"/api/playout/clips/{clip_id}/media", headers={"Range": "bytes=2-5"})

    assert whole.status_code == 200
    assert whole.content == b"0123456789"
    assert part.status_code == 206
    assert part.content == b"2345"


def test_a_missing_or_unknown_clip_is_not_found(playout: PlayoutStudio) -> None:
    (clip_id,) = ready_studio(playout, files=("a.mp4",))
    (playout.clips_folder / "a.mp4").unlink()
    playout.soon(lambda: playout.state(f"library.{clip_id}.missing") is True)

    assert playout.http.get(f"/api/playout/clips/{clip_id}/media").status_code == 404
    assert playout.http.get("/api/playout/clips/999/media").status_code == 404


def test_the_cached_thumbnail_is_served_as_a_jpeg(playout: PlayoutStudio) -> None:
    (clip_id,) = ready_studio(playout, files=("a.mp4",))

    response = playout.http.get(f"/api/playout/clips/{clip_id}/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert playout.http.get("/api/playout/clips/999/thumbnail").status_code == 404


def test_browse_lists_the_subfolders_of_a_folder_on_the_streaming_pc(
    playout: PlayoutStudio, tmp_path: Path
) -> None:
    (tmp_path / "Patrocinadores").mkdir()

    response = playout.http.get("/api/playout/browse", params={"path": str(tmp_path)})

    assert response.status_code == 200
    names = [folder["name"] for folder in response.json()["folders"]]
    assert "Patrocinadores" in names and "clips" in names


def test_saving_folders_as_plain_strings_is_refused_naming_the_field(
    playout: PlayoutStudio,
) -> None:
    # Regression, bench 2026-11-08: the JSON box accepted ["D:/clips"]. The
    # save must be refused (422) with the field and the shape wanted.
    version = playout.http.get("/api/settings/playout").json()["version"]

    response = playout.http.put(
        "/api/settings/playout",
        json={"version": version, "values": {"sources": {"local_folders": ["D:/clips"]}}},
    )

    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"][-2:] == ["sources", "local_folders"]
    assert "must be an object like" in problem["msg"]

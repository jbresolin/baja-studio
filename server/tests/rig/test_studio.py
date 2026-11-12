"""Tests for the rig's studio helpers that need no studio (rig/studio.py).

Setting a studio up over its API is tested against a real one in
test_session.py; here, what `--open` opens and what happens when no
browser takes a page.
"""

from baja_studio.rig.studio import not_ok_checks, open_pages, pages_to_open

BASE = "http://127.0.0.1:8889"


def test_open_shows_the_home_page_and_a_hosts_screen_for_every_seat() -> None:
    # The hosts screen names its seats in the address, so it registers without
    # anyone tapping the seat picker and the readiness "Hosts screens" check is green.
    assert pages_to_open(BASE) == [f"{BASE}/", f"{BASE}/hosts?seats=1,2,3,4"]


def test_pages_no_browser_took_are_returned_to_be_printed() -> None:
    # A stand-in for `webbrowser.open`: it records each page and refuses the hosts screen.
    opened: list[str] = []

    def opener(url: str) -> bool:
        opened.append(url)
        return "hosts" not in url

    missed = open_pages(pages_to_open(BASE), opener)

    assert opened == pages_to_open(BASE)
    assert missed == [f"{BASE}/hosts?seats=1,2,3,4"]


def test_only_checks_that_are_not_ok_are_listed() -> None:
    run = {
        "modules": [
            {
                "name": "hosts",
                "checks": [
                    {"id": "seats", "status": "ok", "detail": "Ana, Bruno"},
                    {"id": "screens", "status": "warn", "detail": "no hosts screen is open"},
                ],
            }
        ]
    }
    assert not_ok_checks(run) == ["hosts/screens: warn no hosts screen is open"]

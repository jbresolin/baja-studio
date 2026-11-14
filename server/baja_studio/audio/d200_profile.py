"""A Ulanzi Studio profile for the D200, built for the studio as it is patched.

Audio setup → D200 → "Download the D200 profile" gives a file to import in
Ulanzi Studio, so nobody retypes channel numbers into button settings: on
the studio PC the hosts are on channels 5-8 and production on 9-10, while
the 2026 profile called 1-4 and 5-6 (2026-11-14 bench).

The layout is the 2026 profile's (the operators' spec, `docs/d200/README.md`):

- **Main menu**: HOSTS/GUESTS and PROD. CREW.
- **Hosts & guests**: a column per seat: mute, talk to production, and the
  seat's in-ear page; MAIN MENU top right.
- **One in-ear page per seat**: every seat's level in that seat's in-ears,
  and VIDEO (the clip sound, PC AUDIO), up and down 3 dB.
- **Production**: a row per production microphone: mute, go live, talk to
  HOST 1, talk to HOST 2, talk to the camera operator.

What changed from 2026: the channels, in-ear buses and production
microphones come from the saved settings (`patch_of`); GUEST 1 and GUEST 2
got their "talk to production" buttons; HOST 2's showed GUEST 1's image;
production got "talk to HOST 2", with "talk to the camera operator" moved to
the last column (João, 2026-11-14). A seat without a microphone gets no
buttons and no page. Every macro button is there even while its macro is
missing from the settings (it answers an error until Macros → "Restore
defaults…" adds it): `missing_macros` lists them for Audio setup → D200.

The file format, as Ulanzi Studio exports it: the line `#Version: 2`, then a
zip holding one `<uuid>.ulanziProfile/` folder: its `manifest.json` lists the
pages in order, and each page's `Profiles/<uuid>/manifest.json` maps keys
("column_row", 5 columns by 3 rows; key 3_2 is the deck's small screen, so
there is no 4_2) to actions. A button's images are files in its page's
folder. "Go to page" names a page by its position in the list; the 2026
export wrote no number at all for the first page, and so do we.
"""

import io
import json
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from baja_studio.adapters.xr18_addresses import AUX_RETURN
from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.patch import patch_of

FORMAT_HEADER = b"#Version: 2\n"
PROFILE_NAME = "Baja Studio"
FILE_NAME = "Baja Studio.ulanziDeckProfile"
# The D200 the 2026 profile was exported from (Ulanzi Studio's device id).
DEVICE_UUID = "799a23e9e0395623"
# Where the button images are, by name ("HOST 1 - MUTE"), as drawn in docs/d200/Template.pptx.
IMAGES = files("baja_studio.audio").joinpath("d200_images")
PROFILE_ICON = "PROFILE ICON"

SEAT_NAMES = {1: "HOST 1", 2: "HOST 2", 3: "GUEST 1", 4: "GUEST 2"}
# The key of each seat's "edit my in-ears" button on the hosts page (3_2 is the screen).
SEAT_PAGE_KEYS = {1: "0_2", 2: "1_2", 3: "2_2", 4: "4_1"}
# Two production rows, as in 2026: the deck has no room for a third.
PRODUCER_NAMES = ("PROD 1", "PROD 2")
# The clip sound: the VIDEO buttons of the in-ear pages.
VIDEO_CHANNEL = AUX_RETURN
NUDGE_DB = 3
SCREEN_KEY = "3_2"

API_REQUEST_PLUGIN = {
    "Name": "API Request",
    "UUID": "com.ulanzi.ulanzideck.APIRequest",
    "Version": "1.1.0",
}
PAGES_PLUGIN = {"Name": "Pages", "UUID": "", "Version": "1.0"}

IdFactory = Callable[[], str]


def d200_base_url(text: str) -> str:
    """The address the D200 calls, as `http(s)://host[:port]`; ValueError if it is not one."""
    parts = urlsplit(text.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("the D200's address must look like http://localhost:8888")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("the D200's address is only http(s)://host:port, with no path")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class _Page:
    """One page: its actions by key, and the images its folder holds (zip name → image name)."""

    page_id: str
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)

    def file(self, image: str) -> str:
        """A button image ("Files/HOST 1 - MUTE.png"), added to the page's folder."""
        path = f"Files/{image}.png"
        self.images[path] = image
        return path

    def icon(self, image: str, new_id: IdFactory) -> dict[str, str]:
        """The image a button shows before its first poll (Ulanzi Studio's "Images/user_…")."""
        path = f"Images/user_{new_id()}.png"
        self.images[path] = image
        return {"Icon": path, "IconRel": path}


class _Builder:
    """Builds the pages for one profile."""

    def __init__(self, settings: AudioSettings, base_url: str, new_id: IdFactory) -> None:
        self.patch = patch_of(settings)
        # Every macro id a button calls, for `missing_macros`.
        self.macros_called: list[str] = []
        self.base = base_url
        self.new_id = new_id

    # --- actions ---------------------------------------------------------------------------

    def _request(
        self, page: _Page, path: str, on: str, off: str, poll: tuple[str, str] | None
    ) -> dict[str, Any]:
        """An API Request "Match Result" button: GET `path`; with `poll`, (state path, field),
        the image follows the state's field being true, polled every second."""
        state_path, state_field = poll if poll else ("", "")
        return {
            "Action": "com.ulanzi.ulanzideck.APIRequest.matchResult",
            "ActionID": self.new_id(),
            "ActionParam": {
                "enable_success_indicator": False,
                "image_matched": page.file(on),
                "image_unmatched": page.file(off),
                "poll_status": "on" if poll else False,
                "poll_status_frequency": "1" if poll else "15",
                "poll_status_method": "GET",
                "poll_status_parse": False,
                "poll_status_parse_field": "",
                "poll_status_parse_value": "",
                "poll_status_url": f"{self.base}{state_path}" if poll else "",
                "request_body": "",
                "request_content_type": "",
                "request_headers": "",
                "request_method": "GET",
                "request_url": f"{self.base}{path}",
                "response_parse": "on",
                "response_parse_field": state_field,
                "response_parse_value": "true" if poll else "",
            },
            "LinkedTitle": True,
            "Name": "Match Result",
            "Plugin": API_REQUEST_PLUGIN,
            "State": 0,
            "ViewParam": [page.icon(off, self.new_id)],
        }

    def mute(self, page: _Page, channel: str, name: str) -> dict[str, Any]:
        return self._request(
            page,
            f"/action/mute/{channel}",
            f"{name} - MUTED",
            f"{name} - MUTE",
            (f"/action/state/{channel}", "muted"),
        )

    def macro(self, page: _Page, macro_id: str, on: str, off: str) -> dict[str, Any]:
        """A macro's toggle; the image follows the macro being on."""
        self.macros_called.append(macro_id)
        return self._request(
            page,
            f"/action/macro/{macro_id}",
            on,
            off,
            (f"/action/macro-state/{macro_id}", "active"),
        )

    def nudge(self, page: _Page, channel: str, bus: str, name: str, up: bool) -> dict[str, Any]:
        image = f"{name} - VOL {'UP' if up else 'DOWN'}"
        delta = NUDGE_DB if up else -NUDGE_DB
        return self._request(page, f"/action/nudge-aux/{channel}/{bus}/{delta}", image, image, None)

    def goto(self, page: _Page, target: int, image: str) -> dict[str, Any]:
        return {
            "Action": "com.ulanzi.ulanzideck.page.goto",
            "ActionID": self.new_id(),
            # Ulanzi Studio writes no number for the first page.
            "ActionParam": {"Page": target} if target else {},
            "LinkedTitle": True,
            "Name": "Go to page",
            "Plugin": PAGES_PLUGIN,
            "State": 0,
            "ViewParam": [{**page.icon(image, self.new_id), "Text": ""}],
        }

    def screen(self, mode: int) -> dict[str, Any]:
        """The deck's small screen (Ulanzi Studio's "background settings" action)."""
        return {
            "Action": "com.ulanzi.ulanzideck.smallwindow.window",
            "ActionID": self.new_id(),
            "ActionParam": {"SmallViewMode": mode},
            "LinkedTitle": True,
            "Name": "Configurações de fundo",
            "Plugin": {},
            "State": 0,
            "ViewParam": [{"Icon": ""}],
        }

    # --- pages -----------------------------------------------------------------------------

    def pages(self) -> list[_Page]:
        seats = self.patch.seats
        menu, hosts, production = (_Page(self.new_id()) for _ in range(3))
        in_ears = [_Page(self.new_id()) for _ in seats]
        # Positions in the profile's page list, for "Go to page".
        hosts_index = 1
        in_ear_index = {seat: 2 + n for n, (seat, _channel, _bus) in enumerate(seats)}
        production_index = 2 + len(seats)

        menu.actions = {
            "0_0": self.goto(menu, hosts_index, "HOSTS GUESTS"),
            "1_0": self.goto(menu, production_index, "PROD CREW"),
            SCREEN_KEY: self.screen(1),
        }

        hosts.actions["4_0"] = self.goto(hosts, 0, "MAIN MENU")
        for seat, channel, _bus in seats:
            column, name = seat - 1, SEAT_NAMES[seat]
            hosts.actions[f"{column}_0"] = self.mute(hosts, channel, name)
            talk_id = defaults.TALK_TO_PRODUCTION[seat][0]
            hosts.actions[f"{column}_1"] = self.macro(
                hosts, talk_id, f"{name} - PRODDED", f"{name} - PROD"
            )
            hosts.actions[SEAT_PAGE_KEYS[seat]] = self.goto(
                hosts, in_ear_index[seat], f"{name} - CONFIG"
            )
        hosts.actions[SCREEN_KEY] = self.screen(2)

        video = VIDEO_CHANNEL in self.patch.others
        for page, (seat, _channel, bus) in zip(in_ears, seats, strict=True):
            page.actions["0_2"] = self.goto(page, hosts_index, "HOSTS GUESTS")
            # Shows whose in-ears this page edits.
            page.actions["2_2"] = self.goto(
                page, in_ear_index[seat], f"{SEAT_NAMES[seat]} - EDITING"
            )
            for source, source_channel, _source_bus in seats:
                column, name = source - 1, SEAT_NAMES[source]
                page.actions[f"{column}_0"] = self.nudge(page, source_channel, bus, name, up=True)
                page.actions[f"{column}_1"] = self.nudge(page, source_channel, bus, name, up=False)
            if video:
                page.actions["4_0"] = self.nudge(page, VIDEO_CHANNEL, bus, "VIDEO", up=True)
                page.actions["4_1"] = self.nudge(page, VIDEO_CHANNEL, bus, "VIDEO", up=False)
            page.actions[SCREEN_KEY] = self.screen(1)

        production.actions["0_2"] = self.goto(production, 0, "MAIN MENU")
        seats_with_microphone = {seat for seat, _channel, _bus in seats}
        producers = zip(
            defaults.PRODUCTION_MACROS, PRODUCER_NAMES, self.patch.production, strict=False
        )
        for row, (ids, name, channel) in enumerate(producers):
            production.actions[f"0_{row}"] = self.mute(production, channel, name)
            production.actions[f"1_{row}"] = self.macro(
                production, ids.go_live, f"{name} - LIVED", f"{name} - LIVE"
            )
            # Talk to one host's in-ears: only while that host's seat has a microphone.
            for column, host, macro_id in ((2, 1, ids.to_host1), (3, 2, ids.to_host2)):
                if host in seats_with_microphone:
                    production.actions[f"{column}_{row}"] = self.macro(
                        production, macro_id, f"{name} - TALKING {host}", f"{name} - TALK {host}"
                    )
            production.actions[f"4_{row}"] = self.macro(
                production, ids.to_cam, f"{name} - TALKING CAM", f"{name} - TALK CAM"
            )
        production.actions[SCREEN_KEY] = self.screen(1)

        return [menu, hosts, *in_ears, production]


def missing_macros(settings: AudioSettings) -> list[str]:
    """The macro ids the profile's buttons call that `settings` has no macro for."""
    builder = _Builder(settings, "http://localhost", lambda: "")
    builder.pages()
    present = {macro.id for macro in settings.macros}
    return [
        macro_id for macro_id in dict.fromkeys(builder.macros_called) if macro_id not in present
    ]


def _page_manifest(page: _Page) -> dict[str, Any]:
    return {
        "Controllers": [
            {"Actions": {}, "Type": "Encoder"},
            {"Actions": dict(sorted(page.actions.items())), "Type": "Keypad"},
        ],
        "Icon": "",
        "Name": "",
    }


def _image(name: str) -> bytes:
    return IMAGES.joinpath(f"{name}.png").read_bytes()


def build_profile(
    settings: AudioSettings, base_url: str, new_id: IdFactory = lambda: str(uuid.uuid4())
) -> bytes:
    """The `.ulanziDeckProfile` for `settings`, calling the studio at `base_url`.

    Fresh ids each time (pass `new_id` for fixed ones): Ulanzi Studio imports
    it as a new profile, next to any earlier one.
    """
    base = d200_base_url(base_url)
    profile = f"{new_id()}.ulanziProfile"
    pages = _Builder(settings, base, new_id).pages()
    manifest = {
        "Device": {"Model": "D200", "UUID": DEVICE_UUID},
        "Icon": "Baja.png",
        "Name": PROFILE_NAME,
        "Pages": {"Current": pages[0].page_id, "Pages": [page.page_id for page in pages]},
        "Version": "2.0",
    }
    entries: list[tuple[str, bytes | None]] = [
        (f"{profile}/", None),
        (f"{profile}/manifest.json", _json(manifest)),
        (f"{profile}/Baja.png", _image(PROFILE_ICON)),
        (f"{profile}/Profiles/", None),
    ]
    for page in pages:
        folder = f"{profile}/Profiles/{page.page_id}"
        entries += [(f"{folder}/", None), (f"{folder}/manifest.json", _json(_page_manifest(page)))]
        for sub in ("Files", "Images"):
            entries.append((f"{folder}/{sub}/", None))
            entries += [
                (f"{folder}/{path}", _image(image))
                for path, image in sorted(page.images.items())
                if path.startswith(f"{sub}/")
            ]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            if data is None:
                info.external_attr = 0o40755 << 16 | 0x10
                zipped.writestr(info, b"", zipfile.ZIP_STORED)
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, data)
    return FORMAT_HEADER + archive.getvalue()


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=4).encode("utf-8")

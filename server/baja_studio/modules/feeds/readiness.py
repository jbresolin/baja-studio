"""The feeds module's readiness checks: MediaMTX up, its ports, its config, feeds, disk.

The service and port checks ask afresh (MediaMTX's API, Windows' port list)
rather than trusting the two-second poll, so a re-run right after a fix
shows the fix. The config and feeds checks read the module's state. None of
them changes anything.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.core.system_probes import disk_space_outcome
from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxApiClient, MediamtxUnreachableError
from baja_studio.modules.feeds.ports import FEEDS_SERVICE_NAME, feed_ports
from baja_studio.modules.feeds.system import FeedsSystem

FEEDS_PAGE_LINK = "/feeds"
START_FEEDS_FIX = (
    f'Start "{FEEDS_SERVICE_NAME}" in the Windows Services app; if it stops again, the Feeds '
    "page shows its last log lines and names whatever holds one of its ports."
)
NOT_CHECKED = "not checked: MediaMTX is not answering (see the check above)"


def feeds_checks(
    settings: FeedsSettings,
    system: FeedsSystem,
    state: Callable[[], dict[str, Any]],
    recordings_folder: Path,
) -> list[ReadinessCheck]:
    """Checks for the settings as they are now; `state()` reads `state.feeds`."""
    client = MediamtxApiClient(f"127.0.0.1:{settings.api_port}", system.api_transport)

    def answering() -> bool:
        try:
            client.info()
        except MediamtxUnreachableError:
            return False
        return True

    checks = [
        ReadinessCheck(
            "mediamtx",
            "Feeds service (MediaMTX) is running",
            lambda: _mediamtx(client),
            fix=START_FEEDS_FIX,
            link=FEEDS_PAGE_LINK,
        ),
        ReadinessCheck(
            "ports",
            "Feed ports are free for MediaMTX",
            lambda: _ports(settings, system, answering),
            fix=START_FEEDS_FIX,
            link=FEEDS_PAGE_LINK,
        ),
        ReadinessCheck(
            "config",
            "MediaMTX runs the feeds settings",
            lambda: _config(state()),
            fix="Open the Feeds page: it says what stopped the settings from being applied.",
            link=FEEDS_PAGE_LINK,
        ),
        ReadinessCheck(
            "live",
            "Every feed is live",
            lambda: _live(settings, state(), answering),
            fix=(
                "Start the stream on the sender (drone app, laptop OBS) with the URL shown on the "
                "Feeds page, or remove feeds that are not used today."
            ),
            link=FEEDS_PAGE_LINK,
        ),
    ]
    if any(feed.record for feed in settings.feeds):
        checks.append(
            ReadinessCheck(
                "recordings_disk",
                "Room for feed recordings",
                lambda: disk_space_outcome(
                    "feed recordings", system.free_disk_bytes(recordings_folder)
                ),
                fix="Free some space, or point the feeds' recordings folder at another drive.",
                link=FEEDS_PAGE_LINK,
            )
        )
    return checks


def _mediamtx(client: MediamtxApiClient) -> CheckOutcome:
    try:
        info = client.info()
    except MediamtxUnreachableError as error:
        return failed(str(error))
    return passed(f"MediaMTX {info.version} answering on {client.address}")


def _ports(
    settings: FeedsSettings, system: FeedsSystem, answering: Callable[[], bool]
) -> CheckOutcome:
    ports = feed_ports(settings)
    if answering():
        # MediaMTX exits when it cannot open a port: running means it holds them all.
        return passed("MediaMTX holds " + ", ".join(port.describe() for port in ports))
    holders = system.port_holders(ports)
    if not holders:
        return passed("nothing else listens on " + ", ".join(port.describe() for port in ports))
    return failed("; ".join(holder.message for holder in holders))


def _config(feeds_state: dict[str, Any]) -> CheckOutcome:
    config = feeds_state.get("config") or {}
    if config.get("error"):
        return failed(str(config["error"]))
    if config.get("waiting_for_reload"):
        return warning("MediaMTX has not confirmed the latest settings yet; run the checks again")
    return passed("the running config matches the settings")


def _live(
    settings: FeedsSettings, feeds_state: dict[str, Any], answering: Callable[[], bool]
) -> CheckOutcome:
    if not settings.feeds:
        return warning("no feeds configured", "Add the drone and the laptops on the Feeds page.")
    if not answering():
        return warning(NOT_CHECKED)
    cards = {card.get("name"): card for card in feeds_state.get("feeds") or []}
    waiting = [
        feed.name for feed in settings.feeds if (cards.get(feed.name) or {}).get("status") != "live"
    ]
    if waiting:
        # A feed not up yet is normal before the show: a warning, not a failure.
        return warning(f"waiting for {', '.join(waiting)}")
    return passed(f"{len(settings.feeds)} live")

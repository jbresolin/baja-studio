"""Playout's cards on the hosts screen (WP9 §3), both drawn from `playout.hosts_view`.

"Now playing" (the clip on air and a big countdown) and "Next up" (the
next three clips and what the queue still holds) are separate cards, so
production can push "Next up" to the top when a clip is about to end
without moving the countdown.
"""

from baja_studio.core.hosts_cards import HostsCard

HOSTS_VIEW_PATH = "playout.hosts_view"


def playout_hosts_cards() -> list[HostsCard]:
    """Now playing first, Next up right after it."""
    return [
        HostsCard("now_playing", "Now playing", 100, HOSTS_VIEW_PATH, "now_playing"),
        HostsCard("next_up", "Next up", 90, HOSTS_VIEW_PATH, "next_up"),
    ]

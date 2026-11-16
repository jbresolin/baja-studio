"""What the feeds module reaches outside the process, gathered so tests can swap all of it.

`main.py` builds the real one (MediaMTX's API on loopback, Windows' port
list, the PC's addresses and disks); tests/modules/feeds_studio.py builds
one from fakes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx2

from baja_studio.core.system_probes import free_disk_bytes
from baja_studio.modules.feeds.ports import PortHolderFinder, installed_port_holders
from baja_studio.modules.feeds.service_log import read_log_tail
from baja_studio.modules.gateway.probes import AddressLister, local_ipv4_addresses


@dataclass(frozen=True)
class FeedsSystem:
    """Everything the feeds module reaches outside the process; tests pass fakes."""

    api_transport: httpx2.BaseTransport | None = None
    port_holders: PortHolderFinder = installed_port_holders
    list_addresses: AddressLister = local_ipv4_addresses
    free_disk_bytes: Callable[[Path], int] = free_disk_bytes
    read_log: Callable[[Path], list[str]] = read_log_tail

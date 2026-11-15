"""Tests for how an OBS connection's state is put into words (cameras/drivers/obs_gateway.py).

Crop cameras, previews and Camera setup show why OBS cannot be asked. For
playout's OBS (on this PC) the words are as before WP13 ("OBS offline:
..."); a remote OBS's reason already names the laptop, so it is shown as is.
"""

from baja_studio.agents.contract import Status
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsLinkView


def test_playouts_obs_is_described_as_before() -> None:
    link = ObsLinkView(False, "cannot reach OBS at ws://127.0.0.1:4455")

    assert link.status == Status.OFFLINE
    assert link.problem == "OBS offline: cannot reach OBS at ws://127.0.0.1:4455"
    assert link.lost("OBS did not answer X in time") == "OBS offline: OBS did not answer X in time"
    assert link.refused("OBS refused X") == "OBS: OBS refused X"


def test_a_remote_obs_is_named_in_every_message() -> None:
    reason = "OBS at 192.168.10.50:4455 refused the password"
    link = ObsLinkView(False, reason, Status.DEGRADED, "192.168.10.50:4455")

    assert link.problem == reason
    assert link.lost("no answer") == "OBS at 192.168.10.50:4455 offline: no answer"
    assert link.refused("OBS refused X") == "OBS at 192.168.10.50:4455: OBS refused X"


def test_no_gateway_is_never_connected() -> None:
    gateway = NoObsGateway("nothing here")

    assert gateway.link() == ObsLinkView(False, "nothing here")
    assert gateway.client() is None
    gateway.add_listener(lambda kind, data: None)()  # the remover does nothing, quietly

"""The cameras module's one health, from its many workers (one per camera and controller).

Worker names say whose they are: `camera:<id>:visca`, `camera:<id>:agent`,
`camera:<id>:fallback`, `controller:<id>`, and the module's own `motion`.
The home card then reads, for instance, "finish-ptz ok · joystick offline".
"""

from baja_studio.core.supervisor import Health, HealthState

MOTION_WORKER = "motion"


def device_of(worker_name: str) -> str | None:
    """The camera or controller id a worker belongs to; None for the module's own workers.

    A camera's fallback worker is left out: the camera's agent worker already
    reports the health of whichever route is in use.
    """
    parts = worker_name.split(":")
    if len(parts) == 3 and parts[0] == "camera" and parts[2] != "fallback":
        return parts[1]
    if len(parts) == 2 and parts[0] == "controller":
        return parts[1]
    return None


def summarise(healths: dict[str, Health]) -> Health:
    """ok if every device is; offline if every device is away; degraded otherwise.

    A failed module worker (the motion loop) makes the module degraded whatever
    the devices say, because then nothing moves.
    """
    motion = healths.get(MOTION_WORKER)
    devices = {
        device: health
        for name, health in healths.items()
        if (device := device_of(name)) is not None
    }
    listing = " · ".join(
        f"{device} {health.state.value}" + (f" ({health.message})" if health.message else "")
        for device, health in devices.items()
    )
    if motion is not None and motion.state != HealthState.OK:
        message = f"motion loop: {motion.message}"
        return Health(HealthState.DEGRADED, f"{message} · {listing}" if listing else message)
    if not devices:
        return Health(HealthState.OK, "no cameras or controllers enabled")
    away = [health for health in devices.values() if health.state != HealthState.OK]
    if not away:
        return Health(HealthState.OK, listing)
    if len(away) == len(devices):
        return Health(HealthState.OFFLINE, listing)
    return Health(HealthState.DEGRADED, listing)

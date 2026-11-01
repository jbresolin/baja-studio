"""Demo child process for the `echo` module: answers `upper` with the text uppercased.

Real adapters (MIDI, UVC) follow the same shape: a handler function passed to
`run_child`, which takes care of the protocol and pings.
"""

from typing import Any

from baja_studio.core.childproc import run_child


def handle_request(method: str, params: dict[str, Any]) -> Any:
    """Called by `run_child` for each request from the service."""
    if method == "upper":
        return str(params["text"]).upper()
    raise ValueError(f"unknown method {method!r}")


if __name__ == "__main__":
    run_child(handle_request)

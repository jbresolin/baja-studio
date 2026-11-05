"""Tests for the OBS transport helpers and errors (adapters/obs_transport.py).

No socket is opened here: the real WebSocket transport is exercised on the
bench. These tests cover the address formatting and the error types the
rest of the studio relies on.
"""

from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsConnectionClosedError,
    ObsError,
    ObsRequestFailedError,
    ObsUnreachableError,
)


def test_an_address_becomes_a_ws_uri() -> None:
    assert ObsAddress("192.168.1.20", 4455).uri == "ws://192.168.1.20:4455"


def test_an_ipv6_address_is_put_in_brackets() -> None:
    # Without brackets the port's colon would be read as part of the address.
    assert ObsAddress("::1", 4455).uri == "ws://[::1]:4455"


def test_addresses_with_the_same_host_and_port_are_equal() -> None:
    # The in-process network and the OBS worker use addresses as dictionary keys.
    assert ObsAddress("obs", 4455) == ObsAddress("obs", 4455)
    assert ObsAddress("obs", 4455) != ObsAddress("obs", 4456)


def test_a_refused_request_keeps_its_code_and_says_what_was_refused() -> None:
    error = ObsRequestFailedError("SetCurrentProgramScene", 600, "No scene named X")

    assert error.code == 600
    assert error.request_type == "SetCurrentProgramScene"
    assert "SetCurrentProgramScene" in str(error)
    assert "No scene named X" in str(error)


def test_a_refused_request_without_a_comment_mentions_the_code() -> None:
    assert "error 204" in str(ObsRequestFailedError("Nope", 204, ""))


def test_a_closed_connection_keeps_its_close_code() -> None:
    error = ObsConnectionClosedError(4009, "Authentication failed.")

    assert error.code == 4009
    assert "4009" in str(error)


def test_a_closed_connection_without_a_code_says_just_closed() -> None:
    assert str(ObsConnectionClosedError(None, "")) == "connection closed"


def test_every_adapter_error_is_an_obs_error() -> None:
    # Modules catch ObsError to mean "anything OBS-related went wrong".
    assert issubclass(ObsUnreachableError, ObsError)
    assert issubclass(ObsRequestFailedError, ObsError)
    assert issubclass(ObsConnectionClosedError, ObsError)

"""Tests for the echo child's request handler (childprocs/echo_child.py)."""

import pytest

from baja_studio.childprocs.echo_child import handle_request


def test_upper_returns_the_text_in_capitals() -> None:
    assert handle_request("upper", {"text": "Baja SAE"}) == "BAJA SAE"


def test_an_unknown_method_raises_so_the_host_gets_an_error_reply() -> None:
    with pytest.raises(ValueError, match="unknown method"):
        handle_request("lower", {"text": "X"})

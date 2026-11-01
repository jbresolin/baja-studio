"""Fake child processes used by test_childproc.py.

Run as `python -m tests.core.fake_children`; the tests put the `server/`
folder on PYTHONPATH so this module can be found, and choose the behaviour
with the FAKE_CHILD_BEHAVIOUR environment variable (the host passes its
environment on to the child).

Behaviours:
  silent   read requests but never answer (simulates a hung adapter)
  chatty   send an unsolicited event, then answer normally
  failing  answer every request (other than ping) with an error
"""

import os
import sys
from typing import Any

from baja_studio.core.childproc import run_child


def refuse_everything(method: str, params: dict[str, Any]) -> Any:
    raise ValueError(f"cannot do {method}")


def answer_with_method_name(method: str, params: dict[str, Any]) -> Any:
    return method


if __name__ == "__main__":
    behaviour = os.environ.get("FAKE_CHILD_BEHAVIOUR", "chatty")
    if behaviour == "silent":
        for _ in sys.stdin:
            pass
    elif behaviour == "failing":
        run_child(refuse_everything)
    else:
        print('{"event": "hello", "data": {"from": "chatty"}}', flush=True)
        print("this line is not JSON and should be ignored", flush=True)
        run_child(answer_with_method_name)

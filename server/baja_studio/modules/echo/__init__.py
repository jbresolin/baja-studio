"""Demo module backed by a child process that uppercases text.

It shows the pattern later adapters (MIDI, UVC) use: the worker owns a child
process, the supervisor restarts it when it dies, and health goes
`offline` → `ok` as it comes back.
"""

import threading
from typing import Any

from baja_studio.core.childproc import ChildProcessHost, ChildRequestError, run_child_session
from baja_studio.core.module import CommandError, Module
from baja_studio.core.supervisor import DeviceOfflineError, WorkerContext, WorkerSpec

CHILD_PYTHON_MODULE = "baja_studio.childprocs.echo_child"


class EchoModule(Module):
    """Round-trips `say` through the child; `kill_child` demonstrates recovery."""

    name = "echo"
    title = "Echo (demo child process)"

    def __init__(self) -> None:
        """No child until the worker starts one."""
        super().__init__()
        self._host_lock = threading.Lock()
        self._host: ChildProcessHost | None = None

    def initial_state(self) -> dict[str, Any]:
        """Nothing said yet."""
        return {"last_reply": None}

    def workers(self) -> list[WorkerSpec]:
        """One worker that owns the child process."""
        return [WorkerSpec("child", self._run_child, starts_offline=True)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`say {text}` returns the text uppercased by the child; `kill_child` kills it."""
        if command == "say":
            if "text" not in args:
                raise CommandError("say needs a 'text' argument")
            try:
                reply = self._current_host().request("upper", {"text": str(args["text"])})
            except (DeviceOfflineError, ChildRequestError) as error:
                raise CommandError(str(error)) from error
            self.context.update_state("last_reply", reply)
            return reply
        if command == "kill_child":
            self.context.logger.warning("killing echo child on request from %s", source)
            self._current_host().kill()
            return None
        raise CommandError(f"echo has no command {command!r}; try 'say' or 'kill_child'")

    def _run_child(self, worker: WorkerContext) -> None:
        """Worker body: a fresh child for each run, pinged until it dies or we stop."""
        host = self.context.new_child_process(CHILD_PYTHON_MODULE)
        with self._host_lock:
            self._host = host
        try:
            run_child_session(host, worker)
        finally:
            with self._host_lock:
                if self._host is host:
                    self._host = None

    def _current_host(self) -> ChildProcessHost:
        with self._host_lock:
            host = self._host
        if host is None or not host.is_running:
            raise CommandError("the echo child is not running right now; try again shortly")
        return host

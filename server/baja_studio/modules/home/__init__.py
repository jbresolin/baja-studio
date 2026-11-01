"""The home module: the landing page's list of modules and their health."""

import threading
from typing import Any

from baja_studio.core.module import Module, ModuleContext
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.supervisor import Health


class HomeModule(Module):
    """Publishes every module's title and health into `state.home.modules`.

    The home page reads that subtree, so health badges update live without
    the page polling `/api/health`.
    """

    name = "home"
    title = "Home"
    can_be_disabled = False

    def __init__(self, registry: ModuleRegistry) -> None:
        """`registry` is where the list of modules (and their titles) comes from."""
        super().__init__()
        self._registry = registry
        self._listening = threading.Event()

    def initial_state(self) -> dict[str, Any]:
        """No modules known until `start`."""
        return {"modules": {}}

    def start(self, context: ModuleContext) -> None:
        """Publish the current health of every module, then follow changes."""
        super().start(context)
        for module in self._registry.modules():
            self._publish(module.name, context.supervisor.module_health(module.name))
        # Home cannot be disabled, but guard anyway so a restart never doubles the listener.
        if not self._listening.is_set():
            self._listening.set()
            context.supervisor.add_health_listener(self._publish)

    def _publish(self, module_name: str, health: Health) -> None:
        """Health listener: write one module's entry into the home state."""
        title = next(
            (module.title for module in self._registry.modules() if module.name == module_name),
            module_name,
        )
        self.context.update_state(
            f"modules.{module_name}",
            {"title": title, "state": health.state.value, "message": health.message},
        )

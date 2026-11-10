"""The tray's words, in Brazilian Portuguese for the operators, English otherwise.

Windows' display language picks the set: Portuguese gets pt-BR, anything
else (or a language that cannot be read) gets English.
"""

import ctypes
import locale
import sys
from collections.abc import Callable
from dataclasses import dataclass

# The primary-language part of a Windows LANGID, for Portuguese (pt-BR and pt-PT).
LANG_PORTUGUESE = 0x16


@dataclass(frozen=True)
class Labels:
    """Every text the tray shows."""

    open_studio: str
    readiness: str
    restart_services: str
    open_logs: str
    diagnostics_bundle: str
    version: str
    quit: str
    all_ok: str
    service_unreachable: str
    problems_one: str
    problems_many: str
    status_prefix: str
    restart_started: str
    restart_cancelled: str
    bundle_building: str
    bundle_ready: str
    bundle_failed: str
    readiness_at: str
    warning_one: str
    warnings_many: str
    failure_one: str
    failures_many: str

    def modules_with_problems(self, titles: list[str]) -> str:
        """`attention: Playout, Audio`; the count when no title came with the answer."""
        if not titles:
            return self.problems_many.format(count="?")
        if len(titles) == 1:
            return self.problems_one.format(names=titles[0])
        return self.problems_many.format(count=len(titles)) + ": " + ", ".join(titles)

    def unexpected_status(self, status: str) -> str:
        """For a status the tray does not know (shown red)."""
        return f"{self.status_prefix} {status}"

    def warnings_count(self, count: int) -> str:
        """`1 warning`, `3 warnings`."""
        return self.warning_one if count == 1 else self.warnings_many.format(count=count)

    def failures_count(self, count: int) -> str:
        """`1 failure`, `3 failures`."""
        return self.failure_one if count == 1 else self.failures_many.format(count=count)

    def readiness_result(self, time: str, counts: list[str]) -> str:
        """`readiness at 14:32: 2 warnings, 1 failure`, for the tooltip."""
        return f"{self.readiness_at.format(time=time)}: {', '.join(counts)}"

    def version_item(self, version: str | None) -> str:
        """The menu's version line."""
        return f"{self.version} {version or '?'}"


PT_BR = Labels(
    open_studio="Abrir o Baja Studio",
    readiness="Verificação antes do evento (Readiness)",
    restart_services="Reiniciar os serviços",
    open_logs="Abrir a pasta de logs",
    diagnostics_bundle="Gerar pacote de diagnóstico",
    version="Versão",
    quit="Sair",
    all_ok="tudo certo",
    service_unreachable="o serviço não responde",
    problems_one="atenção: {names}",
    problems_many="atenção: {count} módulos com problema",
    status_prefix="estado",
    restart_started="Reiniciando os serviços…",
    restart_cancelled="Reinício cancelado.",
    bundle_building="Gerando o pacote de diagnóstico…",
    bundle_ready="Pacote salvo em {path}",
    bundle_failed="Não foi possível gerar o pacote: {error}",
    readiness_at="verificação das {time}",
    warning_one="1 aviso",
    warnings_many="{count} avisos",
    failure_one="1 falha",
    failures_many="{count} falhas",
)

EN = Labels(
    open_studio="Open Baja Studio",
    readiness="Readiness",
    restart_services="Restart services",
    open_logs="Open logs folder",
    diagnostics_bundle="Diagnostics bundle",
    version="Version",
    quit="Quit",
    all_ok="everything ok",
    service_unreachable="the service is not answering",
    problems_one="attention: {names}",
    problems_many="attention: {count} modules with problems",
    status_prefix="status",
    restart_started="Restarting the services…",
    restart_cancelled="Restart cancelled.",
    bundle_building="Building the diagnostics bundle…",
    bundle_ready="Bundle saved to {path}",
    bundle_failed="The bundle could not be built: {error}",
    readiness_at="readiness at {time}",
    warning_one="1 warning",
    warnings_many="{count} warnings",
    failure_one="1 failure",
    failures_many="{count} failures",
)


def labels_for(language: str | None) -> Labels:
    """pt-BR for any Portuguese language tag (`pt-BR`, `pt_BR`, `pt`), English otherwise."""
    if language and language.replace("_", "-").lower().split("-")[0] == "pt":
        return PT_BR
    return EN


def windows_ui_language_is_portuguese(
    user_ui_language: Callable[[], int] | None = None,
) -> bool:
    """True when Windows' display language is Portuguese (its LANGID's primary part)."""
    if user_ui_language is None:
        if sys.platform != "win32":
            return False
        user_ui_language = ctypes.windll.kernel32.GetUserDefaultUILanguage
    return (user_ui_language() & 0x3FF) == LANG_PORTUGUESE


def system_labels() -> Labels:
    """The labels for this PC's display language."""
    if windows_ui_language_is_portuguese():
        return PT_BR
    return labels_for(locale.getlocale()[0])

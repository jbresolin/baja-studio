"""Tests for choosing the tray's language (labels.py)."""

from dataclasses import fields

import pytest

from baja_studio_tray.labels import EN, PT_BR, labels_for, windows_ui_language_is_portuguese


@pytest.mark.parametrize("language", ["pt-BR", "pt_BR", "pt", "PT-br", "pt-PT"])
def test_any_portuguese_language_gets_the_operators_labels(language: str) -> None:
    assert labels_for(language) is PT_BR


@pytest.mark.parametrize("language", ["en-US", "es-ES", "", None])
def test_anything_else_falls_back_to_english(language: str | None) -> None:
    assert labels_for(language) is EN


def test_windows_portuguese_brazil_is_recognised() -> None:
    # 0x0416 is Windows' LANGID for pt-BR; 0x0816 is pt-PT; 0x0409 is en-US.
    assert windows_ui_language_is_portuguese(lambda: 0x0416)
    assert windows_ui_language_is_portuguese(lambda: 0x0816)
    assert not windows_ui_language_is_portuguese(lambda: 0x0409)


def test_every_label_is_translated() -> None:
    # A label left equal to the English one is probably a forgotten translation;
    # the only ones meant to be equal are listed here.
    same_on_purpose = {"problems_one"}
    untranslated = [
        item.name
        for item in fields(PT_BR)
        if getattr(PT_BR, item.name) == getattr(EN, item.name) and item.name not in same_on_purpose
    ]
    assert untranslated == []


def test_the_version_line_shows_a_question_mark_before_the_first_answer() -> None:
    assert PT_BR.version_item(None) == "Versão ?"
    assert EN.version_item("0.2.0") == "Version 0.2.0"

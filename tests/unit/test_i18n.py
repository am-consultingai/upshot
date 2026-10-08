"""The Python side's words (app/i18n.py): the tray, the toasts, the installer's progress."""

from __future__ import annotations

import string

import pytest

from app import i18n
from app.config import default_config
from app.i18n import LANGUAGES, MESSAGES, number, tr
from app.notify import FakeNotifier, make_notifier
from app.tray_state import AppState, RecorderState, icon_for, menu_for


def placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_line_is_in_every_language_with_the_same_fields(key: str) -> None:
    entry = MESSAGES[key]
    assert set(entry) == set(LANGUAGES), key
    for language in LANGUAGES:
        assert entry[language].strip(), (key, language)
        assert placeholders(entry[language]) == placeholders(entry["en"]), (key, language)


def test_the_languages_are_the_settings_ones() -> None:
    from app.config import _ENUMS

    assert set(LANGUAGES) == set(_ENUMS["ui.language"])


def test_an_unknown_language_falls_back_to_english() -> None:
    assert tr("tray.quit", "xx") == "Quit"
    assert tr("tray.update", "pt", version="1.2") == "Restart to update to 1.2"


def test_a_missing_translation_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(i18n.MESSAGES, "test.only", {"en": "Hello {name}"})
    assert tr("test.only", "de", name="Ada") == "Hello Ada"


def test_numbers_take_the_language_s_separator() -> None:
    assert number(6700, "en") == "6,700"
    assert number(6700, "de") == "6.700"
    assert number(6700, "fr") == "6 700"
    assert number(12, "fr") == "12"


# -- the tray


def test_the_tray_menu_in_german() -> None:
    labels = [item.label for item in menu_for(AppState(language="de", update_ready="2.0"))]
    assert labels == [
        "Aufnahme starten",
        "Aufnahme beenden",
        "Pause",
        "Upshot öffnen",
        "Feedback senden…",
        "Neu starten und auf 2.0 aktualisieren",
        "Beenden",
    ]
    paused = menu_for(AppState(recorder=RecorderState.PAUSED, language="de"))
    assert paused[2].label == "Fortsetzen"


def test_the_tray_menu_and_tooltip_in_hebrew() -> None:
    spec = icon_for(
        AppState(recorder=RecorderState.RECORDING, meeting_title="סטנדאפ", language="he")
    )
    assert [item.label for item in spec.menu][:2] == ["התחלת הקלטה", "עצירת הקלטה"]
    assert spec.menu[-1].label == "יציאה"
    assert spec.tooltip == "Upshot — מקליט — סטנדאפ"


def test_the_tooltip_counts_jobs_in_the_language() -> None:
    one = icon_for(AppState(processing=True, queue_depth=1, language="es")).tooltip
    many = icon_for(AppState(processing=True, queue_depth=3, language="es")).tooltip
    assert one == "Upshot — Procesando 1 tarea"
    assert many == "Upshot — Procesando 3 tareas"


def test_english_is_unchanged() -> None:
    labels = [item.label for item in menu_for(AppState())]
    assert labels == [
        "Start recording",
        "Stop recording",
        "Pause",
        "Open Upshot",
        "Send feedback…",
        "Quit",
    ]
    assert icon_for(AppState()).tooltip == "Upshot — Idle"


# -- toasts


def test_a_toast_in_french() -> None:
    notifier = FakeNotifier(language=lambda: "fr")
    notifier.recording_started("m1", "Point hebdo")
    toast = notifier.shown[-1]
    assert toast.title == "Enregistrement démarré : Point hebdo"
    assert [b.label for b in toast.buttons] == ["Arrêter", "Pas une réunion"]

    notifier.recording_ended("m2", 0, reason="both tracks were silent")
    ended = notifier.shown[-1]
    assert ended.title == "Réunion terminée — moins d'une minute. Transcription…"
    assert ended.body.startswith("Personne n'a parlé")

    notifier.failed("m3", "summarize")
    assert notifier.shown[-1].title == "Échec : Résumé — l'audio est en sécurité"


def test_a_stage_the_catalogue_does_not_name_keeps_its_own_name() -> None:
    notifier = FakeNotifier(language=lambda: "de")
    notifier.failed("m1", "upload")
    assert notifier.shown[-1].title == "Upload fehlgeschlagen – die Audiodatei ist sicher"


def test_the_fallback_words_are_translated() -> None:
    notifier = FakeNotifier(language=lambda: "es")
    notifier.meeting_soon("e1", "", minutes=1, conference_url="https://meet.example/x")
    toast = notifier.shown[-1]
    assert toast.title == "Una reunión empieza en 1 minuto"
    assert [b.label for b in toast.buttons] == ["Unirse"]

    notifier.call_detected(
        "Teams.exe", None, candidates=((None, "cal", "ev1", ""), (None, "cal", "ev2", "Plan"))
    )
    detected = notifier.shown[-1]
    assert detected.title == "Reunión iniciada: Teams"
    assert [b.label for b in detected.buttons] == [
        "Grabar: sin título",
        "Grabar: Plan",
        "Descartar",
    ]


def test_the_notifier_follows_the_setting_as_it_changes() -> None:
    config = default_config(delivery__notifier="fake")
    notifier = make_notifier(config)
    notifier.summary_ready("m1", "Sync")
    config.set("ui.language", "de")
    notifier.summary_ready("m2", "Sync")
    assert isinstance(notifier, FakeNotifier)
    assert notifier.titles() == ["Summary ready — Sync", "Zusammenfassung fertig – Sync"]

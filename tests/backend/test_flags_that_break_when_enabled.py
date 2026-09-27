"""Drei Flags, die das Einschalten nicht ueberlebten.

Alle drei sind aus `tasks/backlog/TRIAGE.md` (Triage vom 2026-09-20). Sie haben
eine Eigenschaft gemeinsam, die sie ueberhaupt so lange ueberleben liess: der
Fehler liegt hinter einem AUSGESCHALTETEN Schalter, also faellt kein Test und
kein Betrieb darauf. Nur ein Test, der den Schalter selbst umlegt, sieht ihn.
Deshalb legt jeder Test hier den Schalter um.

🛑 Der vierte Befund der Triage — `MEMORY_EXTRACTION_V2_AUTHORITATIVE` kehre vor
dem Subsume-Tor zurueck — ist UEBERHOLT und braucht keinen Test hier: #1313 hat
das Tor am 2026-09-23 in den v2-Pfad eingebaut
(`conversation_memory_service.py:1596-1600`, dieselbe `_should_subsume_fact`).
`test_memory_extraction_v2.py` deckt es ab.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# 1. VOICE_AUTH_ENABLED — Signatur und Rueckgabetyp
# ---------------------------------------------------------------------------
class TestVoiceAuthCallShape:
    """Die Route rief `identify_speaker(audio_bytes)` auf — EIN Wert, wo zwei
    Pflichtargumente stehen — und las das Ergebnis als `dict`, obwohl ein
    `tuple` kommt. `VOICE_AUTH_ENABLED=true` war damit ein `TypeError` bei jedem
    Anmeldeversuch.
    """

    def test_identify_speaker_needs_two_arguments(self):
        # Der Vertrag, gegen den die Route verstiess. Aendert er sich, muss die
        # Route mitwandern — deshalb steht er hier als Behauptung.
        import inspect

        from services.speaker_service import SpeakerService

        sig = inspect.signature(SpeakerService.identify_speaker)
        required = [
            n for n, p in sig.parameters.items()
            if n != "self" and p.default is inspect.Parameter.empty
        ]
        assert required == ["query_embedding", "known_speakers"]

    def test_identify_speaker_returns_a_tuple_not_a_dict(self):
        from services.speaker_service import SpeakerService

        svc = SpeakerService()
        q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        known = [(7, "Anna", np.array([1.0, 0.0, 0.0], dtype=np.float32))]
        out = svc.identify_speaker(q, known)
        assert isinstance(out, tuple), "die Route las ein dict — das gab es nie"
        assert out[0] == 7 and out[1] == "Anna"

    def test_the_route_does_not_call_the_enrolling_resolver(self):
        """🛑 Ein ANMELDEVERSUCH darf niemals ein Profil anlegen.

        `resolve_speaker_from_embedding` schreibt einen unbekannten Sprecher als
        „Unbekannter Sprecher #N" an und verstaerkt bestehende Profile. Auf einem
        Anmeldepfad hiesse das: jeder Fehlversuch legt einen Stimmabdruck an
        (biometrisches Datum ohne Einwilligung), und ein Fremder koennte ein
        Profil nach und nach auf seine Stimme ziehen. Der Test liest den
        Quelltext, weil das Weglassen eines Aufrufs sonst nicht pruefbar ist.
        """
        from api.routes import auth

        body = _function_body(auth.voice_authenticate)
        assert "resolve_speaker_from_embedding" not in body, (
            "die Sprachanmeldung darf nur LESEN, nicht einschreiben"
        )
        assert "build_known_speaker_centroids" in body

    def test_the_route_sets_cookies_and_reports_the_password_gate(self):
        """Die anderen vier Anmeldewege tun beides — dieser tat keins von beidem.

        Ohne `_set_auth_cookies` richtete eine erfolgreiche Sprachanmeldung
        ueberhaupt keine Sitzung ein (die Oberflaeche arbeitet seit #1125 auf dem
        HttpOnly-Cookie). Und ohne `must_change_password` kaeme ein Konto mit
        Passwortzwang per Stimme daran vorbei.
        """
        import inspect

        from api.routes import auth

        src = inspect.getsource(auth.voice_authenticate)
        assert "_set_auth_cookies(" in src
        assert "must_change_password=user.must_change_password" in src
        assert "must_change_password" in auth.VoiceAuthResponse.model_fields

    def test_the_route_refuses_when_recognition_is_off(self):
        """Ein ECAPA-Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO). Ist die
        Erkennung aus, darf hier auch keiner BERECHNET werden — derselbe
        Grundsatz, den `speaker_resolver` schon durchsetzt."""
        import inspect

        from api.routes import auth

        src = inspect.getsource(auth.voice_authenticate)
        assert "settings.speaker_recognition_enabled" in src


# ---------------------------------------------------------------------------
# 3. /api/speakers/identify — derselbe Massstab wie die Erkennung
# ---------------------------------------------------------------------------
class TestOneCentroidCalculation:
    """Das Diagnose-Endpunkt antwortete aus einem anderen Modell als die
    Erkennung. Drei Abweichungen, jede einzeln genug fuer eine andere Antwort.
    """

    def test_normalisation_follows_either_switch(self, monkeypatch):
        # 🛑 Der Kern. Die Route pruefte nur `speaker_quality_gating_enabled`.
        # Im Haushalt ist `controlled` an und `gating` aus — also normalisierte
        # die Erkennung und die Route nicht.
        from services import speaker_resolver as sr

        monkeypatch.setattr("utils.config.settings.speaker_quality_gating_enabled", False)
        monkeypatch.setattr("utils.config.settings.speaker_controlled_enrollment_enabled", True)
        gating, controlled, quality_active = sr.known_speaker_flags()
        assert gating is False
        assert controlled is True
        assert quality_active is True, "controlled allein muss die Normalisierung einschalten"

        monkeypatch.setattr("utils.config.settings.speaker_controlled_enrollment_enabled", False)
        assert sr.known_speaker_flags()[2] is False

    def test_the_route_uses_the_shared_builder(self):
        import inspect

        from api.routes import speakers

        src = inspect.getsource(speakers.get_speaker_embeddings_averaged)
        assert "build_known_speaker_centroids" in src
        assert "np.linalg.norm" not in src, "keine zweite Kopie der Rechnung"

    def test_controlled_mode_compares_only_against_enrolled_profiles(self):
        from services.speaker_resolver import build_known_speaker_centroids
        known, _with_emb = build_known_speaker_centroids(
            [_speaker(1, "Anna", enrolled=True), _speaker(2, "Gast", enrolled=False)],
            controlled=True, quality_active=True,
        )
        assert [k[1] for k in known] == ["Anna"]
        # Ohne den Schalter sind beide dabei — sonst waere die Route vor Phase 3
        # stillschweigend enger geworden.
        known_all, _ = build_known_speaker_centroids(
            [_speaker(1, "Anna", enrolled=True), _speaker(2, "Gast", enrolled=False)],
            controlled=False, quality_active=True,
        )
        assert sorted(k[1] for k in known_all) == ["Anna", "Gast"]

    def test_only_the_ten_most_recent_embeddings_count(self):
        # Die Route mittelte ALLE. Ein Profil mit 40 alten und 3 neuen
        # Einbettungen ergibt so einen anderen Schwerpunkt als die Erkennung.
        from services.speaker_resolver import (
            MAX_EMBEDDINGS_PER_SPEAKER,
            build_known_speaker_centroids,
        )
        # 12 Einbettungen: die 10 jüngsten sind Einsen, die 2 aeltesten Nullen.
        sp = _speaker(1, "Anna", enrolled=True, values=[1.0] * 10 + [0.0] * 2)
        known, _ = build_known_speaker_centroids(
            [sp], controlled=True, quality_active=False
        )
        assert MAX_EMBEDDINGS_PER_SPEAKER == 10
        assert known[0][2].tolist() == [1.0], "die zwei aeltesten duerfen nicht mitzaehlen"

    def test_normalisation_stops_a_loud_sample_from_dominating(self):
        # Rohe ECAPA-Normen schwanken ~250-410; ohne Normalisierung zieht die
        # laute Aufnahme den Schwerpunkt zu sich.
        from services.speaker_resolver import build_known_speaker_centroids
        sp = _speaker(1, "Anna", enrolled=True, values=[1.0, 100.0])
        raw, _ = build_known_speaker_centroids([sp], controlled=False, quality_active=False)
        norm, _ = build_known_speaker_centroids([sp], controlled=False, quality_active=True)
        assert raw[0][2].tolist() == [50.5]
        assert norm[0][2].tolist() == [1.0]


# --- Helfer -----------------------------------------------------------------
def _function_body(fn) -> str:
    """Der Quelltext OHNE Docstring.

    Sonst schlaegt ein Test auf „dieser Aufruf kommt hier nicht vor" an der
    Begruendung an, die genau diesen Aufruf beim Namen nennt — mir am 2026-09-27
    einmal passiert.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(fn).lstrip())
    node = tree.body[0]
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    return "\n".join(ast.unparse(n) for n in body)


class _Emb:
    def __init__(self, value: float, created_at):
        from services.speaker_service import SpeakerService
        self.embedding = SpeakerService.embedding_to_base64(
            np.array([value], dtype=np.float32)
        )
        self.created_at = created_at


class _Speaker:
    def __init__(self, sid, name, enrolled, embeddings):
        self.id, self.name, self.enrolled, self.embeddings = sid, name, enrolled, embeddings


def _speaker(sid, name, *, enrolled, values=(1.0,)):
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1)
    # Der ERSTE Wert ist der jüngste, damit `values` sich wie „neu zuerst" liest.
    return _Speaker(sid, name, enrolled,
                    [_Emb(v, base - timedelta(days=i)) for i, v in enumerate(values)])

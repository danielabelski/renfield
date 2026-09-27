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
    """Der VERTRAG, an dem die Route einst scheiterte — und der weiter gilt.

    `identify_speaker` lebt weiter: der Resolver und `/api/speakers/identify`
    brauchen die 1:N-Zuordnung. Die ANMELDUNG benutzt sie seit dem Umbau zum
    Zusatzfaktor nicht mehr. Diese Vertragspruefungen bleiben, weil eine Aenderung
    an der Signatur die beiden verbliebenen Aufrufer mitnehmen muss.

    Historisch: die Route rief `identify_speaker(audio_bytes)` auf — EIN Wert, wo zwei
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
        # `build_known_speaker_centroids` stand hier bis zum Umbau zum
        # Zusatzfaktor. Seit dem 2026-09-27 verifiziert die Route 1:1 gegen EIN
        # Profil und baut gar keine Schwerpunkte mehr — das prueft
        # `TestTheRouteIsNoLongerAnIdentifier`.

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


# ---------------------------------------------------------------------------
# 1b. Der ZWEITE Faktor — Verhalten, nicht Quelltext
# ---------------------------------------------------------------------------
@pytest.mark.database
class TestVoiceSecondFactor:
    """Die Route nach dem Umbau vom 2026-09-27: Stimme bestaetigt, sie oeffnet nicht.

    🛑 Diese Klasse FAEHRT die beiden Routen, statt ihren Quelltext zu lesen. Die
    erste Fassung dieser Datei pruefte `inspect.getsource`-Textstellen; das haette
    jede der Bruecken ueberlebt, solange die richtigen Zeichenketten im Koerper
    stehen. Geprueft wird hier ueber Ergebnisse: gesetzte Cookies, ausgegebene
    Token, die ABWESENHEIT von Token, und dass es keinen Weg ohne Stimme gibt.
    """

    @staticmethod
    def _upload(data: bytes = b"RIFFfake", name: str = "turn.wav"):
        class _Up:
            filename = name

            async def read(self):
                return data

        return _Up()

    @staticmethod
    def _request():
        """Eine ECHTE starlette-Anfrage: der Ratenbegrenzer prueft den Typ."""
        from starlette.requests import Request

        return Request({
            "type": "http", "method": "POST", "path": "/api/auth/voice",
            "raw_path": b"/api/auth/voice", "root_path": "", "scheme": "http",
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
            "server": ("test", 80), "app": None,
        })

    async def _user(self, db, *, second_factor=True, with_speaker=True,
                    must_change=False, active=True):
        import numpy as np

        from models.database import Role, Speaker, SpeakerEmbedding, User
        from services.speaker_service import SpeakerService

        role = Role(name=f"V2F{id(self) % 100000}", description="", permissions=["chat.own"])
        db.add(role)
        await db.flush()
        sid = None
        if with_speaker:
            sp = Speaker(name="Anna", enrolled=True)
            db.add(sp)
            await db.flush()
            db.add(SpeakerEmbedding(
                speaker_id=sp.id,
                embedding=SpeakerService.embedding_to_base64(
                    np.array([1.0, 0.0], dtype=np.float32)),
            ))
            sid = sp.id
        user = User(username=f"anna{id(self) % 100000}", password_hash="x",
                    role_id=role.id, is_active=active, token_epoch=0,
                    speaker_id=sid, must_change_password=must_change,
                    voice_second_factor_enabled=second_factor)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    def _stub_voice(monkeypatch, *, verified, score):
        """Nur die ML-Haelfte ersetzen. Der Rest der Route laeuft echt."""
        import numpy as np

        from services import speaker_service as ss

        class _Svc:
            def extract_embedding_from_bytes(self, _b, _n):
                return np.array([1.0, 0.0], dtype=np.float32)

            def verify_speaker(self, _q, _claimed):
                return (verified, score)

            @staticmethod
            def embedding_from_base64(enc):
                return ss.SpeakerService.embedding_from_base64(enc)

        monkeypatch.setattr(ss, "get_speaker_service", lambda: _Svc())

    @staticmethod
    def _stub_ticket(monkeypatch, *, user_id):
        """Redis-freies Ticket. Der Speicher hat seine eigenen Tests."""
        from services import voice_second_factor_store as store

        async def _consume(_t, _ip):
            return store.PendingVoiceFactor(user_id=user_id, client_ip=None)

        monkeypatch.setattr(store, "consume_ticket", _consume)

    @staticmethod
    def _on(monkeypatch, **over):
        from utils.config import settings

        base = {"voice_auth_enabled": True, "speaker_recognition_enabled": True,
                "auth_cookie_enabled": True, "voice_auth_min_confidence": 0.7}
        base.update(over)
        for k, v in base.items():
            monkeypatch.setattr(settings, k, v)

    # --- der gute Fall ---------------------------------------------------

    async def test_both_factors_pass_then_tokens_and_cookies(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        user = await self._user(db_session)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.92)

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, ticket="t", audio_file=self._upload(), db=db_session)

        assert out.success is True
        assert out.access_token and out.refresh_token
        cookies = resp.headers.getlist("set-cookie")
        assert settings.auth_cookie_name in {c.split("=", 1)[0] for c in cookies}
        assert any("httponly" in c.lower() for c in cookies)

    async def test_it_reports_the_password_gate(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session, must_change=True)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.92)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        assert out.success is True
        assert out.must_change_password is True

    # --- die Wege, die ES NICHT GEBEN DARF -------------------------------

    _FORBIDDEN = ("access_token", "refresh_token")

    def _assert_opaque(self, out, resp=None):
        assert out.success is False
        assert out.message == "Voice authentication failed", (
            "der Text verraet, WELCHE Bedingung fehlschlug"
        )
        for f in self._FORBIDDEN:
            assert getattr(out, f) is None, f"{f} auf einem Fehlschlag gesetzt"
        # 🛑 Und die Felder, die das Orakel trugen, existieren im Modell nicht mehr.
        for gone in ("speaker_id", "speaker_name", "confidence", "user_id", "username"):
            assert not hasattr(out, gone), (
                f"{gone} ist zurueck im Antwortmodell — das war das Orakel"
            )
        if resp is not None:
            assert resp.headers.getlist("set-cookie") == []

    async def test_no_ticket_means_no_check_at_all(self, db_session, monkeypatch):
        """🛑 Der Kern des Zusatzfaktors: ohne bestandenen ersten Faktor kommt man
        nicht einmal bis zur Stimmpruefung. Damit ist die Namensaufzaehlung des
        alten Orakels nicht mehr erreichbar."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from services import voice_second_factor_store as store

        await self._user(db_session)
        self._on(monkeypatch)

        async def _no(_t, _ip):
            return None

        monkeypatch.setattr(store, "consume_ticket", _no)
        extracted = []
        import numpy as np

        from services import speaker_service as ss

        class _Svc:
            def extract_embedding_from_bytes(self, _b, _n):
                extracted.append(1)
                return np.array([1.0, 0.0], dtype=np.float32)

        monkeypatch.setattr(ss, "get_speaker_service", lambda: _Svc())

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, ticket="bogus", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out, resp)
        assert not extracted, "ohne Ticket darf keine Einbettung berechnet werden"

    async def test_a_wrong_voice_is_refused_and_mints_nothing(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=False, score=0.31)

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, ticket="t", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out, resp)

    async def test_a_verified_but_weak_score_is_refused(self, db_session, monkeypatch):
        """Die eigene Schwelle der Route gilt zusaetzlich zu `verify_speaker`."""
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session)
        self._on(monkeypatch, voice_auth_min_confidence=0.9)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.75)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out)

    async def test_consent_revoked_between_factors_is_refused(self, db_session, monkeypatch):
        """Dem Ticket wird nicht geglaubt: zwischen erstem und zweitem Faktor kann
        die Einwilligung widerrufen worden sein."""
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session, second_factor=False)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.99)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out)

    async def test_a_deactivated_account_is_refused(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session, active=False)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.99)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out)

    async def test_consent_without_a_profile_fails_closed(self, db_session, monkeypatch):
        """Einwilligung ohne verknuepftes Profil: nichts, wogegen geprueft werden
        koennte. Nicht durchlassen."""
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session, with_speaker=False)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.99)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out)

    async def test_recognition_off_computes_no_embedding(self, db_session, monkeypatch):
        """Ein ECAPA-Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO)."""
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session)
        self._on(monkeypatch, speaker_recognition_enabled=False)
        self._stub_ticket(monkeypatch, user_id=user.id)
        extracted = []
        import numpy as np

        from services import speaker_service as ss

        class _Svc:
            def extract_embedding_from_bytes(self, _b, _n):
                extracted.append(1)
                return np.array([1.0, 0.0], dtype=np.float32)

        monkeypatch.setattr(ss, "get_speaker_service", lambda: _Svc())

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        assert out.success is False
        assert not extracted


class TestTheRouteIsNoLongerAnIdentifier:
    """🛑 Der Riegel gegen das Zurueckrutschen.

    Drei Befunde des Reviews vom 2026-09-27 verschwinden NICHT durch eine Pruefung,
    sondern durch die Form: die Route identifiziert nicht mehr, sie verifiziert.
    Faellt sie auf 1:N zurueck, sind Namensorakel und Muenzwurf sofort wieder da.
    Deshalb pruefen diese drei Behauptungen den Routenkoerper — ergaenzend zu den
    Verhaltenstests oben, nicht an deren Stelle.
    """

    pytestmark = [pytest.mark.unit]

    def test_it_verifies_instead_of_identifying(self):
        from api.routes import auth

        body = _function_body(auth.voice_authenticate)
        assert "verify_speaker" in body, "1:1-Pruefung fehlt"
        assert "identify_speaker" not in body, "zurueck auf Argmax ueber alle Profile"
        assert "build_known_speaker_centroids" not in body, (
            "die Route baut wieder alle Schwerpunkte — das ist 1:N"
        )

    def test_it_requires_a_ticket(self):
        import inspect

        from api.routes import auth

        sig = inspect.signature(auth.voice_authenticate)
        assert "ticket" in sig.parameters, (
            "ohne Ticket waere die Stimme wieder der ERSTE Faktor"
        )

    def test_the_response_model_carries_no_identity(self):
        from api.routes.auth import VoiceAuthResponse

        for gone in ("speaker_id", "speaker_name", "confidence", "user_id", "username"):
            assert gone not in VoiceAuthResponse.model_fields, (
                f"{gone} ist zurueck — genau diese Felder waren das Orakel"
            )

    def test_login_can_withhold_tokens(self):
        """Der Anmeldepfad MUSS Token zurueckhalten koennen, sonst ist der zweite
        Faktor Zierrat."""
        from api.routes.auth import TokenResponse

        assert TokenResponse.model_fields["access_token"].default is None
        assert "second_factor_ticket" in TokenResponse.model_fields


class TestTheMarginGateIsShared:
    """`match_known_speaker` bleibt — fuer die Erkennung und `/api/speakers/identify`.

    Die Anmeldung benutzt es nach dem Umbau NICHT mehr (sie verifiziert 1:1), aber
    die beiden 1:N-Pfade tun es, und dort war die fehlende Laeufer-Marge der
    Muenzwurf-Befund.
    """

    pytestmark = [pytest.mark.unit]

    @staticmethod
    def _two_close_profiles():
        import numpy as np

        q = np.array([1.0, 0.0], dtype=np.float32)
        a = np.array([1.0, 0.05], dtype=np.float32)
        b = np.array([1.0, 0.10], dtype=np.float32)
        return q, [(1, "Anna", a), (2, "Admin", b)]

    def test_a_coin_flip_is_refused_under_controlled(self, monkeypatch):
        from services.speaker_resolver import match_known_speaker

        monkeypatch.setattr("utils.config.settings.speaker_recognition_threshold", 0.25)
        monkeypatch.setattr("utils.config.settings.speaker_match_min_margin", 0.1)
        q, known = self._two_close_profiles()

        mid, best, runner = match_known_speaker(q, known, controlled=True)
        assert best >= 0.25, "Voraussetzung: beide ueber der Schwelle"
        assert (best - runner) < 0.1, "Voraussetzung: zu nah beieinander"
        assert mid is None, "ein Muenzwurf darf NICHT zuordnen"

    def test_without_controlled_it_is_plain_argmax(self, monkeypatch):
        from services.speaker_resolver import match_known_speaker

        monkeypatch.setattr("utils.config.settings.speaker_recognition_threshold", 0.25)
        monkeypatch.setattr("utils.config.settings.speaker_match_min_margin", 0.1)
        q, known = self._two_close_profiles()

        mid, _b, _r = match_known_speaker(q, known, controlled=False)
        assert mid is not None, "ohne kontrollierte Erkennung gilt die Marge nicht"


@pytest.mark.database
class TestLoginWithholdsTokensForSecondFactor:
    """🛑 Der Kern des Zusatzfaktors, und die Stelle, an der er scheitern würde.

    Ein zweiter Faktor, nach dem der erste schon Zugriff gewährt hat, ist keiner.
    `/auth/login` muss für ein Konto mit `voice_second_factor_enabled` deshalb
    KEINE Token und KEINE Cookies ausgeben, sondern nur ein Ticket. Fällt diese
    Verzweigung weg, ist die Stimme Dekoration — und man merkt es nicht, weil sich
    die Anmeldung dann einfach normal anfühlt.
    """

    @pytest.fixture(autouse=True)
    def _own_session(self, monkeypatch, db_session):
        """🛑 `DBProvider` oeffnet eine EIGENE Sitzung (`auth/providers/db.py:40`,
        `AsyncSessionLocal`) — der Vertrag der Anbieter traegt kein `db`. Ohne
        diese Umlenkung sieht der Anbieter-Walk den Testnutzer nicht und die
        Anmeldung endet in 401 „Incorrect username or password", was wie ein
        Testfehler aussieht und keiner ist. Dasselbe Muster wie
        `test_auth_cookies.py::ws_session_factory`.
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        import services.database as db_mod

        monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_sessionmaker(
            db_session.bind, class_=AsyncSession, expire_on_commit=False,
        ))

    @staticmethod
    def _request():
        from starlette.requests import Request

        return Request({
            "type": "http", "method": "POST", "path": "/api/auth/login",
            "raw_path": b"/api/auth/login", "root_path": "", "scheme": "http",
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
            "server": ("test", 80), "app": None,
        })

    @staticmethod
    def _form(username="anna2fa", password="pw"):
        class _F:
            pass

        f = _F()
        f.username = username
        f.password = password
        return f

    async def _user(self, db, *, second_factor: bool):
        """🛑 EINDEUTIGE Namen je Lauf, und das ist kein Stilpunkt.

        Die Testbank ist echtes, geteiltes Postgres, und die Anmeldesperre liegt
        in echtem Redis. Mit festen Namen nimmt ein Lauf den Zustand des vorigen
        mit: meine eigenen fehlgeschlagenen Versuche haben `anna2fa` gesperrt, und
        danach schlugen ALLE Tests dieser Klasse mit 401 fehl — ein Fehlerbild, das
        wie ein Produktfehler aussieht und keiner ist. Dieselbe Falle wie in
        `test_pc20260423_migration.py`; dort mit demselben Mittel gelöst.
        """
        import uuid

        from models.database import Role, User
        from services.auth_service import get_password_hash

        tag = uuid.uuid4().hex[:8]
        role = Role(name=f"L2F-{tag}", description="", permissions=["chat.own"])
        db.add(role)
        await db.flush()
        user = User(username=f"anna-{tag}",
                    password_hash=get_password_hash("pw"), role_id=role.id,
                    is_active=True, token_epoch=0,
                    voice_second_factor_enabled=second_factor)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    def _stub_ticket(monkeypatch, *, ticket="TICKET"):
        from services import voice_second_factor_store as store

        async def _issue(_uid, _ip):
            return ticket

        monkeypatch.setattr(store, "issue_ticket", _issue)

    async def test_it_returns_a_ticket_and_no_tokens(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        user = await self._user(db_session, second_factor=True)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "auth_cookie_enabled", True)
        self._stub_ticket(monkeypatch)

        resp = Response()
        out = await auth_routes.login(
            self._request(), form_data=self._form(user.username), db=db_session,
            response=resp)

        assert out.second_factor == "voice"
        assert out.second_factor_ticket == "TICKET"
        assert out.access_token is None, "der erste Faktor hat Token ausgegeben"
        assert out.refresh_token is None
        assert resp.headers.getlist("set-cookie") == [], (
            "der erste Faktor hat eine Sitzung eingerichtet — dann sperrt die "
            "Stimme nichts mehr"
        )

    async def test_a_normal_account_is_untouched(self, db_session, monkeypatch):
        """Der Flag-off-Pfad muss byte-identisch bleiben."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        user = await self._user(db_session, second_factor=False)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "auth_cookie_enabled", True)

        resp = Response()
        out = await auth_routes.login(
            self._request(), form_data=self._form(user.username), db=db_session,
            response=resp)
        assert out.access_token and out.refresh_token
        assert out.second_factor is None
        assert resp.headers.getlist("set-cookie") != []

    async def test_with_the_voice_route_off_the_hurdle_rests(self, db_session, monkeypatch):
        """🛑 Sonst wäre das Konto ausgesperrt: die Hürde greift nur, wenn es auch
        eine Tür gibt. Ist `VOICE_AUTH_ENABLED` aus, könnte niemand das Ticket
        einlösen — dann läuft der Passwortpfad normal weiter und die Einwilligung
        ruht."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        user = await self._user(db_session, second_factor=True)
        monkeypatch.setattr(settings, "voice_auth_enabled", False)
        monkeypatch.setattr(settings, "auth_cookie_enabled", True)

        out = await auth_routes.login(
            self._request(), form_data=self._form(user.username), db=db_session,
            response=Response())
        assert out.access_token, "ohne Sprachweg muss der Passwortpfad durchlassen"
        assert out.second_factor is None

    async def test_no_ticket_means_no_login_at_all(self, db_session, monkeypatch):
        """🛑 Fail-closed. Ein Redis-Ausfall darf den zweiten Faktor nicht
        stillschweigend entfernen — er muss die Anmeldung verweigern."""
        from fastapi import HTTPException, Response

        from api.routes import auth as auth_routes
        from services import voice_second_factor_store as store
        from utils.config import settings

        user = await self._user(db_session, second_factor=True)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)

        async def _none(_uid, _ip):
            return None

        monkeypatch.setattr(store, "issue_ticket", _none)

        with pytest.raises(HTTPException) as ei:
            await auth_routes.login(
                self._request(), form_data=self._form(user.username),
                db=db_session, response=Response())
        assert ei.value.status_code == 503, (
            "ohne Ticket darf es weder Token noch eine stille Umgehung geben"
        )

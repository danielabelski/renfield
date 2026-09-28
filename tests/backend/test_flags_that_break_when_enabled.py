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


# ---------------------------------------------------------------------------
# 1b. VOICE_AUTH_ENABLED — die Route WIRKLICH fahren, nicht ihren Quelltext lesen
# ---------------------------------------------------------------------------
@pytest.mark.database
class TestVoiceAuthBehaviour:
    """🛑 Die Tests oben pruefen Quelltext-Textstellen. Das war zu wenig.

    Ein Test, der `inspect.getsource` durchsucht, belegt, dass ein Aufruf DASTEHT
    — nicht, dass er wirkt. Er haette jede der vier Bruecken dieser Route
    ueberlebt, solange nur die richtigen Zeichenketten im Koerper stehen. Dieser
    Block fuehrt die Funktion aus und behauptet ueber das ERGEBNIS: gesetzte
    Cookies, gemeldeter Passwortzwang, verweigerte Anmeldung ohne Erkennung, und
    kein geschriebenes Profil.
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
        r"""Eine ECHTE starlette-Anfrage aus einem minimalen ASGI-Scope.

        Nachgebaut ging es nicht: der Ratenbegrenzer-Dekorator prueft den Typ und
        wirft `parameter \`request\` must be an instance of
        starlette.requests.Request`. Ein Attrappen-Objekt haette den Test an
        einer Stelle scheitern lassen, die mit der Route nichts zu tun hat.
        """
        from starlette.requests import Request

        return Request({
            "type": "http",
            "method": "POST",
            "path": "/api/auth/voice",
            "raw_path": b"/api/auth/voice",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "app": None,
        })

    async def _linked_user(self, db, *, must_change=False):
        """Ein Sprecher mit einer Einbettung, verknuepft mit einem Nutzer."""
        import numpy as np

        from models.database import Role, Speaker, SpeakerEmbedding, User
        from services.speaker_service import SpeakerService

        role = Role(name="VA", description="", permissions=["chat.own"])
        db.add(role)
        await db.flush()
        sp = Speaker(name="Anna", enrolled=True)
        db.add(sp)
        await db.flush()
        db.add(SpeakerEmbedding(
            speaker_id=sp.id,
            embedding=SpeakerService.embedding_to_base64(
                np.array([1.0, 0.0], dtype=np.float32)),
        ))
        user = User(username="anna", password_hash="x", role_id=role.id,
                    is_active=True, token_epoch=0, speaker_id=sp.id,
                    must_change_password=must_change)
        db.add(user)
        await db.commit()
        await db.refresh(sp)
        await db.refresh(user)
        return sp, user

    @staticmethod
    def _stub_service(monkeypatch, *, match):
        """Die ML-Haelfte ersetzen: Einbettung und Zuordnung. Alles andere echt."""
        import numpy as np

        from services import speaker_service as ss

        class _Svc:
            def extract_embedding_from_bytes(self, _b, _n):
                return np.array([1.0, 0.0], dtype=np.float32)

            def identify_speaker(self, _q, _known):
                return match

            @staticmethod
            def embedding_from_base64(enc):
                return ss.SpeakerService.embedding_from_base64(enc)

        monkeypatch.setattr(ss, "get_speaker_service", lambda: _Svc())
        monkeypatch.setattr("services.speaker_resolver.get_speaker_service", lambda: _Svc())

    async def test_success_sets_the_httponly_cookies(self, db_session, monkeypatch):
        """🛑 Der Kern. Ohne `_set_auth_cookies` richtete eine erfolgreiche
        Sprachanmeldung ueberhaupt keine Sitzung ein — die Oberflaeche arbeitet
        seit #1125 auf dem HttpOnly-Cookie. Der Quelltext-Test sah nur, dass der
        Aufruf DASTEHT."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        sp, _user2 = await self._linked_user(db_session)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "auth_cookie_enabled", True)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.5)
        self._stub_service(monkeypatch, match=(sp.id, sp.name, 0.99))

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, audio_file=self._upload(), db=db_session)

        assert out.success is True
        assert out.username == "anna"
        cookies = resp.headers.getlist("set-cookie")
        names = {c.split("=", 1)[0] for c in cookies}
        assert settings.auth_cookie_name in names, f"kein Zugriffs-Cookie: {names}"
        assert any("httponly" in c.lower() for c in cookies), "Cookie nicht HttpOnly"

    async def test_it_reports_the_password_gate(self, db_session, monkeypatch):
        """Sonst kaeme ein Konto mit Passwortzwang per Stimme daran vorbei."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        sp, _user = await self._linked_user(db_session, must_change=True)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.5)
        self._stub_service(monkeypatch, match=(sp.id, sp.name, 0.99))

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), audio_file=self._upload(), db=db_session)
        assert out.success is True
        assert out.must_change_password is True

    async def test_recognition_off_refuses_before_any_embedding(self, db_session, monkeypatch):
        """Ein ECAPA-Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO). Ist die
        Erkennung aus, darf hier auch keiner BERECHNET werden."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", False)
        extracted = []

        import numpy as np

        from services import speaker_service as ss

        class _Svc:
            def extract_embedding_from_bytes(self, _b, _n):
                extracted.append(1)
                return np.array([1.0, 0.0], dtype=np.float32)

        monkeypatch.setattr(ss, "get_speaker_service", lambda: _Svc())

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), audio_file=self._upload(), db=db_session)
        assert out.success is False
        assert not extracted, "ohne Erkennung darf keine Einbettung berechnet werden"

    async def test_an_unknown_voice_creates_no_speaker(self, db_session, monkeypatch):
        """🛑 Ein ANMELDEVERSUCH darf niemals ein Profil anlegen — sonst legte
        jeder Fehlversuch einen Stimmabdruck ohne Einwilligung an, und ein
        Fremder koennte ein Profil auf seine Stimme ziehen."""
        from fastapi import Response
        from sqlalchemy import func, select

        from api.routes import auth as auth_routes
        from models.database import Speaker
        from utils.config import settings

        _sp, _user = await self._linked_user(db_session)
        before = (await db_session.execute(select(func.count()).select_from(Speaker))).scalar()

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        self._stub_service(monkeypatch, match=None)   # keine Zuordnung

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), audio_file=self._upload(), db=db_session)
        assert out.success is False
        after = (await db_session.execute(select(func.count()).select_from(Speaker))).scalar()
        assert after == before, "ein Fehlversuch hat ein Sprecherprofil angelegt"

    async def test_low_confidence_is_refused(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        sp, _user = await self._linked_user(db_session)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.9)
        self._stub_service(monkeypatch, match=(sp.id, sp.name, 0.60))

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, audio_file=self._upload(), db=db_session)
        assert out.success is False
        assert out.access_token is None
        assert resp.headers.getlist("set-cookie") == [], "abgelehnt, aber Cookie gesetzt"


@pytest.mark.database
class TestVoiceAuthLeaksNothing:
    """🛑 Das Orakel. Gefunden vom Sicherheits- UND vom adversarialen Durchgang.

    Bis zum /review gab JEDER Fehlschlag `speaker_id`, `speaker_name`,
    `confidence`, und bei einem deaktivierten Konto auch `user_id` und
    `username` an einen UNANGEMELDETEN Aufrufer zurueck. Das ist zweierlei
    Angriff in einem:

    * eine Namensliste des Haushalts ohne Zugangsdaten;
    * ein GRADIENT — `match_known_speaker` trifft ab
      `speaker_recognition_threshold` (0,25), die Route verlangt
      `voice_auth_min_confidence` (0,7). Das Band [0,25 – 0,70) lieferte also
      Name plus zweistelligen Kosinuswert: Audio aendern, Zahl steigen sehen,
      bei 0,70 aufhoeren. Bei einem Faktor ohne Lebendigkeitspruefung ist genau
      das die fehlende Rueckkopplung fuer einen Wiedereinspielungs-Angriff.

    Diese Tests pruefen die ABWESENHEIT von Feldern. Ohne sie kommt das Orakel
    beim naechsten „gib doch eine hilfreichere Fehlermeldung" zurueck.
    """

    _FORBIDDEN = ("speaker_id", "speaker_name", "confidence", "user_id", "username")

    def _assert_opaque(self, out):
        assert out.success is False
        assert out.message == "Voice authentication failed", (
            "der Text verraet, WELCHE Bedingung fehlschlug"
        )
        for field in self._FORBIDDEN:
            value = getattr(out, field)
            assert value in (None, 0.0), f"{field} wird an einen Unangemeldeten verraten: {value!r}"

    async def test_a_match_below_the_auth_bar_reveals_nothing(self, db_session, monkeypatch):
        """Der Gradient: 0,25 <= score < 0,70 — erkannt, aber nicht gut genug."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        b = TestVoiceAuthBehaviour()
        sp, _u = await b._linked_user(db_session)
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_threshold", 0.25)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.7)
        monkeypatch.setattr(settings, "speaker_controlled_enrollment_enabled", False)
        b._stub_service(monkeypatch, match=(sp.id, sp.name, 0.55))

        resp = Response()
        out = await auth_routes.voice_authenticate(
            b._request(), resp, audio_file=b._upload(), db=db_session)
        self._assert_opaque(out)
        assert resp.headers.getlist("set-cookie") == []

    async def test_an_unlinked_speaker_reveals_nothing(self, db_session, monkeypatch):
        """Erkannt, aber kein Konto daran — das waere eine Aussage ueber den
        Haushalt und darf nicht nach draussen."""
        import numpy as np
        from fastapi import Response

        from api.routes import auth as auth_routes
        from models.database import Speaker, SpeakerEmbedding
        from services.speaker_service import SpeakerService
        from utils.config import settings

        sp = Speaker(name="Gast", enrolled=True)
        db_session.add(sp)
        await db_session.flush()
        db_session.add(SpeakerEmbedding(
            speaker_id=sp.id,
            embedding=SpeakerService.embedding_to_base64(np.array([1.0, 0.0], dtype=np.float32)),
        ))
        await db_session.commit()
        await db_session.refresh(sp)

        b = TestVoiceAuthBehaviour()
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.5)
        monkeypatch.setattr(settings, "speaker_controlled_enrollment_enabled", False)
        b._stub_service(monkeypatch, match=(sp.id, sp.name, 0.99))

        out = await auth_routes.voice_authenticate(
            b._request(), Response(), audio_file=b._upload(), db=db_session)
        self._assert_opaque(out)

    async def test_a_disabled_account_reveals_no_username(self, db_session, monkeypatch):
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        b = TestVoiceAuthBehaviour()
        sp, user = await b._linked_user(db_session)
        user.is_active = False
        await db_session.commit()

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "voice_auth_min_confidence", 0.5)
        monkeypatch.setattr(settings, "speaker_controlled_enrollment_enabled", False)
        b._stub_service(monkeypatch, match=(sp.id, sp.name, 0.99))

        out = await auth_routes.voice_authenticate(
            b._request(), Response(), audio_file=b._upload(), db=db_session)
        self._assert_opaque(out)


class TestTheMarginGateIsShared:
    """🛑 Geteilt wurde erst nur der Schwerpunkt-BAUER, nicht die ENTSCHEIDUNG.

    `SpeakerService.identify_speaker` ist reines Argmax ueber einer Schwelle.
    Der Resolver verlangt unter der kontrollierten Erkennung zusaetzlich einen
    Abstand zum Zweitplatzierten. Solange die Anmeldung `identify_speaker`
    benutzte, galt: Audio, das 0,72 gegen ZWEI Haushaltsmitglieder erreicht,
    wird von der Erkennung als Muenzwurf abgelehnt — und haette sich an der
    Anmeldung als das naechstliegende Profil ANGEMELDET. Eines dieser Profile
    kann das Administratorkonto sein.

    Der Test vergleicht die beiden Entscheidungen direkt, damit die Halbierung
    nicht zurueckkommt.
    """

    pytestmark = [pytest.mark.unit]

    @staticmethod
    def _two_close_profiles():
        import numpy as np

        # Zwei Profile, beide nah an der Anfrage und nah aneinander.
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
        assert best >= 0.25, "Voraussetzung: beide liegen ueber der Schwelle"
        assert (best - runner) < 0.1, "Voraussetzung: sie liegen zu nah beieinander"
        assert mid is None, "ein Muenzwurf zwischen zwei Profilen darf NICHT zuordnen"

    def test_without_controlled_it_is_plain_argmax(self, monkeypatch):
        """Die Gegenrichtung — sonst wuerde der Test auch bei einer Marge
        bestehen, die IMMER greift."""
        from services.speaker_resolver import match_known_speaker

        monkeypatch.setattr("utils.config.settings.speaker_recognition_threshold", 0.25)
        monkeypatch.setattr("utils.config.settings.speaker_match_min_margin", 0.1)
        q, known = self._two_close_profiles()

        mid, _best, _runner = match_known_speaker(q, known, controlled=False)
        assert mid is not None, "ohne kontrollierte Erkennung gilt die Marge nicht"

    def test_the_route_uses_the_shared_decision_not_argmax(self):
        """Der Riegel gegen das Zurueckrutschen: die Route darf
        `identify_speaker` nicht wieder direkt aufrufen."""
        from api.routes import auth

        body = _function_body(auth.voice_authenticate)
        assert "match_known_speaker" in body
        assert "identify_speaker" not in body, (
            "die Anmeldung waere wieder reines Argmax ohne Laeufer-Marge"
        )

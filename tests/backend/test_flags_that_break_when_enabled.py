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

    def test_both_sides_of_the_hurdle_ask_the_same_checker(self):
        """🛑 Der Befund, gegen den das steht: `/auth/login` stellte die Huerde
        auf EINE Bedingung und `/auth/voice` raeumte sie auf FUENF ab. Jede
        Luecke dazwischen war eine Aussperrung, denn es gibt keinen Rueckfall
        auf Passwort allein.

        Die Pruefung liegt darum in `services/voice_factor_preconditions`, und
        BEIDE Seiten fragen dort. Das ist bewusst eine Strukturpruefung: das
        Verhalten steht in `TestTheHurdleRestsInsteadOfLockingOut`
        (`test_voice_factor_preconditions.py`) und in
        `test_the_route_refuses_while_recognition_is_off` weiter unten — hier
        geht es darum, dass die beiden Seiten nicht wieder auseinanderlaufen,
        indem jemand eine eigene Bedingung danebenschreibt.
        """
        import inspect

        from api.routes import auth

        login_src = inspect.getsource(auth.login)
        voice_src = inspect.getsource(auth.voice_authenticate)
        assert "second_factor_applies" in login_src
        assert "voice_path_blocker" in voice_src
        # Und nicht mehr von Hand daneben:
        assert "settings.voice_auth_enabled" not in login_src, (
            "die Anmeldeseite prueft wieder selbst — genau so ist der Fehler entstanden"
        )


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

    @pytest.fixture(autouse=True)
    def _no_rate_limit(self, monkeypatch):
        """Ratenbegrenzer aus, weil diese Tests den Handler DIREKT aufrufen.

        Dasselbe Muster wie `test_auth.py:280` ("its wrapper guards everything
        behind `if self.enabled`"): ein Direktaufruf soll nicht an einem
        Zaehler haengen, den andere Tests fuellen, und slowapi greift auf
        `request.app.state.limiter` zu (`extension.py:83`), das eine handgebaute
        `Request` nicht hat.

        🛑 Berichtigung an mir selbst: hier stand zuerst, dieser Bypass behebe
        die vier Fehlschlaege der Vollsuite. Das war FALSCH — er hat sie nicht
        behoben, die Ursache war die Namensbindung in `_own_session`. Ein
        Kommentar, der eine widerlegte Ursache behauptet, kostet den naechsten
        Leser denselben Weg noch einmal. Der Bypass bleibt, weil er fuer sich
        richtig ist; was er kostet, holt `TestTheRateLimitIsStillDeclared`
        zurueck.
        """
        from services.api_rate_limiter import limiter

        monkeypatch.setattr(limiter, "enabled", False)

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
    def _stub_voice(monkeypatch, *, verified, score, duration_s=3.0, embedding=(1.0, 0.0)):
        """Nur die ML-Haelfte ersetzen. Der Rest der Route laeuft echt.

        🛑 Die Einbettung kommt seit dem Review vom VOICE-SERVER, nicht aus
        `SpeakerService`. Der alte Weg konnte in der Produktion nie gelingen:
        `speaker_inprocess_embeddings_enabled` ist auf beiden Instanzen aus, und
        selbst offen waere es der falsche Vektorraum. Deshalb wird hier `stt`
        gestellt — und zwar an dem Modul, aus dem die Route importiert, nicht an
        der Quelle: `from ... import stt` bindet den Namen zur Importzeit.
        """
        from services import voice_server_client as vsc

        async def _stt(_audio, **_kw):
            return {"speaker_embedding": list(embedding), "audio_duration_s": duration_s}

        monkeypatch.setattr(vsc, "stt", _stt)

        from services import speaker_service as ss

        class _Svc:
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

    # --- die Vorbedingungen, verhaltensecht -------------------------------

    async def test_the_route_refuses_while_recognition_is_off(self, db_session, monkeypatch):
        """🛑 Der zweite Riegel, den die Anmeldeseite frueher nie abgefragt hat.

        Ein Stimmabdruck ist biometrisches Datum (Art. 9 DSGVO): ist die
        Erkennung aus, wird auch fuer die Anmeldung keiner berechnet. Der Test
        prueft BEIDES — die Absage und dass kein Audio hinausgeht.
        """
        from fastapi import Response

        from api.routes import auth as auth_routes
        from services import voice_server_client as vsc

        user = await self._user(db_session)
        self._on(monkeypatch, speaker_recognition_enabled=False)
        self._stub_ticket(monkeypatch, user_id=user.id)

        sent = []

        async def _stt(_audio, **_kw):
            sent.append(1)
            return {"speaker_embedding": [1.0, 0.0], "audio_duration_s": 3.0}

        monkeypatch.setattr(vsc, "stt", _stt)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        assert out.success is False
        assert not out.access_token
        assert not sent, "bei abgeschalteter Erkennung darf kein Audio den Prozess verlassen"

    async def test_a_sample_below_the_server_measured_minimum_is_refused(
        self, db_session, monkeypatch
    ):
        """🛑 H3, jetzt geschlossen. Die 1,5 s in der Maske sind Bedienfuehrung,
        keine Sicherheitspruefung — ein Angreifer schickt direkt an die API. Die
        einzige Dauer, der hier zu trauen ist, misst der voice-server selbst.
        """
        from fastapi import Response

        from api.routes import auth as auth_routes

        user = await self._user(db_session)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)
        self._stub_voice(monkeypatch, verified=True, score=0.99, duration_s=0.3)

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        assert out.success is False, "eine 0,3-s-Probe darf nicht anmelden, auch nicht mit 0.99"
        assert not out.access_token

    async def test_a_voice_server_outage_is_not_counted_as_a_failed_attempt(
        self, db_session, monkeypatch
    ):
        """Ein Betriebsfehler ist kein Fehlversuch der Person — sonst sperrt ein
        Ausfall des voice-servers die Leute zusaetzlich aus ihrem Konto aus."""
        from fastapi import Response

        from api.routes import auth as auth_routes
        from services import voice_server_client as vsc

        user = await self._user(db_session)
        self._on(monkeypatch)
        self._stub_ticket(monkeypatch, user_id=user.id)

        async def _boom(_audio, **_kw):
            raise vsc.VoiceServerError("voice-server down")

        monkeypatch.setattr(vsc, "stt", _boom)

        failures = []
        import utils.metrics as metrics

        monkeypatch.setattr(metrics, "record_login_failure", lambda r: failures.append(r))

        out = await auth_routes.voice_authenticate(
            self._request(), Response(), ticket="t", audio_file=self._upload(), db=db_session)
        assert out.success is False
        assert not out.access_token
        assert "voice_second_factor" not in failures

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
        # 🛑 Die Sonde sitzt jetzt am VOICE-SERVER, nicht an `SpeakerService` —
        # dort wird die Einbettung berechnet. Der Test wird dadurch schaerfer:
        # er beweist, dass die Aufnahme das Haus gar nicht erst verlaesst,
        # bevor das Ticket gilt.
        extracted = []

        from services import voice_server_client as vsc

        async def _stt(_audio, **_kw):
            extracted.append(1)
            return {"speaker_embedding": [1.0, 0.0], "audio_duration_s": 3.0}

        monkeypatch.setattr(vsc, "stt", _stt)

        resp = Response()
        out = await auth_routes.voice_authenticate(
            self._request(), resp, ticket="bogus", audio_file=self._upload(), db=db_session)
        self._assert_opaque(out, resp)
        assert not extracted, "ohne gueltiges Ticket darf kein Audio an den voice-server gehen"

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
        # 🛑 Die Sonde sitzt jetzt am VOICE-SERVER, nicht an `SpeakerService` —
        # dort wird die Einbettung berechnet. Der Test wird dadurch schaerfer:
        # er beweist, dass die Aufnahme das Haus gar nicht erst verlaesst,
        # bevor das Ticket gilt.
        extracted = []

        from services import voice_server_client as vsc

        async def _stt(_audio, **_kw):
            extracted.append(1)
            return {"speaker_embedding": [1.0, 0.0], "audio_duration_s": 3.0}

        monkeypatch.setattr(vsc, "stt", _stt)

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
    def _no_rate_limit(self, monkeypatch):
        """Ratenbegrenzer aus, weil diese Tests den Handler DIREKT aufrufen.

        Dasselbe Muster wie `test_auth.py:280` ("its wrapper guards everything
        behind `if self.enabled`"): ein Direktaufruf soll nicht an einem
        Zaehler haengen, den andere Tests fuellen, und slowapi greift auf
        `request.app.state.limiter` zu (`extension.py:83`), das eine handgebaute
        `Request` nicht hat.

        🛑 Berichtigung an mir selbst: hier stand zuerst, dieser Bypass behebe
        die vier Fehlschlaege der Vollsuite. Das war FALSCH — er hat sie nicht
        behoben, die Ursache war die Namensbindung in `_own_session`. Ein
        Kommentar, der eine widerlegte Ursache behauptet, kostet den naechsten
        Leser denselben Weg noch einmal. Der Bypass bleibt, weil er fuer sich
        richtig ist; was er kostet, holt `TestTheRateLimitIsStillDeclared`
        zurueck.
        """
        from services.api_rate_limiter import limiter

        monkeypatch.setattr(limiter, "enabled", False)

    @pytest.fixture(autouse=True)
    def _own_session(self, monkeypatch, db_session):
        """🛑 BEIDE Namen umbiegen — und das ist der eigentliche Fund.

        `DBProvider` oeffnet eine EIGENE Sitzung (der Vertrag der Anbieter traegt
        kein `db`), und `auth/providers/db.py:18` holt sie ueber
        `from services.database import AsyncSessionLocal` — ein auf MODULEBENE
        GEBUNDENER Name. Ein `monkeypatch.setattr` auf `services.database`
        erreicht diese Bindung NICHT, sobald das Modul einmal importiert ist.

        Genau daran hingen vier Tests, und der Fehlermodus war heimtueckisch:

        * **Allein** ist `auth.providers.db` beim Fixture-Lauf noch nicht
          importiert. Der Patch landet zuerst, der spaetere Import KOPIERT den
          gepatchten Wert — die Tests waren gruen.
        * **In der Suite** hat ein frueherer Test das Modul laengst geladen. Es
          haelt die echte Fabrik, der Anbieter sucht den Testnutzer in der
          falschen Datenbank, findet ihn nicht, und die Anmeldung endet in
          `bad credentials` — was wie ein Produktfehler aussieht.

        Also beide Bindungen. Und nicht nur die, die gerade weh tut: `services.database`
        bleibt gepatcht, weil andere Pfade sie zur Laufzeit nachschlagen.
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        import auth.providers.db as db_provider
        import services.database as db_mod

        factory = async_sessionmaker(
            db_session.bind, class_=AsyncSession, expire_on_commit=False,
        )
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
        monkeypatch.setattr(db_provider, "AsyncSessionLocal", factory)

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

        from models.database import Role, Speaker, SpeakerEmbedding, User
        from services.auth_service import get_password_hash

        tag = uuid.uuid4().hex[:8]
        role = Role(name=f"L2F-{tag}", description="", permissions=["chat.own"])
        db.add(role)
        await db.flush()

        # 🛑 MIT Profil und Einbettung, und das ist kein Beiwerk.
        #
        # Diese Klasse bewies „die Anmeldung haelt die Token zurueck" an einem
        # Nutzer OHNE Sprecherprofil — also an einem, der den zweiten Faktor nie
        # haette einloesen koennen. Unter dem alten Code war das genau die
        # Aussperrung, und der Test feierte sie als Erfolg. Seit die Huerde nur
        # noch steht, wenn sie auch faellt, verlangt der Nachweis einen Nutzer,
        # der wirklich hindurchkaeme.
        speaker = Speaker(name=f"stimme-{tag}")
        db.add(speaker)
        await db.flush()
        db.add(SpeakerEmbedding(speaker_id=speaker.id, embedding="AAAA"))

        user = User(username=f"anna-{tag}",
                    password_hash=get_password_hash("pw"), role_id=role.id,
                    is_active=True, token_epoch=0,
                    speaker_id=speaker.id,
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


    async def test_without_a_usable_profile_the_hurdle_rests(self, db_session, monkeypatch):
        """🛑 Die Gegenkontrolle zur ganzen Klasse.

        Sechs Routen koennen das Sprecherprofil nach der Einwilligung
        entfernen. Frueher hielt die Anmeldung danach trotzdem die Token zurueck
        und `/auth/voice` verweigerte fail-closed: das Konto war zu, ohne dass
        jemand etwas falsch gemacht haette. Jetzt ruht die Huerde, der
        Passwortweg bleibt offen, und die Einwilligung bleibt GESPEICHERT — sie
        ist der Nachweis einer Erklaerung der Person, kein Schalter, den das
        System still umlegen darf.
        """
        from fastapi import Response

        from api.routes import auth as auth_routes
        from utils.config import settings

        user = await self._user(db_session, second_factor=True)
        user.speaker_id = None
        await db_session.commit()

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "auth_cookie_enabled", False)

        out = await auth_routes.login(
            self._request(), form_data=self._form(user.username), db=db_session,
            response=Response())

        assert out.access_token, "ohne einloesbaren Faktor darf das Passwort wieder genuegen"
        assert out.second_factor is None
        await db_session.refresh(user)
        assert user.voice_second_factor_enabled is True, "die Einwilligung bleibt stehen"


class TestTheRateLimitIsStillDeclared:
    """🛑 Was der Bypass kostet, wird hier zurückgeholt.

    `TestVoiceSecondFactor` und `TestLoginWithholdsTokensForSecondFactor` schalten
    den Ratenbegrenzer ab, weil sie die Handler DIREKT aufrufen (slowapi greift
    sonst auf `request.app.state.limiter` zu, das eine handgebaute Anfrage nicht
    hat). Damit prüft aber auch kein Test mehr, dass die Begrenzung überhaupt
    noch an den Routen HÄNGT — und beide sind unangemeldet erreichbar.

    Ohne diesen Riegel könnte jemand den Dekorator entfernen und alle Tests
    blieben grün. Das ist genau die Lücke, die ein Workaround typischerweise
    aufreisst, ohne dass es jemand bemerkt.
    """

    pytestmark = [pytest.mark.unit]

    @pytest.mark.parametrize("route", ["login", "voice_authenticate"])
    def test_the_auth_routes_carry_the_rate_limiter(self, route):
        from api.routes import auth

        fn = getattr(auth, route)
        # slowapi wickelt den Handler mit `functools.wraps`; die Kette ist über
        # `__wrapped__` erreichbar. Ist sie leer, ist der Dekorator weg.
        assert hasattr(fn, "__wrapped__"), (
            f"`{route}` traegt keinen Dekorator mehr — die Ratenbegrenzung auf "
            f"einer unangemeldet erreichbaren Anmelderoute ist weg"
        )

    def test_the_auth_limit_is_stricter_than_the_default(self):
        """Die Anmelderouten laufen auf `api_rate_limit_auth`, nicht auf dem
        allgemeinen Wert — sonst waere die strengere Grenze nur Dekoration."""
        from utils.config import settings

        def _per_minute(spec: str) -> float:
            n, _, unit = spec.partition("/")
            factor = {"second": 60.0, "minute": 1.0, "hour": 1 / 60, "day": 1 / 1440}
            return float(n) * factor[unit.strip().rstrip("s") or "minute"]

        assert _per_minute(settings.api_rate_limit_auth) <= _per_minute(
            settings.api_rate_limit_default
        ), "die Anmeldegrenze ist nicht strenger als die allgemeine"

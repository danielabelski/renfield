"""Die Hürde wird genau dann gestellt, wenn sie auch fällt.

🛑 DER BEFUND, GEGEN DEN DIESE DATEI STEHT
==========================================
`/auth/login` stellte die Hürde, sobald `VOICE_AUTH_ENABLED` an war.
`/auth/voice` verlangte zum Einlösen VIER weitere Dinge. Jede Lücke zwischen
den beiden Mengen war eine Aussperrung — es gibt bewusst keinen Rückfall auf
Passwort allein. Gefunden wurden fünf Ausprägungen desselben Fehlers; hier
steht für jede eine Gegenkontrolle.
"""
from unittest.mock import MagicMock

import pytest


def _user(**kw):
    u = MagicMock()
    u.id = kw.get("id", 1)
    u.speaker_id = kw.get("speaker_id", 7)
    u.voice_second_factor_enabled = kw.get("consent", True)
    return u


class TestPathBlocker:
    """Die Schalter der Instanz — beide, nicht nur einer."""

    @pytest.mark.unit
    def test_the_voice_path_being_off_blocks(self, monkeypatch):
        from services.voice_factor_preconditions import PATH_OFF, voice_path_blocker
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", False)
        assert voice_path_blocker() == PATH_OFF

    @pytest.mark.unit
    def test_recognition_being_off_blocks_too(self, monkeypatch):
        """🛑 Der zweite Riegel, den die Anmeldeseite NIE abgefragt hat.

        `/auth/voice` verweigert kategorisch bei abgeschalteter Sprecherkennung.
        Wer sie abschaltet — eine naheliegende Datenschutz-Handlung — sperrte
        damit jedes Konto mit Einwilligung aus, Passwortpfad eingeschlossen.
        """
        from services.voice_factor_preconditions import RECOGNITION_OFF, voice_path_blocker
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", False)
        assert voice_path_blocker() == RECOGNITION_OFF

    @pytest.mark.unit
    def test_both_on_is_open(self, monkeypatch):
        from services.voice_factor_preconditions import voice_path_blocker
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        assert voice_path_blocker() is None


class TestProfileBlocker:
    """Die Tür DIESER Person."""

    @pytest.mark.database
    async def test_no_profile_blocks(self, db_session):
        from services.voice_factor_preconditions import NO_PROFILE, voice_profile_blocker

        assert await voice_profile_blocker(db_session, _user(speaker_id=None)) == NO_PROFILE

    @pytest.mark.database
    async def test_a_profile_without_embeddings_blocks(self, db_session, test_speaker):
        from services.voice_factor_preconditions import NO_EMBEDDINGS, voice_profile_blocker

        got = await voice_profile_blocker(db_session, _user(speaker_id=test_speaker.id))
        assert got == NO_EMBEDDINGS

    @pytest.mark.database
    async def test_a_usable_profile_does_not_block(self, db_session, test_speaker):
        from models.database import SpeakerEmbedding
        from services.voice_factor_preconditions import voice_profile_blocker

        db_session.add(SpeakerEmbedding(speaker_id=test_speaker.id, embedding="AAAA"))
        await db_session.commit()
        assert await voice_profile_blocker(db_session, _user(speaker_id=test_speaker.id)) is None


class TestTheHurdleRestsInsteadOfLockingOut:
    """🛑 Der Kern: sechs Routen konnten das Profil nach der Einwilligung
    entfernen (`DELETE /users/{id}/link-speaker`, `DELETE /speakers/{id}` über
    `ON DELETE SET NULL`, das Löschen der letzten Einbettung, das Neuanlernen).
    Danach hielt `/auth/login` die Token zurück und `/auth/voice` verweigerte
    fail-closed: das Konto war zu.

    Jetzt RUHT die Hürde. Die Einwilligung bleibt gespeichert — sie ist der
    Nachweis einer Erklärung der Person (Art. 9 DSGVO), nicht ein Schalter, den
    das System still umlegen darf — und greift von selbst wieder, sobald das
    Profil zurück ist.
    """

    @pytest.mark.database
    async def test_consent_without_a_profile_lets_the_password_path_through(
        self, db_session, monkeypatch
    ):
        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        assert await second_factor_applies(db_session, _user(speaker_id=None)) is False

    @pytest.mark.database
    async def test_recognition_off_lets_the_password_path_through(
        self, db_session, test_speaker, monkeypatch
    ):
        from models.database import SpeakerEmbedding
        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        db_session.add(SpeakerEmbedding(speaker_id=test_speaker.id, embedding="AAAA"))
        await db_session.commit()
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", False)
        assert await second_factor_applies(
            db_session, _user(speaker_id=test_speaker.id)
        ) is False

    @pytest.mark.database
    async def test_a_resting_hurdle_is_logged_as_a_warning(
        self, db_session, monkeypatch, caplog
    ):
        """Eine ruhende Hürde ist ein Schutz, der gerade NICHT wirkt. Still
        wäre das eine Absenkung, die niemand bemerkt."""
        import logging

        from loguru import logger as loguru_logger

        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)

        seen: list[str] = []
        sink = loguru_logger.add(lambda m: seen.append(m), level="WARNING")
        try:
            await second_factor_applies(db_session, _user(speaker_id=None))
        finally:
            loguru_logger.remove(sink)
        assert any("RESTS" in m for m in seen), seen
        assert logging is not None  # (Import nur zur Klarheit der Absicht)

    @pytest.mark.database
    async def test_the_path_being_off_is_quiet(self, db_session, monkeypatch):
        """`VOICE_AUTH_ENABLED=false` ist der dokumentierte Normalzustand beider
        Instanzen — eine Warnung bei JEDER Anmeldung wäre Rauschen, in dem die
        echten Abweichungen untergehen."""
        from loguru import logger as loguru_logger

        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", False)
        seen: list[str] = []
        sink = loguru_logger.add(lambda m: seen.append(m), level="WARNING")
        try:
            assert await second_factor_applies(db_session, _user()) is False
        finally:
            loguru_logger.remove(sink)
        assert not [m for m in seen if "RESTS" in m]

    @pytest.mark.database
    async def test_without_consent_nothing_applies(self, db_session, monkeypatch):
        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        assert await second_factor_applies(db_session, _user(consent=False)) is False

    @pytest.mark.database
    async def test_everything_in_place_raises_the_hurdle(
        self, db_session, test_speaker, monkeypatch
    ):
        """Die Gegenprobe: sonst bewiesen die fünf Tests darüber nur, dass die
        Funktion immer `False` sagt."""
        from models.database import SpeakerEmbedding
        from services.voice_factor_preconditions import second_factor_applies
        from utils.config import settings

        db_session.add(SpeakerEmbedding(speaker_id=test_speaker.id, embedding="AAAA"))
        await db_session.commit()
        monkeypatch.setattr(settings, "voice_auth_enabled", True)
        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        assert await second_factor_applies(
            db_session, _user(speaker_id=test_speaker.id)
        ) is True

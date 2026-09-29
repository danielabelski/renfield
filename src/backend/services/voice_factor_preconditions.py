"""Die Vorbedingungen des zweiten Faktors — an EINER Stelle.

🛑 DER FEHLER, GEGEN DEN DIESES MODUL GESCHRIEBEN IST
=====================================================
Die Hürde wurde auf EINE Bedingung gestellt und auf FÜNF abgeräumt.

`/auth/login` stellte sie, sobald `voice_auth_enabled` an war. `/auth/voice`
verlangte zum Einlösen zusätzlich: eingeschaltete Sprecherkennung, ein
verknüpftes Profil, mindestens eine Einbettung — und einen Einbettungsweg, den
ein älterer P0-Riegel in der Produktion zuhält. **Jede Lücke zwischen den
beiden Mengen ist eine Aussperrung**, denn es gibt bewusst keinen Rückfall auf
Passwort allein.

Der Kommentar an der Login-Stelle sprach die Regel bereits aus — „die Hürde
greift nur, wenn es auch eine Tür gibt" — und prüfte dann nur einen der Flügel.
Deshalb liegt die Prüfung jetzt hier, und alle drei Aufrufer laufen hindurch:

* `POST /auth/login` — stellt die Hürde NUR, wenn sie auch fällt.
* `POST /users/{id}/voice-second-factor` — 409 statt einer scharfen Einwilligung,
  die ins Leere greift.
* `POST /auth/voice` — prüft vor dem Einlösen dasselbe noch einmal, weil sich
  zwischen Passwort und Aufnahme etwas geändert haben kann.

🛑 WARUM DIE EINWILLIGUNG DABEI STEHEN BLEIBT
=============================================
Fehlt die Tür, RUHT die Hürde — die Spalte wird nicht gelöscht. Zwei Gründe:

1. Es ist genau das Verhalten, das für `voice_auth_enabled=false` schon
   dokumentiert und gewollt ist (`docs/ENVIRONMENT_VARIABLES.md`): ohne
   Sprachweg könnte niemand einlösen, also ruht die Anforderung, statt das
   Konto auszusperren. Eine Regel, nicht zwei.
2. Die Spalte IST die Einwilligung (Art. 9 DSGVO). Sie beim Entzug des Profils
   still zu löschen, vernichtete den Nachweis einer Erklärung, die die Person
   abgegeben hat. Kehrt das Profil zurück, greift die Anforderung von selbst
   wieder.

Ein Angreifer gewinnt dadurch nichts: um die Hürde zum Ruhen zu bringen, muss
er das Profil entfernen, und das verlangt `users.manage` bzw. `speakers.all` —
dieselbe Berechtigung, mit der er die Einwilligung ohnehin direkt abschalten
könnte. Neu ist keine Fähigkeit, nur ein Protokolleintrag: das Ruhen wird als
WARNING geschrieben, sonst wäre es eine stille Absenkung des Schutzes.
"""

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import SpeakerEmbedding, User
from utils.config import settings

# Gründe, maschinenlesbar. Sie gehen NIE an den Anmeldenden heraus (eine
# Fehlschlagsantwort unterscheidet nichts), sondern nur ins Protokoll und in
# die 409-Antwort der Verwaltungsroute, wo die Person ihr eigenes Konto ansieht.
PATH_OFF = "voice_path_off"
RECOGNITION_OFF = "recognition_off"
NO_PROFILE = "no_profile"
NO_EMBEDDINGS = "no_embeddings"


def voice_path_blocker() -> str | None:
    """Was den Einlöseweg für ALLE Konten versperrt, oder ``None``.

    Nur Schalter der Instanz. Bewusst getrennt vom Profil-Prüfer: beim
    **Einschalten** der Einwilligung darf `voice_auth_enabled=false` kein
    Hindernis sein, sonst wäre die Reihenfolge des Cutovers (erst Einwilligung
    sammeln, dann Flag umlegen) nicht durchführbar.
    """
    if not settings.voice_auth_enabled:
        return PATH_OFF
    # Ein Stimmabdruck ist biometrisches Datum. Ist die Erkennung aus, wird auch
    # für die Anmeldung keiner berechnet — derselbe Grundsatz wie im Resolver.
    if not settings.speaker_recognition_enabled:
        return RECOGNITION_OFF
    return None


async def voice_profile_blocker(db: AsyncSession, user: User) -> str | None:
    """Was DIESES Konto am Einlösen hindert, oder ``None``.

    Ein verknüpftes Profil ohne Einbettungen ist derselbe Fall wie gar keines:
    es gäbe nichts, wogegen verglichen werden könnte.
    """
    if not user.speaker_id:
        return NO_PROFILE
    count = (await db.execute(
        select(func.count()).select_from(SpeakerEmbedding)
        .where(SpeakerEmbedding.speaker_id == user.speaker_id)
    )).scalar() or 0
    return None if count else NO_EMBEDDINGS


async def voice_factor_blocker(db: AsyncSession, user: User) -> str | None:
    """Was den zweiten Faktor für DIESES Konto heute versperrt, oder ``None``.

    Die volle Wahrheit: erst die Schalter der Instanz, dann das Profil. Getrennt
    von der EINSCHALT-Prüfung in `POST /users/{id}/voice-second-factor`, und das
    ist Absicht — dort darf `PATH_OFF` nicht blockieren, sonst wäre die
    Reihenfolge des Cutovers (erst Einwilligung sammeln, dann Flag umlegen)
    nicht durchführbar. Zum ANZEIGEN dagegen zählt `PATH_OFF` mit: eine Hürde,
    die ruht, weil der Sprachweg aus ist, soll die Person auch so genannt
    bekommen.

    🛑 Der Grund ist UNABHÄNGIG von der Einwilligung. Die Seite „Mein Konto"
    zeigte ihn vorher nur im Zustand „scharf" — also nie für jemanden, der
    gerade überlegt einzuwilligen. Gemessen am 2026-09-29 im Haushalt: bei
    6 von 7 Konten greift `NO_PROFILE`, und genau die sahen ein blankes „Aus"
    mit einer Schaltfläche, die fehlschlagen musste.
    """
    return voice_path_blocker() or await voice_profile_blocker(db, user)


async def second_factor_applies(db: AsyncSession, user: User) -> bool:
    """Darf `/auth/login` diesem Konto die Token vorenthalten?

    Nur wenn die Einwilligung vorliegt UND der Weg heraus offen ist. Sonst ruht
    die Hürde — mit einem WARNING, denn eine ruhende Hürde ist ein Schutz, der
    gerade nicht wirkt, und das soll der Betreiber im Protokoll sehen.
    """
    if not user.voice_second_factor_enabled:
        return False

    blocker = await voice_factor_blocker(db, user)
    if blocker is None:
        return True

    if blocker != PATH_OFF:
        # `PATH_OFF` ist der dokumentierte Normalzustand beider Instanzen und
        # würde das Protokoll bei jeder Anmeldung fluten. Die anderen drei sind
        # Abweichungen, die jemand beheben sollte.
        logger.warning(
            f"Voice second factor RESTS for user {user.id}: {blocker}. "
            f"The consent is recorded but cannot be redeemed, so the password "
            f"path stays open — restore the profile or revoke the consent."
        )
    return False


def voice_factor_lock_id(user_id: int) -> str:
    """Die Kennung, unter der die Fehlversuche des ZWEITEN Faktors zaehlen.

    🛑 Bewusst ein anderer Namensraum als der Benutzername: ein Fehlversuch der
    Stimme darf den Passwortpfad NICHT mitsperren, sonst waere der zweite Faktor
    ein Weg, jemanden mit fremden Mitteln aus seinem Konto zu draengen.

    Der Befund, weshalb die Kennung hier steht statt als Zeichenkette in der
    Route: die Verwaltungsoberflaeche fragte die Sperre unter dem BENUTZERNAMEN
    ab. Beide trafen sich nie — die Liste meldete „nicht gesperrt", und der
    Entsperr-Knopf raeumte nichts (`cleared_keys=0`), waehrend die Person
    tatsaechlich nicht hereinkam. Ausgerechnet auf dem Bildschirm, der der
    Wiederherstellungsweg ist. Wer die Sperre setzt und wer sie anzeigt oder
    raeumt, liest die Kennung jetzt von hier.
    """
    return f"voice2fa:{user_id}"

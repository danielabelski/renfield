"""Das Zwischenticket zwischen Passwort und Stimme.

WARUM ES DAS GIBT
-----------------
`POST /auth/voice` war bis 2026-09-27 der ERSTE Faktor: Tonaufnahme rein, Zugriffs-
und Erneuerungstoken raus. Eine Aufnahme der Stimme genügte damit für eine
vollständige Anmeldung, ohne Lebendigkeitsprüfung und ohne zweiten Faktor. Die
Entscheidung vom 2026-09-27 war, daraus einen ZUSATZfaktor zu machen.

Ein Zusatzfaktor ist nur dann einer, wenn er nach einem ersten kommt und ohne ihn
nichts wert ist. Also braucht die Stimmprüfung einen Nachweis „Passwort wurde
gezeigt". Dieses Modul hält ihn: `/auth/login` legt für ein Konto mit dem Flag ein
einmaliges, kurzlebiges Ticket ab und gibt KEINE Token aus; `/auth/voice` löst das
Ticket ein und gibt erst dann Token.

🛑 WAS DAS NEBENBEI ERLEDIGT
---------------------------
Weil der Nutzer nach dem ersten Faktor BEKANNT ist, kippt die Route von „wer ist
das?" zu „ist das die behauptete Person?" — von 1:N auf 1:1. Damit verschwinden
zwei Befunde des Reviews vom 2026-09-27 strukturell statt durch einen Riegel:

* Das **Namensorakel**: es gibt nichts mehr zu identifizieren, also auch keinen
  Namen zurückzugeben. Der Aufrufer weiß ohnehin, wer er zu sein behauptet.
* Der **Münzwurf**: `verify_speaker` prüft gegen die Einbettungen EINES Profils.
  Die Läufer-Marge, die bei 1:N nötig war (und deren Fehlen die Anmeldung von der
  Erkennung trennte), hat hier keinen Gegenstand mehr.

FORM
----
Bewusst gebaut wie `sso_handoff_store`: ein 256-Bit-Token als Schlüssel, die
Nutzlast in Redis, Einlösung als atomares `GETDEL` (also einmalig, auch bei zwei
gleichzeitigen Versuchen), kurzer Ablauf. **Es liegt KEIN Token in Redis** — nur
die aufgelöste Identität. `/auth/voice` prägt die JWTs frisch und prüft den Nutzer
dabei erneut, sodass ein in der Zwischenzeit deaktiviertes Konto nicht durchkommt
und `must_change_password` aktuell ist.

🛑 Das Ticket ist an die ANFRAGEADRESSE gebunden, wenn diese fälschungsresistent
ist. Ohne die Bindung könnte ein Angreifer, der ein Ticket abfängt, es von
irgendwo einlösen; mit ihr muss er auch die Adresse halten. Ist die Adresse nicht
fälschungsresistent (kein `TRUSTED_PROXIES`), wird NICHT gebunden statt falsch
gebunden — eine Bindung an einen fälschbaren Wert wäre ein Zugewinn für den
Angreifer, nicht für uns.
"""
from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass

from loguru import logger

from services.redis_client import get_redis
from utils.config import settings

_KEY_PREFIX = "voice2fa:"

#: Wie lange das Ticket gilt. Kurz: es überbrückt nur die Sekunden zwischen
#: Passworteingabe und Sprachaufnahme. Zu lang wäre ein Zeitfenster, in dem ein
#: abgefangenes Ticket noch etwas wert ist.
DEFAULT_TTL_SECONDS = 180


@dataclass(frozen=True)
class PendingVoiceFactor:
    """Der Nachweis „erster Faktor bestanden" — NICHT die Token.

    ``client_ip`` ist None, wenn die Adresse nicht fälschungsresistent ist; dann
    findet keine Adressbindung statt (siehe Modulkopf).
    """
    user_id: int
    client_ip: str | None


def _key(ticket: str) -> str:
    return f"{_KEY_PREFIX}{ticket}"


def _ttl() -> int:
    return int(getattr(settings, "voice_second_factor_ttl_seconds", DEFAULT_TTL_SECONDS))


async def issue_ticket(user_id: int, client_ip: str | None) -> str | None:
    """Lege ein Einmalticket ab und gib es zurück. None, wenn Redis nicht mitspielt.

    🛑 Fail-closed: ohne Ticket gibt `/auth/login` keine Token aus und der Nutzer
    kommt nicht herein. Das ist die richtige Richtung — ein Redis-Ausfall darf
    nicht dazu führen, dass der zweite Faktor stillschweigend entfällt.
    """
    ticket = secrets.token_urlsafe(32)  # 256 Bit
    payload = json.dumps({"user_id": int(user_id), "client_ip": client_ip})
    try:
        stored = await get_redis().set(_key(ticket), payload, ex=_ttl(), nx=True)
    except Exception as e:
        logger.warning(f"voice 2fa: Ticket konnte nicht abgelegt werden: {e}")
        return None
    if not stored:
        # Kollision auf einem 256-Bit-Token heisst: irgendetwas ist grundlegend
        # falsch. Kein Wiederholen, kein stilles Weitergehen.
        logger.error("voice 2fa: Ticketkollision — kein Ticket ausgegeben")
        return None
    return ticket


async def consume_ticket(ticket: str, client_ip: str | None) -> PendingVoiceFactor | None:
    """Löse das Ticket EINMALIG ein (atomares `GETDEL`).

    None bei unbekanntem, bereits benutztem oder abgelaufenem Ticket — und auch
    dann, wenn die Adressbindung nicht passt. Der Aufrufer darf diese Fälle nicht
    unterscheidbar nach außen melden.
    """
    if not ticket:
        return None
    try:
        raw = await get_redis().getdel(_key(ticket))
    except Exception as e:
        logger.warning(f"voice 2fa: GETDEL fehlgeschlagen: {e}")
        return None
    if not raw:
        return None
    try:
        d = json.loads(raw)
        pending = PendingVoiceFactor(
            user_id=int(d["user_id"]),
            client_ip=d.get("client_ip"),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"voice 2fa: unbrauchbare Nutzlast verworfen: {e}")
        return None

    # Adressbindung, aber nur wenn beim Ausstellen eine gebunden wurde. Vergleich
    # in konstanter Zeit — die Adresse ist kein Geheimnis, aber der Vergleich ist
    # gratis und die Gewohnheit richtig.
    if pending.client_ip is not None:
        if client_ip is None or not hmac.compare_digest(pending.client_ip, client_ip):
            logger.warning(
                "voice 2fa: Ticket von einer anderen Adresse eingelöst — verworfen"
            )
            return None
    return pending

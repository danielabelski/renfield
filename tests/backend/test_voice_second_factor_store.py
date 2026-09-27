"""Das Zwischenticket: einmalig, kurzlebig, adressgebunden, fail-closed.

🛑 Warum diese vier Eigenschaften einzeln geprüft werden: sie sind genau die, an
denen ein Zwischenticket lautlos scheitert, und keine davon fällt im Betrieb auf.

* **Nicht einmalig** → ein abgefangenes Ticket ist beliebig oft einlösbar, der
  zweite Faktor also einmal überwunden und danach dauerhaft offen.
* **Nicht adressgebunden** → wer das Ticket abfängt, löst es von irgendwo ein.
* **Nicht fail-closed** → ein Redis-Ausfall entfernt den zweiten Faktor
  stillschweigend, und niemand merkt es, weil die Anmeldung dann einfach klappt.
* **Bindung an einen fälschbaren Wert** → schlimmer als keine Bindung, weil sie
  Sicherheit vortäuscht: der Angreifer setzt den Kopf selbst.
"""
from __future__ import annotations

import json

import pytest

from services import voice_second_factor_store as store

pytestmark = [pytest.mark.unit]


class _FakeRedis:
    """Nur die zwei Aufrufe, die der Speicher macht — `set(nx=True)` und `getdel`."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.fail_set = False
        self.fail_getdel = False

    async def set(self, key, value, ex=None, nx=False):
        if self.fail_set:
            raise RuntimeError("Redis weg")
        self.set_calls.append({"key": key, "ex": ex, "nx": nx})
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def getdel(self, key):
        if self.fail_getdel:
            raise RuntimeError("Redis weg")
        return self.data.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(store, "get_redis", lambda: r)
    return r


class TestSingleUse:
    async def test_a_ticket_works_exactly_once(self, redis):
        """🛑 Die wichtigste Eigenschaft. Ein zweites Einlösen muss scheitern,
        sonst ist der zweite Faktor nach einem Mitschnitt dauerhaft überwunden."""
        t = await store.issue_ticket(42, None)
        assert t

        first = await store.consume_ticket(t, None)
        assert first is not None
        assert first.user_id == 42

        second = await store.consume_ticket(t, None)
        assert second is None, "das Ticket war zweimal einlösbar"

    async def test_it_is_written_with_nx_and_a_ttl(self, redis):
        """`nx` verhindert das Überschreiben eines fremden Tickets, `ex` begrenzt
        das Zeitfenster, in dem ein abgefangenes Ticket etwas wert ist."""
        await store.issue_ticket(1, None)
        call = redis.set_calls[-1]
        assert call["nx"] is True
        assert call["ex"] == store.DEFAULT_TTL_SECONDS

    async def test_an_unknown_ticket_is_refused(self, redis):
        assert await store.consume_ticket("gibtesnicht", None) is None

    async def test_an_empty_ticket_never_reaches_redis(self, redis):
        assert await store.consume_ticket("", None) is None
        assert redis.set_calls == []

    async def test_tickets_are_unguessable(self, redis):
        a = await store.issue_ticket(1, None)
        b = await store.issue_ticket(1, None)
        assert a != b
        # 256 Bit als URL-sicherer Text: deutlich über 40 Zeichen.
        assert len(a) > 40


class TestAddressBinding:
    async def test_a_bound_ticket_is_refused_from_another_address(self, redis):
        t = await store.issue_ticket(7, "10.0.0.1")
        assert await store.consume_ticket(t, "10.0.0.2") is None

    async def test_a_bound_ticket_is_refused_without_an_address(self, redis):
        """Kein Freifahrtschein: wer beim Einlösen keine belastbare Adresse hat,
        löst ein gebundenes Ticket nicht ein."""
        t = await store.issue_ticket(7, "10.0.0.1")
        assert await store.consume_ticket(t, None) is None

    async def test_a_bound_ticket_works_from_the_same_address(self, redis):
        t = await store.issue_ticket(7, "10.0.0.1")
        p = await store.consume_ticket(t, "10.0.0.1")
        assert p is not None and p.user_id == 7

    async def test_an_unbound_ticket_ignores_the_address(self, redis):
        """🛑 Ohne `TRUSTED_PROXIES` wird bewusst NICHT gebunden. Eine Bindung an
        einen fälschbaren Kopf wäre ein Zugewinn für den Angreifer, nicht für uns:
        er setzt ihn selbst und schließt damit andere aus."""
        t = await store.issue_ticket(7, None)
        p = await store.consume_ticket(t, "10.0.0.99")
        assert p is not None and p.user_id == 7

    async def test_a_failed_binding_still_consumes_the_ticket(self, redis):
        """Das Ticket ist nach einem Einlöseversuch von der falschen Adresse WEG.
        Sonst könnte ein Angreifer beliebig oft raten, welche Adresse gebunden
        ist, und das Ticket bliebe für den rechtmäßigen Nutzer liegen."""
        t = await store.issue_ticket(7, "10.0.0.1")
        assert await store.consume_ticket(t, "10.0.0.2") is None
        assert await store.consume_ticket(t, "10.0.0.1") is None, (
            "ein Fehlversuch hat das Ticket nicht verbraucht"
        )


class TestFailClosed:
    async def test_no_ticket_when_redis_refuses_the_write(self, redis):
        """🛑 Ohne Ticket gibt `/auth/login` keine Token aus. Ein Redis-Ausfall
        darf den zweiten Faktor nicht stillschweigend entfernen — er muss die
        Anmeldung verweigern."""
        redis.fail_set = True
        assert await store.issue_ticket(1, None) is None

    async def test_no_consumption_when_redis_refuses_the_read(self, redis):
        t = await store.issue_ticket(1, None)
        redis.fail_getdel = True
        assert await store.consume_ticket(t, None) is None

    async def test_a_collision_yields_no_ticket(self, redis, monkeypatch):
        """Auf 256 Bit praktisch unmöglich — aber wenn es passiert, wird kein
        Ticket ausgegeben, statt ein fremdes zu überschreiben."""
        monkeypatch.setattr(store.secrets, "token_urlsafe", lambda _n: "immergleich")
        assert await store.issue_ticket(1, None) is not None
        assert await store.issue_ticket(2, None) is None

    async def test_a_corrupt_payload_is_discarded(self, redis):
        t = await store.issue_ticket(1, None)
        redis.data[store._key(t)] = "{kein json"
        assert await store.consume_ticket(t, None) is None

    async def test_a_payload_without_a_user_is_discarded(self, redis):
        t = await store.issue_ticket(1, None)
        redis.data[store._key(t)] = json.dumps({"client_ip": None})
        assert await store.consume_ticket(t, None) is None


class TestNoSecretsInRedis:
    async def test_only_the_identity_is_stored(self, redis):
        """🛑 Es liegt KEIN Token in Redis. `/auth/voice` prägt die JWTs frisch und
        prüft den Nutzer dabei erneut — sonst käme ein zwischenzeitlich
        deaktiviertes Konto durch, und `must_change_password` wäre veraltet."""
        t = await store.issue_ticket(5, "10.0.0.1")
        raw = json.loads(redis.data[store._key(t)])
        assert set(raw) == {"user_id", "client_ip"}
        assert raw["user_id"] == 5

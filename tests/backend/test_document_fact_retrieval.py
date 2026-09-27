"""Tests for DocumentFactRetrieval.

Production is Postgres, so every behavioral test runs against **real Postgres**
(``pg_db_session`` + the ``renfield_test`` scratch DB) — the sqlite shim
diverges in ways that hide real bugs (``obligation_date`` is a ``date`` on PG
but a ``str`` on sqlite; the ``::text::int`` json cast that bit prod passed on
sqlite). The GENERATED ``search_vector`` column is installed explicitly by the
``df_fts_installed`` fixture (``create_all`` lays it down as a plain TSVECTOR;
the multilingual GENERATED form lives only in the migration), mirroring
``test_fts_multilingual_pg.py``.

Two pure-logic test classes need no DB and always run:
- ``TestIdentifierTokens`` — the Finding-1 token gate.
- ``TestPostgresIlikeGating`` — asserts the ILIKE branch appears in the built
  SQL iff the query carries an identifier-shaped token (recording fake db).
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import TIER_PUBLIC, Atom, Document, DocumentFact, Role, User
from services.document_fact_retrieval import (
    DocumentFactRetrieval,
    _identifier_tokens,
)
from services.fts_languages import build_generated_tsvector_expression
from utils.config import settings

# Must match alembic/versions/pc20260602_document_facts_fts.py::_CONTENT_EXPR.
# Replicated (not imported) because alembic version modules aren't an importable
# package; the behavioral FTS tests below guard against drift (a wrong column
# set would break the "Finanzamt matches" assertion).
_FACT_CONTENT_EXPR = (
    "value || ' ' || coalesce(normalized_value, '') || ' ' || "
    "coalesce(excerpt, '') || ' ' || kind"
)


# =========================================================================== #
# Pure-logic tests — no DB, always run.
# =========================================================================== #
class TestIdentifierTokens:
    def test_prose_query_has_no_identifier_tokens(self):
        assert _identifier_tokens("Finanzamt") == []
        assert _identifier_tokens("Widerspruch einlegen") == []

    def test_identifier_query_extracts_token(self):
        assert _identifier_tokens("114/5876/5293") == ["114/5876/5293"]

    def test_identifier_embedded_in_prose(self):
        assert _identifier_tokens("Steuernummer 114/5876/5293") == ["114/5876/5293"]

    def test_bare_year_counts_as_identifier(self):
        assert _identifier_tokens("Rechnung 2024") == ["2024"]

    def test_short_numeric_token_dropped(self):
        assert _identifier_tokens("nr 12") == []

    def test_surrounding_punctuation_stripped(self):
        assert _identifier_tokens("(114/5876/5293)") == ["114/5876/5293"]

    def test_all_punctuation_token_dropped(self):
        # An all-slash run survives the digit/'/' trigger and the length gate,
        # but has no alphanumeric char — it must not build a no-op ILIKE.
        assert _identifier_tokens("//////") == []
        assert _identifier_tokens("Pos //////  -.-") == []


class _FakeResult:
    def fetchall(self):
        return []


class _RecordingDb:
    """Async db stub that forces the Postgres branch and records executed SQL."""

    def __init__(self):
        self.bind = MagicMock()
        self.bind.dialect.name = "postgresql"
        self.captured: list[tuple[str, dict]] = []

    async def execute(self, sql, params):
        self.captured.append((str(sql), params))
        return _FakeResult()


@pytest.mark.asyncio
class TestPostgresIlikeGating:
    async def test_prose_query_omits_ilike_branch(self):
        db = _RecordingDb()
        await DocumentFactRetrieval(db).search("Finanzamt", asker_id=None, top_k=10)
        sql, params = db.captured[-1]
        assert "ILIKE" not in sql
        assert not any(k.startswith("ident_") for k in params)

    async def test_identifier_query_adds_ilike_branch(self):
        db = _RecordingDb()
        await DocumentFactRetrieval(db).search("114/5876/5293", asker_id=None, top_k=10)
        sql, params = db.captured[-1]
        assert "ILIKE" in sql
        assert any(k.startswith("ident_") for k in params)
        assert params["ident_0"] == "%114/5876/5293%"


class _RaisingDb:
    """Async db stub whose execute() always raises a given exception."""

    def __init__(self, exc: Exception):
        self.bind = MagicMock()
        self.bind.dialect.name = "postgresql"
        self._exc = exc

    async def execute(self, sql, params):
        raise self._exc


@pytest.mark.asyncio
class TestFetchErrorHandling:
    """A broken-DB / unapplied-migration error must NOT be masked as an empty
    corpus (F1) — operational errors re-raise; input errors swallow to []."""

    async def test_operational_error_propagates(self):
        from sqlalchemy.exc import ProgrammingError
        db = _RaisingDb(ProgrammingError("stmt", {}, Exception("column search_vector does not exist")))
        with pytest.raises(ProgrammingError):
            await DocumentFactRetrieval(db).search("Finanzamt", asker_id=None, top_k=10)

    async def test_input_shaped_error_swallowed_to_empty(self):
        db = _RaisingDb(ValueError("malformed something"))
        r = await DocumentFactRetrieval(db).search("Finanzamt", asker_id=None, top_k=10)
        assert r == []


# =========================================================================== #
# Real-Postgres behavioral tests.
# =========================================================================== #
# Holds the owner user id for the current test (atoms.owner_user_id is a real
# FK on Postgres, so a users row must exist). Set by df_fts_installed; read by
# _mk_fact. The outer transaction rolls back per test, recreating it each time.
_owner = {"id": None}


@pytest.fixture
async def df_fts_installed(pg_db_session: AsyncSession) -> None:
    """Create the owner user + swap document_facts.search_vector for the
    GENERATED multilingual column the migration creates. Per-test (the outer
    txn rolls back).
    """
    role = Role(name="df-test-role", description="t", permissions=[])
    pg_db_session.add(role)
    await pg_db_session.flush()
    user = User(username="df-owner", password_hash="x", is_active=True, role_id=role.id)
    pg_db_session.add(user)
    await pg_db_session.flush()
    _owner["id"] = user.id

    expr = build_generated_tsvector_expression(_FACT_CONTENT_EXPR)
    await pg_db_session.execute(
        text("ALTER TABLE document_facts DROP COLUMN IF EXISTS search_vector")
    )
    await pg_db_session.execute(
        text(
            f"ALTER TABLE document_facts "
            f"ADD COLUMN search_vector tsvector "
            f"GENERATED ALWAYS AS ({expr}) STORED"
        )
    )
    await pg_db_session.flush()


_seq = 0


async def _mk_doc(session: AsyncSession, *, status: str = "completed", kb_id=None) -> int:
    doc = Document(
        knowledge_base_id=kb_id, filename="d.pdf", file_path="/x/d.pdf",
        status=status, circle_tier=0,
    )
    session.add(doc)
    await session.flush()
    return doc.id


async def _mk_fact(
    session: AsyncSession, *, doc_id: int, category: str, kind: str, value: str,
    normalized_value: str | None = None, excerpt: str | None = None,
    obligation_date: dt.date | None = None, tier: int = 0,
    amount_value=None, amount_currency: str | None = None,
    legal_gate: bool = False, source: str = "deterministic",
) -> int:
    global _seq
    _seq += 1
    atom_id = f"00000000-0000-0000-0000-{_seq:012d}"
    session.add(Atom(
        atom_id=atom_id, atom_type="document_fact", source_table="document_facts",
        source_id=f"seq-{_seq}", owner_user_id=_owner["id"], policy={"tier": tier},
    ))
    await session.flush()
    fact = DocumentFact(
        document_id=doc_id, category=category, kind=kind, value=value,
        normalized_value=normalized_value, excerpt=excerpt,
        obligation_date=obligation_date, amount_value=amount_value,
        amount_currency=amount_currency, legal_gate=legal_gate,
        source=source, atom_id=atom_id, circle_tier=tier,
    )
    session.add(fact)
    await session.flush()
    return fact.id


@pytest.mark.postgres
@pytest.mark.asyncio
class TestSearchFTS:
    async def test_prose_query_matches_via_tsvector(self, pg_db_session, df_fts_installed):
        doc = await _mk_doc(pg_db_session)
        fid = await _mk_fact(
            pg_db_session, doc_id=doc, category="universal", kind="issuer",
            value="Finanzverwaltung NRW",
            excerpt="… zuständig ist das Finanzamt Finanzverwaltung NRW …",
        )
        r = await DocumentFactRetrieval(pg_db_session).search(
            "Finanzamt", asker_id=None, top_k=10,
        )
        assert [f["id"] for f in r] == [fid]
        assert r[0]["similarity"] > 0  # real ts_rank score

    async def test_identifier_query_matches_via_normalized_value(
        self, pg_db_session, df_fts_installed,
    ):
        """An identifier query finds a fact through its collapsed
        normalized_value, even when the verbatim value is poppler-letter-spaced.

        (The match may land via FTS-on-normalized OR the identifier-ILIKE
        branch — the GENERATED search_vector indexes normalized_value too, so we
        can't deterministically isolate ILIKE here; the ILIKE branch is
        defense-in-depth for query/index tokenizer mismatch and is unit-gated in
        TestPostgresIlikeGating. This asserts the user-visible outcome.)
        """
        doc = await _mk_doc(pg_db_session)
        fid = await _mk_fact(
            pg_db_session, doc_id=doc, category="identifier", kind="steuernummer",
            value="11 4 / 5 8 7 6 / 5 2 9 3",      # poppler -layout letter-spaced
            normalized_value="114/5876/5293",       # whitespace-collapsed
        )
        r = await DocumentFactRetrieval(pg_db_session).search(
            "114/5876/5293", asker_id=None, top_k=10,
        )
        assert [f["id"] for f in r] == [fid]

    async def test_combined_prose_and_identifier_query_matches_both(
        self, pg_db_session, df_fts_installed,
    ):
        """A query carrying BOTH a prose token and an identifier token exercises
        the OR-of-branches + GREATEST(rank...) path: a prose-only fact and an
        identifier fact both come back from one query.
        """
        doc = await _mk_doc(pg_db_session)
        prose_id = await _mk_fact(
            pg_db_session, doc_id=doc, category="universal", kind="issuer",
            value="Finanzverwaltung NRW",
            excerpt="… das Finanzamt Finanzverwaltung NRW …",
        )
        ident_id = await _mk_fact(
            pg_db_session, doc_id=doc, category="identifier", kind="steuernummer",
            value="999/8888/7777", normalized_value="999/8888/7777",
        )
        r = await DocumentFactRetrieval(pg_db_session).search(
            "Finanzamt 999/8888/7777", asker_id=None, top_k=10,
        )
        assert {f["id"] for f in r} == {prose_id, ident_id}

    async def test_no_match_returns_empty(self, pg_db_session, df_fts_installed):
        doc = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=doc, category="universal",
                       kind="issuer", value="Stadtwerke München")
        r = await DocumentFactRetrieval(pg_db_session).search(
            "Finanzamt", asker_id=None, top_k=10,
        )
        assert r == []

    async def test_only_completed_documents(self, pg_db_session, df_fts_installed):
        doc = await _mk_doc(pg_db_session, status="processing")
        await _mk_fact(pg_db_session, doc_id=doc, category="universal",
                       kind="issuer", value="Finanzverwaltung NRW")
        r = await DocumentFactRetrieval(pg_db_session).search(
            "Finanzverwaltung", asker_id=None, top_k=10,
        )
        assert r == []


@pytest.mark.postgres
@pytest.mark.asyncio
class TestThinQuery:
    async def test_empty_query_returns_empty(self, pg_db_session, df_fts_installed):
        r = await DocumentFactRetrieval(pg_db_session).search("", asker_id=1, top_k=10)
        assert r == []

    async def test_short_token_returns_empty(self, pg_db_session, df_fts_installed):
        r = await DocumentFactRetrieval(pg_db_session).search("ab", asker_id=1, top_k=10)
        assert r == []


@pytest.mark.postgres
@pytest.mark.asyncio
class TestFactsForDocument:
    async def test_returns_only_that_documents_facts(self, pg_db_session, df_fts_installed):
        d1 = await _mk_doc(pg_db_session)
        d2 = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=d1, category="identifier", kind="steuernummer", value="A")
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer", value="B")
        await _mk_fact(pg_db_session, doc_id=d2, category="identifier", kind="iban", value="C")
        r = await DocumentFactRetrieval(pg_db_session).facts_for_document(d1, asker_id=None)
        assert {f["value"] for f in r} == {"A", "B"}

    async def test_ordered_by_category_then_kind(self, pg_db_session, df_fts_installed):
        d1 = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer", value="U")
        await _mk_fact(pg_db_session, doc_id=d1, category="identifier", kind="steuernummer", value="I")
        r = await DocumentFactRetrieval(pg_db_session).facts_for_document(d1, asker_id=None)
        assert [f["category"] for f in r] == ["identifier", "universal"]

    async def test_factless_document_returns_empty(self, pg_db_session, df_fts_installed):
        d1 = await _mk_doc(pg_db_session)
        r = await DocumentFactRetrieval(pg_db_session).facts_for_document(d1, asker_id=None)
        assert r == []


@pytest.mark.postgres
@pytest.mark.asyncio
class TestObligations:
    async def _seed(self, session) -> int:
        d1 = await _mk_doc(session)
        await _mk_fact(session, doc_id=d1, category="obligation", kind="zahlung",
                       value="Z1", obligation_date=dt.date(2026, 6, 10))
        await _mk_fact(session, doc_id=d1, category="obligation", kind="zahlung",
                       value="Z2", obligation_date=dt.date(2026, 7, 1))
        await _mk_fact(session, doc_id=d1, category="obligation", kind="frist",
                       value="Z3", obligation_date=dt.date(2026, 6, 20))
        await _mk_fact(session, doc_id=d1, category="obligation", kind="zahlung",
                       value="NODATE")  # excluded: no date
        await _mk_fact(session, doc_id=d1, category="identifier", kind="steuernummer",
                       value="ID", obligation_date=dt.date(2026, 6, 1))  # excluded: not obligation
        return d1

    async def test_ascending_order_and_exclusions(self, pg_db_session, df_fts_installed):
        await self._seed(pg_db_session)
        r = await DocumentFactRetrieval(pg_db_session).obligations(asker_id=None)
        assert [f["value"] for f in r] == ["Z1", "Z3", "Z2"]
        vals = {f["value"] for f in r}
        assert "NODATE" not in vals and "ID" not in vals

    async def test_due_before_filter(self, pg_db_session, df_fts_installed):
        await self._seed(pg_db_session)
        r = await DocumentFactRetrieval(pg_db_session).obligations(
            asker_id=None, due_before=dt.date(2026, 6, 20),
        )
        assert [f["value"] for f in r] == ["Z1", "Z3"]

    async def test_limit_caps_results(self, pg_db_session, df_fts_installed):
        await self._seed(pg_db_session)
        r = await DocumentFactRetrieval(pg_db_session).obligations(asker_id=None, limit=1)
        assert [f["value"] for f in r] == ["Z1"]

    async def test_obligation_date_serialized_iso(self, pg_db_session, df_fts_installed):
        await self._seed(pg_db_session)
        r = await DocumentFactRetrieval(pg_db_session).obligations(asker_id=None, limit=1)
        assert r[0]["obligation_date"] == "2026-06-10"


@pytest.mark.postgres
@pytest.mark.asyncio
class TestCircleFilterGate:
    async def test_auth_off_bypass_sees_all_tiers(self, pg_db_session, df_fts_installed, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", False)
        d1 = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer",
                       value="SelfTierFact", tier=0)
        r = await DocumentFactRetrieval(pg_db_session).facts_for_document(d1, asker_id=None)
        assert {f["value"] for f in r} == {"SelfTierFact"}

    async def test_anonymous_authed_sees_public_only(self, pg_db_session, df_fts_installed, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        d1 = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer",
                       value="SelfFact", tier=0)
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer",
                       value="PublicFact", tier=TIER_PUBLIC)
        r = await DocumentFactRetrieval(pg_db_session).facts_for_document(d1, asker_id=None)
        assert {f["value"] for f in r} == {"PublicFact"}

    async def test_authed_non_owner_denied_self_tier_fact(
        self, pg_db_session, df_fts_installed, monkeypatch,
    ):
        """Defense-in-depth: the document_facts_circles_filter SQL itself denies
        a real second authed user (no membership, no grant) a tier-0 fact —
        through BOTH facts_for_document and search, not just the route gate.
        """
        monkeypatch.setattr(settings, "auth_enabled", True)
        # Second user, distinct from the fixture's _owner.
        role = Role(name="df-outsider-role", description="t", permissions=[])
        pg_db_session.add(role)
        await pg_db_session.flush()
        outsider = User(username="df-outsider", password_hash="x", is_active=True, role_id=role.id)
        pg_db_session.add(outsider)
        await pg_db_session.flush()

        d1 = await _mk_doc(pg_db_session)
        await _mk_fact(pg_db_session, doc_id=d1, category="universal", kind="issuer",
                       value="OwnerSecret", tier=0)

        assert await DocumentFactRetrieval(pg_db_session).facts_for_document(
            d1, asker_id=outsider.id,
        ) == []
        assert await DocumentFactRetrieval(pg_db_session).search(
            "OwnerSecret", asker_id=outsider.id, top_k=10,
        ) == []

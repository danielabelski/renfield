"""Unit tests for the KG-extraction eval runner (D6).

Validates the PURE check_expectations logic + the corpus schema, without an LLM
(the real extraction run in bin/run_kg_extraction_eval.py needs Ollama and is
on-demand). Mirrors tests/eval/test_extraction_eval_runner.py for the memory eval.
"""
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Discover the runner. Local dev: parents[2]/bin. The .159 container does not
# mount bin/, so the run there sets KG_EVAL_RUNNER_DIR to a mounted path the
# runner was copied into (mirrors the memory eval's MEMORY_BASELINE_RUNNER_DIR).
_env_dir = os.environ.get("KG_EVAL_RUNNER_DIR")
_runner_dir = Path(_env_dir) if _env_dir else Path(__file__).resolve().parents[2] / "bin"
if not (_runner_dir / "run_kg_extraction_eval.py").exists():
    raise RuntimeError(
        f"run_kg_extraction_eval.py not found in {_runner_dir}. "
        f"Set KG_EVAL_RUNNER_DIR to a directory reachable from the test mount."
    )
if str(_runner_dir) not in sys.path:
    sys.path.insert(0, str(_runner_dir))

import run_kg_extraction_eval as R

_CORPUS = Path(__file__).resolve().parent / "kg_extraction_eval.yaml"


class TestEntityExpectations:
    def test_must_include_matches_by_name_and_type(self):
        ents = [{"name": "Alice", "type": "person"}]
        ok, fails = R.check_expectations(ents, [], {"entities_must_include": [{"name": "alice", "type": "person"}]})
        assert ok and not fails

    def test_must_include_fails_when_missing(self):
        ok, fails = R.check_expectations([], [], {"entities_must_include": [{"name": "Alice"}]})
        assert not ok and any("missing entity" in f for f in fails)

    def test_type_mismatch_fails(self):
        ents = [{"name": "Alice", "type": "place"}]
        ok, _ = R.check_expectations(ents, [], {"entities_must_include": [{"name": "Alice", "type": "person"}]})
        assert not ok

    def test_types_include_subset(self):
        ents = [{"name": "Sam Star", "type": "person", "types": ["person", "musician"]}]
        ok, _ = R.check_expectations(ents, [], {"entities_must_include": [{"name": "Sam Star", "types_include": ["musician"]}]})
        assert ok

    def test_must_not_include_flags_forbidden(self):
        ents = [{"name": "Musik", "type": "concept"}]
        ok, fails = R.check_expectations(ents, [], {"entities_must_not_include": ["Musik"]})
        assert not ok and any("forbidden entity" in f for f in fails)


class TestRelationExpectations:
    def test_predicate_substring_match(self):
        rels = [{"subject": "Alice", "predicate": "mag_musik_von", "object": "Sam Star"}]
        ok, _ = R.check_expectations([], rels, {"relations_must_include": [{"subject": "Alice", "predicate": "mag", "object": "Sam Star"}]})
        assert ok

    def test_conflation_guard_flags_forbidden_relation(self):
        # Carol wrongly inherited a music preference -> must fail (the gold-case gate)
        rels = [{"subject": "Carol", "predicate": "mag", "object": "Jazz"}]
        ok, fails = R.check_expectations([], rels, {"relations_must_not_include": [{"subject": "Carol", "predicate": "mag"}]})
        assert not ok and any("conflation" in f for f in fails)

    def test_no_forbidden_relation_passes(self):
        rels = [{"subject": "Alice", "predicate": "mag", "object": "Jazz"}]
        ok, _ = R.check_expectations([], rels, {"relations_must_not_include": [{"subject": "Carol", "predicate": "mag"}]})
        assert ok


class TestCounts:
    def test_entity_cap(self):
        ents = [{"name": n} for n in ("a", "b", "c")]
        ok, _ = R.check_expectations(ents, [], {"entity_count_at_most": 2})
        assert not ok

    def test_relations_floor(self):
        ok, _ = R.check_expectations([], [], {"relations_count_at_least": 1})
        assert not ok


class TestCorpusSchema:
    def test_corpus_loads_and_is_well_formed(self):
        import yaml
        data = yaml.safe_load(_CORPUS.read_text(encoding="utf-8"))
        cases = data.get("cases", [])
        assert len(cases) >= 3
        known_keys = {
            "entities_must_include", "entities_must_not_include",
            "relations_must_include", "relations_must_not_include",
            "entity_count_at_most", "relations_count_at_least",
        }
        ids = set()
        for c in cases:
            assert c.get("id") and c["id"] not in ids, f"missing/dup id: {c.get('id')}"
            ids.add(c["id"])
            assert c.get("user_message")
            assert isinstance(c.get("expect"), dict) and c["expect"]
            assert set(c["expect"]).issubset(known_keys), f"unknown expect key in {c['id']}"

    def test_gold_case_present(self):
        import yaml
        data = yaml.safe_load(_CORPUS.read_text(encoding="utf-8"))
        assert any(c["id"] == "case-subject-binding-gold" for c in data["cases"])

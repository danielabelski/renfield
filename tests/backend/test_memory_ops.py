"""
Unit tests for services.memory_ops — the Pydantic v2 schema for the
Mem0-style batched extraction tool call.

Pure Pydantic; no DB or LLM dependency. Per Renfield CLAUDE.md these
run on the .159 build box.

Coverage targets:
  - OpType enum surface and string interop
  - MemoryOp field requirements per op type (ADD / UPDATE / DELETE / NOOP)
  - MemoryOp value ranges (importance 0.1-1.0, content len, category set)
  - MemoryOpsList batch constraints (max length, duplicate-target rejection)
  - MemoryOpsList iteration + len conveniences
  - validate_against_candidates returns None vs "id_reject"
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.database import (
    MEMORY_CATEGORY_FACT,
    MEMORY_CATEGORY_PREFERENCE,
)
from services.memory_ops import (
    MAX_CONTENT_CHARS,
    MAX_OPS_PER_BATCH,
    MAX_REASON_CHARS,
    MemoryOp,
    MemoryOpsList,
    OpType,
    validate_against_candidates,
)

# ---------------------------------------------------------------------------
# OpType enum
# ---------------------------------------------------------------------------

class TestOpType:

    @pytest.mark.unit
    def test_op_type_inherits_from_str(self):
        # Inheriting from str matters for JSON round-tripping
        assert OpType.ADD == "ADD"
        assert OpType.UPDATE == "UPDATE"
        assert OpType.DELETE == "DELETE"
        assert OpType.NOOP == "NOOP"

    @pytest.mark.unit
    def test_op_type_values_are_uppercase(self):
        # Matches the legacy _apply_contradiction_resolution vocabulary
        # so prompts that compute "valid_actions" inline still work.
        for op in OpType:
            assert op.value == op.value.upper()


# ---------------------------------------------------------------------------
# MemoryOp — ADD
# ---------------------------------------------------------------------------

class TestMemoryOpAdd:

    @pytest.mark.unit
    def test_valid_add_with_content_and_category(self):
        op = MemoryOp(op=OpType.ADD, content="Prefers German", category=MEMORY_CATEGORY_PREFERENCE)
        assert op.op == OpType.ADD
        assert op.content == "Prefers German"
        assert op.target_id is None

    @pytest.mark.unit
    def test_add_with_optional_importance(self):
        op = MemoryOp(op=OpType.ADD, content="X", category=MEMORY_CATEGORY_FACT, importance=0.8)
        assert op.importance == 0.8

    @pytest.mark.unit
    def test_add_rejects_target_id(self):
        with pytest.raises(ValidationError, match="ADD ops must not carry target_id"):
            MemoryOp(op=OpType.ADD, target_id=42, content="X", category=MEMORY_CATEGORY_FACT)

    @pytest.mark.unit
    def test_add_requires_content(self):
        with pytest.raises(ValidationError, match="ADD requires content"):
            MemoryOp(op=OpType.ADD, category=MEMORY_CATEGORY_FACT)

    @pytest.mark.unit
    def test_add_requires_category(self):
        with pytest.raises(ValidationError, match="ADD requires category"):
            MemoryOp(op=OpType.ADD, content="X")

    @pytest.mark.unit
    def test_add_rejects_invalid_category(self):
        with pytest.raises(ValidationError, match="category must be one of"):
            MemoryOp(op=OpType.ADD, content="X", category="not-a-real-category")


# ---------------------------------------------------------------------------
# MemoryOp — UPDATE
# ---------------------------------------------------------------------------

class TestMemoryOpUpdate:

    @pytest.mark.unit
    def test_valid_update(self):
        op = MemoryOp(op=OpType.UPDATE, target_id=42, content="Updated content")
        assert op.target_id == 42
        assert op.content == "Updated content"

    @pytest.mark.unit
    def test_update_requires_target_id(self):
        with pytest.raises(ValidationError, match="UPDATE requires target_id"):
            MemoryOp(op=OpType.UPDATE, content="X")

    @pytest.mark.unit
    def test_update_requires_content(self):
        with pytest.raises(ValidationError, match="UPDATE requires content"):
            MemoryOp(op=OpType.UPDATE, target_id=42)

    @pytest.mark.unit
    def test_update_category_is_optional(self):
        # category omitted on UPDATE preserves the existing row's category
        op = MemoryOp(op=OpType.UPDATE, target_id=42, content="X")
        assert op.category is None

    @pytest.mark.unit
    def test_update_target_id_must_be_positive(self):
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.UPDATE, target_id=0, content="X")
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.UPDATE, target_id=-1, content="X")


# ---------------------------------------------------------------------------
# MemoryOp — DELETE
# ---------------------------------------------------------------------------

class TestMemoryOpDelete:

    @pytest.mark.unit
    def test_valid_delete(self):
        op = MemoryOp(op=OpType.DELETE, target_id=42, reason="User retracted")
        assert op.op == OpType.DELETE
        assert op.target_id == 42
        assert op.reason == "User retracted"

    @pytest.mark.unit
    def test_delete_requires_target_id(self):
        with pytest.raises(ValidationError, match="DELETE requires target_id"):
            MemoryOp(op=OpType.DELETE, reason="x")

    @pytest.mark.unit
    def test_delete_rejects_content(self):
        with pytest.raises(ValidationError, match="DELETE must not carry content"):
            MemoryOp(op=OpType.DELETE, target_id=42, content="x")

    @pytest.mark.unit
    def test_delete_rejects_category(self):
        with pytest.raises(ValidationError, match="DELETE must not carry category"):
            MemoryOp(op=OpType.DELETE, target_id=42, category=MEMORY_CATEGORY_FACT)


# ---------------------------------------------------------------------------
# MemoryOp — NOOP
# ---------------------------------------------------------------------------

class TestMemoryOpNoop:

    @pytest.mark.unit
    def test_noop_alone(self):
        op = MemoryOp(op=OpType.NOOP)
        assert op.op == OpType.NOOP

    @pytest.mark.unit
    def test_noop_with_reason(self):
        op = MemoryOp(op=OpType.NOOP, reason="Nothing personal in this turn")
        assert op.reason == "Nothing personal in this turn"

    @pytest.mark.unit
    def test_noop_does_not_require_any_field(self):
        # NOOP is the only op with no positive field requirements
        MemoryOp(op=OpType.NOOP)  # must not raise


# ---------------------------------------------------------------------------
# MemoryOp — value ranges
# ---------------------------------------------------------------------------

class TestMemoryOpValueRanges:

    @pytest.mark.unit
    def test_importance_range(self):
        # In-range
        MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT, importance=0.1)
        MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT, importance=1.0)
        # Out of range
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT, importance=0.05)
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT, importance=1.5)

    @pytest.mark.unit
    def test_content_max_length(self):
        # Just under
        MemoryOp(op=OpType.ADD, content="a" * MAX_CONTENT_CHARS, category=MEMORY_CATEGORY_FACT)
        # One over
        with pytest.raises(ValidationError):
            MemoryOp(
                op=OpType.ADD,
                content="a" * (MAX_CONTENT_CHARS + 1),
                category=MEMORY_CATEGORY_FACT,
            )

    @pytest.mark.unit
    def test_content_min_length_one(self):
        # Empty content should fail before the "ADD requires content" check
        # (Pydantic field-level min_length=1 fires first)
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.ADD, content="", category=MEMORY_CATEGORY_FACT)

    @pytest.mark.unit
    def test_reason_max_length(self):
        MemoryOp(op=OpType.NOOP, reason="a" * MAX_REASON_CHARS)
        with pytest.raises(ValidationError):
            MemoryOp(op=OpType.NOOP, reason="a" * (MAX_REASON_CHARS + 1))

    @pytest.mark.unit
    def test_all_memory_categories_accepted(self):
        from models.database import MEMORY_CATEGORIES
        for cat in MEMORY_CATEGORIES:
            MemoryOp(op=OpType.ADD, content="x", category=cat)


# ---------------------------------------------------------------------------
# MemoryOpsList — batch
# ---------------------------------------------------------------------------

class TestMemoryOpsList:

    @pytest.mark.unit
    def test_empty_list_is_valid(self):
        # Empty list is semantically equivalent to [NOOP]; the LLM is
        # allowed to emit []
        ops = MemoryOpsList(root=[])
        assert len(ops) == 0

    @pytest.mark.unit
    def test_single_noop_is_valid(self):
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.NOOP)])
        assert len(ops) == 1

    @pytest.mark.unit
    def test_mixed_batch(self):
        ops = MemoryOpsList(
            root=[
                MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT),
                MemoryOp(op=OpType.UPDATE, target_id=10, content="y"),
                MemoryOp(op=OpType.DELETE, target_id=20),
            ]
        )
        assert len(ops) == 3
        assert [op.op for op in ops.ops] == [OpType.ADD, OpType.UPDATE, OpType.DELETE]

    @pytest.mark.unit
    def test_max_batch_size_at_limit(self):
        # MAX_OPS_PER_BATCH NOOPs should pass
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.NOOP) for _ in range(MAX_OPS_PER_BATCH)])
        assert len(ops) == MAX_OPS_PER_BATCH

    @pytest.mark.unit
    def test_max_batch_size_exceeded(self):
        with pytest.raises(ValidationError, match=f"max is {MAX_OPS_PER_BATCH}"):
            MemoryOpsList(root=[MemoryOp(op=OpType.NOOP) for _ in range(MAX_OPS_PER_BATCH + 1)])

    @pytest.mark.unit
    def test_duplicate_target_id_rejected(self):
        with pytest.raises(ValidationError, match="target_id 42 appears in more than one op"):
            MemoryOpsList(
                root=[
                    MemoryOp(op=OpType.UPDATE, target_id=42, content="first"),
                    MemoryOp(op=OpType.DELETE, target_id=42),
                ]
            )

    @pytest.mark.unit
    def test_duplicate_check_ignores_ops_without_target(self):
        # Multiple ADD / NOOP in the same batch must NOT trigger
        # duplicate detection (they have target_id=None).
        ops = MemoryOpsList(
            root=[
                MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT),
                MemoryOp(op=OpType.ADD, content="y", category=MEMORY_CATEGORY_PREFERENCE),
                MemoryOp(op=OpType.NOOP),
            ]
        )
        assert len(ops) == 3

    @pytest.mark.unit
    def test_iter_yields_memory_ops(self):
        items = [
            MemoryOp(op=OpType.ADD, content="a", category=MEMORY_CATEGORY_FACT),
            MemoryOp(op=OpType.NOOP),
        ]
        ops = MemoryOpsList(root=items)
        assert ops.ops == items
        assert list(ops.ops) == items


# ---------------------------------------------------------------------------
# validate_against_candidates — optimistic-concurrency drift check
# ---------------------------------------------------------------------------

class TestValidateAgainstCandidates:

    @pytest.mark.unit
    def test_passes_when_all_target_ids_in_candidate_set(self):
        ops = MemoryOpsList(
            root=[
                MemoryOp(op=OpType.UPDATE, target_id=1, content="a"),
                MemoryOp(op=OpType.DELETE, target_id=2),
                MemoryOp(op=OpType.ADD, content="b", category=MEMORY_CATEGORY_FACT),
            ]
        )
        assert validate_against_candidates(ops, {1, 2, 3}) is None

    @pytest.mark.unit
    def test_rejects_hallucinated_target_id(self):
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.UPDATE, target_id=999, content="x")])
        result = validate_against_candidates(ops, {1, 2, 3})
        assert result == "id_reject"

    @pytest.mark.unit
    def test_rejects_when_target_drifted_out_of_set(self):
        # Simulates: between retrieve and apply, another task DELETEd
        # row 2 so the candidate set lost it.
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.UPDATE, target_id=2, content="x")])
        result = validate_against_candidates(ops, {1, 3})  # 2 missing
        assert result == "id_reject"

    @pytest.mark.unit
    def test_passes_with_empty_ops_list(self):
        ops = MemoryOpsList(root=[])
        assert validate_against_candidates(ops, set()) is None

    @pytest.mark.unit
    def test_passes_with_only_add_and_noop(self):
        # ADD and NOOP have no target_id, so candidate set is irrelevant
        ops = MemoryOpsList(
            root=[
                MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT),
                MemoryOp(op=OpType.NOOP),
            ]
        )
        assert validate_against_candidates(ops, set()) is None

    @pytest.mark.unit
    def test_returns_short_label_suitable_for_metric(self):
        # The return value goes into a Prometheus label, must be a
        # bounded, stable string (not a free-text error message).
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.UPDATE, target_id=999, content="x")])
        result = validate_against_candidates(ops, {1})
        assert isinstance(result, str)
        assert " " not in result  # one-word label
        assert len(result) < 32

    @pytest.mark.unit
    def test_mixed_batch_with_one_invalid_target_returns_id_reject(self):
        # First-encounter wins: validate_against_candidates short-circuits.
        # Either ordering should produce 'id_reject' (the label is bounded).
        ops_a = MemoryOpsList(root=[
            MemoryOp(op=OpType.UPDATE, target_id=1, content="ok"),
            MemoryOp(op=OpType.DELETE, target_id=999),  # invalid
        ])
        assert validate_against_candidates(ops_a, {1}) == "id_reject"

        ops_b = MemoryOpsList(root=[
            MemoryOp(op=OpType.UPDATE, target_id=999, content="ok"),  # invalid
            MemoryOp(op=OpType.DELETE, target_id=2),
        ])
        assert validate_against_candidates(ops_b, {2}) == "id_reject"


# ---------------------------------------------------------------------------
# JSON round-trip — the schema's whole purpose is to validate LLM tool-call
# JSON arriving over the wire. These tests lock the round-trip behavior so
# a Pydantic v2 enum-serialization change can't slip through silently.
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:

    @pytest.mark.unit
    def test_op_type_serializes_as_uppercase_string(self):
        op = MemoryOp(op=OpType.ADD, content="x", category=MEMORY_CATEGORY_FACT)
        dumped = op.model_dump_json()
        # The enum must appear as a plain string in JSON, not as
        # {"op": {"value": "ADD"}} or similar.
        assert '"op":"ADD"' in dumped

    @pytest.mark.unit
    def test_memory_op_round_trip(self):
        original = MemoryOp(
            op=OpType.UPDATE,
            target_id=42,
            content="updated content",
            importance=0.8,
            reason="user clarified",
        )
        json_str = original.model_dump_json()
        round_tripped = MemoryOp.model_validate_json(json_str)
        assert round_tripped == original

    @pytest.mark.unit
    def test_memory_ops_list_round_trip(self):
        original = MemoryOpsList(root=[
            MemoryOp(op=OpType.ADD, content="prefers German", category=MEMORY_CATEGORY_PREFERENCE),
            MemoryOp(op=OpType.UPDATE, target_id=10, content="new"),
            MemoryOp(op=OpType.DELETE, target_id=20, reason="user retracted"),
            MemoryOp(op=OpType.NOOP),
        ])
        json_str = original.model_dump_json()
        round_tripped = MemoryOpsList.model_validate_json(json_str)
        assert round_tripped.ops == original.ops

    @pytest.mark.unit
    def test_pydantic_default_iter_preserved(self):
        # F8 from adversarial review: previously MemoryOpsList overrode
        # __iter__ to yield MemoryOp objects, which broke `dict(ops)`,
        # `**ops`, and any Pydantic-native code expecting BaseModel's
        # (field_name, value) tuples. The override is now removed.
        ops = MemoryOpsList(root=[MemoryOp(op=OpType.NOOP)])
        # The Pydantic-canonical access path:
        assert ops.root == [MemoryOp(op=OpType.NOOP)]
        # And the convenience property we added:
        assert ops.ops == [MemoryOp(op=OpType.NOOP)]
        # dict() must yield a 'root' key, NOT raise TypeError:
        as_dict = dict(ops)
        assert "root" in as_dict
        assert as_dict["root"] == [MemoryOp(op=OpType.NOOP)]

    @pytest.mark.unit
    def test_memory_ops_list_parses_llm_style_json(self):
        # Simulates an LLM tool-call response shape.
        llm_output = (
            '[{"op": "ADD", "content": "prefers German", "category": "preference"}, '
            '{"op": "NOOP", "reason": "nothing personal"}]'
        )
        parsed = MemoryOpsList.model_validate_json(llm_output)
        assert len(parsed) == 2
        assert parsed.ops[0].op == OpType.ADD
        assert parsed.ops[1].op == OpType.NOOP

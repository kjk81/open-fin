from __future__ import annotations

from agent.graph import _enforce_numeric_verification


def test_unverified_numeric_claim_emits_cannot_verify_warning():
    response = "Revenue was 123 in Q4 without source."
    out = _enforce_numeric_verification(
        response,
        allowed_ref_ids=set(),
        allowed_numeric_tokens=set(),
    )
    assert "Cannot Verify" in out


def test_verified_numeric_claim_is_preserved():
    response = "Revenue was 123 [REF-1] in Q4."
    out = _enforce_numeric_verification(
        response,
        allowed_ref_ids={"REF-1"},
        allowed_numeric_tokens={"123"},
    )
    assert "Cannot Verify" not in out
    assert "123 [REF-1]" in out

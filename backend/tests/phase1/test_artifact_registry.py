"""Phase 1 — Tests for agent.graph artifact registry and numeric verification."""

from __future__ import annotations

from typing import Any, List, Dict, Set

from agent.graph import (
    _build_artifact_registry,
    _format_artifacts_for_prompt,
    _enforce_numeric_verification,
)


def _sample_tool_results() -> List[Dict[str, Any]]:
    return [
        {
            "tool": "get_company_profile",
            "args": {"symbol": "AAPL"},
            "result": '{"data": {"market_cap": 1234567890}, "sources": [{"url": "https://example.com/a", "title": "A"}]}',
        },
        {
            "tool": "get_financial_statements",
            "args": {"symbol": "AAPL", "period": "annual", "limit": 4},
            "result": '{"data": [{"revenue": 1000}], "sources": [{"url": "https://example.com/b", "title": "B"}]}',
        },
    ]


def test_build_artifact_registry_is_deterministic_with_stable_refs():
    tool_results = _sample_tool_results()
    citations: list[dict[str, Any]] = [
        {"url": "https://example.com/c", "title": "C"},
    ]

    artifacts_1, numbers_1 = _build_artifact_registry(tool_results, citations)
    artifacts_2, numbers_2 = _build_artifact_registry(tool_results, citations)

    # Deterministic ordering and REF IDs for identical inputs
    assert [a["stable_key"] for a in artifacts_1] == [a["stable_key"] for a in artifacts_2]
    assert [a["ref_id"] for a in artifacts_1] == [a["ref_id"] for a in artifacts_2]

    # Allowed numeric tokens should be identical across runs
    assert numbers_1 == numbers_2


def test_build_artifact_registry_includes_citation_artifacts_and_numbers():
    tool_results = []
    citations = [
        {"url": "https://example.com/a", "title": "A"},
        {"url": "https://example.com/b", "title": "B"},
    ]

    artifacts, numbers = _build_artifact_registry(tool_results, citations)

    # All citations should become artifacts with tool="citation" and a ref_id
    assert len(artifacts) == 2
    assert {a["tool"] for a in artifacts} == {"citation"}
    assert all("ref_id" in a for a in artifacts)
    assert isinstance(numbers, set)


def test_format_artifacts_for_prompt_yields_ref_blocks():
    tool_results = _sample_tool_results()
    citations: list[dict[str, Any]] = []

    artifacts, _ = _build_artifact_registry(tool_results, citations)
    prompt_block = _format_artifacts_for_prompt(artifacts)

    # Ensure REF IDs and key fields appear in the rendered block
    for artifact in artifacts:
        ref = f"[{artifact['ref_id']}]"
        assert ref in prompt_block
        assert f"TOOL: {artifact['tool']}" in prompt_block


def test_enforce_numeric_verification_strips_unbacked_numbers_and_keeps_verified_lines():
    response_text = "\n".join(
        [
            "Intro line without numbers.",
            "Unverified value 999 should be removed.",
            "Verified value 123 with ref [REF-1].",
            "Bad ref 123 [REF-2] should be removed.",
            "Mismatched number 555 [REF-1] should be removed.",
        ]
    )

    allowed_ref_ids: Set[str] = {"REF-1"}
    allowed_numeric_tokens: Set[str] = {"123"}

    processed = _enforce_numeric_verification(
        response_text,
        allowed_ref_ids=allowed_ref_ids,
        allowed_numeric_tokens=allowed_numeric_tokens,
    )
    lines = processed.splitlines()

    warning = (
        "Cannot Verify: A numeric claim was removed because it could not be "
        "verified against current-turn artifacts."
    )

    assert lines[0] == "Intro line without numbers."
    assert lines[1] == warning
    assert lines[2] == "Verified value 123 with ref [REF-1]."
    assert lines[3] == warning
    assert lines[4] == warning


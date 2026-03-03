from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def build_embedding_text(
    node_type: str,
    name: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Return canonical high-value embedding text for a KG node.

    Eligible categories:
    - Run summaries / research takeaways
    - KG node descriptions (company, sector, industry)
    - Key sentiment and filing takeaways

    Returns ``None`` for transient/low-value node types.
    """
    metadata = metadata or {}
    node_type_norm = _norm(node_type).lower()
    name_norm = _norm(name)

    if node_type_norm in {"run_summary", "research_takeaway", "episodic_summary"}:
        summary = _norm(metadata.get("summary") or metadata.get("text") or name_norm)
        return summary or None

    if node_type_norm == "company":
        description = _norm(metadata.get("description"))
        company_name = _norm(metadata.get("company_name") or metadata.get("name"))
        sector = _norm(metadata.get("sector"))
        industry = _norm(metadata.get("industry"))
        informative = [company_name, description, sector, industry]
        if not any(informative):
            return None
        parts = [name_norm, company_name, description, sector, industry]
        text = " ".join(part for part in parts if part).strip()
        return text or None

    if node_type_norm == "sector":
        label = name_norm.removeprefix("sector:")
        return f"Sector: {label}" if label else None

    if node_type_norm == "industry":
        label = name_norm.removeprefix("industry:")
        return f"Industry: {label}" if label else None

    if node_type_norm == "sentiment":
        ticker = _norm(metadata.get("ticker"))
        bias = _norm(metadata.get("overall_bias"))
        majority = _norm(metadata.get("majority_opinion"))
        catalysts = metadata.get("key_catalysts")
        catalyst_text = ""
        if isinstance(catalysts, list):
            catalyst_text = " ".join(_norm(item) for item in catalysts if _norm(item))
        parts = [name_norm, ticker, bias, majority, catalyst_text]
        text = " ".join(part for part in parts if part).strip()
        return text or None

    if node_type_norm == "filing":
        filing_type = _norm(metadata.get("filing_type"))
        ticker = _norm(metadata.get("company_ticker"))
        filed_date = _norm(metadata.get("filed_date"))
        url = _norm(metadata.get("url"))
        parts = [name_norm, filing_type, ticker, filed_date, url]
        text = " ".join(part for part in parts if part).strip()
        return text or None

    return None

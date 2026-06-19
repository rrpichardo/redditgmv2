"""Shared briefing synthesis used by HTTP and subprocess adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.gm_insights import (
    ProviderConfig,
    fallback_synthesis,
    generate_synthesis_with_llm,
    summary_payload,
)


@dataclass(frozen=True)
class BriefingResult:
    report: str
    used_llm: bool
    warning: str | None = None


def generate_briefing(
    df: pd.DataFrame,
    *,
    provider: ProviderConfig | None = None,
    use_llm: bool = False,
    fallback_on_error: bool = False,
) -> BriefingResult:
    """Generate one briefing, optionally degrading an LLM error to deterministic output."""
    payload = summary_payload(df)
    if not use_llm:
        return BriefingResult(fallback_synthesis(payload), used_llm=False)
    if provider is None:
        raise ValueError("provider is required when use_llm=True")
    try:
        return BriefingResult(
            generate_synthesis_with_llm(payload, provider),
            used_llm=True,
        )
    except Exception as exc:
        if not fallback_on_error:
            raise
        warning = f"LLM synthesis failed; deterministic fallback used: {exc}"
        return BriefingResult(fallback_synthesis(payload), used_llm=False, warning=warning[:500])


def write_briefing(
    df: pd.DataFrame,
    output_path: Path,
    *,
    provider: ProviderConfig | None = None,
    use_llm: bool = False,
    fallback_on_error: bool = False,
) -> BriefingResult:
    """Generate and atomically persist a briefing."""
    result = generate_briefing(
        df,
        provider=provider,
        use_llm=use_llm,
        fallback_on_error=fallback_on_error,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(result.report, encoding="utf-8")
    os.replace(tmp_path, output_path)
    return result

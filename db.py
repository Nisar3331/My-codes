from __future__ import annotations
from typing import Dict, List, Tuple
import re


def match_sops(sops: List[Dict], platform: str, error_text: str) -> List[Tuple[Dict, float]]:
    """Deterministic SOP matcher: signatures + regex.

    You can layer vector retrieval on top (FAISS) and then filter by these matches.
    """
    et = error_text or ""
    et_low = et.lower()
    hits: List[Tuple[Dict, float]] = []

    for sop in sops:
        sop_platform = sop.get("platform", "*")
        if sop_platform not in ("*", platform):
            continue

        score = 0.0
        for s in (sop.get("match", {}).get("error_signatures", []) or []):
            if str(s).lower() in et_low:
                score += 0.25
        for rx in (sop.get("match", {}).get("log_regex", []) or []):
            if re.search(rx, et, flags=re.IGNORECASE):
                score += 0.40

        if score > 0:
            hits.append((sop, min(score, 1.0)))

    hits.sort(key=lambda x: x[1], reverse=True)
    return hits

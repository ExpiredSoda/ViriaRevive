"""Resolve likely game identity before fetching game knowledge.

This layer keeps game detection conservative: filename/user/title hints are
evidence, Wikidata search finds possible QIDs, and `game_context` validates that
the chosen item is actually a video game before the result enters AI prompts.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from game_context import (
    compact_game_context_for_prompt,
    get_game_context,
    get_game_context_by_qid,
    normalize_game_title,
)


GAME_IDENTITY_SCHEMA_VERSION = 1
WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_USER_AGENT = "ViriaRevive game-identity resolver (local creator tool)"

_STOPWORDS = {
    "a", "an", "and", "at", "blind", "clip", "clips", "episode", "final",
    "first", "for", "game", "gameplay", "gaming", "highlight", "highlights",
    "in", "lets", "live", "my", "of", "official", "part", "playthrough",
    "recording", "run", "short", "shorts", "stream", "the", "to", "video",
    "vod", "walkthrough", "with",
}


def resolve_game_identity(
    *,
    source_path: str | Path | None = None,
    explicit_title: str | None = None,
    creator_context: str | None = None,
    transcript: str | None = None,
    allow_network: bool = True,
    timeout: int = 8,
    db_path: str | Path | None = None,
) -> dict:
    """Resolve a likely game identity and attach compact game context."""
    candidates = collect_game_title_candidates(
        source_path=source_path,
        explicit_title=explicit_title,
        creator_context=creator_context,
        transcript=transcript,
    )
    result = {
        "schema_version": GAME_IDENTITY_SCHEMA_VERSION,
        "status": "no_candidates" if not candidates else "not_started",
        "provider": "wikidata",
        "selection_impact": "game_context_lookup",
        "confidence": 0.0,
        "title": normalize_game_title(explicit_title) if explicit_title else "",
        "qid": "",
        "evidence": [],
        "candidates": candidates[:8],
        "game_context": {},
        "game_context_prompt": {"available": False, "status": "missing"},
    }
    if not candidates:
        return result

    best = _resolve_from_cache(candidates, db_path=db_path)
    if not best and allow_network:
        best = _resolve_from_search(candidates, timeout=timeout, db_path=db_path)
    if not best:
        result["status"] = "no_match"
        result["title"] = candidates[0]["title"]
        return result

    context = best["game_context"]
    prompt_context = compact_game_context_for_prompt(context)
    result.update({
        "status": best.get("status", "ok"),
        "confidence": round(float(best.get("confidence") or 0.0), 4),
        "title": context.get("label") or best.get("title") or candidates[0]["title"],
        "qid": context.get("qid") or best.get("qid") or "",
        "matched_candidate": best.get("candidate"),
        "matched_via": best.get("matched_via"),
        "evidence": best.get("evidence") or [],
        "game_context": context,
        "game_context_prompt": prompt_context,
    })
    return result


def collect_game_title_candidates(
    *,
    source_path: str | Path | None = None,
    explicit_title: str | None = None,
    creator_context: str | None = None,
    transcript: str | None = None,
) -> list[dict]:
    """Collect likely title strings from trusted local hints."""
    candidates: list[dict] = []

    def add(title, source: str, weight: float):
        cleaned = normalize_game_title(title)
        if not cleaned or _too_generic(cleaned):
            return
        key = _lookup_key(cleaned)
        for item in candidates:
            if item["key"] == key:
                item["weight"] = max(item["weight"], round(float(weight), 3))
                if source not in item["sources"]:
                    item["sources"].append(source)
                return
        candidates.append({
            "title": cleaned,
            "key": key,
            "weight": round(float(weight), 3),
            "sources": [source],
        })

    add(explicit_title, "explicit_title", 1.0)
    for variant in _title_variants(explicit_title):
        add(variant, "explicit_title_variant", 0.92)
    if source_path:
        try:
            path = Path(source_path)
            add(path.stem, "source_filename", 0.82)
            for variant in _title_variants(path.stem):
                add(variant, "source_filename_variant", 0.76)
            add(path.parent.name, "source_folder", 0.70)
            if path.parent.parent:
                add(path.parent.parent.name, "source_parent_folder", 0.55)
        except Exception:
            pass
    for text, source, weight in (
        (creator_context, "creator_context", 0.74),
        (transcript, "transcript_hint", 0.38),
    ):
        for phrase in _explicit_game_phrases(text):
            add(phrase, source, weight)
    candidates.sort(key=lambda item: item["weight"], reverse=True)
    return candidates[:12]


def search_wikidata_games(query: str, *, limit: int = 5, timeout: int = 8) -> list[dict]:
    """Search Wikidata item labels/aliases. Results are validated later."""
    cleaned = normalize_game_title(query)
    if not cleaned:
        return []
    params = {
        "action": "wbsearchentities",
        "search": cleaned,
        "language": "en",
        "uselang": "en",
        "type": "item",
        "format": "json",
        "limit": max(1, min(10, int(limit or 5))),
    }
    url = f"{WIKIDATA_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": WIKIDATA_USER_AGENT})
    with urllib.request.urlopen(request, timeout=max(1, int(timeout or 8))) as response:
        data = json.loads(response.read().decode("utf-8"))
    rows = data.get("search", []) if isinstance(data, dict) else []
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("id") or "").strip().upper()
        if not re.fullmatch(r"Q\d+", qid):
            continue
        results.append({
            "qid": qid,
            "label": _safe_text(row.get("label"), limit=120),
            "description": _safe_text(row.get("description"), limit=220),
            "match": _safe_text(row.get("match", {}).get("text") if isinstance(row.get("match"), dict) else "", limit=120),
        })
    return results


def score_identity_match(candidate_title: str, game_context: dict | None, *, candidate_weight: float = 1.0) -> dict:
    """Score how well a candidate title matches a validated game context."""
    context = game_context if isinstance(game_context, dict) else {}
    facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
    label = str(context.get("label") or "")
    aliases = context.get("aliases") if isinstance(context.get("aliases"), list) else []
    names = [label, *aliases]
    candidate_key = _lookup_key(candidate_title)
    candidate_tokens = _tokens(candidate_title)
    best_name = ""
    best = 0.0
    reasons = []
    for name in names:
        name_key = _lookup_key(name)
        if not name_key:
            continue
        score = 0.0
        if candidate_key == name_key:
            score = 0.95
            reasons.append("exact_label_or_alias")
        elif candidate_key and (candidate_key in name_key or name_key in candidate_key):
            score = 0.78
            reasons.append("contained_label_or_alias")
        else:
            overlap = _token_overlap(candidate_tokens, _tokens(name))
            if overlap >= 0.60:
                score = 0.48 + (0.30 * overlap)
                reasons.append("token_overlap")
        if score > best:
            best = score
            best_name = name
    if facts.get("genres") or facts.get("developers") or facts.get("series"):
        best += 0.03
    best *= max(0.25, min(1.0, float(candidate_weight or 1.0)))
    return {
        "confidence": round(max(0.0, min(0.99, best)), 4),
        "matched_name": best_name,
        "reasons": sorted(set(reasons)),
    }


def _resolve_from_cache(candidates: list[dict], *, db_path=None) -> dict | None:
    best = None
    for candidate in candidates:
        context = get_game_context(
            candidate["title"],
            allow_network=False,
            db_path=db_path,
        )
        if context.get("status") not in {"ok", "cache_hit"}:
            continue
        scored = score_identity_match(candidate["title"], context, candidate_weight=candidate["weight"])
        item = {
            "status": "cache_hit",
            "confidence": scored["confidence"],
            "qid": context.get("qid"),
            "title": context.get("label") or candidate["title"],
            "candidate": candidate,
            "matched_via": "local_cache",
            "evidence": [
                *_evidence_for_candidate(candidate),
                {"type": "local_cache", "qid": context.get("qid"), "matched_name": scored.get("matched_name")},
                *({"type": reason} for reason in scored.get("reasons", [])),
            ],
            "game_context": context,
        }
        if not best or item["confidence"] > best["confidence"]:
            best = item
    return best if best and best["confidence"] >= 0.48 else None


def _resolve_from_search(candidates: list[dict], *, timeout: int, db_path=None) -> dict | None:
    best = None
    for candidate in candidates[:5]:
        try:
            search_results = search_wikidata_games(candidate["title"], limit=5, timeout=timeout)
        except Exception:
            continue
        for search_result in search_results:
            context = get_game_context_by_qid(
                search_result["qid"],
                allow_network=True,
                timeout=timeout,
                db_path=db_path,
            )
            if context.get("status") not in {"ok", "cache_hit"}:
                continue
            scored = score_identity_match(candidate["title"], context, candidate_weight=candidate["weight"])
            # Search result descriptions are only hints; game_context already
            # validated the QID as a video game.
            if "video game" in str(search_result.get("description") or "").lower():
                scored["confidence"] = min(0.99, round(scored["confidence"] + 0.03, 4))
            item = {
                "status": "ok",
                "confidence": scored["confidence"],
                "qid": context.get("qid"),
                "title": context.get("label") or search_result.get("label") or candidate["title"],
                "candidate": candidate,
                "matched_via": "wikidata_search",
                "evidence": [
                    *_evidence_for_candidate(candidate),
                    {
                        "type": "wikidata_search",
                        "qid": search_result.get("qid"),
                        "label": search_result.get("label"),
                        "description": search_result.get("description"),
                    },
                    *({"type": reason} for reason in scored.get("reasons", [])),
                ],
                "game_context": context,
            }
            if not best or item["confidence"] > best["confidence"]:
                best = item
        if best and best["confidence"] >= 0.82:
            break
    return best if best and best["confidence"] >= 0.45 else None


def _explicit_game_phrases(value: str | None) -> list[str]:
    text = str(value or "")
    if not text.strip():
        return []
    phrases = []
    for pattern in (
        r"(?i)\bgame\s*[:=-]\s*([A-Z0-9][A-Za-z0-9:'’&\-\s]{2,60})",
        r"(?i)\bplaying\s+([A-Z0-9][A-Za-z0-9:'’&\-\s]{2,60})",
        r"(?i)\bin\s+([A-Z][A-Za-z0-9:'’&\-\s]{2,40})\s+(?:run|playthrough|gameplay|stream)",
        r"(?i)\b(?:blind|first|new|casual)?\s*([A-Z][A-Za-z0-9:'’&\-\s]{2,50})\s+(?:run|playthrough|gameplay|stream|vod)\b",
    ):
        for match in re.finditer(pattern, text):
            phrases.append(match.group(1).strip(" .,:;"))
    return phrases[:4]


def _title_variants(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    variants = []
    text = re.sub(r"[\[\(].{0,80}[\]\)]", " ", text)
    pieces = re.split(r"\s+(?:-|--|—|–|\||/)\s+|\s+[:：]\s+", text)
    for piece in pieces[:4]:
        cleaned = _strip_episode_tail(piece)
        if cleaned and cleaned != text:
            variants.append(cleaned)
    stripped = _strip_episode_tail(text)
    if stripped and stripped != text:
        variants.append(stripped)
    result = []
    for variant in variants:
        cleaned = normalize_game_title(variant)
        if cleaned and cleaned not in result and not _too_generic(cleaned):
            result.append(cleaned)
    return result[:5]


def _strip_episode_tail(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)\b(?:part|episode|ep|pt|chapter|stream|vod|playthrough|walkthrough)\s*#?\d+.*$",
        "",
        text,
    )
    text = re.sub(r"(?i)\b\d+\s*(?:hour|hr|minute|min)\b.*$", "", text)
    text = re.sub(r"(?i)\b(?:blind|first)\s+(?:run|playthrough)\b.*$", "", text)
    return text.strip(" ._-:|/")


def _evidence_for_candidate(candidate: dict) -> list[dict]:
    return [
        {
            "type": "title_candidate",
            "title": candidate.get("title"),
            "weight": candidate.get("weight"),
            "sources": candidate.get("sources", []),
        }
    ]


def _tokens(value: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalize_game_title(value).lower())
        if token and token not in _STOPWORDS and len(token) > 1
    }


def _token_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _too_generic(value: str) -> bool:
    if not re.search(r"[A-Za-z]", str(value or "")):
        return True
    tokens = _tokens(value)
    return not tokens or all(token in _STOPWORDS for token in tokens)


def _lookup_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_game_title(value).lower())


def _safe_text(value, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text[: max(0, int(limit))]

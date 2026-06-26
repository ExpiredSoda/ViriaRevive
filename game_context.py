"""Small Wikidata-backed game context cache for local AI analysis.

The module stores compact, attributed facts only. It does not cache raw wiki
pages or walkthrough prose, and prompt payloads intentionally include only the
few fields useful for clip analysis and metadata generation.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

from config import GAME_CONTEXT_DB_FILE, GAME_CONTEXT_DIR


GAME_CONTEXT_SCHEMA_VERSION = 1
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_SOURCE_BASE_URL = "https://www.wikidata.org/wiki/"
WIKIDATA_LICENSE = "CC0-1.0"
WIKIDATA_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
WIKIDATA_USER_AGENT = "ViriaRevive game-context cache (local creator tool)"
QUERY_TIMEOUT_SECONDS = 8

CURATED_PROPERTIES = {
    "P31": ("instance_of", "instance of"),
    "P577": ("first_release_date", "publication date"),
    "P178": ("developers", "developer"),
    "P123": ("publishers", "publisher"),
    "P136": ("genres", "genre"),
    "P400": ("platforms", "platform"),
    "P179": ("series", "part of the series"),
    "P1434": ("fictional_universes", "takes place in fictional universe"),
    "P674": ("characters", "characters"),
    "P840": ("narrative_locations", "narrative location"),
    "P8411": ("environments", "set in environment"),
    "P404": ("game_modes", "game mode"),
}

SINGLE_VALUE_FIELDS = {"first_release_date"}
PROMPT_LIST_FIELDS = (
    "aliases",
    "series",
    "fictional_universes",
    "genres",
    "developers",
    "publishers",
    "characters",
    "narrative_locations",
    "environments",
    "game_modes",
    "platforms",
)


def get_game_context(
    game_title: str | None,
    *,
    allow_network: bool = True,
    force_refresh: bool = False,
    timeout: int = QUERY_TIMEOUT_SECONDS,
    db_path: str | Path | None = None,
) -> dict:
    """Return compact game context from cache, optionally refreshing Wikidata."""
    normalized_title = normalize_game_title(game_title)
    if not normalized_title:
        return _empty_context("no_game_title", game_title=game_title or "")

    path = Path(db_path) if db_path else GAME_CONTEXT_DB_FILE
    conn = None
    cached_qid = ""
    try:
        conn = _connect(path)
        cached_qid = _find_cached_qid(conn, normalized_title)
        if cached_qid and not force_refresh:
            context = _context_from_cache(conn, cached_qid, status="cache_hit")
            if context:
                return context
        if not allow_network:
            if cached_qid:
                context = _context_from_cache(conn, cached_qid, status="cache_hit")
                if context:
                    return context
            return _empty_context("cache_miss", game_title=normalized_title)

        rows = _query_wikidata_game(normalized_title, timeout=timeout)
        if not rows:
            return _empty_context("no_match", game_title=normalized_title)
        qid = _store_wikidata_rows(conn, rows)
        if not qid:
            return _empty_context("no_match", game_title=normalized_title)
        context = _context_from_cache(conn, qid, status="ok")
        return context or _empty_context("no_match", game_title=normalized_title)
    except Exception as exc:
        if conn is not None and cached_qid:
            context = _context_from_cache(conn, cached_qid, status="cache_hit")
            if context:
                context["warning"] = f"refresh_failed: {str(exc)[:140]}"
                return context
        return _empty_context("query_error", game_title=normalized_title, error=str(exc)[:180])
    finally:
        if conn is not None:
            conn.close()


def get_game_context_by_qid(
    qid: str | None,
    *,
    allow_network: bool = True,
    force_refresh: bool = False,
    timeout: int = QUERY_TIMEOUT_SECONDS,
    db_path: str | Path | None = None,
) -> dict:
    """Return compact game context by Wikidata QID."""
    normalized_qid = _safe_qid(qid)
    if not normalized_qid:
        return _empty_context("invalid_qid", game_title=str(qid or ""))

    path = Path(db_path) if db_path else GAME_CONTEXT_DB_FILE
    conn = None
    try:
        conn = _connect(path)
        if not force_refresh:
            context = _context_from_cache(conn, normalized_qid, status="cache_hit")
            if context:
                return context
        if not allow_network:
            return _empty_context("cache_miss", game_title=normalized_qid)

        rows = _query_wikidata_game_by_qid(normalized_qid, timeout=timeout)
        if not rows:
            return _empty_context("no_match", game_title=normalized_qid)
        stored_qid = _store_wikidata_rows(conn, rows)
        if not stored_qid:
            return _empty_context("no_match", game_title=normalized_qid)
        context = _context_from_cache(conn, stored_qid, status="ok")
        return context or _empty_context("no_match", game_title=normalized_qid)
    except Exception as exc:
        context = _context_from_cache(conn, normalized_qid, status="cache_hit") if conn is not None else {}
        if context:
            context["warning"] = f"refresh_failed: {str(exc)[:140]}"
            return context
        return _empty_context("query_error", game_title=normalized_qid, error=str(exc)[:180])
    finally:
        if conn is not None:
            conn.close()


def seed_recent_game_context(
    *,
    limit: int = 20,
    since_year: int = 2025,
    offset: int = 0,
    skip_existing: bool = True,
    timeout: int = 12,
    db_path: str | Path | None = None,
) -> dict:
    """Fetch a small newest-to-oldest batch of released games into the cache."""
    limit = max(1, min(50, int(limit or 20)))
    since_year = max(1970, min(2100, int(since_year or 2025)))
    offset = max(0, int(offset or 0))
    started = time.monotonic()
    result = {
        "schema_version": GAME_CONTEXT_SCHEMA_VERSION,
        "status": "not_started",
        "provider": "wikidata",
        "requested_limit": limit,
        "since_year": since_year,
        "offset": offset,
        "skip_existing": bool(skip_existing),
        "seeded_count": 0,
        "skipped_existing_count": 0,
        "failed_count": 0,
        "games": [],
        "elapsed_seconds": 0.0,
    }
    try:
        recent = query_recent_wikidata_games(limit=limit, since_year=since_year, offset=offset, timeout=timeout)
    except Exception as exc:
        result["status"] = "query_error"
        result["error"] = _safe_text(exc, limit=180)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    seen_qids = set()
    for item in recent:
        qid = _safe_qid(item.get("qid"))
        if not qid or qid in seen_qids:
            continue
        seen_qids.add(qid)
        if skip_existing:
            cached = get_game_context_by_qid(
                qid,
                allow_network=False,
                db_path=db_path,
            )
            if cached.get("status") in {"ok", "cache_hit"}:
                result["skipped_existing_count"] += 1
                result["games"].append({
                    "qid": qid,
                    "label": cached.get("label") or item.get("label") or qid,
                    "release_date": item.get("release_date"),
                    "status": "skipped_existing",
                })
                continue
        context = get_game_context_by_qid(
            qid,
            allow_network=True,
            timeout=timeout,
            db_path=db_path,
        )
        if context.get("status") in {"ok", "cache_hit"}:
            result["seeded_count"] += 1
        else:
            result["failed_count"] += 1
        result["games"].append({
            "qid": qid,
            "label": context.get("label") or item.get("label") or qid,
            "release_date": item.get("release_date"),
            "status": context.get("status"),
        })
    result["status"] = "ok" if result["seeded_count"] else (
        "all_existing" if result["skipped_existing_count"] else "no_games_seeded"
    )
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def query_recent_wikidata_games(
    *,
    limit: int = 20,
    since_year: int = 2025,
    offset: int = 0,
    timeout: int = 12,
) -> list[dict]:
    """Return recent released Wikidata video-game items, newest first."""
    limit = max(1, min(50, int(limit or 20)))
    since_year = max(1970, min(2100, int(since_year or 2025)))
    offset = max(0, int(offset or 0))
    query = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT DISTINCT ?game ?gameLabel ?gameDescription ?release ?sitelinks WHERE {{
  ?game wdt:P31/wdt:P279* wd:Q7889 .
  ?game wdt:P577 ?release .
  ?game rdfs:label ?gameLabel .
  FILTER(LANG(?gameLabel) = "en")
  FILTER(?release <= NOW())
  FILTER(?release >= "{since_year}-01-01T00:00:00Z"^^xsd:dateTime)
  OPTIONAL {{ ?game schema:description ?gameDescription FILTER(LANG(?gameDescription) = "en") . }}
  OPTIONAL {{ ?game wikibase:sitelinks ?sitelinks . }}
}}
ORDER BY DESC(?release) DESC(?sitelinks)
LIMIT {limit}
OFFSET {offset}
"""
    rows = _sparql_rows(query, timeout=timeout)
    result = []
    seen_qids = set()
    for row in rows:
        qid = _qid_from_uri(_binding_value(row, "game"))
        label = _safe_text(_binding_value(row, "gameLabel"), limit=120)
        if not qid or not label or qid in seen_qids:
            continue
        seen_qids.add(qid)
        result.append({
            "qid": qid,
            "label": label,
            "description": _safe_text(_binding_value(row, "gameDescription"), limit=220),
            "release_date": _safe_text(_binding_value(row, "release"), limit=40),
            "sitelinks": _safe_int(_binding_value(row, "sitelinks")),
        })
    return result


def compact_game_context_for_prompt(game_context: dict | None) -> dict:
    """Return a prompt-safe subset for Ollama and heuristic scoring."""
    context = game_context if isinstance(game_context, dict) else {}
    if context.get("status") not in {"ok", "cache_hit"}:
        return {
            "status": context.get("status") or "missing",
            "available": False,
        }
    facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
    prompt = {
        "status": context.get("status"),
        "available": True,
        "provider": "wikidata",
        "qid": _safe_text(context.get("qid"), limit=24),
        "label": _safe_text(context.get("label"), limit=120),
        "description": _safe_text(context.get("description"), limit=180),
        "release_year": _release_year(facts.get("first_release_date")),
        "source_url": _safe_source_url(context.get("source_url")),
        "license": WIKIDATA_LICENSE,
    }
    for field in PROMPT_LIST_FIELDS:
        values = facts.get(field) if field != "aliases" else context.get("aliases")
        cleaned = _safe_list(values, limit=8)
        if cleaned:
            prompt[field] = cleaned
    if facts.get("first_release_date"):
        prompt["first_release_date"] = _safe_text(facts.get("first_release_date"), limit=32)
    return {key: value for key, value in prompt.items() if value not in ("", [], None)}


def normalize_game_title(value: str | None) -> str:
    text = re.sub(r"[_\\/\-]+", " ", str(value or ""))
    text = re.sub(r"(?i)(vertical|horizontal|shorts?|clips?|recordings?|captures?|gameplay|vod)$", " ", text)
    text = re.sub(r"(?i)\b(vertical|horizontal|shorts?|clips?|recordings?|captures?|gameplay|vod)\b", " ", text)
    text = re.sub(r"\b\d{4}[- ]\d{2}[- ]\d{2}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    return text[:120]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            qid TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            description TEXT,
            sitelinks INTEGER DEFAULT 0,
            source_url TEXT NOT NULL,
            license TEXT NOT NULL DEFAULT 'CC0-1.0',
            license_url TEXT NOT NULL DEFAULT 'https://creativecommons.org/publicdomain/zero/1.0/',
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS game_aliases (
            qid TEXT NOT NULL REFERENCES games(qid) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            PRIMARY KEY (qid, alias)
        );
        CREATE TABLE IF NOT EXISTS game_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT NOT NULL REFERENCES games(qid) ON DELETE CASCADE,
            property_id TEXT NOT NULL,
            property_label TEXT NOT NULL,
            prompt_field TEXT NOT NULL,
            value_kind TEXT NOT NULL,
            value_id TEXT,
            value_label TEXT,
            value_text TEXT,
            datatype TEXT,
            source_url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(qid, property_id, value_id, value_text)
        );
        CREATE INDEX IF NOT EXISTS idx_game_aliases_alias ON game_aliases(alias COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_game_facts_qid_prop ON game_facts(qid, property_id);
        CREATE INDEX IF NOT EXISTS idx_game_facts_prompt_field ON game_facts(qid, prompt_field);
        """
    )
    return conn


def _find_cached_qid(conn: sqlite3.Connection, title: str) -> str:
    key = _lookup_key(title)
    for qid, label in conn.execute("SELECT qid, label FROM games"):
        if _lookup_key(label) == key:
            return str(qid)
    for qid, alias in conn.execute("SELECT qid, alias FROM game_aliases"):
        if _lookup_key(alias) == key:
            return str(qid)
    return ""


def _context_from_cache(conn: sqlite3.Connection, qid: str, *, status: str) -> dict | None:
    row = conn.execute(
        "SELECT qid, label, description, sitelinks, source_url, license, license_url, fetched_at FROM games WHERE qid=?",
        (qid,),
    ).fetchone()
    if not row:
        return None
    aliases = [
        _safe_text(value, limit=80)
        for (value,) in conn.execute("SELECT alias FROM game_aliases WHERE qid=? ORDER BY alias", (qid,))
        if _safe_text(value, limit=80)
    ][:12]
    facts: dict[str, list[str] | str] = {}
    for prompt_field, value_label, value_text in conn.execute(
        "SELECT prompt_field, value_label, value_text FROM game_facts WHERE qid=? ORDER BY prompt_field, value_label, value_text",
        (qid,),
    ):
        value = _display_value(value_label, value_text)
        if not value:
            continue
        if prompt_field in SINGLE_VALUE_FIELDS:
            facts.setdefault(prompt_field, value)
        else:
            bucket = facts.setdefault(prompt_field, [])
            if isinstance(bucket, list) and value not in bucket and len(bucket) < 12:
                bucket.append(value)
    return {
        "schema_version": GAME_CONTEXT_SCHEMA_VERSION,
        "status": status,
        "provider": "wikidata",
        "qid": row[0],
        "label": row[1],
        "description": row[2] or "",
        "aliases": aliases,
        "sitelinks": int(row[3] or 0),
        "source_url": row[4],
        "license": row[5] or WIKIDATA_LICENSE,
        "license_url": row[6] or WIKIDATA_LICENSE_URL,
        "fetched_at": row[7],
        "facts": facts,
    }


def _query_wikidata_game(title: str, *, timeout: int) -> list[dict]:
    properties = " ".join(f"wd:{pid}" for pid in CURATED_PROPERTIES)
    escaped_title = title.replace("\\", "\\\\").replace('"', '\\"').lower()
    query = f"""
SELECT ?game ?gameLabel ?gameDescription ?sitelinks ?alias ?property ?propertyLabel ?value ?valueLabel ?valueType WHERE {{
  ?game wdt:P31/wdt:P279* wd:Q7889 .
  ?game rdfs:label ?gameLabel .
  FILTER(LANG(?gameLabel) = "en")
  FILTER(LCASE(STR(?gameLabel)) = "{escaped_title}")
  OPTIONAL {{ ?game schema:description ?gameDescription FILTER(LANG(?gameDescription) = "en") . }}
  OPTIONAL {{ ?game wikibase:sitelinks ?sitelinks . }}
  OPTIONAL {{ ?game skos:altLabel ?alias FILTER(LANG(?alias) = "en") . }}
  OPTIONAL {{
    VALUES ?property {{ {properties} }}
    ?property wikibase:directClaim ?directProperty .
    ?game ?directProperty ?value .
    BIND(DATATYPE(?value) AS ?valueType)
    OPTIONAL {{ ?value rdfs:label ?valueLabel FILTER(LANG(?valueLabel) = "en") . }}
  }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en" .
    ?property rdfs:label ?propertyLabel .
  }}
}}
ORDER BY DESC(?sitelinks) ?propertyLabel ?valueLabel ?value
"""
    return _sparql_rows(query, timeout=timeout)


def _query_wikidata_game_by_qid(qid: str, *, timeout: int) -> list[dict]:
    properties = " ".join(f"wd:{pid}" for pid in CURATED_PROPERTIES)
    query = f"""
SELECT ?game ?gameLabel ?gameDescription ?sitelinks ?alias ?property ?propertyLabel ?value ?valueLabel ?valueType WHERE {{
  VALUES ?game {{ wd:{qid} }}
  ?game wdt:P31/wdt:P279* wd:Q7889 .
  ?game rdfs:label ?gameLabel .
  FILTER(LANG(?gameLabel) = "en")
  OPTIONAL {{ ?game schema:description ?gameDescription FILTER(LANG(?gameDescription) = "en") . }}
  OPTIONAL {{ ?game wikibase:sitelinks ?sitelinks . }}
  OPTIONAL {{ ?game skos:altLabel ?alias FILTER(LANG(?alias) = "en") . }}
  OPTIONAL {{
    VALUES ?property {{ {properties} }}
    ?property wikibase:directClaim ?directProperty .
    ?game ?directProperty ?value .
    BIND(DATATYPE(?value) AS ?valueType)
    OPTIONAL {{ ?value rdfs:label ?valueLabel FILTER(LANG(?valueLabel) = "en") . }}
  }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en" .
    ?property rdfs:label ?propertyLabel .
  }}
}}
ORDER BY ?propertyLabel ?valueLabel ?value
"""
    return _sparql_rows(query, timeout=timeout)


def _sparql_rows(query: str, *, timeout: int) -> list[dict]:
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    request = urllib.request.Request(
        WIKIDATA_SPARQL_URL,
        data=body,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": WIKIDATA_USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=max(1, int(timeout or QUERY_TIMEOUT_SECONDS))) as response:
        data = json.loads(response.read().decode("utf-8"))
    rows = data.get("results", {}).get("bindings", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _binding_value(row: dict, key: str) -> str:
    item = row.get(key) if isinstance(row.get(key), dict) else {}
    return str(item.get("value") or "")


def _store_wikidata_rows(conn: sqlite3.Connection, rows: list[dict]) -> str:
    def value(row: dict, key: str) -> str:
        item = row.get(key) if isinstance(row.get(key), dict) else {}
        return str(item.get("value") or "")

    first = rows[0]
    game_uri = value(first, "game")
    qid = _qid_from_uri(game_uri)
    if not qid:
        return ""
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    label = _safe_text(value(first, "gameLabel"), limit=120) or qid
    description = _safe_text(value(first, "gameDescription"), limit=220)
    sitelinks = _safe_int(value(first, "sitelinks"))
    source_url = f"{WIKIDATA_SOURCE_BASE_URL}{qid}"
    conn.execute(
        """
        INSERT INTO games(qid, label, description, sitelinks, source_url, license, license_url, fetched_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(qid) DO UPDATE SET
            label=excluded.label,
            description=excluded.description,
            sitelinks=excluded.sitelinks,
            source_url=excluded.source_url,
            license=excluded.license,
            license_url=excluded.license_url,
            fetched_at=excluded.fetched_at
        """,
        (qid, label, description, sitelinks, source_url, WIKIDATA_LICENSE, WIKIDATA_LICENSE_URL, fetched_at),
    )
    conn.execute("DELETE FROM game_aliases WHERE qid=?", (qid,))
    conn.execute("DELETE FROM game_facts WHERE qid=?", (qid,))
    for row in rows:
        alias = _safe_text(value(row, "alias"), limit=80)
        if alias:
            conn.execute("INSERT OR IGNORE INTO game_aliases(qid, alias) VALUES(?,?)", (qid, alias))
        property_id = _qid_from_uri(value(row, "property"))
        if property_id not in CURATED_PROPERTIES:
            continue
        prompt_field, fallback_label = CURATED_PROPERTIES[property_id]
        raw_value = value(row, "value")
        if not raw_value:
            continue
        value_id = _qid_from_uri(raw_value) if raw_value.startswith("http://www.wikidata.org/entity/") else ""
        value_kind = "entity" if value_id else ("url" if raw_value.startswith(("http://", "https://")) else "literal")
        conn.execute(
            """
            INSERT OR IGNORE INTO game_facts(
                qid, property_id, property_label, prompt_field, value_kind, value_id,
                value_label, value_text, datatype, source_url, fetched_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                qid,
                property_id,
                _safe_text(value(row, "propertyLabel"), limit=80) or fallback_label,
                prompt_field,
                value_kind,
                value_id,
                _safe_text(value(row, "valueLabel"), limit=120),
                _safe_text(raw_value, limit=240),
                _safe_text(value(row, "valueType"), limit=120),
                source_url,
                fetched_at,
            ),
        )
    conn.commit()
    return qid


def _empty_context(status: str, *, game_title: str = "", error: str = "") -> dict:
    result = {
        "schema_version": GAME_CONTEXT_SCHEMA_VERSION,
        "status": status,
        "provider": "wikidata",
        "game_title": _safe_text(game_title, limit=120),
        "available": False,
    }
    if error:
        result["error"] = _safe_text(error, limit=180)
    return result


def _display_value(label: str | None, text: str | None) -> str:
    label = _safe_text(label, limit=120)
    if label and not re.fullmatch(r"Q\d+", label):
        return label
    text = _safe_text(text, limit=160)
    if not text or text.startswith("http://www.wikidata.org/entity/"):
        return ""
    return text


def _safe_list(values, *, limit: int) -> list[str]:
    result = []
    for value in values or []:
        cleaned = _safe_text(value, limit=120)
        if cleaned and cleaned not in result and not re.fullmatch(r"Q\d+", cleaned):
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _safe_text(value, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text[: max(0, int(limit))]


def _safe_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_source_url(value) -> str:
    text = _safe_text(value, limit=180)
    return text if text.startswith(WIKIDATA_SOURCE_BASE_URL) else ""


def _lookup_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_game_title(value).lower())


def _qid_from_uri(value: str) -> str:
    if not value:
        return ""
    tail = str(value).rsplit("/", 1)[-1]
    return tail if re.fullmatch(r"[QP]\d+", tail) else ""


def _safe_qid(value) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"Q\d+", text) else ""


def _release_year(value) -> int | None:
    match = re.search(r"\b(\d{4})\b", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1950 <= year <= 2100 else None


GAME_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

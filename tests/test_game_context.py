import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_context import (  # noqa: E402
    compact_game_context_for_prompt,
    get_game_context,
    get_game_context_by_qid,
    normalize_game_title,
    query_recent_wikidata_games,
    seed_recent_game_context,
)


def _binding(value, kind="literal"):
    item = {"type": kind, "value": value}
    if kind == "literal":
        item["xml:lang"] = "en"
    return item


def _row(property_id, property_label, value, value_label=None):
    row = {
        "game": _binding("http://www.wikidata.org/entity/Q575505", "uri"),
        "gameLabel": _binding("Alan Wake"),
        "gameDescription": _binding("2010 video game"),
        "sitelinks": {"type": "literal", "value": "35"},
        "property": _binding(f"http://www.wikidata.org/entity/{property_id}", "uri"),
        "propertyLabel": _binding(property_label),
        "value": _binding(value, "uri" if value.startswith("http") else "literal"),
        "valueType": {"type": "literal", "value": "http://www.w3.org/2001/XMLSchema#string"},
    }
    if value_label is not None:
        row["valueLabel"] = _binding(value_label)
    return row


class _FakeResponse:
    status = 200

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"results": {"bindings": self.rows}}).encode("utf-8")


class GameContextTests(unittest.TestCase):
    def test_normalize_game_title_removes_capture_suffixes(self):
        self.assertEqual(normalize_game_title("Alan WakeVertical"), "Alan Wake")
        self.assertEqual(normalize_game_title("Alan_Wake-2026-06-09-clips"), "Alan Wake")

    def test_fetches_curated_wikidata_facts_into_sqlite_cache(self):
        rows = [
            _row("P577", "publication date", "2010-05-14T00:00:00Z"),
            _row("P136", "genre", "http://www.wikidata.org/entity/Q343568", "action-adventure game"),
            _row("P179", "part of the series", "http://www.wikidata.org/entity/Q108370518", "Alan Wake"),
            _row("P1434", "takes place in fictional universe", "http://www.wikidata.org/entity/Q119846985", "Remedy Connected Universe"),
            _row("P674", "characters", "http://www.wikidata.org/entity/Q21010056", "Alan Wake"),
            _row("P178", "developer", "http://www.wikidata.org/entity/Q830947", "Remedy Entertainment"),
            _row("P4769", "GameFAQs game ID", "11532"),
        ]
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = req.data.decode("utf-8")
            captured["timeout"] = timeout
            return _FakeResponse(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "game_context.sqlite3"
            with patch("game_context.urllib.request.urlopen", side_effect=fake_urlopen):
                context = get_game_context("Alan Wake", db_path=db_path, allow_network=True)

            prompt = compact_game_context_for_prompt(context)
            self.assertEqual(context["status"], "ok")
            self.assertEqual(context["qid"], "Q575505")
            self.assertEqual(prompt["label"], "Alan Wake")
            self.assertEqual(prompt["release_year"], 2010)
            self.assertIn("action-adventure game", prompt["genres"])
            self.assertIn("Remedy Entertainment", prompt["developers"])
            self.assertIn("Remedy Connected Universe", prompt["fictional_universes"])
            self.assertNotIn("GameFAQs", json.dumps(prompt))
            self.assertIn("P1434", captured["body"])
            self.assertNotIn("P4769", captured["body"])

            with patch("game_context.urllib.request.urlopen") as urlopen:
                cached = get_game_context("Alan Wake", db_path=db_path, allow_network=False)
            urlopen.assert_not_called()
            self.assertEqual(cached["status"], "cache_hit")

    def test_fetches_context_by_qid(self):
        rows = [
            _row("P577", "publication date", "2010-05-14T00:00:00Z"),
            _row("P136", "genre", "http://www.wikidata.org/entity/Q343568", "action-adventure game"),
        ]
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = req.data.decode("utf-8")
            return _FakeResponse(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("game_context.urllib.request.urlopen", side_effect=fake_urlopen):
                context = get_game_context_by_qid(
                    "Q575505",
                    db_path=Path(temp_dir) / "game_context.sqlite3",
                    allow_network=True,
                )

        self.assertEqual(context["status"], "ok")
        self.assertEqual(context["qid"], "Q575505")
        self.assertIn("wd%3AQ575505", captured["body"])

    def test_recent_game_query_returns_newest_seed_rows(self):
        rows = [
            {
                "game": _binding("http://www.wikidata.org/entity/Q1", "uri"),
                "gameLabel": _binding("Newest Game"),
                "gameDescription": _binding("2026 video game"),
                "release": _binding("2026-06-01T00:00:00Z"),
                "sitelinks": {"type": "literal", "value": "5"},
            },
            {
                "game": _binding("http://www.wikidata.org/entity/Q1", "uri"),
                "gameLabel": _binding("Newest Game"),
                "gameDescription": _binding("2026 video game"),
                "release": _binding("2026-06-01T00:00:00Z"),
                "sitelinks": {"type": "literal", "value": "5"},
            }
        ]
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = req.data.decode("utf-8")
            return _FakeResponse(rows)

        with patch("game_context.urllib.request.urlopen", side_effect=fake_urlopen):
            games = query_recent_wikidata_games(limit=20, since_year=2025, offset=20)

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["qid"], "Q1")
        self.assertEqual(games[0]["label"], "Newest Game")
        self.assertIn("ORDER+BY+DESC", captured["body"])
        self.assertIn("LIMIT+20", captured["body"])
        self.assertIn("OFFSET+20", captured["body"])

    def test_recent_seed_skips_existing_qids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "game_context.sqlite3"
            existing = {
                "status": "cache_hit",
                "qid": "Q1",
                "label": "Already Cached",
                "facts": {},
            }
            with patch("game_context.query_recent_wikidata_games", return_value=[
                {"qid": "Q1", "label": "Already Cached", "release_date": "2026-01-01T00:00:00Z"},
            ]), \
                    patch("game_context.get_game_context_by_qid", return_value=existing) as by_qid:
                result = seed_recent_game_context(limit=1, db_path=db_path)

        by_qid.assert_called_once()
        self.assertEqual(result["status"], "all_existing")
        self.assertEqual(result["seeded_count"], 0)
        self.assertEqual(result["skipped_existing_count"], 1)

    def test_network_failure_returns_soft_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("game_context.urllib.request.urlopen", side_effect=TimeoutError("slow")):
                context = get_game_context(
                    "Alan Wake",
                    db_path=Path(temp_dir) / "game_context.sqlite3",
                    allow_network=True,
                )

        self.assertEqual(context["status"], "query_error")
        self.assertFalse(compact_game_context_for_prompt(context)["available"])

    def test_cache_connect_failure_returns_soft_error(self):
        with patch("game_context._connect", side_effect=OSError("cache locked")):
            context = get_game_context("Alan Wake", allow_network=False)

        self.assertEqual(context["status"], "query_error")
        self.assertIn("cache locked", context["error"])

        with patch("game_context._connect", side_effect=OSError("cache locked")):
            by_qid = get_game_context_by_qid("Q575505", allow_network=False)

        self.assertEqual(by_qid["status"], "query_error")
        self.assertIn("cache locked", by_qid["error"])


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_identity import (  # noqa: E402
    collect_game_title_candidates,
    resolve_game_identity,
    score_identity_match,
)


def _context(label="Alan Wake", qid="Q575505"):
    return {
        "schema_version": 1,
        "status": "ok",
        "provider": "wikidata",
        "qid": qid,
        "label": label,
        "description": "2010 video game",
        "aliases": ["Alan Wake Remastered"],
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "license": "CC0-1.0",
        "facts": {
            "first_release_date": "2010-05-14T00:00:00Z",
            "genres": ["survival horror"],
            "developers": ["Remedy Entertainment"],
        },
    }


class GameIdentityTests(unittest.TestCase):
    def test_collects_title_candidates_from_filename_and_explicit_context(self):
        candidates = collect_game_title_candidates(
            source_path=r"D:\Recording Video Files\Alan WakeVertical\2026-06-09 23-27-05-vertical.mkv",
            explicit_title="Alan Wake Remastered - Part 4 - Getting Chased",
            creator_context="Game: Alan Wake Remastered nursing home section",
        )

        titles = [item["title"] for item in candidates]
        self.assertIn("Alan Wake Remastered", titles)
        self.assertNotIn("23 27 05", titles)
        self.assertIn("Alan Wake Remastered nursing home section", titles)
        self.assertTrue(any("source_folder" in item["sources"] for item in candidates))

    def test_collects_natural_creator_note_game_run_phrase(self):
        candidates = collect_game_title_candidates(
            creator_context="blind Alan Wake run in the nursing home chapter",
        )

        titles = [item["title"] for item in candidates]
        self.assertIn("Alan Wake", titles)

    def test_scores_exact_alias_match_high(self):
        score = score_identity_match("Alan Wake Remastered", _context(), candidate_weight=1.0)

        self.assertGreaterEqual(score["confidence"], 0.9)
        self.assertIn("exact_label_or_alias", score["reasons"])

    def test_resolve_uses_local_cache_before_network(self):
        with patch("game_identity.get_game_context", return_value={**_context(), "status": "cache_hit"}) as cached, \
                patch("game_identity.search_wikidata_games") as search:
            result = resolve_game_identity(
                source_path=r"D:\Recordings\Alan WakeVertical\clip.mkv",
                allow_network=True,
            )

        cached.assert_called()
        search.assert_not_called()
        self.assertEqual(result["status"], "cache_hit")
        self.assertEqual(result["qid"], "Q575505")
        self.assertGreater(result["confidence"], 0.4)
        self.assertEqual(result["game_context"]["label"], "Alan Wake")

    def test_resolve_search_validates_qid_as_game_context(self):
        with patch("game_identity.get_game_context", return_value={"status": "cache_miss"}), \
                patch("game_identity.search_wikidata_games", return_value=[
                    {"qid": "Q575505", "label": "Alan Wake", "description": "video game"},
                ]), \
                patch("game_identity.get_game_context_by_qid", return_value=_context()) as by_qid:
            result = resolve_game_identity(
                explicit_title="Alan Wake",
                allow_network=True,
            )

        by_qid.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["matched_via"], "wikidata_search")
        self.assertEqual(result["title"], "Alan Wake")
        self.assertTrue(result["game_context_prompt"]["available"])


if __name__ == "__main__":
    unittest.main()

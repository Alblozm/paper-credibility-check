import importlib.util
import json
import unittest
from pathlib import Path

from credibility_r2.core import validate_case

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("credibility_adapter", ROOT / "scripts/build_credibility_case_from_parsed.py")
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class CredibilityIntegrationTests(unittest.TestCase):
    def test_first_round_adapter_and_real_evidence_chain(self):
        case = ADAPTER.build(
            ROOT / "data/parsed/paper_001/v3",
            ROOT / "data/credibility_cases/annotations/paper_001_one_claim.json",
        )
        validate_case(case)
        self.assertFalse(case["synthetic"])
        self.assertFalse(case["scope_complete"])
        self.assertEqual(case["paper_version"]["paper_version_id"], "paper_001_v3")
        self.assertEqual(case["claim_checks"][0]["verdict"], "supported")

    def test_committed_case_matches_deterministic_adapter(self):
        expected = ADAPTER.build(
            ROOT / "data/parsed/paper_001/v3",
            ROOT / "data/credibility_cases/annotations/paper_001_one_claim.json",
        )
        actual = json.loads((ROOT / "data/credibility_cases/reviewed/paper_001_v3.json").read_text())
        # reviewed_at comes from the annotation, so this is fully deterministic.
        self.assertEqual(actual, expected)

    def test_sql_schema_binds_to_first_round_tables(self):
        sql = (ROOT / "sql/030_credibility_schema.sql").read_text()
        self.assertIn("REFERENCES public.paper_versions", sql)
        self.assertIn("REFERENCES public.paper_text_chunks", sql)
        self.assertIn("REFERENCES public.paper_elements", sql)
        self.assertNotIn("credibility_r2_draft", sql)


if __name__ == "__main__":
    unittest.main()

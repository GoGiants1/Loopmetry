from __future__ import annotations

import os
import unittest

from loopmetry.llm_provider import ProviderError, probe, _load_result_schema, _strip_numeric_constraints, validate_llm_evaluation_result, check_evidence_ids


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("LOOPMETRY_TEST_KEY", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["LOOPMETRY_TEST_KEY"] = self._saved

    def test_probe_reports_missing_key(self) -> None:
        result = probe(api_key_env="LOOPMETRY_TEST_KEY")
        self.assertEqual(
            result,
            {"provider": "anthropic", "api_key_env": "LOOPMETRY_TEST_KEY", "available": False},
        )

    def test_probe_reports_present_key(self) -> None:
        os.environ["LOOPMETRY_TEST_KEY"] = "sk-fake"
        result = probe(api_key_env="LOOPMETRY_TEST_KEY")
        self.assertTrue(result["available"])


class SchemaHandlingTests(unittest.TestCase):
    def test_strip_numeric_constraints_removes_range_keys(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "rating": {"type": ["integer", "null"], "minimum": 0, "maximum": 4},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "nested": {
                    "type": "array",
                    "items": {"type": "integer", "multipleOf": 2, "minimum": 0},
                },
            },
        }
        stripped = _strip_numeric_constraints(schema)
        self.assertEqual(stripped["properties"]["rating"], {"type": ["integer", "null"]})
        self.assertEqual(stripped["properties"]["confidence"], {"type": "number"})
        self.assertEqual(stripped["properties"]["nested"]["items"], {"type": "integer"})

    def test_load_result_schema_reads_the_real_schema_file(self) -> None:
        schema = _load_result_schema()
        self.assertEqual(schema["title"], "Loopmetry LLM Evaluation Result v1")
        self.assertIn("dimensions", schema["properties"])


def _valid_result() -> dict:
    return {
        "schema_version": "0.1",
        "rubric_id": "project-work-v1",
        "scope": "project",
        "verdict": "partial",
        "summary": "Goal mostly met with one unresolved verification gap.",
        "dimensions": [
            {
                "key": "goal_fidelity",
                "label": "Goal fidelity",
                "assessability": "assessable",
                "rating": 3,
                "confidence": 0.8,
                "rationale": "Implemented change matches the recorded requirement.",
                "evidence_ids": ["evt-1"],
                "counterevidence_ids": [],
                "missing_evidence": [],
            }
        ],
        "risks": [
            {
                "severity": "low",
                "description": "No material risk identified.",
                "evidence_ids": ["evt-1"],
            }
        ],
        "missing_evidence": [],
        "needs_human_review": False,
    }


class ResultValidationTests(unittest.TestCase):
    def test_accepts_a_valid_result(self) -> None:
        result = validate_llm_evaluation_result(_valid_result())
        self.assertEqual(result["verdict"], "partial")

    def test_rejects_missing_required_field(self) -> None:
        raw = _valid_result()
        del raw["verdict"]
        with self.assertRaises(ProviderError):
            validate_llm_evaluation_result(raw)

    def test_rejects_bad_enum_value(self) -> None:
        raw = _valid_result()
        raw["verdict"] = "maybe"
        with self.assertRaises(ProviderError):
            validate_llm_evaluation_result(raw)

    def test_rejects_out_of_range_rating(self) -> None:
        raw = _valid_result()
        raw["dimensions"][0]["rating"] = 9
        with self.assertRaises(ProviderError):
            validate_llm_evaluation_result(raw)

    def test_accepts_null_rating(self) -> None:
        raw = _valid_result()
        raw["dimensions"][0]["rating"] = None
        validate_llm_evaluation_result(raw)  # must not raise

    def test_rejects_unexpected_top_level_key(self) -> None:
        raw = _valid_result()
        raw["unexpected_field"] = "surprise"
        with self.assertRaises(ProviderError):
            validate_llm_evaluation_result(raw)

    def test_rejects_dimension_missing_rating_key_entirely(self) -> None:
        raw = _valid_result()
        del raw["dimensions"][0]["rating"]
        with self.assertRaises(ProviderError):
            validate_llm_evaluation_result(raw)


class EvidenceIdCheckTests(unittest.TestCase):
    def _bundle(self) -> dict:
        return {"events": [{"event_id": "evt-1"}, {"event_id": "evt-2"}]}

    def test_accepts_known_evidence_ids(self) -> None:
        result = _valid_result()  # cites "evt-1" only
        check_evidence_ids(result, self._bundle())  # must not raise

    def test_rejects_unknown_evidence_id(self) -> None:
        result = _valid_result()
        result["risks"][0]["evidence_ids"] = ["evt-999"]
        with self.assertRaises(ProviderError):
            check_evidence_ids(result, self._bundle())


if __name__ == "__main__":
    unittest.main()

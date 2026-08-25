from __future__ import annotations

import json as _json
import os
import unittest
from unittest import mock

from loopmetry.llm_provider import ProviderError, probe, _load_result_schema, _strip_numeric_constraints, validate_llm_evaluation_result, check_evidence_ids
from loopmetry.llm_provider import evaluate


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
                "summary": {
                    "type": "string",
                    "description": "A short summary.",
                    "minLength": 1,
                    "maxLength": 2000,
                },
                "risks": {
                    "type": "array",
                    "description": "Identified risks.",
                    "minItems": 0,
                    "maxItems": 20,
                    "items": {"type": "string"},
                },
            },
            "$defs": {
                "evidenceIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                }
            },
        }
        stripped = _strip_numeric_constraints(schema)
        self.assertEqual(stripped["properties"]["rating"], {"type": ["integer", "null"]})
        self.assertEqual(stripped["properties"]["confidence"], {"type": "number"})
        self.assertEqual(stripped["properties"]["nested"]["items"], {"type": "integer"})
        self.assertEqual(
            stripped["properties"]["summary"],
            {"type": "string", "description": "A short summary."},
        )
        self.assertEqual(
            stripped["properties"]["risks"],
            {"type": "array", "description": "Identified risks.", "items": {"type": "string"}},
        )
        self.assertEqual(
            stripped["$defs"]["evidenceIds"],
            {"type": "array", "items": {"type": "string"}},
        )

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


class EvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["LOOPMETRY_TEST_KEY"] = "sk-fake"

    def tearDown(self) -> None:
        os.environ.pop("LOOPMETRY_TEST_KEY", None)

    def _bundle(self) -> dict:
        return {
            "bundle_id": "sha256:" + "a" * 64,
            "events": [{"event_id": "evt-1"}],
        }

    def test_missing_api_key_raises_before_any_network_call(self) -> None:
        os.environ.pop("LOOPMETRY_TEST_KEY", None)
        with mock.patch("urllib.request.urlopen") as mocked_urlopen:
            with self.assertRaises(ProviderError):
                evaluate(
                    self._bundle(),
                    "rubric text",
                    api_key_env="LOOPMETRY_TEST_KEY",
                )
            mocked_urlopen.assert_not_called()

    def test_happy_path_returns_validated_result_and_usage(self) -> None:
        api_response = {
            "model": "claude-opus-5",
            "usage": {"input_tokens": 111, "output_tokens": 22},
            "content": [{"type": "text", "text": _json.dumps(_valid_result())}],
        }
        fake_response = mock.MagicMock()
        fake_response.read.return_value = _json.dumps(api_response).encode("utf-8")
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            outcome = evaluate(
                self._bundle(),
                "rubric text",
                api_key_env="LOOPMETRY_TEST_KEY",
            )

        self.assertEqual(outcome["result"]["verdict"], "partial")
        self.assertEqual(outcome["usage"], {"input_tokens": 111, "output_tokens": 22})
        self.assertEqual(outcome["model"], "claude-opus-5")

    def test_happy_path_with_leading_thinking_block_still_succeeds(self) -> None:
        api_response = {
            "model": "claude-opus-5",
            "usage": {"input_tokens": 111, "output_tokens": 22},
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": _json.dumps(_valid_result())},
            ],
        }
        fake_response = mock.MagicMock()
        fake_response.read.return_value = _json.dumps(api_response).encode("utf-8")
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            outcome = evaluate(
                self._bundle(),
                "rubric text",
                api_key_env="LOOPMETRY_TEST_KEY",
            )

        self.assertEqual(outcome["result"]["verdict"], "partial")
        self.assertEqual(outcome["usage"], {"input_tokens": 111, "output_tokens": 22})
        self.assertEqual(outcome["model"], "claude-opus-5")

    def test_rubric_id_mismatch_raises(self) -> None:
        mismatched = _valid_result()
        mismatched["rubric_id"] = "some-other-rubric"
        api_response = {
            "model": "claude-opus-5",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": _json.dumps(mismatched)}],
        }
        fake_response = mock.MagicMock()
        fake_response.read.return_value = _json.dumps(api_response).encode("utf-8")
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            with self.assertRaises(ProviderError):
                evaluate(
                    self._bundle(),
                    "rubric text",
                    api_key_env="LOOPMETRY_TEST_KEY",
                    rubric_id="project-work-v1",
                )


if __name__ == "__main__":
    unittest.main()

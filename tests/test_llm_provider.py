from __future__ import annotations

import os
import unittest

from loopmetry.llm_provider import ProviderError, probe, _load_result_schema, _strip_numeric_constraints


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


if __name__ == "__main__":
    unittest.main()

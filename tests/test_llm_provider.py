from __future__ import annotations

import os
import unittest

from loopmetry.llm_provider import ProviderError, probe


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


if __name__ == "__main__":
    unittest.main()

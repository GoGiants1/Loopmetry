import unittest

from loopmetry.hook_integration import (
    INTEGRATION_HOOK_EVENTS,
    build_hook_args,
    merge_settings,
    remove_settings,
)


def _entry(project_id: str | None = None) -> dict:
    return {"type": "command", "command": "loopmetry", "args": build_hook_args(project_id)}


class MergeSettingsTests(unittest.TestCase):
    def test_merge_into_empty_dict_adds_all_events(self) -> None:
        merged, changed = merge_settings({}, None)
        self.assertTrue(changed)
        self.assertEqual(set(merged["hooks"].keys()), set(INTEGRATION_HOOK_EVENTS))
        for event in INTEGRATION_HOOK_EVENTS:
            self.assertEqual(merged["hooks"][event], [{"hooks": [_entry(None)]}])

    def test_merge_preserves_unrelated_settings_and_hooks(self) -> None:
        existing = {
            "otherSetting": True,
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool"}]}],
            },
        }
        merged, changed = merge_settings(existing, None)
        self.assertTrue(changed)
        self.assertTrue(merged["otherSetting"])
        self.assertIn(
            {"hooks": [{"type": "command", "command": "other-tool"}]},
            merged["hooks"]["UserPromptSubmit"],
        )
        self.assertIn({"hooks": [_entry(None)]}, merged["hooks"]["UserPromptSubmit"])

    def test_merge_is_idempotent(self) -> None:
        merged_once, _ = merge_settings({}, None)
        merged_twice, changed = merge_settings(merged_once, None)
        self.assertFalse(changed)
        self.assertEqual(merged_once, merged_twice)

    def test_changing_project_id_replaces_rather_than_duplicates(self) -> None:
        merged, _ = merge_settings({}, None)
        merged_with_id, changed = merge_settings(merged, "my-project")
        self.assertTrue(changed)
        for event in INTEGRATION_HOOK_EVENTS:
            self.assertEqual(merged_with_id["hooks"][event], [{"hooks": [_entry("my-project")]}])

    def test_project_id_with_spaces_and_shell_metacharacters_is_preserved_as_one_arg(self) -> None:
        merged, changed = merge_settings({}, "course 2026; rm -rf /")
        self.assertTrue(changed)
        args = merged["hooks"]["UserPromptSubmit"][0]["hooks"][0]["args"]
        self.assertEqual(args[-1], "course 2026; rm -rf /")

    def test_duplicate_managed_blocks_collapse_to_one(self) -> None:
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [_entry(None)]},
                    {"hooks": [_entry(None)]},
                ]
            }
        }
        merged, changed = merge_settings(existing, None)
        self.assertTrue(changed)
        self.assertEqual(merged["hooks"]["UserPromptSubmit"], [{"hooks": [_entry(None)]}])

    def test_matcher_scoped_block_is_not_treated_as_full_integration(self) -> None:
        existing = {
            "hooks": {
                "PostToolUse": [{"matcher": "Bash", "hooks": [_entry(None)]}],
            }
        }
        merged, changed = merge_settings(existing, None)
        self.assertTrue(changed)
        self.assertIn(
            {"matcher": "Bash", "hooks": [_entry(None)]}, merged["hooks"]["PostToolUse"]
        )
        self.assertIn({"hooks": [_entry(None)]}, merged["hooks"]["PostToolUse"])

    def test_handler_with_if_condition_is_not_treated_as_full_integration(self) -> None:
        conditional_entry = {**_entry(None), "if": "Bash(git *)"}
        existing = {"hooks": {"PostToolUse": [{"hooks": [conditional_entry]}]}}
        merged, changed = merge_settings(existing, None)
        self.assertTrue(changed)
        self.assertIn({"hooks": [conditional_entry]}, merged["hooks"]["PostToolUse"])
        self.assertIn({"hooks": [_entry(None)]}, merged["hooks"]["PostToolUse"])

    def test_does_not_mutate_input(self) -> None:
        existing = {"hooks": {}}
        merge_settings(existing, None)
        self.assertEqual(existing, {"hooks": {}})

    def test_hooks_not_an_object_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_settings({"hooks": []}, None)

    def test_event_value_not_an_array_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_settings({"hooks": {"PostToolUse": "invalid"}}, None)


class RemoveSettingsTests(unittest.TestCase):
    def test_remove_on_dict_with_nothing_managed_is_noop(self) -> None:
        existing = {"otherSetting": True}
        merged, changed = remove_settings(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)

    def test_remove_strips_only_managed_blocks(self) -> None:
        merged_after_apply, _ = merge_settings(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "other-tool"}]}
                    ]
                }
            },
            None,
        )
        merged, changed = remove_settings(merged_after_apply)
        self.assertTrue(changed)
        self.assertEqual(
            merged["hooks"]["UserPromptSubmit"],
            [{"hooks": [{"type": "command", "command": "other-tool"}]}],
        )
        for event in INTEGRATION_HOOK_EVENTS:
            if event != "UserPromptSubmit":
                self.assertNotIn(event, merged["hooks"])

    def test_remove_prunes_empty_hooks_key(self) -> None:
        merged_after_apply, _ = merge_settings({}, None)
        merged, changed = remove_settings(merged_after_apply)
        self.assertTrue(changed)
        self.assertNotIn("hooks", merged)

    def test_remove_never_touches_events_outside_installer_scope(self) -> None:
        existing = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "loopmetry",
                                "args": [
                                    "capture-hook",
                                    "--source",
                                    "claude-code",
                                    "--output",
                                    "custom.jsonl",
                                ],
                            }
                        ]
                    }
                ]
            }
        }
        merged, changed = remove_settings(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)

    def test_remove_does_not_strip_matcher_scoped_or_conditional_handlers(self) -> None:
        existing = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Bash", "hooks": [_entry(None)]},
                    {"hooks": [{**_entry(None), "if": "Bash(git *)"}]},
                ]
            }
        }
        merged, changed = remove_settings(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)

    def test_remove_hooks_not_an_object_raises(self) -> None:
        with self.assertRaises(ValueError):
            remove_settings({"hooks": []})


if __name__ == "__main__":
    unittest.main()

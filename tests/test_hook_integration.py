import unittest

from loopmetry.hook_integration import (
    INTEGRATION_HOOK_EVENTS,
    build_hook_command,
    merge_settings,
    remove_settings,
)


class MergeSettingsTests(unittest.TestCase):
    def test_merge_into_empty_dict_adds_all_events(self) -> None:
        merged, changed = merge_settings({}, None)
        self.assertTrue(changed)
        self.assertEqual(set(merged["hooks"].keys()), set(INTEGRATION_HOOK_EVENTS))
        for event in INTEGRATION_HOOK_EVENTS:
            self.assertEqual(
                merged["hooks"][event],
                [{"hooks": [{"type": "command", "command": build_hook_command(None)}]}],
            )

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
        self.assertIn(
            {"hooks": [{"type": "command", "command": build_hook_command(None)}]},
            merged["hooks"]["UserPromptSubmit"],
        )

    def test_merge_is_idempotent(self) -> None:
        merged_once, _ = merge_settings({}, None)
        merged_twice, changed = merge_settings(merged_once, None)
        self.assertFalse(changed)
        self.assertEqual(merged_once, merged_twice)

    def test_project_id_changes_command_and_is_a_real_change(self) -> None:
        merged, _ = merge_settings({}, None)
        merged_with_id, changed = merge_settings(merged, "my-project")
        self.assertTrue(changed)
        commands = {
            entry["command"]
            for block in merged_with_id["hooks"]["UserPromptSubmit"]
            for entry in block["hooks"]
        }
        self.assertIn(build_hook_command("my-project"), commands)
        self.assertIn(build_hook_command(None), commands)

    def test_does_not_mutate_input(self) -> None:
        existing = {"hooks": {}}
        merge_settings(existing, None)
        self.assertEqual(existing, {"hooks": {}})


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


if __name__ == "__main__":
    unittest.main()

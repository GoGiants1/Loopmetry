import unittest

from loopmetry.hook_integration_codex import build_hook_command, merge_config, remove_config


class BuildHookCommandTests(unittest.TestCase):
    def test_no_project_id(self) -> None:
        self.assertEqual(build_hook_command(None), "loopmetry capture-hook --source codex")

    def test_project_id_with_spaces_is_shell_quoted(self) -> None:
        command = build_hook_command("course 2026; rm -rf /")
        self.assertIn("'course 2026; rm -rf /'", command)
        import shlex

        tokens = shlex.split(command)
        self.assertEqual(tokens[-2:], ["--project-id", "course 2026; rm -rf /"])


class MergeConfigTests(unittest.TestCase):
    def test_merge_into_empty_file_adds_all_events(self) -> None:
        merged, changed = merge_config("", None)
        self.assertTrue(changed)
        for event in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "TaskCompleted", "SessionEnd"):
            self.assertIn(f"[[hooks.{event}]]", merged)
            self.assertIn(f"[[hooks.{event}.hooks]]", merged)
        self.assertIn('command = "loopmetry capture-hook --source codex"', merged)
        self.assertIn("timeout = 3", merged)

    def test_merge_is_idempotent(self) -> None:
        once, _ = merge_config("", None)
        twice, changed = merge_config(once, None)
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_merge_preserves_unrelated_toml_content(self) -> None:
        existing = '[model]\nname = "gpt-5"\n\n[[hooks.UserPromptSubmit]]\n\n[[hooks.UserPromptSubmit.hooks]]\ntype = "command"\ncommand = "other-tool"\ntimeout = 5\n'
        merged, changed = merge_config(existing, None)
        self.assertTrue(changed)
        self.assertIn('name = "gpt-5"', merged)
        self.assertIn('command = "other-tool"', merged)
        self.assertIn('command = "loopmetry capture-hook --source codex"', merged)

    def test_changing_project_id_replaces_rather_than_duplicates(self) -> None:
        once, _ = merge_config("", None)
        merged, changed = merge_config(once, "my-project")
        self.assertTrue(changed)
        self.assertEqual(merged.count("[[hooks.UserPromptSubmit]]"), 1)
        self.assertIn("--project-id my-project", merged)

    def test_invalid_existing_toml_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_config("not [ valid toml", None)

    def test_hooks_value_not_a_table_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_config("hooks = 1\n", None)

    def test_owned_block_with_nested_handler_table_round_trips_as_owned(self) -> None:
        # Regression guard: the block boundary regex must not treat the
        # block's own nested "[[hooks.<Event>.hooks]]" sub-header as the end
        # of the span. If it did, the handler table (type/command/timeout)
        # would be truncated away, merge_config would see the block as
        # unowned, and re-merging an already-merged file would duplicate the
        # block instead of leaving it alone (this test would then fail via
        # test_merge_is_idempotent-style duplication, or here directly: a
        # second merge with the same project_id must report no change).
        once, _ = merge_config("", "proj-a")
        again, changed = merge_config(once, "proj-a")
        self.assertFalse(changed)
        self.assertEqual(once, again)
        self.assertEqual(once.count("[[hooks.UserPromptSubmit]]"), 1)
        self.assertEqual(once.count("[[hooks.UserPromptSubmit.hooks]]"), 1)


class RemoveConfigTests(unittest.TestCase):
    def test_remove_on_file_with_nothing_managed_is_noop(self) -> None:
        existing = '[model]\nname = "gpt-5"\n'
        merged, changed = remove_config(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)

    def test_remove_strips_only_managed_blocks(self) -> None:
        merged_after_apply, _ = merge_config(
            '[[hooks.UserPromptSubmit]]\n\n[[hooks.UserPromptSubmit.hooks]]\ntype = "command"\ncommand = "other-tool"\ntimeout = 5\n',
            None,
        )
        merged, changed = remove_config(merged_after_apply)
        self.assertTrue(changed)
        self.assertIn('command = "other-tool"', merged)
        self.assertNotIn("loopmetry capture-hook", merged)

    def test_remove_never_touches_events_outside_installer_scope(self) -> None:
        existing = '[[hooks.SessionStart]]\n\n[[hooks.SessionStart.hooks]]\ntype = "command"\ncommand = "loopmetry capture-hook --source codex --output custom.jsonl"\ntimeout = 3\n'
        merged, changed = remove_config(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)


if __name__ == "__main__":
    unittest.main()

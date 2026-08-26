import base64
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import rebuild_conversations as fix


def make_conversation_db(path, payloads):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversation_steps "
        "(idx INTEGER NOT NULL, step_type INTEGER NOT NULL, "
        "step_payload BLOB NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO conversation_steps (idx, step_type, step_payload) "
        "VALUES (?, ?, ?)",
        payloads,
    )
    conn.commit()
    conn.close()


def title_payload(title):
    nested = fix.encode_string_field(2, title)
    return fix.encode_length_delimited(19, nested)


def entry_inner_blob(entry):
    sub_message = next(fix.iter_length_delimited_fields(entry, 2))
    info_b64 = next(fix.iter_length_delimited_fields(sub_message, 1))
    return base64.b64decode(info_b64)


class ConversationTitleTests(unittest.TestCase):
    def test_formats_prompt_titles_concisely_and_deterministically(self):
        self.assertEqual(
            fix.format_prompt_title(
                "  see this link\nhttps://content.openx.xyz/api/export?videoId=abc  "
            ),
            "see this link content.openx.xyz",
        )
        self.assertEqual(
            fix.format_prompt_title("is our cli latest? i mean antigravity cli"),
            "is our cli latest?",
        )
        self.assertEqual(
            fix.format_prompt_title("# Agent Broker Request\nTopic: strict route test"),
            "Agent Broker Request Topic: strict route test",
        )
        title = fix.format_prompt_title(
            "go check the switchboard mcp request codex sent a simple "
            "translation request to another agent and report everything"
        )
        self.assertLessEqual(len(title), 60)
        self.assertLessEqual(len(title.rstrip("…").split()), 10)
        self.assertTrue(title.endswith("…"))

    def test_scans_past_invalid_type14_rows_and_keeps_database_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "conversation.db")
            make_conversation_db(
                path,
                [
                    (5, 7, title_payload("ignored")),
                    (10, 14, b"\x9a\x01\xff"),
                    (20, 14, title_payload("  Useful\n\tprompt title  ")),
                ],
            )
            with open(path, "rb") as fixture:
                before_bytes = fixture.read()
            before_mtime = os.stat(path).st_mtime_ns

            title = fix.extract_title_from_conversation_db(path)

            self.assertEqual(title, "Useful prompt title")
            with open(path, "rb") as fixture:
                self.assertEqual(fixture.read(), before_bytes)
            self.assertEqual(os.stat(path).st_mtime_ns, before_mtime)

    def test_malformed_database_or_payload_returns_none_without_creating_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "missing.db")
            self.assertIsNone(fix.extract_title_from_conversation_db(missing))
            self.assertFalse(os.path.exists(missing))

            malformed = os.path.join(temp_dir, "malformed.db")
            make_conversation_db(malformed, [(1, 14, b"\x9a\x01\xff")])
            self.assertIsNone(fix.extract_title_from_conversation_db(malformed))

    def test_title_priority_existing_then_brain_then_conversation_then_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "abc.db")
            make_conversation_db(
                db_path, [(1, 14, title_payload("Conversation title"))]
            )

            with mock.patch.object(fix, "get_title_from_brain", return_value="Brain title"):
                self.assertEqual(
                    fix.resolve_title("abc", {"abc": "Existing title"}, db_path),
                    ("Existing title", "preserved"),
                )
                self.assertEqual(
                    fix.resolve_title("abc", {}, db_path),
                    ("Brain title", "brain"),
                )

            with mock.patch.object(fix, "get_title_from_brain", return_value=None):
                self.assertEqual(
                    fix.resolve_title("abc", {}, db_path),
                    ("Conversation title", "conversation"),
                )
                pb_path = os.path.join(temp_dir, "fallback-id.pb")
                with open(pb_path, "wb") as fixture:
                    fixture.write(b"fixture")
                fallback, source = fix.resolve_title("fallback-id", {}, pb_path)
                self.assertTrue(fallback.startswith("Conversation ("))
                self.assertEqual(source, "fallback")

    def test_migrates_only_the_exact_earlier_v107_raw_prompt_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "abc.db")
            prompt = (
                "go check the switchboard mcp request codex sent a simple "
                "translation request to another agent and report everything"
            )
            make_conversation_db(db_path, [(1, 14, title_payload(prompt))])
            old_v107_title = fix.normalize_prompt_text(prompt)[:80]

            with mock.patch.object(fix, "get_title_from_brain", return_value=None):
                migrated, source = fix.resolve_title(
                    "abc", {"abc": old_v107_title}, db_path
                )
                preserved, preserved_source = fix.resolve_title(
                    "abc", {"abc": old_v107_title + "!"}, db_path
                )

            self.assertNotEqual(migrated, old_v107_title)
            self.assertLessEqual(len(migrated), 60)
            self.assertEqual(source, "conversation")
            self.assertEqual(preserved, old_v107_title + "!")
            self.assertEqual(preserved_source, "preserved")
            self.assertFalse(
                fix._is_generated_fallback_title(
                    "Conversation Planning Review", "abc12345-full-id"
                )
            )
            self.assertTrue(
                fix._is_generated_fallback_title(
                    "Conversation (Aug 16) abc12345", "abc12345-full-id"
                )
            )

    def test_recovers_legacy_pb_title_from_first_explicit_user_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conversation_id = "legacy-id"
            brain_path = os.path.join(temp_dir, conversation_id)
            logs_path = os.path.join(brain_path, ".system_generated", "logs")
            os.makedirs(logs_path)
            transcript = os.path.join(logs_path, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fixture:
                fixture.write("{truncated json\n")
                fixture.write('{"type":"USER_INPUT","content":"partial"\n')
                fixture.write('{"type":"TOOL_OUTPUT","content":"ignore me"}\n')
                fixture.write(
                    '{"event_type":"USER_EXPLICIT","payload":'
                    '{"content":"<USER_REQUEST>\\nFirst legacy request. More text'
                    '\\n</USER_REQUEST>\\n<ADDITIONAL_METADATA>ignore</ADDITIONAL_METADATA>"}}\n'
                )
                fixture.write(
                    '{"type":"USER_INPUT","content":"Second request"}\n'
                )
            pb_path = os.path.join(temp_dir, conversation_id + ".pb")
            with open(pb_path, "wb") as fixture:
                fixture.write(b"opaque")

            with mock.patch.object(fix, "_ALL_BRAIN_DIRS", [temp_dir]), \
                    mock.patch.object(fix, "get_title_from_brain", return_value=None):
                title, source = fix.resolve_title(conversation_id, {}, pb_path)

            self.assertEqual(title, "First legacy request.")
            self.assertEqual(source, "conversation")

    def test_legacy_overview_is_tolerant_and_unreadable_pb_falls_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conversation_id = "overview-id"
            logs_path = os.path.join(
                temp_dir, conversation_id, ".system_generated", "logs"
            )
            os.makedirs(logs_path)
            with open(os.path.join(logs_path, "overview.txt"), "w", encoding="utf-8") as fixture:
                fixture.write(
                    '{"source":"USER_EXPLICIT","type":"USER_INPUT",'
                    '"content":"<USER_REQUEST>\\nset a shut down in 30 minutes'
                    '\\n<ADDITIONAL_METADATA>truncated"}\n'
                )
            pb_path = os.path.join(temp_dir, conversation_id + ".pb")
            with open(pb_path, "wb") as fixture:
                fixture.write(b"encrypted")

            with mock.patch.object(fix, "_ALL_BRAIN_DIRS", [temp_dir]), \
                    mock.patch.object(fix, "get_title_from_brain", return_value=None):
                self.assertEqual(
                    fix.resolve_title(conversation_id, {}, pb_path),
                    ("set a shut down in 30 minutes", "conversation"),
                )
                fallback, source = fix.resolve_title("missing-artifact", {}, pb_path)

            self.assertTrue(fallback.startswith("Conversation ("))
            self.assertEqual(source, "fallback")

    def test_brain_heading_can_follow_leading_blank_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conversation_id = "heading-id"
            brain_path = os.path.join(temp_dir, conversation_id)
            os.makedirs(brain_path)
            with open(os.path.join(brain_path, "plan.md"), "w", encoding="utf-8") as fixture:
                fixture.write("\n\n# Recovered Plan Title\n\nDetails")

            with mock.patch.object(fix, "_ALL_BRAIN_DIRS", [temp_dir]):
                self.assertEqual(
                    fix.get_title_from_brain(conversation_id),
                    "Recovered Plan Title",
                )


class ConversationCatalogTests(unittest.TestCase):
    def test_prefers_db_within_directory_but_preserves_directory_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = os.path.join(temp_dir, "first")
            second = os.path.join(temp_dir, "second")
            os.makedirs(first)
            os.makedirs(second)
            for path in (
                os.path.join(first, "same.pb"),
                os.path.join(first, "same.db"),
                os.path.join(first, "priority.pb"),
                os.path.join(second, "priority.db"),
            ):
                with open(path, "wb") as fixture:
                    fixture.write(b"fixture")

            with mock.patch.object(fix, "_ALL_CONV_DIRS", [first, second]):
                catalog = fix._collect_all_conversations()

            self.assertEqual(catalog["same"], os.path.join(first, "same.db"))
            self.assertEqual(catalog["priority"], os.path.join(first, "priority.pb"))


class TimestampRefreshTests(unittest.TestCase):
    def setUp(self):
        self.field7 = fix.build_timestamp_field(7, 70)
        self.field10 = fix.build_timestamp_field(10, 80)
        self.workspace = fix.encode_length_delimited(9, b"\x0a\x03abc")
        self.unknown = fix.encode_string_field(12, "opaque-state")
        self.existing = (
            fix.encode_string_field(1, "Old title")
            + fix.build_timestamp_field(3, 100)
            + self.field7
            + self.field10
            + self.workspace
            + self.unknown
            + fix.build_timestamp_field(3, 110)
        )

    def build_inner(self, mtime, source_is_db=True, existing=None):
        entry = fix.build_trajectory_entry(
            "conversation-id",
            "New title",
            self.existing if existing is None else existing,
            conversation_mtime=mtime,
            source_is_db=source_is_db,
        )
        return entry_inner_blob(entry)

    def test_db_timestamp_noop_when_file_is_not_newer(self):
        inner = self.build_inner(109)
        self.assertEqual(
            list(fix.iter_length_delimited_fields(inner, 3)),
            list(fix.iter_length_delimited_fields(self.existing, 3)),
        )

    def test_db_refresh_removes_duplicate_field3_and_preserves_other_bytes(self):
        inner = self.build_inner(120)
        field3_values = list(fix.iter_length_delimited_fields(inner, 3))
        self.assertEqual(len(field3_values), 1)
        self.assertEqual(fix.extract_timestamp_seconds(inner, 3), 120)
        self.assertEqual(list(fix.iter_length_delimited_fields(inner, 7)), [b"\x08F"])
        self.assertEqual(list(fix.iter_length_delimited_fields(inner, 10)), [b"\x08P"])
        self.assertEqual(list(fix.iter_length_delimited_fields(inner, 9)), [b"\x0a\x03abc"])
        self.assertEqual(
            list(fix.iter_length_delimited_fields(inner, 12)), [b"opaque-state"]
        )

    def test_db_missing_field3_adds_only_field3(self):
        existing = (
            fix.encode_string_field(1, "Old title")
            + self.field7
            + self.field10
            + self.workspace
            + self.unknown
        )
        inner = self.build_inner(120, existing=existing)
        self.assertEqual(fix.extract_timestamp_seconds(inner, 3), 120)
        self.assertEqual(len(list(fix.iter_length_delimited_fields(inner, 7))), 1)
        self.assertEqual(len(list(fix.iter_length_delimited_fields(inner, 10))), 1)

    def test_pb_timestamp_is_not_refreshed(self):
        inner = self.build_inner(999, source_is_db=False)
        self.assertEqual(fix.extract_timestamp_seconds(inner, 3), 110)
        self.assertEqual(len(list(fix.iter_length_delimited_fields(inner, 3))), 2)


class ActiveProcessTests(unittest.TestCase):
    def test_posix_check_cannot_match_script_or_repository_path(self):
        result = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(fix, "_SYSTEM", "Linux"), \
                mock.patch.object(fix, "_IS_WSL", False), \
                mock.patch.object(fix.subprocess, "run", return_value=result) as run:
            self.assertFalse(fix.is_antigravity_running())

        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0:2], ['pgrep', '-x'])
            self.assertNotIn('-f', call.args[0])

    def test_active_process_aborts_before_update_discovery_or_write(self):
        with mock.patch.object(fix, "is_antigravity_running", return_value=True), \
                mock.patch.object(fix, "check_for_updates") as update, \
                mock.patch.object(fix, "_collect_all_conversations") as discover, \
                mock.patch.object(fix, "write_index_to_database") as write:
            result = fix.main()

        self.assertEqual(result, 1)
        update.assert_not_called()
        discover.assert_not_called()
        write.assert_not_called()


class CliWrapperTests(unittest.TestCase):
    def test_cli_wrapper_pauses_once_after_an_ordinary_return(self):
        with mock.patch.object(fix, "main", return_value=0), \
                mock.patch("builtins.input", return_value="") as prompt:
            result = fix.run_cli()

        self.assertEqual(result, 0)
        prompt.assert_called_once_with("\n  Finished. Press Enter to close...")

    def test_cli_wrapper_pauses_once_after_an_unexpected_exception(self):
        with mock.patch.object(fix, "main", side_effect=RuntimeError("boom")), \
                mock.patch("builtins.input", return_value="") as prompt:
            result = fix.run_cli()

        self.assertEqual(result, 1)
        prompt.assert_called_once_with("\n  Finished. Press Enter to close...")

    def test_direct_main_active_process_return_does_not_pause(self):
        with mock.patch.object(fix, "is_antigravity_running", return_value=True), \
                mock.patch("builtins.input") as prompt:
            result = fix.main()

        self.assertEqual(result, 1)
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()

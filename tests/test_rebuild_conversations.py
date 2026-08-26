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
    def test_derives_normalizes_and_truncates_first_prompt_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "conversation.db")
            raw_title = "  Generated\n\tTitle  " + ("x" * 100)
            make_conversation_db(
                path,
                [
                    (5, 7, title_payload("ignored")),
                    (30, 14, title_payload("later title")),
                    (10, 14, title_payload(raw_title)),
                ],
            )
            with open(path, "rb") as fixture:
                before_bytes = fixture.read()
            before_mtime = os.stat(path).st_mtime_ns

            title = fix.extract_title_from_conversation_db(path)

            self.assertEqual(title, ("Generated Title " + ("x" * 100))[:80])
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


if __name__ == "__main__":
    unittest.main()

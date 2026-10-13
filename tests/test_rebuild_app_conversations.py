import os
import sqlite3
import tempfile
import unittest

import rebuild_app_conversations as app_fix
import rebuild_conversations as fix


class AppConversationRebuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_convo.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_canonical_title_from_db(self):
        # Create a mock SQLite db with steps table containing tag 30 -> tag 4
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE steps (idx INTEGER NOT NULL, step_type INTEGER NOT NULL, step_payload BLOB NOT NULL)"
        )
        # Construct payload with tag 30 -> submessage with tag 4 (title)
        expected_title = "Deploying Antigrative Dashboard"
        title_field = fix.encode_string_field(4, expected_title)
        tag_30_field = fix.encode_length_delimited(30, title_field)
        dummy_payload = b"\x08\x01" + tag_30_field + b"\x10\x02"

        conn.execute(
            "INSERT INTO steps (idx, step_type, step_payload) VALUES (?, ?, ?)",
            (1, 23, dummy_payload),
        )
        conn.commit()
        conn.close()

        extracted = app_fix.extract_canonical_title_from_db(self.db_path)
        self.assertEqual(extracted, expected_title)

    def test_get_trajectory_id_from_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE trajectory_meta (trajectory_id TEXT, cascade_id TEXT)"
        )
        conn.execute(
            "INSERT INTO trajectory_meta VALUES (?, ?)",
            ("traj-uuid-1234", "cascade-uuid-5678"),
        )
        conn.commit()
        conn.close()

        tid = app_fix.get_trajectory_id(self.db_path, "fallback-id")
        self.assertEqual(tid, "traj-uuid-1234")

    def test_get_trajectory_id_fallback(self):
        tid = app_fix.get_trajectory_id("/non/existent/path.db", "fallback-id")
        self.assertEqual(tid, "fallback-id")

    def test_build_app_inner_blob_direct_conversation(self):
        cid = "conv-123"
        title = "Direct Chat"
        mtime = 1700000000
        blob = app_fix.build_app_inner_blob(
            cid=cid,
            title=title,
            ws_path=None,
            mtime=mtime,
            step_count=10,
            db_path=None,
        )

        # Check title in field 1
        pos = 0
        tag, pos = fix.decode_varint(blob, pos)
        l, pos = fix.decode_varint(blob, pos)
        extracted_title = blob[pos : pos + l].decode("utf-8")
        self.assertEqual(extracted_title, title)

        # Confirm field 9 (workspace) is NOT present
        ws_hint = fix.extract_workspace_hint(blob)
        self.assertIsNone(ws_hint)

    def test_build_app_inner_blob_workspace_conversation(self):
        cid = "conv-456"
        title = "Project Chat"
        ws = "file:///home/user/project"
        mtime = 1700000000
        blob = app_fix.build_app_inner_blob(
            cid=cid,
            title=title,
            ws_path=ws,
            mtime=mtime,
            step_count=5,
            db_path=None,
        )

        ws_hint = fix.extract_workspace_hint(blob)
        self.assertIsNotNone(ws_hint)
        self.assertIn(ws, ws_hint)


if __name__ == "__main__":
    unittest.main()

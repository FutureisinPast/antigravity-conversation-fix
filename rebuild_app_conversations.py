#!/usr/bin/env python3
"""
Antigravity Desktop App Conversation Rebuilder
==============================================
Rebuilds or updates ~/.gemini/antigravity/agyhub_summaries_proto.pb for the
standalone Antigravity desktop app (hub).

Fully generic & automated:
- Extracts canonical AI-generated titles directly from SQLite step payloads.
- Extracts accurate trajectory_id from trajectory_meta.
- Accurately detects direct conversations vs workspace-bound conversations.
- Idempotent and lossless with automatic timestamped backups.
"""

import os
import sys
import time
import shutil
import sqlite3
import subprocess
import base64

# Import helpers from rebuild_conversations
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rebuild_conversations as fix

APP_GEMINI_DIR = os.path.expanduser("~/.gemini/antigravity")
PB_PATH = os.path.join(APP_GEMINI_DIR, "agyhub_summaries_proto.pb")
CONV_DIR = os.path.join(APP_GEMINI_DIR, "conversations")
BRAIN_DIR = os.path.join(APP_GEMINI_DIR, "brain")


def is_desktop_app_running():
    """Check if the standalone desktop app process is running."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", "/.local/opt/antigravity/antigravity"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass
    return False


def get_step_count(db_path):
    """Return count of rows in steps table if available."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT count(*) FROM steps").fetchone()
        conn.close()
        return row[0] if row else 1
    except Exception:
        return 1


def get_trajectory_id(db_path, fallback_cid):
    """Read trajectory_id from trajectory_meta table."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT trajectory_id FROM trajectory_meta").fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return fallback_cid


def extract_canonical_title_from_db(db_path):
    """
    Extract the canonical backend-generated conversation title directly from
    the step payload in SQLite. Antigravity stores the title in step idx 1-3
    under protobuf tag 30 (submessage) -> tag 4 (string title).
    """
    try:
        conn = sqlite3.connect(db_path)
        for row in conn.execute("SELECT step_payload FROM steps WHERE idx <= 3"):
            payload = row[0]
            pos = 0
            while pos < len(payload):
                tag, pos = fix.decode_varint(payload, pos)
                fn, wt = tag >> 3, tag & 7
                if fn == 30 and wt == 2:
                    l, pos = fix.decode_varint(payload, pos)
                    sub = payload[pos : pos + l]
                    sp = 0
                    while sp < len(sub):
                        stag, sp = fix.decode_varint(sub, sp)
                        sfn, swt = stag >> 3, stag & 7
                        if sfn == 4 and swt == 2:
                            sl, sp = fix.decode_varint(sub, sp)
                            title = sub[sp : sp + sl].decode("utf-8", errors="ignore")
                            conn.close()
                            return title
                        sp = fix.skip_protobuf_field(sub, sp, swt)
                pos = fix.skip_protobuf_field(payload, pos, wt)
        conn.close()
    except Exception:
        pass
    return None


def get_ide_canonical_inner(target_cid):
    """Extract canonical inner protobuf blob from Antigravity IDE state.vscdb if present."""
    vscdb_path = os.path.expanduser(
        "~/.config/Antigravity IDE/User/globalStorage/state.vscdb"
    )
    if not os.path.exists(vscdb_path):
        return None
    try:
        conn = sqlite3.connect(vscdb_path)
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.trajectorySummaries'"
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        raw = base64.b64decode(row[0])
        for entry in fix.iter_length_delimited_fields(raw, 1):
            e_cid = next(fix.iter_length_delimited_fields(entry, 1)).decode("utf-8")
            if e_cid == target_cid:
                sub = next(fix.iter_length_delimited_fields(entry, 2))
                info_b64 = next(fix.iter_length_delimited_fields(sub, 1))
                return base64.b64decode(info_b64)
    except Exception:
        pass
    return None


def build_app_inner_blob(cid, title, ws_path, mtime, step_count=1, db_path=None):
    """Build a TrajectorySummary protobuf blob for the standalone app."""
    tid = get_trajectory_id(db_path, cid) if db_path else cid

    # Field 1: Title (string)
    inner = fix.encode_string_field(1, title)

    # Field 2: Step count (varint)
    inner += fix.encode_varint((2 << 3) | 0) + fix.encode_varint(step_count)

    # Timestamps (fields 3, 7, 10)
    seconds = int(mtime)
    ts_bytes = fix.encode_varint((1 << 3) | 0) + fix.encode_varint(seconds)
    inner += fix.encode_length_delimited(3, ts_bytes)

    # Field 4: trajectory_id (string)
    inner += fix.encode_string_field(4, tid)

    # Field 5: varint 1
    inner += fix.encode_varint((5 << 3) | 0) + fix.encode_varint(1)

    inner += fix.encode_length_delimited(7, ts_bytes)

    # Field 9: Workspace (only if explicitly assigned)
    if ws_path:
        inner += fix.build_workspace_field(ws_path)

    inner += fix.encode_length_delimited(10, ts_bytes)

    # Field 22: varint 4
    inner += fix.encode_varint((22 << 3) | 0) + fix.encode_varint(4)

    return inner


def main():
    print("=" * 60)
    print("  Antigravity Desktop App Conversation Rebuilder")
    print("=" * 60)
    print()

    if is_desktop_app_running():
        print("  [!] WARNING: Antigravity Desktop App is currently running.")
        print("      Please close the Desktop App first to prevent it from")
        print("      overwriting agyhub_summaries_proto.pb upon exit.")
        print()
        choice = input("  Has Antigravity Desktop App been closed? (y/N): ").strip().lower()
        if choice not in ("y", "yes"):
            print("  Aborted. Please close the desktop app and run again.")
            return 1
        if is_desktop_app_running():
            print("  [!] Antigravity Desktop App process is still detected. Aborting.")
            return 1

    if not os.path.exists(PB_PATH):
        print(f"  [!] {PB_PATH} does not exist.")
        return 1

    # Backup
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = f"{PB_PATH}.bak_{stamp}"
    shutil.copy2(PB_PATH, backup_path)
    print(f"  [+] Backup saved to: {backup_path}")

    with open(PB_PATH, "rb") as f:
        orig_data = f.read()

    existing_entries = {}  # cid -> (inner_bytes, timestamp, original_val)
    tombstones = []  # (fn, val)

    pos = 0
    while pos < len(orig_data):
        tag, pos = fix.decode_varint(orig_data, pos)
        fn, wt = tag >> 3, tag & 7
        l, pos = fix.decode_varint(orig_data, pos)
        val = orig_data[pos : pos + l]
        pos += l
        if fn == 1:
            ep = 0
            t1, ep = fix.decode_varint(val, ep)
            l1, ep = fix.decode_varint(val, ep)
            cid = val[ep : ep + l1].decode("utf-8")
            ep += l1

            t2, ep = fix.decode_varint(val, ep)
            l2, ep = fix.decode_varint(val, ep)
            inner = val[ep : ep + l2]

            ts = (
                fix.extract_timestamp_seconds(inner, 10)
                or fix.extract_timestamp_seconds(inner, 3)
                or 0
            )
            existing_entries[cid] = (inner, ts, val)
        else:
            tombstones.append((fn, val))

    print(f"  [*] Loaded {len(existing_entries)} existing entries from index.")

    # Scan disk conversations
    disk_cids = {}
    if os.path.isdir(CONV_DIR):
        for fname in os.listdir(CONV_DIR):
            if fname.endswith(".db"):
                cid = fname[:-3]
                p = os.path.join(CONV_DIR, fname)
                disk_cids[cid] = p

    print(f"  [*] Found {len(disk_cids)} conversation databases on disk.")

    added_count = 0
    known_ws = fix.load_known_workspace_uris()

    for cid, db_path in disk_cids.items():
        mtime = os.path.getmtime(db_path)

        if cid in existing_entries:
            # Refresh timestamp if disk file is newer
            inner, ts, old_val = existing_entries[cid]
            if int(mtime) > ts:
                inner_new = fix.strip_field_from_protobuf(inner, 3)
                ts_bytes = fix.encode_varint((1 << 3) | 0) + fix.encode_varint(int(mtime))
                inner_new += fix.encode_length_delimited(3, ts_bytes)
                existing_entries[cid] = (inner_new, int(mtime), None)
            continue

        # Missing entry - dynamically reconstruct:
        # Priority 1: Check if IDE state.vscdb already has canonical blob
        canonical_inner = get_ide_canonical_inner(cid)
        if canonical_inner:
            existing_entries[cid] = (canonical_inner, int(mtime), None)
            print(f"  [+] Injected canonical IDE blob: {cid[:8]}")
        else:
            # Priority 2: Extract backend canonical title directly from steps table
            title = extract_canonical_title_from_db(db_path)
            if not title:
                # Priority 3: Derive title from prompt or brain artifacts
                title, _ = fix.resolve_title(cid, {}, db_path)

            # Detect workspace if explicitly assigned to known workspace
            ws = fix.infer_workspace_from_brain(cid, known_ws)
            step_count = get_step_count(db_path)
            inner = build_app_inner_blob(cid, title, ws, mtime, step_count, db_path)
            existing_entries[cid] = (inner, int(mtime), None)

            ws_label = f" [{os.path.basename(ws)}]" if ws else " (直接对话)"
            print(f"  [+] Reconstructed: {cid[:8]} -> {title[:40]}{ws_label}")

        added_count += 1

    # Sort all entries by timestamp descending (newest first)
    sorted_cids = sorted(
        existing_entries.keys(), key=lambda c: existing_entries[c][1], reverse=True
    )

    rebuilt_data = b""
    for cid in sorted_cids:
        inner, _, _ = existing_entries[cid]
        entry = fix.encode_string_field(1, cid) + fix.encode_length_delimited(2, inner)
        rebuilt_data += fix.encode_length_delimited(1, entry)

    # Append tombstones
    for fn, val in tombstones:
        rebuilt_data += fix.encode_length_delimited(fn, val)

    # Write atomically
    temp_pb = PB_PATH + ".tmp"
    with open(temp_pb, "wb") as f:
        f.write(rebuilt_data)
    os.replace(temp_pb, PB_PATH)

    print()
    print("=" * 60)
    print(f"  SUCCESS! Total entries in app index: {len(sorted_cids)} (Added {added_count})")
    print("=" * 60)
    print()
    print("  Next steps: Open Antigravity Desktop App to see restored conversations!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

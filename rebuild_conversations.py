"""
Antigravity Conversation Fix
=============================
Rebuilds the Antigravity conversation index so all your chat history
appears correctly — sorted by date (newest first) with proper titles.

Fixes:
  - Missing conversations in the sidebar
  - Wrong ordering (not sorted by date)
  - Missing/placeholder titles

Usage:
  1. CLOSE Antigravity completely (File > Exit, or kill from Task Manager)
  2. Run this script (or use run.bat)
  3. REBOOT your PC (full restart, not just app restart)
  4. Open Antigravity — your conversations should appear, sorted by date

Requirements: Python 3.7+ (no external packages needed)
License: MIT
"""

import sqlite3
import base64
import os
import re
import sys
import time
import subprocess
import platform

# ─── Paths ────────────────────────────────────────────────────────────────────

_SYSTEM = platform.system()

if _SYSTEM == "Windows":
    DB_PATH = os.path.expandvars(
        r"%APPDATA%\antigravity\User\globalStorage\state.vscdb"
    )
    CONVERSATIONS_DIR = os.path.expandvars(
        r"%USERPROFILE%\.gemini\antigravity\conversations"
    )
    BRAIN_DIR = os.path.expandvars(
        r"%USERPROFILE%\.gemini\antigravity\brain"
    )
elif _SYSTEM == "Darwin":  # macOS
    _home = os.path.expanduser("~")
    DB_PATH = os.path.join(
        _home, "Library", "Application Support",
        "antigravity", "User", "globalStorage", "state.vscdb"
    )
    CONVERSATIONS_DIR = os.path.join(
        _home, ".gemini", "antigravity", "conversations"
    )
    BRAIN_DIR = os.path.join(
        _home, ".gemini", "antigravity", "brain"
    )
else:  # Linux and other POSIX systems
    _home = os.path.expanduser("~")
    DB_PATH = os.path.join(
        _home, ".config", "Antigravity",
        "User", "globalStorage", "state.vscdb"
    )
    CONVERSATIONS_DIR = os.path.join(
        _home, ".gemini", "antigravity", "conversations"
    )
    BRAIN_DIR = os.path.join(
        _home, ".gemini", "antigravity", "brain"
    )

BACKUP_FILENAME = "trajectorySummaries_backup.txt"


# ─── Protobuf Varint Helpers ─────────────────────────────────────────────────

def encode_varint(value):
    """Encode an integer as a protobuf varint."""
    result = b""
    while value > 0x7F:
        result += bytes([(value & 0x7F) | 0x80])
        value >>= 7
    result += bytes([value & 0x7F])
    return result or b'\x00'


def decode_varint(data, pos):
    """Decode a protobuf varint at the given position. Returns (value, new_pos)."""
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, pos + 1
        shift += 7
        pos += 1
    return result, pos


def skip_protobuf_field(data, pos, wire_type):
    """Skip over a protobuf field value at the given position. Returns new_pos."""
    if wire_type == 0:    # varint
        _, pos = decode_varint(data, pos)
    elif wire_type == 2:  # length-delimited
        length, pos = decode_varint(data, pos)
        pos += length
    elif wire_type == 1:  # 64-bit fixed
        pos += 8
    elif wire_type == 5:  # 32-bit fixed
        pos += 4
    return pos


def strip_field_from_protobuf(data, target_field_number):
    """
    Remove all instances of a specific field from raw protobuf bytes.
    Returns the remaining bytes with the target field stripped out.
    """
    remaining = b""
    pos = 0
    while pos < len(data):
        start_pos = pos
        try:
            tag, pos = decode_varint(data, pos)
        except Exception:
            remaining += data[start_pos:]
            break
        wire_type = tag & 7
        field_num = tag >> 3
        new_pos = skip_protobuf_field(data, pos, wire_type)
        if new_pos == pos and wire_type not in (0, 1, 2, 5):
            # Unknown wire type — keep everything from here
            remaining += data[start_pos:]
            break
        pos = new_pos
        if field_num != target_field_number:
            remaining += data[start_pos:pos]
    return remaining


# ─── Protobuf Write Helpers ──────────────────────────────────────────────────

def encode_length_delimited(field_number, data):
    """Encode a length-delimited protobuf field (wire type 2)."""
    tag = (field_number << 3) | 2
    return encode_varint(tag) + encode_varint(len(data)) + data


def encode_string_field(field_number, string_value):
    """Encode a string as a protobuf field."""
    return encode_length_delimited(field_number, string_value.encode('utf-8'))


# ─── Metadata Extraction ─────────────────────────────────────────────────────

def extract_existing_metadata(db_path):
    """
    Read metadata already stored in the database's trajectory data.
    Returns two dicts:
      - titles:      {conversation_id: title}  (real, non-fallback titles)
      - inner_blobs: {conversation_id: raw_inner_protobuf_bytes}
    The inner_blobs contain workspace URIs, timestamps, tool state, etc.
    These are preserved so re-running the script doesn't lose workspace assignments.
    """
    titles = {}
    inner_blobs = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM ItemTable "
            "WHERE key='antigravityUnifiedStateSync.trajectorySummaries'"
        )
        row = cur.fetchone()
        conn.close()

        if not row or not row[0]:
            return titles, inner_blobs

        decoded = base64.b64decode(row[0])
        pos = 0

        while pos < len(decoded):
            tag, pos = decode_varint(decoded, pos)
            wire_type = tag & 7

            if wire_type != 2:
                break

            length, pos = decode_varint(decoded, pos)
            entry = decoded[pos:pos + length]
            pos += length

            # Parse each entry for UUID (field 1) and info blob (field 2)
            ep, uid, info_b64 = 0, None, None
            while ep < len(entry):
                t, ep = decode_varint(entry, ep)
                fn, wt = t >> 3, t & 7
                if wt == 2:
                    l, ep = decode_varint(entry, ep)
                    content = entry[ep:ep + l]
                    ep += l
                    if fn == 1:
                        uid = content.decode('utf-8', errors='replace')
                    elif fn == 2:
                        sp = 0
                        _, sp = decode_varint(content, sp)
                        sl, sp = decode_varint(content, sp)
                        info_b64 = content[sp:sp + sl].decode('utf-8', errors='replace')
                elif wt == 0:
                    _, ep = decode_varint(entry, ep)
                else:
                    break

            if uid and info_b64:
                try:
                    raw_inner = base64.b64decode(info_b64)
                    # Save the full inner blob for metadata preservation
                    inner_blobs[uid] = raw_inner

                    # Also extract the title (field 1 of the inner blob)
                    ip = 0
                    _, ip = decode_varint(raw_inner, ip)
                    il, ip = decode_varint(raw_inner, ip)
                    title = raw_inner[ip:ip + il].decode('utf-8', errors='replace')
                    # Only keep real titles (skip fallback placeholders)
                    if not title.startswith("Conversation (") and not title.startswith("Conversation "):
                        titles[uid] = title
                except Exception:
                    pass

    except Exception:
        pass

    return titles, inner_blobs


def get_title_from_brain(conversation_id):
    """
    Try to extract a title from brain artifact .md files.
    Returns the first markdown heading found, or None.
    """
    brain_path = os.path.join(BRAIN_DIR, conversation_id)
    if not os.path.isdir(brain_path):
        return None

    for item in sorted(os.listdir(brain_path)):
        if item.startswith('.') or not item.endswith('.md'):
            continue
        try:
            filepath = os.path.join(brain_path, item)
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                first_line = f.readline().strip()
            if first_line.startswith('#'):
                return first_line.lstrip('# ').strip()[:80]
        except Exception:
            pass

    return None


def resolve_title(conversation_id, existing_titles):
    """
    Determine the best title for a conversation. Priority:
      1. Brain artifact .md heading
      2. Existing title from database (preserved from previous run)
      3. Fallback: date + short UUID
    Returns (title, source) where source is 'brain', 'preserved', or 'fallback'.
    """
    # Priority 1: Brain artifacts
    brain_title = get_title_from_brain(conversation_id)
    if brain_title:
        return brain_title, "brain"

    # Priority 2: Existing title from database
    if conversation_id in existing_titles:
        return existing_titles[conversation_id], "preserved"

    # Priority 3: Fallback with date
    conv_file = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.pb")
    if os.path.exists(conv_file):
        mod_time = time.strftime("%b %d", time.localtime(os.path.getmtime(conv_file)))
        return f"Conversation ({mod_time}) {conversation_id[:8]}", "fallback"

    return f"Conversation {conversation_id[:8]}", "fallback"


# ─── Workspace Helpers ───────────────────────────────────────────────────────

def path_to_workspace_uri(folder_path):
    """
    Convert a local folder path to a file:/// URI matching Antigravity's format.
    Example: D:/Repos/antigravity-conversation-fix  →  file:///d%3A/Repos/antigravity-conversation-fix
    """
    p = folder_path.replace("\\", "/")
    # Lowercase drive letter + URL-encode the colon
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + "%3A" + p[2:]
    return "file:///" + p.rstrip("/")


def build_workspace_field(folder_path):
    """
    Build protobuf field 9 (workspace sub-message) from a filesystem path.
    Sub-message structure:
      sub-field 1 (string) = workspace URI
      sub-field 2 (string) = workspace URI (duplicate)
      sub-field 3 (string) = "" (corpus)
      sub-field 4 (string) = "main" (branch)
    Returns raw bytes for one field-9 entry.
    """
    uri = path_to_workspace_uri(folder_path)
    sub_msg = (
        encode_string_field(1, uri)
        + encode_string_field(2, uri)
        + encode_string_field(3, "")
        + encode_string_field(4, "main")
    )
    return encode_length_delimited(9, sub_msg)


def build_timestamp_fields(epoch_seconds):
    """
    Build protobuf timestamp fields 3, 7, and 10 from an epoch timestamp.
    Each is a sub-message with: sub-field 1 (varint) = seconds since epoch.
    Field 3 = updated_at, Field 7 = created_at, Field 10 = also updated_at.
    Returns raw protobuf bytes containing all three fields.
    """
    seconds = int(epoch_seconds)
    # Timestamp sub-message: field 1 (varint) = seconds
    ts_inner = encode_varint((1 << 3) | 0) + encode_varint(seconds)
    return (
        encode_length_delimited(3, ts_inner)    # updated_at
        + encode_length_delimited(7, ts_inner)   # created_at
        + encode_length_delimited(10, ts_inner)  # also updated_at
    )


def has_timestamp_fields(inner_blob):
    """
    Check if the inner blob contains any timestamp fields (3, 7, or 10).
    Returns True if at least one is found.
    """
    if not inner_blob:
        return False
    try:
        pos = 0
        while pos < len(inner_blob):
            tag, pos = decode_varint(inner_blob, pos)
            fn, wt = tag >> 3, tag & 7
            if fn in (3, 7, 10):
                return True
            pos = skip_protobuf_field(inner_blob, pos, wt)
    except Exception:
        pass
    return False


def extract_workspace_hint(inner_blob):
    """
    Try to extract workspace URI from the protobuf inner blob.
    Scans length-delimited fields for strings matching file:/// patterns.
    Returns the workspace path string or None.
    """
    if not inner_blob:
        return None
    try:
        pos = 0
        while pos < len(inner_blob):
            tag, pos = decode_varint(inner_blob, pos)
            wire_type = tag & 7
            field_num = tag >> 3
            if wire_type == 2:
                l, pos = decode_varint(inner_blob, pos)
                content = inner_blob[pos:pos + l]
                pos += l
                if field_num > 1:
                    try:
                        text = content.decode("utf-8", errors="strict")
                        if "file:///" in text:
                            return text
                    except Exception:
                        pass
            elif wire_type == 0:
                _, pos = decode_varint(inner_blob, pos)
            elif wire_type == 1:
                pos += 8
            elif wire_type == 5:
                pos += 4
            else:
                break
    except Exception:
        pass
    return None


def infer_workspace_from_brain(conversation_id):
    """
    Scan brain .md files for file:/// paths and infer the workspace
    from the most common path prefix.
    Returns a filesystem path string or None.
    """
    brain_path = os.path.join(BRAIN_DIR, conversation_id)
    if not os.path.isdir(brain_path):
        return None

    path_pattern = re.compile(r"file:///([A-Za-z]:/[^)\s\"']+)")
    path_counts = {}

    try:
        for name in os.listdir(brain_path):
            if not name.endswith(".md") or name.startswith("."):
                continue
            filepath = os.path.join(brain_path, name)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(8192)
                for match in path_pattern.finditer(content):
                    path = match.group(1).replace("\\", "/")
                    parts = path.split("/")
                    if len(parts) >= 4:
                        ws = "/".join(parts[:4])  # e.g. D:/Repos/antigravity-conversation-fix
                        path_counts[ws] = path_counts.get(ws, 0) + 1
            except Exception:
                pass
    except Exception:
        return None

    if not path_counts:
        return None

    best = max(path_counts, key=path_counts.get)
    # Convert back from URL-decoded path to filesystem path
    return best.replace("/", os.sep)


def _prompt_valid_folder(prompt_text):
    """
    Keep asking for a folder path until user gives a valid one or presses Enter.
    Returns valid folder path string, or None if user skips (empty input).
    """
    while True:
        raw = input(prompt_text).strip()
        if raw == "":
            return None
        folder = raw.strip('"').strip("'").rstrip("\\/")
        if os.path.isdir(folder):
            print(f"    ✓ Mapped to {folder}")
            return folder
        else:
            print(f"    ✗ Path not found: {folder}")
            print(f"      (make sure the folder exists — try again or press Enter to skip)")


def prompt_workspace_path(label):
    """
    Prompt user for a workspace folder path with validation.
    Returns (path, mode) where mode is:
      - 'assigned': user entered a valid path
      - 'all':      user typed 'all', then provided a valid path
      - 'skip':     user pressed Enter
      - 'quit':     user typed 'q'
    """
    print(f"  {label}")
    while True:
        raw = input("    Workspace path (Enter=skip, 'all'=batch, 'q'=stop): ").strip()
        if raw == "":
            return None, "skip"
        if raw.lower() == "q":
            return None, "quit"
        if raw.lower() == "all":
            folder = _prompt_valid_folder("    Path for ALL remaining (Enter=cancel batch): ")
            if folder is None:
                continue  # cancelled batch, loop back to main prompt
            return folder, "all"
        # Normal path entry
        folder = raw.strip('"').strip("'").rstrip("\\/")
        if os.path.isdir(folder):
            print(f"    ✓ Mapped to {folder}")
            return folder, "assigned"
        else:
            print(f"    ✗ Path not found: {folder}")
            print(f"      (make sure the folder exists — try again or press Enter to skip)")


def interactive_workspace_assignment(unmapped_entries):
    """
    Show unmapped conversations and let user assign workspace paths.
    unmapped_entries: list of (index, conversation_id, title)
    Returns dict: {conversation_id: folder_path}
    """
    if not unmapped_entries:
        return {}

    print()
    print("  " + "=" * 58)
    print("  WORKSPACE ASSIGNMENT")
    print("  " + "=" * 58)
    print(f"  {len(unmapped_entries)} conversation(s) have no workspace mapping.")
    print("  You can assign each to a workspace folder.")
    print()
    for idx, cid, title in unmapped_entries:
        print(f"    [{idx:3d}] {title[:55]}")
    print()

    assignments = {}
    batch_path = None

    for idx, cid, title in unmapped_entries:
        if batch_path:
            # batch mode — apply same path to all remaining
            assignments[cid] = batch_path
            print(f"    [{idx:3d}] {title[:45]}  → {os.path.basename(batch_path)}")
            continue

        label = f"[{idx:3d}] {title[:55]}"
        folder, mode = prompt_workspace_path(label)

        if mode == "quit":
            print("    Stopped — remaining conversations left unmapped.")
            break
        elif mode == "skip":
            print("    Skipped.")
            continue
        elif mode == "all":
            batch_path = folder
            assignments[cid] = folder
        elif mode == "assigned":
            assignments[cid] = folder

    assigned_count = len(assignments)
    if assigned_count:
        print()
        print(f"  ✓ Assigned workspace to {assigned_count} conversation(s)")
    print()
    return assignments


# ─── Protobuf Entry Builder ──────────────────────────────────────────────────

def build_trajectory_entry(conversation_id, title, existing_inner_data=None,
                           workspace_path=None, pb_mtime=None):
    """
    Build a single trajectory summary protobuf entry.
    If existing_inner_data is provided, the title (field 1) is replaced
    but ALL other fields (workspace URIs, timestamps, tool state) are preserved.
    If workspace_path is provided and there is no existing_inner_data,
    a workspace field (field 9) is injected.
    If pb_mtime is provided and timestamps are missing, timestamp fields
    (3, 7, 10) are injected so Antigravity sorts correctly.
    Structure:
      field 1 (string) = conversation UUID
      field 2 (sub-message) = { field 1 (string) = base64(inner_info) }
      inner_info = { field 1 (string) = title, ... preserved fields ... }
    """
    if existing_inner_data:
        # Strip old title (field 1), prepend the new resolved title
        preserved_fields = strip_field_from_protobuf(existing_inner_data, 1)
        inner_info = encode_string_field(1, title) + preserved_fields
        # Inject workspace only if blob has no existing workspace and user assigned one
        if workspace_path and not extract_workspace_hint(existing_inner_data):
            inner_info += build_workspace_field(workspace_path)
        # Inject timestamps if missing
        if pb_mtime and not has_timestamp_fields(existing_inner_data):
            inner_info += build_timestamp_fields(pb_mtime)
    else:
        inner_info = encode_string_field(1, title)
        if workspace_path:
            inner_info += build_workspace_field(workspace_path)
        if pb_mtime:
            inner_info += build_timestamp_fields(pb_mtime)

    info_b64 = base64.b64encode(inner_info).decode('utf-8')
    sub_message = encode_string_field(1, info_b64)

    entry = encode_string_field(1, conversation_id)
    entry += encode_length_delimited(2, sub_message)
    return entry


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 62)
    print("   Antigravity Conversation Fix")
    print("   Rebuilds your conversation index — sorted by date")
    print("=" * 62)
    print()

    # ── Check if Antigravity is running ───────────────────────────────────

    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq antigravity.exe'],
            capture_output=True, text=True, creationflags=0x08000000
        )
        if 'antigravity.exe' in result.stdout.lower():
            print("  WARNING: Antigravity is still running!")
            print()
            print("  The fix will NOT work correctly while Antigravity is open.")
            print("  Please close it first: File > Exit, or kill from Task Manager.")
            print()
            choice = input("  Close Antigravity and press Enter to continue (or type Q to quit): ")
            if choice.strip().lower() == 'q':
                return 1
            print()
    except Exception:
        pass  # If tasklist fails, proceed anyway

    # ── Validate paths ──────────────────────────────────────────────────────

    if not os.path.exists(DB_PATH):
        print(f"  ERROR: Database not found at:")
        print(f"    {DB_PATH}")
        print()
        print("  Make sure Antigravity has been installed and opened at least once.")
        input("\n  Press Enter to close...")
        return 1

    if not os.path.isdir(CONVERSATIONS_DIR):
        print(f"  ERROR: Conversations directory not found at:")
        print(f"    {CONVERSATIONS_DIR}")
        input("\n  Press Enter to close...")
        return 1

    # ── Discover conversations ──────────────────────────────────────────────

    conv_files = [f for f in os.listdir(CONVERSATIONS_DIR) if f.endswith('.pb')]

    if not conv_files:
        print("  No conversations found on disk. Nothing to fix.")
        input("\n  Press Enter to close...")
        return 0

    # Sort by file modification time — newest first
    conv_files.sort(
        key=lambda f: os.path.getmtime(os.path.join(CONVERSATIONS_DIR, f)),
        reverse=True
    )
    conversation_ids = [f[:-3] for f in conv_files]

    print(f"  Found {len(conversation_ids)} conversations on disk")
    print()

    # ── Preserve existing titles ────────────────────────────────────────────

    print("  Reading existing metadata from database...")
    existing_titles, existing_inner_blobs = extract_existing_metadata(DB_PATH)
    ws_count = sum(1 for v in existing_inner_blobs.values() if len(v) > 100)
    print(f"  Found {len(existing_titles)} existing titles to preserve")
    print(f"  Found {ws_count} conversations with workspace/metadata to preserve")
    print()

    # ── Scan conversations & detect unmapped ─────────────────────────────────

    print("  Scanning conversations (newest first):")
    print("  " + "-" * 58)

    resolved = []  # list of (cid, title, source, inner_data, has_workspace)
    stats = {"brain": 0, "preserved": 0, "fallback": 0}
    markers = {"brain": "+", "preserved": "~", "fallback": "?"}

    for i, cid in enumerate(conversation_ids, 1):
        title, source = resolve_title(cid, existing_titles)
        inner_data = existing_inner_blobs.get(cid)
        has_ws = bool(inner_data and extract_workspace_hint(inner_data))
        resolved.append((cid, title, source, inner_data, has_ws))
        stats[source] += 1
        marker = markers[source]
        ws_flag = " [WS]" if has_ws else ""
        print(f"    [{i:3d}] {marker} {title[:50]}{ws_flag}")

    print("  " + "-" * 58)
    print(f"  Legend: [+] brain artifact  [~] preserved  [?] date fallback")
    print(f"  Totals: {stats['brain']} from brain, {stats['preserved']} preserved, {stats['fallback']} fallback")
    print()

    # ── Interactive workspace assignment ────────────────────────────────────

    unmapped = [(i, cid, title)
                for i, (cid, title, _, inner_data, has_ws) in enumerate(resolved, 1)
                if not has_ws]

    ws_assignments = {}  # cid → folder_path
    if unmapped:
        ws_assignments = interactive_workspace_assignment(unmapped)

    # ── Build the new index ─────────────────────────────────────────────────

    print("  Building final index...")
    result = b""
    ws_assigned_count = 0

    for cid, title, source, inner_data, has_ws in resolved:
        ws_path = ws_assignments.get(cid)
        # Get .pb mtime for timestamp injection
        pb_path = os.path.join(CONVERSATIONS_DIR, f"{cid}.pb")
        pb_mtime = os.path.getmtime(pb_path) if os.path.exists(pb_path) else None
        entry = build_trajectory_entry(cid, title, inner_data, ws_path, pb_mtime)
        result += encode_length_delimited(1, entry)
        if ws_path:
            ws_assigned_count += 1

    if ws_assigned_count:
        print(f"  Workspace assigned to {ws_assigned_count} conversation(s)")
    print()

    # ── Backup current data ─────────────────────────────────────────────────

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT value FROM ItemTable "
        "WHERE key='antigravityUnifiedStateSync.trajectorySummaries'"
    )
    row = cur.fetchone()

    backup_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), BACKUP_FILENAME)
    if row and row[0]:
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(row[0])
        print(f"  Backup saved to: {BACKUP_FILENAME}")

    # ── Write the new index ─────────────────────────────────────────────────

    encoded = base64.b64encode(result).decode('utf-8')

    if row:
        cur.execute(
            "UPDATE ItemTable SET value=? "
            "WHERE key='antigravityUnifiedStateSync.trajectorySummaries'",
            (encoded,)
        )
    else:
        cur.execute(
            "INSERT INTO ItemTable (key, value) "
            "VALUES ('antigravityUnifiedStateSync.trajectorySummaries', ?)",
            (encoded,)
        )

    conn.commit()
    conn.close()

    # ── Done ────────────────────────────────────────────────────────────────

    total = len(conversation_ids)
    print()
    print("  " + "=" * 58)
    print(f"  SUCCESS! Rebuilt index with {total} conversations.")
    print("  " + "=" * 58)
    print()
    print("  NEXT STEPS:")
    print("    1. Make sure Antigravity is fully closed")
    print("    2. REBOOT your PC (full restart, not just app restart)")
    print("    3. Open Antigravity — conversations should appear sorted by date")
    print()
    input("  Press Enter to close...")
    return 0


if __name__ == "__main__":
    sys.exit(main())

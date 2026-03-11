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
import sys
import time
import platform

# ─── Paths ────────────────────────────────────────────────────────────────────

_SYSTEM = platform.system()  # "Windows", "Linux", or "Darwin" (macOS)

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


# ─── Protobuf Write Helpers ──────────────────────────────────────────────────

def encode_length_delimited(field_number, data):
    """Encode a length-delimited protobuf field (wire type 2)."""
    tag = (field_number << 3) | 2
    return encode_varint(tag) + encode_varint(len(data)) + data


def encode_string_field(field_number, string_value):
    """Encode a string as a protobuf field."""
    return encode_length_delimited(field_number, string_value.encode('utf-8'))


# ─── Title Extraction ────────────────────────────────────────────────────────

def extract_existing_titles(db_path):
    """
    Read titles already stored in the database's trajectory data.
    These are preserved so re-running the script doesn't lose app-generated titles.
    Returns a dict of {conversation_id: title}.
    """
    titles = {}
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
            return titles

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
                    info_data = base64.b64decode(info_b64)
                    ip = 0
                    _, ip = decode_varint(info_data, ip)
                    il, ip = decode_varint(info_data, ip)
                    title = info_data[ip:ip + il].decode('utf-8', errors='replace')
                    # Only keep real titles (skip fallback placeholders)
                    if not title.startswith("Conversation (") and not title.startswith("Conversation "):
                        titles[uid] = title
                except Exception:
                    pass

    except Exception:
        pass

    return titles


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


# ─── Protobuf Entry Builder ──────────────────────────────────────────────────

def build_trajectory_entry(conversation_id, title):
    """
    Build a single trajectory summary protobuf entry.
    Structure:
      field 1 (string) = conversation UUID
      field 2 (sub-message) = { field 1 (string) = base64(inner_info) }
      inner_info = { field 1 (string) = title }
    """
    inner_info = encode_string_field(1, title)
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

    print("  Reading existing titles from database...")
    existing_titles = extract_existing_titles(DB_PATH)
    print(f"  Found {len(existing_titles)} existing titles to preserve")
    print()

    # ── Build the new index ─────────────────────────────────────────────────

    print("  Building conversation index (newest first):")
    print("  " + "-" * 58)

    result = b""
    stats = {"brain": 0, "preserved": 0, "fallback": 0}
    markers = {"brain": "+", "preserved": "~", "fallback": "?"}

    for i, cid in enumerate(conversation_ids, 1):
        title, source = resolve_title(cid, existing_titles)
        entry = build_trajectory_entry(cid, title)
        result += encode_length_delimited(1, entry)
        stats[source] += 1
        marker = markers[source]
        print(f"    [{i:3d}] {marker} {title[:55]}")

    print("  " + "-" * 58)
    print(f"  Legend: [+] brain artifact  [~] preserved  [?] date fallback")
    print(f"  Totals: {stats['brain']} from brain, {stats['preserved']} preserved, {stats['fallback']} fallback")
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

"""
Antigravity Conversation Fix  (v1.08 - Complete Remote SSH Support)
=============================
Rebuilds the Antigravity conversation index so all your chat history
appears correctly — sorted by date (newest first) with proper titles.

New in v1.08:
  - Fixed "key cannot be used for signing" SSH error by enforcing strict password auth.
  - Added prompt for custom Remote Home Path (e.g. /home1/user) for non-standard server setups.

Usage:
  1. CLOSE Antigravity completely (File > Exit, or kill from Task Manager)
  2. Install paramiko if you haven't: pip install paramiko
  3. Run this script locally: python rebuild_conversations.py
  4. REBOOT your PC (full restart, not just app restart)

Requirements: Python 3.7+, paramiko (for Remote SSH)
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
import shutil
import tempfile
import getpass
import stat
from urllib.parse import quote

# ─── Default Paths ────────────────────────────────────────────────────────────

_SYSTEM = platform.system()

if _SYSTEM == "Windows":
    DB_PATH = os.path.expandvars(
        r"%APPDATA%\antigravity\User\globalStorage\state.vscdb"
    )
    LOCAL_CONV_DIR = os.path.expandvars(
        r"%USERPROFILE%\.gemini\antigravity\conversations"
    )
    LOCAL_BRAIN_DIR = os.path.expandvars(
        r"%USERPROFILE%\.gemini\antigravity\brain"
    )
elif _SYSTEM == "Darwin":  # macOS
    _home = os.path.expanduser("~")
    DB_PATH = os.path.join(
        _home, "Library", "Application Support",
        "antigravity", "User", "globalStorage", "state.vscdb"
    )
    LOCAL_CONV_DIR = os.path.join(
        _home, ".gemini", "antigravity", "conversations"
    )
    LOCAL_BRAIN_DIR = os.path.join(
        _home, ".gemini", "antigravity", "brain"
    )
else:  # Linux and other POSIX systems
    _home = os.path.expanduser("~")
    DB_PATH = os.path.join(
        _home, ".config", "Antigravity",
        "User", "globalStorage", "state.vscdb"
    )
    LOCAL_CONV_DIR = os.path.join(
        _home, ".gemini", "antigravity", "conversations"
    )
    LOCAL_BRAIN_DIR = os.path.join(
        _home, ".gemini", "antigravity", "brain"
    )

BACKUP_FILENAME = "trajectorySummaries_backup.txt"


# ─── Protobuf Varint Helpers ─────────────────────────────────────────────────

def encode_varint(value):
    result = b""
    while value > 0x7F:
        result += bytes([(value & 0x7F) | 0x80])
        value >>= 7
    result += bytes([value & 0x7F])
    return result or b'\x00'

def decode_varint(data, pos):
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
    if wire_type == 0:
        _, pos = decode_varint(data, pos)
    elif wire_type == 2:
        length, pos = decode_varint(data, pos)
        pos += length
    elif wire_type == 1:
        pos += 8
    elif wire_type == 5:
        pos += 4
    return pos

def strip_field_from_protobuf(data, target_field_number):
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
            remaining += data[start_pos:]
            break
        pos = new_pos
        if field_num != target_field_number:
            remaining += data[start_pos:pos]
    return remaining


# ─── Protobuf Write Helpers ──────────────────────────────────────────────────

def encode_length_delimited(field_number, data):
    tag = (field_number << 3) | 2
    return encode_varint(tag) + encode_varint(len(data)) + data

def encode_string_field(field_number, string_value):
    return encode_length_delimited(field_number, string_value.encode('utf-8'))


# ─── Workspace Helpers ───────────────────────────────────────────────────────

def _is_remote_uri(path_or_uri):
    return path_or_uri.startswith("vscode-remote://") or path_or_uri.startswith("file:///")

def path_to_workspace_uri(folder_path):
    if _is_remote_uri(folder_path):
        return folder_path

    p = folder_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:]
    else:
        drive = None
        rest = p

    segments = rest.split("/")
    encoded_segments = [quote(seg, safe="") for seg in segments]
    encoded_path = "/".join(encoded_segments)

    if drive:
        return f"file:///{drive}%3A{encoded_path}"
    else:
        return f"file:///{encoded_path.lstrip('/')}"

def build_workspace_field(folder_path):
    uri = path_to_workspace_uri(folder_path)
    sub_msg = encode_string_field(1, uri) + encode_string_field(2, uri)
    return encode_length_delimited(9, sub_msg)

def extract_workspace_hint(inner_blob):
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
                        if "file:///" in text or "vscode-remote://" in text:
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

def infer_workspace_from_brain(conversation_id, brain_dir_base):
    brain_path = os.path.join(brain_dir_base, conversation_id)
    if not os.path.isdir(brain_path):
        return None

    if _SYSTEM == "Windows":
        local_pattern = re.compile(r"file:///([A-Za-z](?:%3A|:)/[^)\s\"'\]>]+)")
    else:
        local_pattern = re.compile(r"file:///([^)\s\"'\]>]+)")
    remote_pattern = re.compile(r"(vscode-remote://[^)\s\"'\]>]+)")

    path_counts = {}
    try:
        for name in os.listdir(brain_path):
            if not name.endswith(".md") or name.startswith("."):
                continue
            filepath = os.path.join(brain_path, name)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(16384)

                for match in remote_pattern.finditer(content):
                    uri = match.group(1)
                    path_counts[uri] = path_counts.get(uri, 0) + 1

                for match in local_pattern.finditer(content):
                    raw = match.group(1)
                    raw = raw.replace("%3A", ":").replace("%3a", ":").replace("%20", " ")
                    parts = raw.replace("\\", "/").split("/")
                    depth = 5 if _SYSTEM == "Windows" else 4
                    if len(parts) >= depth:
                        ws = "/".join(parts[:depth])
                        path_counts[ws] = path_counts.get(ws, 0) + 1
            except Exception:
                pass
    except Exception:
        return None

    if not path_counts:
        return None

    best = max(path_counts, key=path_counts.get)
    if best.startswith("vscode-remote://"):
        return best
    return best.replace("/", os.sep)


# ─── Timestamp Helpers ───────────────────────────────────────────────────────

def build_timestamp_fields(epoch_seconds):
    seconds = int(epoch_seconds)
    ts_inner = encode_varint((1 << 3) | 0) + encode_varint(seconds)
    return (
        encode_length_delimited(3, ts_inner)
        + encode_length_delimited(7, ts_inner)
        + encode_length_delimited(10, ts_inner)
    )

def has_timestamp_fields(inner_blob):
    if not inner_blob:
        return False
    try:
        pos = 0
        while pos < len(inner_blob):
            tag, pos = decode_varint(inner_blob, pos)
            fn = tag >> 3
            wt = tag & 7
            if fn in (3, 7, 10):
                return True
            pos = skip_protobuf_field(inner_blob, pos, wt)
    except Exception:
        pass
    return False


# ─── Interactive Workspace Assignment ────────────────────────────────────────

def _prompt_valid_folder(prompt_text):
    while True:
        raw = input(prompt_text).strip()
        if raw == "":
            return None
        folder = raw.strip('"').strip("'").rstrip("\\/")
        if _is_remote_uri(folder):
            print(f"    + Mapped remote URI: {folder}")
            return folder
        if os.path.isdir(folder):
            print(f"    + Mapped to {folder}")
            return folder
        else:
            print(f"    x Path not found: {folder}\n      (Try again or press Enter to skip)")

def interactive_workspace_assignment(unmapped_entries):
    if not unmapped_entries:
        return {}

    print("\n  " + "=" * 58)
    print("  WORKSPACE ASSIGNMENT (optional)")
    print("  " + "=" * 58)
    print(f"  {len(unmapped_entries)} conversation(s) have no workspace.")
    print("  You can assign each to a workspace folder now,")
    print("  or press Enter to skip and leave them unassigned.\n")

    assignments = {}
    batch_path = None

    for idx, cid, title, _ in unmapped_entries:
        if batch_path:
            assignments[cid] = batch_path
            print(f"    [{idx:3d}] {title[:45]}  -> {os.path.basename(batch_path)}")
            continue

        print(f"  [{idx:3d}] {title[:55]}")
        while True:
            raw = input("    Workspace path (Enter=skip, 'all'=batch, 'q'=stop): ").strip()
            if raw == "":
                print("    Skipped.")
                break
            if raw.lower() == "q":
                print("    Stopped — remaining conversations left unmapped.")
                return assignments
            if raw.lower() == "all":
                folder = _prompt_valid_folder("    Path for ALL remaining (Enter=cancel): ")
                if folder is None:
                    continue
                batch_path = folder
                assignments[cid] = folder
                break

            folder = raw.strip('"').strip("'").rstrip("\\/")
            if _is_remote_uri(folder):
                print(f"    + Mapped remote URI: {folder}")
                assignments[cid] = folder
                break
            if os.path.isdir(folder):
                print(f"    + Mapped to {folder}")
                assignments[cid] = folder
                break
            else:
                print(f"    x Path not found: {folder}\n      (Try again or press Enter to skip)")

    if assignments:
        print(f"\n  + Assigned workspace to {len(assignments)} conversation(s)")
    print()
    return assignments


# ─── Metadata Extraction ─────────────────────────────────────────────────────

def extract_existing_metadata(db_path):
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
                    inner_blobs[uid] = raw_inner

                    ip = 0
                    _, ip = decode_varint(raw_inner, ip)
                    il, ip = decode_varint(raw_inner, ip)
                    title = raw_inner[ip:ip + il].decode('utf-8', errors='replace')
                    if not title.startswith("Conversation (") and not title.startswith("Conversation "):
                        titles[uid] = title
                except Exception:
                    pass
    except Exception:
        pass

    return titles, inner_blobs


def get_title_from_brain(conversation_id, brain_dir_base):
    brain_path = os.path.join(brain_dir_base, conversation_id)
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


def resolve_title(cid, existing_titles, conv_info):
    brain_title = get_title_from_brain(cid, conv_info['brain_dir'])
    if brain_title:
        return brain_title, "brain"

    if cid in existing_titles:
        return existing_titles[cid], "preserved"

    if os.path.exists(conv_info['pb_path']):
        mod_time = time.strftime("%b %d", time.localtime(conv_info['mtime']))
        return f"Conversation ({mod_time}) {cid[:8]}", "fallback"

    return f"Conversation {cid[:8]}", "fallback"


def build_trajectory_entry(conversation_id, title, existing_inner_data=None,
                           workspace_path=None, pb_mtime=None):
    if existing_inner_data:
        preserved_fields = strip_field_from_protobuf(existing_inner_data, 1)
        inner_info = encode_string_field(1, title) + preserved_fields
        if workspace_path:
            inner_info = strip_field_from_protobuf(inner_info, 9)
            inner_info += build_workspace_field(workspace_path)
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


# ─── Remote SSH Download Helper ───────────────────────────────────────────────

def fetch_remote_files(host, user, pwd, remote_home):
    """Connects via SSH and downloads the artifacts to a temporary directory."""
    try:
        import paramiko
    except ImportError:
        print("\n  [!] ERROR: 'paramiko' library is required to fetch remote files.")
        print("  Please install it by running the following command:")
        print("    pip install paramiko\n")
        sys.exit(1)

    print(f"\n  Connecting to {user}@{host}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # allow_agent=False and look_for_keys=False prevent paramiko from trying
        # to use incompatible local SSH agents or keys, strictly enforcing password auth.
        ssh.connect(host, username=user, password=pwd, timeout=10,
                    look_for_keys=False, allow_agent=False)
    except Exception as e:
        print(f"  [!] Failed to connect to remote server: {e}")
        sys.exit(1)

    sftp = ssh.open_sftp()
    
    # Path resolution on remote Linux server using user-provided home path
    remote_home = remote_home.rstrip('/')
    remote_base = f"{remote_home}/.gemini/antigravity"
    remote_conv = f"{remote_base}/conversations"
    remote_brain = f"{remote_base}/brain"

    # Create local temporary directories
    temp_dir = tempfile.mkdtemp(prefix="antigravity_remote_")
    local_conv = os.path.join(temp_dir, "conversations")
    local_brain = os.path.join(temp_dir, "brain")

    def download_dir(rem_dir, loc_dir):
        try:
            os.makedirs(loc_dir, exist_ok=True)
            for item in sftp.listdir_attr(rem_dir):
                rem_path = f"{rem_dir}/{item.filename}"
                loc_path = os.path.join(loc_dir, item.filename)
                
                if stat.S_ISDIR(item.st_mode):
                    download_dir(rem_path, loc_path)
                else:
                    sftp.get(rem_path, loc_path)
                    # Preserve modified time for proper sorting
                    os.utime(loc_path, (item.st_atime, item.st_mtime))
        except IOError:
            # File/Folder doesn't exist on remote
            pass

    print(f"  Downloading remote conversations from: {remote_conv}")
    download_dir(remote_conv, local_conv)
    
    print(f"  Downloading remote brain artifacts from: {remote_brain}")
    download_dir(remote_brain, local_brain)

    sftp.close()
    ssh.close()
    
    print("  Successfully fetched remote data.\n")
    return temp_dir, local_conv, local_brain


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 62)
    print("   Antigravity Conversation Fix  v1.08")
    print("   Rebuilds your local & remote index — sorted by date")
    print("=" * 62)
    print()

    # ── Check if Antigravity is running ─────────────────────────────────────
    if _SYSTEM == "Windows":
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq antigravity.exe'],
                capture_output=True, text=True, creationflags=0x08000000
            )
            if 'antigravity.exe' in result.stdout.lower():
                print("  WARNING: Antigravity is still running!")
                print("  The fix will NOT work correctly while Antigravity is open.")
                print("  Please close it first: File > Exit, or kill from Task Manager.\n")
                choice = input("  Close Antigravity and press Enter to continue (or Q to quit): ")
                if choice.strip().lower() == 'q':
                    return 1
                print()
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(['pgrep', '-f', 'antigravity'], capture_output=True, text=True)
            if result.stdout.strip():
                print("  WARNING: Antigravity may still be running!")
                print("  Please close it before proceeding.\n")
                choice = input("  Press Enter to continue anyway (or Q to quit): ")
                if choice.strip().lower() == 'q':
                    return 1
                print()
        except Exception:
            pass

    if not os.path.exists(DB_PATH):
        print(f"  ERROR: Database not found at:\n    {DB_PATH}\n")
        print("  Make sure Antigravity has been installed locally at least once.")
        input("\n  Press Enter to close...")
        return 1

    # ── Interactive Remote Setup ────────────────────────────────────────────
    
    print("  Do you also want to fix remote conversations?")
    print("  (This will SSH into your server, grab the remote files, and")
    print("  merge them with your local database).")
    use_remote = input("  [y/N]: ").strip().lower() == 'y'
    
    temp_remote_dir = None
    remote_conv_dir = None
    remote_brain_dir = None

    if use_remote:
        host = input("    Remote IP/Hostname: ").strip()
        user = input("    Remote Username: ").strip()
        pwd = getpass.getpass("    Remote Password: ")
        
        default_home = f"/home/{user}"
        home_input = input(f"    Remote Home Path (default: {default_home}): ").strip()
        remote_home = home_input if home_input else default_home

        temp_remote_dir, remote_conv_dir, remote_brain_dir = fetch_remote_files(host, user, pwd, remote_home)

    try:
        # ── Discover conversations (Merge Local & Remote) ───────────────────────
        
        all_conversations = {} # map cid -> info dict
        
        def add_conversations(conv_dir, brain_dir, is_remote_flag):
            if not os.path.isdir(conv_dir):
                return
            for f in os.listdir(conv_dir):
                if f.endswith('.pb'):
                    cid = f[:-3]
                    pb_path = os.path.join(conv_dir, f)
                    mtime = os.path.getmtime(pb_path)
                    
                    # If exists, keep the one with the newest modified time
                    if cid not in all_conversations or mtime > all_conversations[cid]['mtime']:
                        all_conversations[cid] = {
                            'pb_path': pb_path,
                            'brain_dir': brain_dir,
                            'mtime': mtime,
                            'is_remote': is_remote_flag
                        }

        add_conversations(LOCAL_CONV_DIR, LOCAL_BRAIN_DIR, is_remote_flag=False)
        if use_remote:
            add_conversations(remote_conv_dir, remote_brain_dir, is_remote_flag=True)

        if not all_conversations:
            print("  No conversations found locally or remotely.")
            input("\n  Press Enter to close...")
            return 0

        # Sort by latest modified time (newest first)
        conversation_ids = sorted(
            all_conversations.keys(),
            key=lambda c: all_conversations[c]['mtime'],
            reverse=True
        )

        print(f"  Found {len(conversation_ids)} total conversations across sources.")
        print()

        # ── Preserve existing metadata ──────────────────────────────────────────
        print("  Reading existing metadata from local database...")
        existing_titles, existing_inner_blobs = extract_existing_metadata(DB_PATH)
        ws_count = sum(1 for v in existing_inner_blobs.values() if extract_workspace_hint(v))
        print(f"  Found {len(existing_titles)} existing titles to preserve")
        print(f"  Found {ws_count} conversations with workspace metadata\n")

        # ── Scan conversations ──────────────────────────────────────────────────
        print("  Scanning conversations (newest first):")
        print("  " + "-" * 58)

        resolved = []  # (cid, title, source, inner_data, has_ws, is_remote)
        stats = {"brain": 0, "preserved": 0, "fallback": 0}
        markers = {"brain": "+", "preserved": "~", "fallback": "?"}

        for i, cid in enumerate(conversation_ids, 1):
            conv_info = all_conversations[cid]
            title, source = resolve_title(cid, existing_titles, conv_info)
            inner_data = existing_inner_blobs.get(cid)
            has_ws = bool(inner_data and extract_workspace_hint(inner_data))
            
            resolved.append((cid, title, source, inner_data, has_ws, conv_info))
            stats[source] += 1
            marker = markers[source]
            ws_flag = " [WS]" if has_ws else ""
            remote_flag = " [Remote]" if conv_info['is_remote'] else ""
            print(f"    [{i:3d}] {marker} {title[:45]}{ws_flag}{remote_flag}")

        print("  " + "-" * 58)
        print(f"  Legend: [+] brain  [~] preserved  [?] fallback  [WS] workspace")
        print(f"  Totals: {stats['brain']} brain, {stats['preserved']} preserved, {stats['fallback']} fallback\n")

        # ── Workspace assignment ───────────────────────────────────────────────
        unmapped = [(i, cid, title, conv_info)
                    for i, (cid, title, _, _, has_ws, conv_info) in enumerate(resolved, 1)
                    if not has_ws]

        ws_assignments = {}

        if unmapped:
            print(f"  {len(unmapped)} conversation(s) have no workspace assigned.\n")
            print("  Press Enter or 1: Auto-assign workspaces (recommended)")
            print("  Press 2:          Auto-assign + manually assign the rest\n")
            choice = input("  Your choice: ").strip()

            print("\n  Auto-assigning workspaces from brain artifacts...")
            auto_count = 0
            for idx, cid, title, conv_info in unmapped:
                inferred = infer_workspace_from_brain(cid, conv_info['brain_dir'])
                if inferred:
                    ws_assignments[cid] = inferred
                    auto_count += 1
                    print(f"    [{idx:3d}] -> {os.path.basename(inferred)}")
                    
            if auto_count:
                print(f"  Auto-assigned {auto_count} workspace(s)\n")
            else:
                print("  No workspaces could be auto-detected.\n")

            if choice == '2':
                still_unmapped = [(idx, cid, title, conv_info)
                                  for idx, cid, title, conv_info in unmapped
                                  if cid not in ws_assignments]
                if still_unmapped:
                    user_assignments = interactive_workspace_assignment(still_unmapped)
                    ws_assignments.update(user_assignments)

        # ── Build the new index ─────────────────────────────────────────────────
        print("  Building final index...")
        result_bytes = b""
        ws_total = 0
        ts_injected = 0

        for cid, title, source, inner_data, has_ws, conv_info in resolved:
            ws_path = ws_assignments.get(cid)
            pb_mtime = conv_info['mtime']

            entry = build_trajectory_entry(cid, title, inner_data, ws_path, pb_mtime)
            result_bytes += encode_length_delimited(1, entry)

            if has_ws or ws_path:
                ws_total += 1
            if pb_mtime and (not inner_data or not has_timestamp_fields(inner_data)):
                ts_injected += 1

        print(f"  Workspace: {ws_total} mapped  |  Timestamps injected: {ts_injected}\n")

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
        encoded = base64.b64encode(result_bytes).decode('utf-8')

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
        print("\n  " + "=" * 58)
        print(f"  SUCCESS! Rebuilt index with {len(conversation_ids)} conversations.")
        print("  " + "=" * 58)
        print("\n  NEXT STEPS:")
        print("    1. Make sure Antigravity is fully closed")
        print("    2. REBOOT your PC (full restart, not just app restart)")
        print("    3. Open Antigravity — local and remote conversations should appear together")
        print()
        input("  Press Enter to close...")
        
    finally:
        # ── Cleanup temporary files ──────────────────────────────────────────────
        if temp_remote_dir and os.path.exists(temp_remote_dir):
            shutil.rmtree(temp_remote_dir, ignore_errors=True)

    return 0

if __name__ == "__main__":
    sys.exit(main())

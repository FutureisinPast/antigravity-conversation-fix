#!/usr/bin/env python3
"""
Antigravity Conversation Fix  (v1.05)
=============================
Rebuilds the Antigravity conversation index so all your chat history
appears correctly — sorted by date (newest first) with proper titles.

Fixes:
  - Missing conversations in the sidebar
  - Wrong ordering (not sorted by date)
  - Missing/placeholder titles
  - Workspace assignments stripped or lost
  - Missing timestamps causing sort issues

Usage:
  1. CLOSE Antigravity completely (File > Exit, or kill from Task Manager)
  2. Run this script (or use run.bat on Windows)
  3. Open Antigravity — your conversations should appear, sorted by date
  4. Reboot only if the changes do not appear after reopening Antigravity

Requirements: Python 3.7+ (no external packages needed)
License: MIT
"""

# ─── Python Version Guard ────────────────────────────────────────────────────
# If accidentally launched with Python 2 (e.g. `python` points to 2.x on
# legacy systems), automatically re-exec with python3 instead of crashing
# with syntax errors.  If python3 isn't available either, give a clear message.
import sys
import os

if sys.version_info[0] < 3:
    try:
        sys.stdout.flush()
        os.execvp("python3", ["python3"] + sys.argv)
    except OSError:
        sys.stderr.write(
            "ERROR: This script requires Python 3.7+.\n"
            "       'python' on this system is Python {}.{}, and 'python3' was not found.\n"
            "       Please install Python 3: https://www.python.org/downloads/\n"
            .format(sys.version_info[0], sys.version_info[1])
        )
        sys.exit(1)

if sys.version_info < (3, 7):
    sys.stderr.write(
        "ERROR: This script requires Python 3.7+, but you are running Python {}.{}.\n"
        "       Please upgrade: https://www.python.org/downloads/\n"
        .format(sys.version_info[0], sys.version_info[1])
    )
    sys.exit(1)

import sqlite3
import base64
import json
import re
import time
import subprocess
import platform
import webbrowser
from urllib.parse import quote, unquote
from urllib.request import urlopen, Request

_CURRENT_VERSION = "1.06"
_GITHUB_REPO = "FutureisinPast/antigravity-conversation-fix"
_RELEASES_URL = f"https://github.com/{_GITHUB_REPO}/releases/latest"

# ─── Path Detection ──────────────────────────────────────────────────────────
# Antigravity was renamed to "Antigravity IDE" in a recent update.
# We check the new name first, then fall back to the old name so the tool
# works on both old and new installations.

_SYSTEM = platform.system()
_ANTIGRAVITY_NAMES = ("Antigravity IDE", "antigravity", "Antigravity")


def _is_wsl():
    """Detect if running inside Windows Subsystem for Linux."""
    if _SYSTEM != "Linux":
        return False
    if "microsoft" in platform.release().lower():
        return True
    try:
        with open("/proc/version", "r") as f:
            if "microsoft" in f.read().lower():
                return True
    except Exception:
        pass
    return False


_IS_WSL = _is_wsl()


def _get_wsl_windows_appdata():
    """
    Resolve the Windows %APPDATA% path from inside WSL.
    Strategy 1: Ask Windows directly via cmd.exe and convert with wslpath.
    Strategy 2: Scan /mnt/c/Users/ for folders that have Antigravity installed.
    Returns a WSL-accessible path string, or None if resolution fails.
    """
    # Strategy 1: cmd.exe %APPDATA% → wslpath
    try:
        proc = subprocess.run(
            ['cmd.exe', '/c', 'echo %APPDATA%'],
            capture_output=True, text=True, check=True
        )
        win_path = proc.stdout.strip()
        if win_path and win_path != "%APPDATA%":
            proc_wsl = subprocess.run(
                ['wslpath', win_path],
                capture_output=True, text=True, check=True
            )
            wsl_path = proc_wsl.stdout.strip()
            if os.path.exists(wsl_path):
                return wsl_path
    except Exception:
        pass

    # Strategy 2: Scan /mnt/c/Users/ for user folders that have Antigravity
    if os.path.exists("/mnt/c/Users"):
        _skip = {"Default", "Default User", "All Users", "desktop.ini", "Public"}
        try:
            for user in os.listdir("/mnt/c/Users"):
                if user in _skip:
                    continue
                appdata = os.path.join("/mnt/c/Users", user, "AppData", "Roaming")
                if not os.path.exists(appdata):
                    continue
                for name in _ANTIGRAVITY_NAMES:
                    if os.path.exists(os.path.join(appdata, name)):
                        return appdata
        except Exception:
            pass

    return None


def _first_existing(*candidates):
    """Return the first path that exists on disk, or the first candidate if none exist."""
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def _existing_paths(*candidates):
    """Return all candidate paths that exist on disk, preserving order."""
    return [p for p in candidates if p and os.path.exists(p)]


if _SYSTEM == "Windows":
    _appdata = os.path.expandvars(r"%APPDATA%")
    _profile = os.path.expandvars(r"%USERPROFILE%")
    _gemini = os.path.join(_profile, ".gemini")

    _DB_CANDIDATES = (
        os.path.join(_appdata, "Antigravity IDE", "User", "globalStorage", "state.vscdb"),
        os.path.join(_appdata, "antigravity", "User", "globalStorage", "state.vscdb"),
        os.path.join(_appdata, "Antigravity", "User", "globalStorage", "state.vscdb"),
    )
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    CONVERSATIONS_DIR = _first_existing(
        os.path.join(_gemini, "antigravity-ide", "conversations"),
        os.path.join(_gemini, "antigravity", "conversations"),
    )
    BRAIN_DIR = _first_existing(
        os.path.join(_gemini, "antigravity-ide", "brain"),
        os.path.join(_gemini, "antigravity", "brain"),
    )
    WORKSPACE_STORAGE_DIR = _first_existing(
        os.path.join(_appdata, "Antigravity IDE", "User", "workspaceStorage"),
        os.path.join(_appdata, "antigravity", "User", "workspaceStorage"),
    )
    _ALL_CONV_DIRS = [
        os.path.join(_gemini, "antigravity-ide", "conversations"),
        os.path.join(_gemini, "antigravity", "conversations"),
        os.path.join(_gemini, "antigravity-backup", "conversations"),
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini, "antigravity-ide", "brain"),
        os.path.join(_gemini, "antigravity", "brain"),
        os.path.join(_gemini, "antigravity-backup", "brain"),
    ]
elif _IS_WSL:
    _wsl_appdata = _get_wsl_windows_appdata()
    _home = os.path.expanduser("~")

    if _wsl_appdata:
        _DB_CANDIDATES = (
            os.path.join(_wsl_appdata, "Antigravity IDE", "User", "globalStorage", "state.vscdb"),
            os.path.join(_wsl_appdata, "antigravity", "User", "globalStorage", "state.vscdb"),
            os.path.join(_wsl_appdata, "Antigravity", "User", "globalStorage", "state.vscdb"),
        )
        DB_PATH = _first_existing(*_DB_CANDIDATES)
        WORKSPACE_STORAGE_DIR = _first_existing(
            os.path.join(_wsl_appdata, "Antigravity IDE", "User", "workspaceStorage"),
            os.path.join(_wsl_appdata, "antigravity", "User", "workspaceStorage"),
            os.path.join(_wsl_appdata, "Antigravity", "User", "workspaceStorage"),
        )
    else:
        _DB_CANDIDATES = ()
        DB_PATH = ""
        WORKSPACE_STORAGE_DIR = ""

    CONVERSATIONS_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "conversations"),
        os.path.join(_home, ".gemini", "antigravity", "conversations"),
    )
    BRAIN_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "brain"),
        os.path.join(_home, ".gemini", "antigravity", "brain"),
    )
    _gemini_wsl = os.path.join(_home, ".gemini")
    _ALL_CONV_DIRS = [
        os.path.join(_gemini_wsl, "antigravity-ide", "conversations"),
        os.path.join(_gemini_wsl, "antigravity", "conversations"),
        os.path.join(_gemini_wsl, "antigravity-backup", "conversations"),
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini_wsl, "antigravity-ide", "brain"),
        os.path.join(_gemini_wsl, "antigravity", "brain"),
        os.path.join(_gemini_wsl, "antigravity-backup", "brain"),
    ]
elif _SYSTEM == "Darwin":  # macOS
    _home = os.path.expanduser("~")
    _support = os.path.join(_home, "Library", "Application Support")

    _DB_CANDIDATES = (
        os.path.join(_support, "Antigravity IDE", "User", "globalStorage", "state.vscdb"),
        os.path.join(_support, "antigravity", "User", "globalStorage", "state.vscdb"),
    )
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    CONVERSATIONS_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "conversations"),
        os.path.join(_home, ".gemini", "antigravity", "conversations"),
    )
    BRAIN_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "brain"),
        os.path.join(_home, ".gemini", "antigravity", "brain"),
    )
    WORKSPACE_STORAGE_DIR = _first_existing(
        os.path.join(_support, "Antigravity IDE", "User", "workspaceStorage"),
        os.path.join(_support, "antigravity", "User", "workspaceStorage"),
    )
    _gemini_mac = os.path.join(_home, ".gemini")
    _ALL_CONV_DIRS = [
        os.path.join(_gemini_mac, "antigravity-ide", "conversations"),
        os.path.join(_gemini_mac, "antigravity", "conversations"),
        os.path.join(_gemini_mac, "antigravity-backup", "conversations"),
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini_mac, "antigravity-ide", "brain"),
        os.path.join(_gemini_mac, "antigravity", "brain"),
        os.path.join(_gemini_mac, "antigravity-backup", "brain"),
    ]
else:  # Linux and other POSIX systems
    _home = os.path.expanduser("~")
    _config = os.path.join(_home, ".config")

    _DB_CANDIDATES = (
        os.path.join(_config, "Antigravity IDE", "User", "globalStorage", "state.vscdb"),
        os.path.join(_config, "Antigravity", "User", "globalStorage", "state.vscdb"),
    )
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    CONVERSATIONS_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "conversations"),
        os.path.join(_home, ".gemini", "antigravity", "conversations"),
    )
    BRAIN_DIR = _first_existing(
        os.path.join(_home, ".gemini", "antigravity-ide", "brain"),
        os.path.join(_home, ".gemini", "antigravity", "brain"),
    )
    WORKSPACE_STORAGE_DIR = _first_existing(
        os.path.join(_config, "Antigravity IDE", "User", "workspaceStorage"),
        os.path.join(_config, "Antigravity", "User", "workspaceStorage"),
    )
    _gemini_linux = os.path.join(_home, ".gemini")
    _ALL_CONV_DIRS = [
        os.path.join(_gemini_linux, "antigravity-ide", "conversations"),
        os.path.join(_gemini_linux, "antigravity", "conversations"),
        os.path.join(_gemini_linux, "antigravity-backup", "conversations"),
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini_linux, "antigravity-ide", "brain"),
        os.path.join(_gemini_linux, "antigravity", "brain"),
        os.path.join(_gemini_linux, "antigravity-backup", "brain"),
    ]

DB_PATHS = _existing_paths(*_DB_CANDIDATES)
BACKUP_FILENAME = "trajectorySummaries_backup.txt"


def _backup_dir():
    """
    Folder where rollback backups are written.

    When frozen with PyInstaller (--onefile), __file__ points inside the
    temporary _MEIxxxx extraction folder, which Windows deletes the moment
    the exe exits — backups written there are silently destroyed and the
    user has no way to roll back. Use the folder the exe actually lives in.
    Falls back to the current working directory if that folder is not
    writable (e.g. exe launched from a read-only location).
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    if os.access(base, os.W_OK):
        return base
    return os.getcwd()


def _find_brain_path(conversation_id):
    """Return the first existing brain folder for this conversation across all locations."""
    for brain_dir in _ALL_BRAIN_DIRS:
        p = os.path.join(brain_dir, conversation_id)
        if os.path.isdir(p):
            return p
    return None


def _collect_all_conversations():
    """
    Merge conversation files from all folders (new, old, backup).
    Supports both .pb (protobuf, legacy) and .db (SQLite, v2.x+) formats.
    Deduplicates by conversation ID — first seen wins (priority: new > old > backup).
    Returns dict: {conversation_id: full_file_path}
    """
    catalog = {}
    for conv_dir in _ALL_CONV_DIRS:
        if not os.path.isdir(conv_dir):
            continue
        try:
            for name in os.listdir(conv_dir):
                # Accept .pb (legacy protobuf) and .db (new SQLite) files
                # Skip SQLite journal files (.db-shm, .db-wal)
                if name.endswith(".pb"):
                    cid = name[:-3]
                elif name.endswith(".db") and not name.endswith((".db-shm", ".db-wal")):
                    cid = name[:-3]
                else:
                    continue
                if cid not in catalog:
                    catalog[cid] = os.path.join(conv_dir, name)
        except Exception:
            pass
    return catalog


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


# ─── Workspace Helpers ───────────────────────────────────────────────────────

def _is_remote_uri(path_or_uri):
    """Check if a string is already a remote/absolute URI (not a local path)."""
    return path_or_uri.startswith("vscode-remote://") or path_or_uri.startswith("file:///")


def path_to_workspace_uri(folder_path):
    """
    Convert a local folder path to a file:/// URI matching Antigravity's format.
    Passes through remote URIs (vscode-remote://, file:///) unchanged.
    Uses raw paths (no URL-encoding) for clean display in Antigravity's sidebar.
    Example: D:\\Repos\\My Project  →  file:///d:/Repos/My Project
    WSL:     /mnt/c/Users/name/Project → file:///c:/Users/name/Project
    """
    # Pass through URIs that are already in the correct format
    if _is_remote_uri(folder_path):
        return folder_path

    # WSL: convert /mnt/<drive>/... to file:///<drive>:/...
    if _IS_WSL and folder_path.startswith("/mnt/"):
        parts = folder_path.split("/")
        if len(parts) >= 3 and len(parts[2]) == 1:
            drive = parts[2].lower()
            rest = "/".join(parts[3:])
            return f"file:///{drive}:/{rest}"

    p = folder_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:]
    else:
        drive = None
        rest = p

    if drive:
        return f"file:///{drive}:{rest}"
    else:
        return f"file:///{rest.lstrip('/')}"


def build_workspace_field(folder_path):
    """
    Build protobuf field 9 (workspace sub-message) from a filesystem path.
    Sub-message structure:
      sub-field 1 (string) = workspace URI
      sub-field 2 (string) = workspace URI (duplicate)
    Returns raw bytes for one field-9 entry.
    """
    uri = path_to_workspace_uri(folder_path)
    sub_msg = (
        encode_string_field(1, uri)
        + encode_string_field(2, uri)
    )
    return encode_length_delimited(9, sub_msg)


def extract_workspace_hint(inner_blob):
    """
    Try to extract a workspace URI from the protobuf inner blob.
    Scans length-delimited fields for strings matching file:/// or
    vscode-remote:// patterns. Returns the URI string if found, or None.
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


def load_known_workspace_uris():
    """
    Load all known workspace URIs from Antigravity's workspaceStorage.
    Each subfolder contains a workspace.json with a 'folder' or 'workspace' URI.
    Returns a list of URI strings sorted longest-first for prefix matching.
    """
    uris = []
    if not os.path.isdir(WORKSPACE_STORAGE_DIR):
        return uris
    try:
        for name in os.listdir(WORKSPACE_STORAGE_DIR):
            ws_json = os.path.join(WORKSPACE_STORAGE_DIR, name, "workspace.json")
            if os.path.exists(ws_json):
                try:
                    with open(ws_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    uri = data.get("folder") or data.get("workspace")
                    if uri:
                        uris.append(uri)
                except Exception:
                    pass
    except Exception:
        pass
    # Sort longest first so more-specific paths match before parent paths
    uris.sort(key=len, reverse=True)
    return uris


def _uri_to_local_path(file_uri):
    """
    Convert a file:/// URI to a local filesystem path.
    Handles URL-encoding (e.g. %20 -> space, %3A -> colon).
    On WSL, converts file:///C:/... to /mnt/c/...
    Returns None for non-file URIs.
    """
    if not file_uri.startswith("file:///"):
        return None
    raw = unquote(file_uri[len("file://"):])
    # On Windows, file:///C:/... -> C:/...
    if _SYSTEM == "Windows" and len(raw) >= 3 and raw[0] == '/' and raw[2] == ':':
        raw = raw[1:]  # strip leading /
    # On WSL, file:///C:/... -> /mnt/c/...
    elif _IS_WSL and len(raw) >= 3 and raw[0] == '/' and raw[2] == ':':
        drive = raw[1].lower()
        raw = f"/mnt/{drive}{raw[3:]}"
    return raw


def infer_workspace_from_brain(conversation_id, known_ws_uris=None):
    """
    Scan brain .md files for file:/// and vscode-remote:// paths and infer
    the workspace by matching against known workspace URIs.
    Falls back to a heuristic depth-based approach if no known URIs match.
    Returns a filesystem path string, a remote URI string, or None.
    """
    brain_path = _find_brain_path(conversation_id)
    if not brain_path:
        return None

    # Two separate patterns: local file:/// and remote vscode-remote://
    if _SYSTEM == "Windows":
        local_pattern = re.compile(r"file:///([A-Za-z](?:%3A|:)/[^)\s\"'\]>]+)")
    else:
        local_pattern = re.compile(r"file:///([^)\s\"'\]>]+)")
    remote_pattern = re.compile(r"(vscode-remote://[^)\s\"'\]>]+)")

    # Collect all file URIs found in brain .md files
    found_uris = []     # full file:/// URIs
    found_remote = []   # full vscode-remote:// URIs

    try:
        for name in os.listdir(brain_path):
            if not name.endswith(".md") or name.startswith("."):
                continue
            filepath = os.path.join(brain_path, name)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(16384)

                for match in remote_pattern.finditer(content):
                    found_remote.append(match.group(1))

                for match in local_pattern.finditer(content):
                    found_uris.append("file:///" + match.group(1))
            except Exception:
                pass
    except Exception:
        return None

    if not found_uris and not found_remote:
        return None

    # ── Strategy 1: Match against known workspace URIs (preferred) ────────
    if known_ws_uris:
        ws_counts = {}
        for file_uri in found_uris:
            normalized = file_uri.replace("%3A", ":").replace("%3a", ":")
            normalized = normalized.replace("%20", " ")
            for ws_uri in known_ws_uris:
                ws_norm = ws_uri.replace("%3A", ":").replace("%3a", ":")
                ws_norm = ws_norm.replace("%20", " ")
                if normalized.startswith(ws_norm + "/") or normalized == ws_norm:
                    ws_counts[ws_uri] = ws_counts.get(ws_uri, 0) + 1
                    break  # matched most-specific (sorted longest-first)

        for remote_uri in found_remote:
            for ws_uri in known_ws_uris:
                if remote_uri.startswith(ws_uri + "/") or remote_uri == ws_uri:
                    ws_counts[ws_uri] = ws_counts.get(ws_uri, 0) + 1
                    break

        if ws_counts:
            best_ws_uri = max(ws_counts, key=ws_counts.get)
            local = _uri_to_local_path(best_ws_uri)
            if local:
                return local
            return best_ws_uri

    # ── Strategy 2: Fallback — heuristic depth-based approach ─────────────
    path_counts = {}
    for file_uri in found_uris:
        raw = file_uri[len("file:///"):]
        raw = raw.replace("%3A", ":").replace("%3a", ":")
        raw = raw.replace("%20", " ")

        # WSL: normalize Windows drive letters in URIs to /mnt/ paths
        if _IS_WSL and len(raw) >= 2 and raw[1] == ':':
            drive = raw[0].lower()
            raw = f"mnt/{drive}/{raw[3:]}"

        parts = raw.replace("\\", "/").split("/")
        # On Windows paths like C:/Users/name/Desktop/Project → 5 segments.
        # On WSL paths like mnt/c/Users/name/Project → 5 segments.
        # On Linux/Mac like home/user/projects/Project → 4 segments + re-add /.
        if _SYSTEM == "Windows":
            depth = 5
        elif _IS_WSL and raw.startswith("mnt/"):
            depth = 5
        else:
            depth = 4
        if len(parts) >= depth:
            ws = "/".join(parts[:depth])
            if _SYSTEM != "Windows" and not ws.startswith("/"):
                ws = "/" + ws
            path_counts[ws] = path_counts.get(ws, 0) + 1

    for remote_uri in found_remote:
        path_counts[remote_uri] = path_counts.get(remote_uri, 0) + 1

    if not path_counts:
        return None

    best = max(path_counts, key=path_counts.get)
    # Remote URIs are returned as-is; local paths get OS-native separators
    if best.startswith("vscode-remote://"):
        return best
    return best.replace("/", os.sep)


# ─── Timestamp Helpers ───────────────────────────────────────────────────────

def build_timestamp_fields(epoch_seconds):
    """
    Build protobuf timestamp fields 3, 7, and 10 from an epoch timestamp.
    Each is a sub-message with: sub-field 1 (varint) = seconds since epoch.
    Returns raw protobuf bytes containing all three fields.
    """
    seconds = int(epoch_seconds)
    ts_inner = encode_varint((1 << 3) | 0) + encode_varint(seconds)
    return (
        encode_length_delimited(3, ts_inner)
        + encode_length_delimited(7, ts_inner)
        + encode_length_delimited(10, ts_inner)
    )


def has_timestamp_fields(inner_blob):
    """Check if the inner blob already contains timestamp fields (3, 7, or 10)."""
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
    """Keep asking for a folder until user gives a valid one or presses Enter."""
    while True:
        raw = input(prompt_text).strip()
        if raw == "":
            return None
        folder = raw.strip('"').strip("'").rstrip("\\/")
        # Accept remote URIs without filesystem validation
        if _is_remote_uri(folder):
            print(f"    + Mapped remote URI: {folder}")
            return folder
        if os.path.isdir(folder):
            print(f"    + Mapped to {folder}")
            return folder
        else:
            print(f"    x Path not found: {folder}")
            print(f"      (Make sure the folder exists. Try again or press Enter to skip)")


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
    print("  WORKSPACE ASSIGNMENT (optional)")
    print("  " + "=" * 58)
    print(f"  {len(unmapped_entries)} conversation(s) have no workspace.")
    print("  You can assign each to a workspace folder now,")
    print("  or press Enter to skip and leave them unassigned.")
    print()

    assignments = {}
    batch_path = None

    for idx, cid, title in unmapped_entries:
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
            # Normal path entry
            folder = raw.strip('"').strip("'").rstrip("\\/")
            # Accept remote URIs without filesystem validation
            if _is_remote_uri(folder):
                print(f"    + Mapped remote URI: {folder}")
                assignments[cid] = folder
                break
            if os.path.isdir(folder):
                print(f"    + Mapped to {folder}")
                assignments[cid] = folder
                break
            else:
                print(f"    x Path not found: {folder}")
                print(f"      (Try again or press Enter to skip)")

    if assignments:
        print()
        print(f"  + Assigned workspace to {len(assignments)} conversation(s)")
    print()
    return assignments


# ─── Metadata Extraction ─────────────────────────────────────────────────────

def extract_existing_metadata(db_path):
    """
    Read metadata already stored in the database's trajectory data.
    Returns two dicts:
      - titles:      {conversation_id: title}  (real, non-fallback titles)
      - inner_blobs: {conversation_id: raw_inner_protobuf_bytes}
    The inner_blobs contain workspace URIs, timestamps, tool state, etc.
    These are preserved so re-running the script doesn't lose data.
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


def extract_existing_metadata_from_paths(db_paths):
    """
    Read metadata from ALL existing Antigravity databases.
    First DB wins for each conversation ID, so metadata is not overwritten
    by a later DB that might have stale data.
    """
    merged_titles = {}
    merged_inner_blobs = {}
    for db_path in db_paths:
        titles, inner_blobs = extract_existing_metadata(db_path)
        for cid, title in titles.items():
            if cid not in merged_titles:
                merged_titles[cid] = title
        for cid, blob in inner_blobs.items():
            if cid not in merged_inner_blobs:
                merged_inner_blobs[cid] = blob
    return merged_titles, merged_inner_blobs


def write_index_to_database(db_path, encoded_value, backup_suffix):
    """Back up and write the rebuilt trajectory index into one state.vscdb."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT value FROM ItemTable "
        "WHERE key='antigravityUnifiedStateSync.trajectorySummaries'"
    )
    row = cur.fetchone()

    # Timestamped so re-running never overwrites an earlier backup — otherwise
    # a second run would replace the pristine pre-tool state with this tool's
    # own previous output, leaving nothing to roll back to.
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if backup_suffix:
        backup_name = f"trajectorySummaries_backup_{backup_suffix}_{stamp}.txt"
    else:
        backup_name = f"trajectorySummaries_backup_{stamp}.txt"
    backup_path = os.path.join(_backup_dir(), backup_name)
    if row and row[0]:
        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(row[0])
        except OSError as e:
            conn.close()
            raise RuntimeError(
                f"Could not write rollback backup to {backup_path}: {e}\n"
                f"    Refusing to modify the database without a backup."
            )

    if row:
        cur.execute(
            "UPDATE ItemTable SET value=? "
            "WHERE key='antigravityUnifiedStateSync.trajectorySummaries'",
            (encoded_value,)
        )
    else:
        cur.execute(
            "INSERT INTO ItemTable (key, value) "
            "VALUES ('antigravityUnifiedStateSync.trajectorySummaries', ?)",
            (encoded_value,)
        )

    conn.commit()
    conn.close()
    return backup_path if row and row[0] else None


def get_title_from_brain(conversation_id):
    """
    Try to extract a title from brain artifact .md files.
    Returns the first markdown heading found, or None.
    """
    brain_path = _find_brain_path(conversation_id)
    if not brain_path:
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


def resolve_title(conversation_id, existing_titles, pb_path=None):
    """
    Determine the best title for a conversation. Priority:
      1. Existing title from database (canonical Antigravity title)
      2. Brain artifact .md heading (fallback for new/missing conversations)
      3. Fallback: date + short UUID
    Returns (title, source) where source is 'preserved', 'brain', or 'fallback'.
    """
    # Prefer the canonical title Antigravity already has in the database
    if conversation_id in existing_titles:
        return existing_titles[conversation_id], "preserved"

    # Fall back to brain artifact heading for conversations not yet indexed
    brain_title = get_title_from_brain(conversation_id)
    if brain_title:
        return brain_title, "brain"

    conv_file = pb_path
    if not conv_file:
        for conv_dir in _ALL_CONV_DIRS:
            p = os.path.join(conv_dir, f"{conversation_id}.pb")
            if os.path.exists(p):
                conv_file = p
                break
    if conv_file and os.path.exists(conv_file):
        mod_time = time.strftime("%b %d", time.localtime(os.path.getmtime(conv_file)))
        return f"Conversation ({mod_time}) {conversation_id[:8]}", "fallback"

    return f"Conversation {conversation_id[:8]}", "fallback"


# ─── Protobuf Entry Builder ──────────────────────────────────────────────────

def build_trajectory_entry(conversation_id, title, existing_inner_data=None,
                           workspace_path=None, pb_mtime=None):
    """
    Build a single trajectory summary protobuf entry.

    - If existing_inner_data is provided, title (field 1) is replaced but
      ALL other fields (workspace, timestamps, tool state) are preserved.
    - If workspace_path is provided and there is no existing workspace,
      a workspace field (field 9) is injected.
    - If pb_mtime is provided and timestamps are missing,
      timestamp fields (3, 7, 10) are injected for proper sorting.
    """
    if existing_inner_data:
        preserved_fields = strip_field_from_protobuf(existing_inner_data, 1)
        inner_info = encode_string_field(1, title) + preserved_fields

        # Decode %20/%3A in existing workspace URIs so folder names display
        # correctly in Antigravity's sidebar (e.g. "Pine Script Project" not
        # "Pine%20Script%20Project")
        if not workspace_path:
            existing_ws = extract_workspace_hint(inner_info)
            if existing_ws and ("%20" in existing_ws or "%3A" in existing_ws or "%3a" in existing_ws):
                decoded_ws = unquote(existing_ws)
                inner_info = strip_field_from_protobuf(inner_info, 9)
                inner_info += build_workspace_field(decoded_ws)

        # Override workspace if user assigned a new one
        if workspace_path:
            # Strip old workspace (field 9) and inject the new one
            inner_info = strip_field_from_protobuf(inner_info, 9)
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


# ─── Update Check ─────────────────────────────────────────────────────────────

def check_for_updates():
    """
    Check GitHub for a newer release. Non-blocking — silently returns
    on any network error so offline users are not affected.
    """
    try:
        api_url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
        req = Request(api_url, headers={"User-Agent": "AntigravityConversationFix"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "").lstrip("Vv")
        if not tag:
            return

        # Simple numeric comparison (e.g. "1.06" vs "1.07")
        try:
            remote = tuple(int(x) for x in tag.split("."))
            local = tuple(int(x) for x in _CURRENT_VERSION.split("."))
        except ValueError:
            return

        if remote <= local:
            return

        print("  " + "*" * 58)
        print(f"  UPDATE AVAILABLE: v{_CURRENT_VERSION} -> v{tag}")
        print(f"  {_RELEASES_URL}")
        print("  " + "*" * 58)
        print()
        choice = input("  Open download page in browser? (Y/n): ").strip().lower()
        if choice in ("", "y", "yes"):
            webbrowser.open(_RELEASES_URL)
            print("  Opened in browser. You can continue or close this window.")
        print()
    except Exception:
        pass  # No internet, API down, etc. — just continue silently


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 62)
    print(f"   Antigravity Conversation Fix  v{_CURRENT_VERSION}")
    print("   Rebuilds your conversation index — sorted by date")
    print("=" * 62)
    print()

    check_for_updates()

    print("  IMPORTANT: Close Antigravity completely before continuing.")
    print("  If it is open, this fix may be overwritten when the app exits.")
    print()

    # ── Check if Antigravity is running ────────────────────────────────────

    _ag_running = False
    if _SYSTEM == "Windows":
        for exe_name in ("antigravity.exe", "antigravity ide.exe"):
            try:
                result = subprocess.run(
                    ['tasklist', '/FI', f'IMAGENAME eq {exe_name}'],
                    capture_output=True, text=True, creationflags=0x08000000
                )
                if exe_name in result.stdout.lower():
                    _ag_running = True
                    break
            except Exception:
                pass
    elif _IS_WSL:
        # Check the Windows host process first, then Linux processes
        for exe_name in ("antigravity.exe", "antigravity ide.exe"):
            try:
                result = subprocess.run(
                    ['tasklist.exe', '/FI', f'IMAGENAME eq {exe_name}'],
                    capture_output=True, text=True
                )
                if exe_name in result.stdout.lower():
                    _ag_running = True
                    break
            except Exception:
                pass
        if not _ag_running:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'antigravity'],
                    capture_output=True, text=True
                )
                if result.stdout.strip():
                    _ag_running = True
            except Exception:
                pass
    else:
        # Linux / macOS: check for antigravity process
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'antigravity'],
                capture_output=True, text=True
            )
            if result.stdout.strip():
                _ag_running = True
        except Exception:
            pass

    if _ag_running:
        print("  WARNING: Antigravity may still be running!")
        print()
        print("  The fix will NOT work correctly while Antigravity is open.")
        print("  Please close it first: File > Exit, or kill it.")
        print()
        choice = input("  Press Enter to continue anyway (or type Q to quit): ")
        if choice.strip().lower() == 'q':
            return 1
        print()

    # ── Validate paths ──────────────────────────────────────────────────────

    if not DB_PATHS:
        print(f"  ERROR: Database not found at any known Antigravity location:")
        for candidate in _DB_CANDIDATES:
            print(f"    {candidate}")
        print()
        print("  Make sure Antigravity has been installed and opened at least once.")
        input("\n  Press Enter to close...")
        return 1

    # ── Discover conversations (multi-folder merge with dedup) ───────────

    conv_catalog = _collect_all_conversations()

    if not conv_catalog:
        print("  No conversations found on disk. Nothing to fix.")
        input("\n  Press Enter to close...")
        return 0

    # Sort by modification time (newest first)
    conversation_ids = sorted(
        conv_catalog.keys(),
        key=lambda cid: os.path.getmtime(conv_catalog[cid]),
        reverse=True,
    )

    # Show folder scan summary
    dir_counts = {}
    for cid, pb_path in conv_catalog.items():
        parent = os.path.dirname(pb_path)
        dir_counts[parent] = dir_counts.get(parent, 0) + 1
    for d, c in dir_counts.items():
        folder_name = os.path.basename(os.path.dirname(d))  # antigravity-ide, antigravity, etc.
        print(f"    {folder_name}: {c} conversation(s)")
    print(f"  Found {len(conversation_ids)} unique conversations across all folders")
    print()

    # ── Preserve existing metadata ──────────────────────────────────────────

    print("  Reading existing metadata from database(s)...")
    for db_path in DB_PATHS:
        app_name = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(db_path))))
        print(f"    {app_name}: {db_path}")
    existing_titles, existing_inner_blobs = extract_existing_metadata_from_paths(DB_PATHS)
    ws_count = sum(1 for v in existing_inner_blobs.values()
                   if extract_workspace_hint(v))
    print(f"  Found {len(existing_titles)} existing titles to preserve")
    print(f"  Found {ws_count} conversations with workspace metadata")
    print()

    # ── Scan conversations ──────────────────────────────────────────────────

    print("  Scanning conversations (newest first):")
    print("  " + "-" * 58)

    resolved = []  # (cid, title, source, inner_data, has_ws)
    stats = {"brain": 0, "preserved": 0, "fallback": 0}
    markers = {"brain": "+", "preserved": "~", "fallback": "?"}

    for i, cid in enumerate(conversation_ids, 1):
        title, source = resolve_title(cid, existing_titles, conv_catalog.get(cid))
        inner_data = existing_inner_blobs.get(cid)
        has_ws = bool(inner_data and extract_workspace_hint(inner_data))
        resolved.append((cid, title, source, inner_data, has_ws))
        stats[source] += 1
        marker = markers[source]
        ws_flag = " [WS]" if has_ws else ""
        print(f"    [{i:3d}] {marker} {title[:50]}{ws_flag}")

    print("  " + "-" * 58)
    print(f"  Legend: [+] brain  [~] preserved  [?] fallback  [WS] workspace")
    print(f"  Totals: {stats['brain']} brain, {stats['preserved']} preserved, {stats['fallback']} fallback")
    print()

    # ── Workspace assignment ───────────────────────────────────────────────

    unmapped = [(i, cid, title)
                for i, (cid, title, _, inner_data, has_ws) in enumerate(resolved, 1)
                if not has_ws]

    ws_assignments = {}  # cid -> folder_path

    # Load known workspace URIs from workspaceStorage for accurate matching
    known_ws_uris = load_known_workspace_uris()
    if known_ws_uris:
        print(f"  Loaded {len(known_ws_uris)} known workspace(s) from workspaceStorage")
    else:
        print("  No workspaceStorage found — using fallback heuristic")
    print()

    if unmapped:
        print(f"  {len(unmapped)} conversation(s) have no workspace assigned.")
        print()
        print("  Press Enter or 1: Auto-assign workspaces (recommended)")
        print("  Press 2:          Auto-assign + manually assign the rest")
        print()
        choice = input("  Your choice: ").strip()

        # Auto-infer from brain artifacts (both options do this)
        print()
        print("  Auto-assigning workspaces from brain artifacts...")
        auto_count = 0
        for idx, cid, title in unmapped:
            inferred = infer_workspace_from_brain(cid, known_ws_uris)
            if inferred and (_is_remote_uri(inferred) or os.path.isdir(inferred)):
                ws_assignments[cid] = inferred
                auto_count += 1
                display = os.path.basename(inferred) if not _is_remote_uri(inferred) else inferred
                print(f"    [{idx:3d}] -> {display}")
        if auto_count:
            print(f"  Auto-assigned {auto_count} workspace(s)")
        else:
            print("  No workspaces could be auto-detected.")
        print()

        # Option 2: also do manual assignment for the rest
        if choice == '2':
            still_unmapped = [(idx, cid, title)
                              for idx, cid, title in unmapped
                              if cid not in ws_assignments]
            if still_unmapped:
                user_assignments = interactive_workspace_assignment(still_unmapped)
                ws_assignments.update(user_assignments)
            else:
                print("  All conversations were auto-assigned — nothing left to assign manually.")
                print()

    # ── Build the new index ─────────────────────────────────────────────────

    print("  Building final index...")
    result_bytes = b""
    ws_total = 0
    ts_injected = 0

    for cid, title, source, inner_data, has_ws in resolved:
        ws_path = ws_assignments.get(cid)
        pb_path = conv_catalog.get(cid)
        pb_mtime = os.path.getmtime(pb_path) if pb_path and os.path.exists(pb_path) else None

        entry = build_trajectory_entry(cid, title, inner_data, ws_path, pb_mtime)
        result_bytes += encode_length_delimited(1, entry)

        if has_ws or ws_path:
            ws_total += 1
        if pb_mtime and (not inner_data or not has_timestamp_fields(inner_data)):
            ts_injected += 1

    print(f"  Workspace: {ws_total} mapped  |  Timestamps injected: {ts_injected}")
    print()

    # ── Write the rebuilt index to ALL databases ────────────────────────────

    encoded = base64.b64encode(result_bytes).decode('utf-8')

    print("  Writing rebuilt index to database(s):")
    saved_backups = []
    for db_path in DB_PATHS:
        app_name = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(db_path))))
        suffix = re.sub(r"[^A-Za-z0-9]+", "_", app_name).strip("_").lower()
        backup_path = write_index_to_database(db_path, encoded, suffix)
        print(f"    {app_name}: updated")
        if backup_path:
            saved_backups.append(backup_path)
            print(f"      backup: {backup_path}")

    # ── Done ────────────────────────────────────────────────────────────────

    total = len(conversation_ids)
    print()
    print("  " + "=" * 58)
    print(f"  SUCCESS! Rebuilt index with {total} conversations.")
    print("  " + "=" * 58)
    print()
    print("  NEXT STEPS:")
    if _IS_WSL:
        print("    1. Make sure Antigravity is fully closed on the Windows side")
        print("    2. Open Antigravity — conversations should appear sorted by date")
        print("    3. If changes do not appear, reboot and open Antigravity again")
    else:
        print("    1. Make sure Antigravity is fully closed")
        print("    2. Open Antigravity — conversations should appear sorted by date")
        print("    3. If changes do not appear, reboot and open Antigravity again")
    print()
    if saved_backups:
        print("  ROLLBACK: your previous index was saved to")
        print(f"    {_backup_dir()}")
        print("  Keep those .txt files if you may want to undo this.")
        print()
    input("  Press Enter to close...")
    return 0


if __name__ == "__main__":
    sys.exit(main())

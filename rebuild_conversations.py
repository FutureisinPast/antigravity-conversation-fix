#!/usr/bin/env python3
"""
Antigravity Conversation Fix  (v1.07)
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

import argparse
import sqlite3
import base64
import json
import re
import time
import subprocess
import platform
import webbrowser
from urllib.parse import quote, unquote, urlparse
from urllib.request import urlopen, Request

_CURRENT_VERSION = "1.07"
_GITHUB_REPO = "FutureisinPast/antigravity-conversation-fix"
_RELEASES_URL = f"https://github.com/{_GITHUB_REPO}/releases/latest"

# ─── Path Detection ──────────────────────────────────────────────────────────
# Antigravity was renamed to "Antigravity IDE" in a recent update.
# We check all known variants (both casing and naming) across Windows, Linux,
# WSL, and macOS to guarantee full compatibility.

_SYSTEM = platform.system()
_ANTIGRAVITY_NAMES = ("Antigravity IDE", "antigravity", "Antigravity", "antigravity-ide")
_GEMINI_FOLDER_NAMES = ("antigravity-ide", "antigravity", "antigravity-backup", "Antigravity IDE", "Antigravity")


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


def _get_wsl_windows_userprofile():
    """
    Resolve the Windows %USERPROFILE% path from inside WSL.
    Returns a WSL-accessible path string, or None if resolution fails.
    """
    # Strategy 1: cmd.exe %USERPROFILE% → wslpath
    try:
        proc = subprocess.run(
            ['cmd.exe', '/c', 'echo %USERPROFILE%'],
            capture_output=True, text=True, check=True
        )
        win_path = proc.stdout.strip()
        if win_path and win_path != "%USERPROFILE%":
            proc_wsl = subprocess.run(
                ['wslpath', win_path],
                capture_output=True, text=True, check=True
            )
            wsl_path = proc_wsl.stdout.strip()
            if os.path.exists(wsl_path):
                return wsl_path
    except Exception:
        pass

    # Strategy 2: Derive from _get_wsl_windows_appdata()
    appdata = _get_wsl_windows_appdata()
    if appdata:
        profile = os.path.dirname(os.path.dirname(appdata))
        if os.path.exists(profile):
            return profile

    # Strategy 3: Scan /mnt/c/Users/ for user folders that have .gemini
    if os.path.exists("/mnt/c/Users"):
        _skip = {"Default", "Default User", "All Users", "desktop.ini", "Public"}
        try:
            for user in os.listdir("/mnt/c/Users"):
                if user in _skip:
                    continue
                user_path = os.path.join("/mnt/c/Users", user)
                if os.path.isdir(os.path.join(user_path, ".gemini")):
                    return user_path
        except Exception:
            pass

    return None


def _first_existing(*candidates):
    """Return the first path that exists on disk, or the first candidate if none exist."""
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return candidates[0] if candidates else ""


def _existing_paths(*candidates):
    """Return all candidate paths that exist on disk, preserving order and deduplicating."""
    seen = set()
    result = []
    for p in candidates:
        if p and os.path.exists(p) and p not in seen:
            seen.add(p)
            result.append(p)
    return result


if _SYSTEM == "Windows":
    _appdata = os.path.expandvars(r"%APPDATA%")
    _profile = os.path.expandvars(r"%USERPROFILE%")
    _gemini = os.path.join(_profile, ".gemini")

    _DB_CANDIDATES = tuple(
        os.path.join(_appdata, name, "User", "globalStorage", "state.vscdb")
        for name in _ANTIGRAVITY_NAMES
    )
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    CONVERSATIONS_DIR = _first_existing(
        *(os.path.join(_gemini, name, "conversations") for name in _GEMINI_FOLDER_NAMES)
    )
    BRAIN_DIR = _first_existing(
        *(os.path.join(_gemini, name, "brain") for name in _GEMINI_FOLDER_NAMES)
    )
    _ws_candidates = [
        os.path.join(_appdata, name, "User", "workspaceStorage")
        for name in _ANTIGRAVITY_NAMES
    ]
    WORKSPACE_STORAGE_DIR = _first_existing(*_ws_candidates)
    _ALL_WORKSPACE_STORAGE_DIRS = _ws_candidates
    _ALL_CONV_DIRS = [
        os.path.join(_gemini, name, "conversations") for name in _GEMINI_FOLDER_NAMES
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini, name, "brain") for name in _GEMINI_FOLDER_NAMES
    ]
elif _IS_WSL:
    _wsl_appdata = _get_wsl_windows_appdata()
    _wsl_userprofile = _get_wsl_windows_userprofile()
    _home = os.path.expanduser("~")
    _config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home, ".config")

    _db_candidates_list = []
    _ws_storage_candidates_list = []

    # Windows side candidates (if resolved via WSL)
    if _wsl_appdata:
        for name in _ANTIGRAVITY_NAMES:
            _db_candidates_list.append(
                os.path.join(_wsl_appdata, name, "User", "globalStorage", "state.vscdb")
            )
            _ws_storage_candidates_list.append(
                os.path.join(_wsl_appdata, name, "User", "workspaceStorage")
            )

    # Linux side candidates in WSL
    for name in _ANTIGRAVITY_NAMES:
        _db_candidates_list.append(
            os.path.join(_config, name, "User", "globalStorage", "state.vscdb")
        )
        _ws_storage_candidates_list.append(
            os.path.join(_config, name, "User", "workspaceStorage")
        )

    _DB_CANDIDATES = tuple(_db_candidates_list)
    DB_PATH = _first_existing(*_DB_CANDIDATES) if _DB_CANDIDATES else ""
    WORKSPACE_STORAGE_DIR = _first_existing(*_ws_storage_candidates_list) if _ws_storage_candidates_list else ""
    _ALL_WORKSPACE_STORAGE_DIRS = _ws_storage_candidates_list

    _conv_dirs_list = []
    _brain_dirs_list = []

    # Linux side conversations and brain
    for name in _GEMINI_FOLDER_NAMES:
        _conv_dirs_list.append(os.path.join(_home, ".gemini", name, "conversations"))
        _brain_dirs_list.append(os.path.join(_home, ".gemini", name, "brain"))

    # Windows side conversations and brain (if user profile found in WSL)
    if _wsl_userprofile:
        for name in _GEMINI_FOLDER_NAMES:
            _conv_dirs_list.append(os.path.join(_wsl_userprofile, ".gemini", name, "conversations"))
            _brain_dirs_list.append(os.path.join(_wsl_userprofile, ".gemini", name, "brain"))

    CONVERSATIONS_DIR = _first_existing(*_conv_dirs_list)
    BRAIN_DIR = _first_existing(*_brain_dirs_list)
    _ALL_CONV_DIRS = _conv_dirs_list
    _ALL_BRAIN_DIRS = _brain_dirs_list
elif _SYSTEM == "Darwin":  # macOS
    _home = os.path.expanduser("~")
    _support = os.path.join(_home, "Library", "Application Support")

    _DB_CANDIDATES = tuple(
        os.path.join(_support, name, "User", "globalStorage", "state.vscdb")
        for name in _ANTIGRAVITY_NAMES
    )
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    CONVERSATIONS_DIR = _first_existing(
        *(os.path.join(_home, ".gemini", name, "conversations") for name in _GEMINI_FOLDER_NAMES)
    )
    BRAIN_DIR = _first_existing(
        *(os.path.join(_home, ".gemini", name, "brain") for name in _GEMINI_FOLDER_NAMES)
    )
    _ws_candidates = [
        os.path.join(_support, name, "User", "workspaceStorage")
        for name in _ANTIGRAVITY_NAMES
    ]
    WORKSPACE_STORAGE_DIR = _first_existing(*_ws_candidates)
    _ALL_WORKSPACE_STORAGE_DIRS = _ws_candidates
    _gemini_mac = os.path.join(_home, ".gemini")
    _ALL_CONV_DIRS = [
        os.path.join(_gemini_mac, name, "conversations") for name in _GEMINI_FOLDER_NAMES
    ]
    _ALL_BRAIN_DIRS = [
        os.path.join(_gemini_mac, name, "brain") for name in _GEMINI_FOLDER_NAMES
    ]
else:  # Linux and other POSIX systems
    _home = os.path.expanduser("~")
    _config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home, ".config")

    _db_candidates_list = [
        os.path.join(_config, name, "User", "globalStorage", "state.vscdb")
        for name in _ANTIGRAVITY_NAMES
    ]
    _ws_candidates = [
        os.path.join(_config, name, "User", "workspaceStorage")
        for name in _ANTIGRAVITY_NAMES
    ]

    _DB_CANDIDATES = tuple(_db_candidates_list)
    DB_PATH = _first_existing(*_DB_CANDIDATES)
    WORKSPACE_STORAGE_DIR = _first_existing(*_ws_candidates)
    _ALL_WORKSPACE_STORAGE_DIRS = _ws_candidates

    _conv_dirs_list = [
        os.path.join(_home, ".gemini", name, "conversations")
        for name in _GEMINI_FOLDER_NAMES
    ]
    _brain_dirs_list = [
        os.path.join(_home, ".gemini", name, "brain")
        for name in _GEMINI_FOLDER_NAMES
    ]
    CONVERSATIONS_DIR = _first_existing(*_conv_dirs_list)
    BRAIN_DIR = _first_existing(*_brain_dirs_list)
    _ALL_CONV_DIRS = _conv_dirs_list
    _ALL_BRAIN_DIRS = _brain_dirs_list

DB_PATHS = _existing_paths(*_DB_CANDIDATES)
BACKUP_FILENAME = "trajectorySummaries_backup.txt"


def _backup_dir(custom_dir=None):
    """
    Folder where rollback backups are written.

    When frozen with PyInstaller (--onefile), __file__ points inside the
    temporary _MEIxxxx extraction folder, which Windows deletes the moment
    the exe exits — backups written there are silently destroyed and the
    user has no way to roll back. Use the folder the exe actually lives in.
    Falls back to the current working directory if that folder is not
    writable (e.g. exe launched from a read-only location).
    """
    if custom_dir:
        try:
            os.makedirs(custom_dir, exist_ok=True)
            if os.access(custom_dir, os.W_OK):
                return custom_dir
        except Exception:
            pass

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


def _iter_brain_paths(conversation_id):
    """Yield every matching brain folder in configured priority order."""
    for brain_dir in _ALL_BRAIN_DIRS:
        path = os.path.join(brain_dir, conversation_id)
        if os.path.isdir(path):
            yield path


def _collect_all_conversations(conv_dirs=None):
    """
    Merge conversation files from all folders (new, old, backup).
    Supports both .pb (protobuf, legacy) and .db (SQLite, v2.x+) formats.
    Deduplicates by conversation ID — first seen wins (priority: new > old > backup).
    Returns dict: {conversation_id: full_file_path}
    """
    catalog = {}
    dirs_to_scan = conv_dirs if conv_dirs is not None else _ALL_CONV_DIRS
    for conv_dir in dirs_to_scan:
        if not os.path.isdir(conv_dir):
            continue
        try:
            # Choose within a folder first so a .db always beats a legacy .pb
            # for the same ID. Only then merge into the global catalog, keeping
            # the configured new > old > backup directory priority.
            folder_catalog = {}
            for name in sorted(os.listdir(conv_dir)):
                lower_name = name.lower()
                if lower_name.endswith(".db") and not lower_name.endswith((".db-shm", ".db-wal")):
                    cid = name[:-3]
                    folder_catalog[cid] = os.path.join(conv_dir, name)
                elif lower_name.endswith(".pb"):
                    cid = name[:-3]
                    if cid not in folder_catalog:
                        folder_catalog[cid] = os.path.join(conv_dir, name)
            for cid, path in folder_catalog.items():
                if cid not in catalog:
                    catalog[cid] = path
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


def iter_length_delimited_fields(data, target_field_number):
    """Yield well-formed length-delimited values for one protobuf field."""
    pos = 0
    try:
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            field_number, wire_type = tag >> 3, tag & 7
            if wire_type == 2:
                length, pos = decode_varint(data, pos)
                end = pos + length
                if end > len(data):
                    return
                if field_number == target_field_number:
                    yield data[pos:end]
                pos = end
            else:
                new_pos = skip_protobuf_field(data, pos, wire_type)
                if new_pos <= pos or new_pos > len(data):
                    return
                pos = new_pos
    except Exception:
        return


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


def load_known_workspace_uris(storage_dirs=None):
    """
    Load all known workspace URIs from Antigravity's workspaceStorage.
    Each subfolder contains a workspace.json with a 'folder' or 'workspace' URI.
    Returns a list of URI strings sorted longest-first for prefix matching.
    """
    uris = []
    seen = set()
    dirs_to_scan = []
    if storage_dirs:
        dirs_to_scan = list(storage_dirs)
    elif "_ALL_WORKSPACE_STORAGE_DIRS" in globals() and _ALL_WORKSPACE_STORAGE_DIRS:
        dirs_to_scan = _existing_paths(*_ALL_WORKSPACE_STORAGE_DIRS)
    elif WORKSPACE_STORAGE_DIR and os.path.isdir(WORKSPACE_STORAGE_DIR):
        dirs_to_scan = [WORKSPACE_STORAGE_DIR]

    for storage_dir in dirs_to_scan:
        if not storage_dir or not os.path.isdir(storage_dir):
            continue
        try:
            for name in os.listdir(storage_dir):
                ws_json = os.path.join(storage_dir, name, "workspace.json")
                if os.path.exists(ws_json):
                    try:
                        with open(ws_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        uri = data.get("folder") or data.get("workspace")
                        if uri and uri not in seen:
                            seen.add(uri)
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


def build_timestamp_field(field_number, epoch_seconds):
    """Build one protobuf timestamp field with seconds in nested field 1."""
    ts_inner = encode_varint((1 << 3) | 0) + encode_varint(int(epoch_seconds))
    return encode_length_delimited(field_number, ts_inner)


def extract_timestamp_seconds(inner_blob, field_number=3):
    """Return the newest seconds value found in a repeated timestamp field."""
    values = []
    for timestamp_blob in iter_length_delimited_fields(inner_blob or b"", field_number):
        pos = 0
        try:
            while pos < len(timestamp_blob):
                tag, pos = decode_varint(timestamp_blob, pos)
                nested_field, wire_type = tag >> 3, tag & 7
                if nested_field == 1 and wire_type == 0:
                    seconds, pos = decode_varint(timestamp_blob, pos)
                    values.append(seconds)
                    break
                new_pos = skip_protobuf_field(timestamp_blob, pos, wire_type)
                if new_pos <= pos or new_pos > len(timestamp_blob):
                    break
                pos = new_pos
        except Exception:
            continue
    return max(values) if values else None


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

def _is_generated_fallback_title(title, conversation_id):
    """Return True only for this tool's exact date/ID fallback formats."""
    short_id = re.escape(conversation_id[:8])
    return bool(
        re.fullmatch(r"Conversation \([A-Z][a-z]{2} \d{2}\) " + short_id, title)
        or title == "Conversation " + conversation_id[:8]
    )


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
                    if not _is_generated_fallback_title(title, uid):
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


def _open_sqlite_readonly(db_path):
    """Open an existing SQLite file without allowing SQLite to create it."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    absolute = os.path.abspath(db_path).replace("\\", "/")
    uri = "file:" + quote(absolute, safe="/:") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        conn.close()
        raise
    return conn


def _quote_sqlite_identifier(identifier):
    """Safely quote a SQLite table or column identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _find_step_table(conn):
    """Return the quoted step table name and whether it has an idx column."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    for (table_name,) in cursor.fetchall():
        quoted = _quote_sqlite_identifier(table_name)
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(" + quoted + ")").fetchall()
        }
        if {"step_type", "step_payload"}.issubset(columns):
            return quoted, "idx" in columns
    return None, False


def normalize_prompt_text(prompt):
    """Normalize prompt whitespace without otherwise rewriting its contents."""
    if not isinstance(prompt, str):
        return ""
    return re.sub(r"\s+", " ", prompt).strip()


def _collapse_prompt_urls(prompt):
    """Replace HTTP(S) URLs with their hostnames, retaining sentence punctuation."""
    def replace_url(match):
        raw_url = match.group(0)
        url = raw_url
        trailing = ""
        while url and url[-1] in ".,!?;:)]}":
            trailing = url[-1] + trailing
            url = url[:-1]
        hostname = urlparse(url).hostname
        return (hostname or url) + trailing

    return re.sub(r"https?://[^\s<>()]+", replace_url, prompt, flags=re.IGNORECASE)


def _truncate_prompt_title(text, max_chars=60, max_words=10):
    """Return a word-boundary title within both limits, adding an ellipsis."""
    words = text.split()
    truncated = len(words) > max_words
    words = words[:max_words]
    result = ""
    for word in words:
        candidate = word if not result else result + " " + word
        if len(candidate) > max_chars:
            truncated = True
            break
        result = candidate
    if not result and text:
        result = text[:max_chars].rstrip()
        truncated = len(text) > len(result)
    if truncated:
        result = result.rstrip(".,;:!?")
        while result and len(result) + 1 > max_chars:
            if " " in result:
                result = result.rsplit(" ", 1)[0]
            else:
                result = result[:max_chars - 1].rstrip()
                break
        result += "…"
    return result


def format_prompt_title(prompt):
    """Create a concise, deterministic title from a user prompt."""
    normalized = normalize_prompt_text(prompt)
    if not normalized:
        return None
    normalized = normalize_prompt_text(_collapse_prompt_urls(normalized))
    normalized = re.sub(r"^#{1,6}\s+", "", normalized)

    sentence_match = re.match(r"^(.+?[.!?])(?:\s|$)", normalized)
    if sentence_match:
        first_sentence = sentence_match.group(1).strip()
        if len(first_sentence) <= 60 and len(first_sentence.split()) <= 10:
            return first_sentence

    return _truncate_prompt_title(normalized) or None


def extract_prompt_from_conversation_db(db_path):
    """
    Read the first valid user prompt from a v2 conversation database.

    Prompt text is protobuf field 2 nested inside field 19 of an ordered
    step_type=14 payload. Conversation databases are opened read-only.
    """
    conn = None
    try:
        conn = _open_sqlite_readonly(db_path)
        table, has_idx = _find_step_table(conn)
        if not table:
            return None
        order_column = _quote_sqlite_identifier("idx") if has_idx else "rowid"
        rows = conn.execute(
            "SELECT step_payload FROM " + table
            + " WHERE step_type=? ORDER BY " + order_column,
            (14,),
        )
        for row in rows:
            if not row or row[0] is None:
                continue
            payload = row[0]
            if isinstance(payload, memoryview):
                payload = payload.tobytes()
            if not isinstance(payload, bytes):
                continue
            for title_container in iter_length_delimited_fields(payload, 19):
                for title_bytes in iter_length_delimited_fields(title_container, 2):
                    try:
                        prompt = title_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    prompt = normalize_prompt_text(prompt)
                    if prompt:
                        return prompt
        return None
    except (OSError, sqlite3.Error):
        return None
    finally:
        if conn is not None:
            conn.close()


def extract_title_from_conversation_db(db_path):
    """Derive a concise title from the first valid v2 user prompt."""
    return format_prompt_title(extract_prompt_from_conversation_db(db_path))


_USER_REQUEST_TYPES = {"USER_EXPLICIT", "USER_INPUT"}
_REQUEST_TYPE_KEYS = (
    "type", "request_type", "message_type", "event_type", "kind", "role",
)
_REQUEST_TEXT_KEYS = ("text", "content", "message", "request", "prompt", "input", "query")
_REQUEST_CONTAINER_KEYS = ("data", "payload", "event", "details")


def _unwrap_user_request_text(value):
    """Remove Antigravity's request envelope while tolerating truncated logs."""
    if not isinstance(value, str):
        return ""
    opening = "<USER_REQUEST>"
    start = value.find(opening)
    if start >= 0:
        value = value[start + len(opening):]
        end_positions = [
            value.find(marker)
            for marker in (
                "</USER_REQUEST>",
                "<ADDITIONAL_METADATA>",
                "<USER_SETTINGS_CHANGE>",
                "<truncated",
            )
            if value.find(marker) >= 0
        ]
        if end_positions:
            value = value[:min(end_positions)]
    return normalize_prompt_text(value)


def _text_from_request_value(value):
    """Extract text from common transcript content shapes."""
    if isinstance(value, str):
        return _unwrap_user_request_text(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _text_from_request_value(item)
            if text:
                parts.append(text)
        return normalize_prompt_text(" ".join(parts))
    if isinstance(value, dict):
        for key in _REQUEST_TEXT_KEYS:
            if key in value:
                text = _text_from_request_value(value[key])
                if text:
                    return text
    return ""


def _request_text_from_json(node):
    """Find the first explicitly tagged user request in a JSON value."""
    if isinstance(node, dict):
        request_type = None
        for key in _REQUEST_TYPE_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.upper() in _USER_REQUEST_TYPES:
                request_type = value.upper()
                break
        if request_type:
            for key in _REQUEST_TEXT_KEYS + _REQUEST_CONTAINER_KEYS:
                if key in node:
                    text = _text_from_request_value(node[key])
                    if text and text.upper() not in _USER_REQUEST_TYPES:
                        return text
        for value in node.values():
            text = _request_text_from_json(value)
            if text:
                return text
    elif isinstance(node, list):
        for value in node:
            text = _request_text_from_json(value)
            if text:
                return text
    return None


def _extract_request_from_log(log_path):
    """Read a transcript/overview line by line and return its first user request."""
    marker_pattern = re.compile(r"\b(?:USER_EXPLICIT|USER_INPUT)\b", re.IGNORECASE)
    waiting_for_text = False
    is_jsonl = log_path.lower().endswith(".jsonl")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
            for raw_line in log_file:
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    parsed = json.loads(line)
                except (TypeError, ValueError):
                    parsed = None
                if parsed is not None:
                    text = _request_text_from_json(parsed)
                    if text:
                        return text
                elif is_jsonl:
                    # A partially written JSONL record must not be mistaken for
                    # plaintext. Skip it and keep scanning later complete rows.
                    continue

                marker = marker_pattern.search(line)
                if marker:
                    tail = line[marker.end():].lstrip(" \t:=-|]})\"")
                    tail = tail.rstrip(" \t,\"")
                    if tail:
                        return normalize_prompt_text(tail)
                    waiting_for_text = True
                    continue

                if waiting_for_text:
                    key_value = re.match(
                        r"^(?:text|content|message|request|prompt|input|query)\s*[:=]\s*(.+)$",
                        line,
                        flags=re.IGNORECASE,
                    )
                    candidate = key_value.group(1) if key_value else line
                    candidate = candidate.strip(" \t,\"")
                    candidate = normalize_prompt_text(candidate)
                    if candidate:
                        return candidate
    except OSError:
        return None
    return None


def get_title_from_brain_request(conversation_id):
    """Recover a legacy title from the first readable user request artifact."""
    for brain_path in _iter_brain_paths(conversation_id):
        logs_path = os.path.join(brain_path, ".system_generated", "logs")
        for filename in ("transcript.jsonl", "overview.txt"):
            log_path = os.path.join(logs_path, filename)
            if not os.path.isfile(log_path):
                continue
            prompt = _extract_request_from_log(log_path)
            title = format_prompt_title(prompt)
            if title:
                return title
    return None


def get_title_from_brain(conversation_id):
    """
    Try to extract a title from brain artifact .md files.
    Returns the first markdown heading found, or None.
    """
    for brain_path in _iter_brain_paths(conversation_id):
        for item in sorted(os.listdir(brain_path)):
            if item.startswith('.') or not item.endswith('.md'):
                continue
            try:
                filepath = os.path.join(brain_path, item)
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for line_number, line in enumerate(f):
                        if line_number >= 100:
                            break
                        heading = line.strip()
                        if heading.startswith('#'):
                            return heading.lstrip('# ').strip()[:80]
            except Exception:
                pass

    return None


def resolve_title(conversation_id, existing_titles, conversation_path=None):
    """
    Determine the best title for a conversation. Priority:
      1. Existing title from database (canonical Antigravity title), unless it
         exactly matches the raw title written by the earlier v1.07 build
      2. Brain artifact .md heading (fallback for new/missing conversations)
      3. A concise title derived from a v2 prompt or readable legacy log
      4. Fallback: date + short UUID
    Returns (title, source), with source identifying the selected title source.
    """
    conv_file = conversation_path
    if not conv_file:
        for conv_dir in _ALL_CONV_DIRS:
            db_path = os.path.join(conv_dir, f"{conversation_id}.db")
            pb_path = os.path.join(conv_dir, f"{conversation_id}.pb")
            if os.path.exists(db_path):
                conv_file = db_path
                break
            if os.path.exists(pb_path):
                conv_file = pb_path
                break

    db_prompt = None
    if conv_file and conv_file.lower().endswith(".db"):
        db_prompt = extract_prompt_from_conversation_db(conv_file)

    # Preserve canonical Antigravity titles. The sole migration exception is
    # the exact normalized-prompt[:80] value written by the first v1.07 build.
    existing_title = existing_titles.get(conversation_id)
    if existing_title is not None:
        old_v107_title = db_prompt[:80] if db_prompt else None
        if old_v107_title is not None and existing_title == old_v107_title:
            migrated_title = format_prompt_title(db_prompt)
            if migrated_title and migrated_title != existing_title:
                return migrated_title, "conversation"
        return existing_title, "preserved"

    # Fall back to brain artifact heading for conversations not yet indexed
    brain_title = get_title_from_brain(conversation_id)
    if brain_title:
        return brain_title, "brain"

    if db_prompt:
        conversation_title = format_prompt_title(db_prompt)
        if conversation_title:
            return conversation_title, "conversation"
    if conv_file and conv_file.lower().endswith(".pb"):
        conversation_title = get_title_from_brain_request(conversation_id)
        if conversation_title:
            return conversation_title, "conversation"
    if conv_file and os.path.exists(conv_file):
        mod_time = time.strftime("%b %d", time.localtime(os.path.getmtime(conv_file)))
        return f"Conversation ({mod_time}) {conversation_id[:8]}", "fallback"

    return f"Conversation {conversation_id[:8]}", "fallback"


# ─── Protobuf Entry Builder ──────────────────────────────────────────────────

def build_trajectory_entry(conversation_id, title, existing_inner_data=None,
                           workspace_path=None, conversation_mtime=None,
                           source_is_db=False):
    """
    Build a single trajectory summary protobuf entry.

    - If existing_inner_data is provided, title (field 1) is replaced but
      ALL other fields (workspace, timestamps, tool state) are preserved.
    - If workspace_path is provided and there is no existing workspace,
      a workspace field (field 9) is injected.
    - Existing .db entries refresh only updated-at field 3 when the source file
      is newer. Existing .pb entries retain their current timestamps.
    - New entries receive timestamp fields 3, 7, and 10 from the source mtime.
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
        if source_is_db and conversation_mtime:
            indexed_mtime = extract_timestamp_seconds(existing_inner_data, 3)
            if indexed_mtime is None or int(conversation_mtime) > indexed_mtime:
                inner_info = strip_field_from_protobuf(inner_info, 3)
                inner_info += build_timestamp_field(3, conversation_mtime)
        elif conversation_mtime and not has_timestamp_fields(existing_inner_data):
            inner_info += build_timestamp_fields(conversation_mtime)
    else:
        inner_info = encode_string_field(1, title)
        if workspace_path:
            inner_info += build_workspace_field(workspace_path)
        if conversation_mtime:
            inner_info += build_timestamp_fields(conversation_mtime)

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

def is_antigravity_running():
    """Return True when an Antigravity desktop process is detected."""
    if _SYSTEM == "Windows":
        for exe_name in ("antigravity.exe", "antigravity ide.exe"):
            try:
                result = subprocess.run(
                    ['tasklist', '/FI', f'IMAGENAME eq {exe_name}'],
                    capture_output=True, text=True, creationflags=0x08000000
                )
                if exe_name in result.stdout.lower():
                    return True
            except Exception:
                pass
        return False

    if _IS_WSL:
        for exe_name in ("antigravity.exe", "antigravity ide.exe"):
            try:
                result = subprocess.run(
                    ['tasklist.exe', '/FI', f'IMAGENAME eq {exe_name}'],
                    capture_output=True, text=True
                )
                if exe_name in result.stdout.lower():
                    return True
            except Exception:
                pass

    # Exact process-name checks avoid matching this script, its repository
    # path, or another command line that merely contains "antigravity".
    for process_name in (
        "antigravity", "Antigravity", "antigravity-ide",
        "antigravity ide", "Antigravity IDE",
    ):
        try:
            result = subprocess.run(
                ['pgrep', '-x', process_name], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
    return False


def parse_args(argv=None):
    """Parse command-line arguments for rebuild_conversations."""
    parser = argparse.ArgumentParser(
        prog="rebuild_conversations.py",
        description="Rebuilds the Antigravity conversation index so all chat history appears correctly sorted by date.",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force rebuild even if Antigravity is running (use with caution; reopen app afterward)",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Simulate the rebuild and show resolved titles/workspaces without modifying databases",
    )
    parser.add_argument(
        "-y", "--yes", "--auto",
        dest="auto",
        action="store_true",
        help="Auto-assign detected workspaces from brain artifacts without interactive prompts",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not pause for user input on finish (ideal for automated scripts and headless runs)",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip checking GitHub for newer releases",
    )
    parser.add_argument(
        "--db",
        dest="custom_dbs",
        action="append",
        help="Explicit path to Antigravity state.vscdb (can be specified multiple times)",
    )
    parser.add_argument(
        "--conv-dir",
        dest="custom_conv_dirs",
        action="append",
        help="Explicit conversation directory to scan (can be specified multiple times)",
    )
    parser.add_argument(
        "--brain-dir",
        dest="custom_brain_dirs",
        action="append",
        help="Explicit brain directory to scan (can be specified multiple times)",
    )
    parser.add_argument(
        "--backup-dir",
        dest="custom_backup_dir",
        help="Directory to save rollback backups",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"Antigravity Conversation Fix v{_CURRENT_VERSION}",
    )
    return parser.parse_args(argv)


def main(opts=None):
    if opts is None:
        opts = argparse.Namespace(
            force=False,
            dry_run=False,
            auto=False,
            non_interactive=False,
            no_update_check=False,
            custom_dbs=None,
            custom_conv_dirs=None,
            custom_brain_dirs=None,
            custom_backup_dir=None,
        )

    print()
    print("=" * 62)
    print(f"   Antigravity Conversation Fix  v{_CURRENT_VERSION}")
    print("   Rebuilds your conversation index — sorted by date")
    if getattr(opts, "dry_run", False):
        print("   >>> DRY RUN MODE - No database changes will be made <<<")
    print("=" * 62)
    print()

    print("  IMPORTANT: Close Antigravity completely before continuing.")
    print("  If it is open, this fix may be overwritten when the app exits.")
    print()

    # ── Check if Antigravity is running ────────────────────────────────────
    is_running = is_antigravity_running()
    if is_running:
        if getattr(opts, "force", False):
            print("  WARNING: Antigravity is currently running, but --force was given.")
            print("  Proceeding anyway. Ensure you restart Antigravity afterward.")
            print()
        else:
            print("  ERROR: Antigravity is still running.")
            print()
            print("  No files or databases were changed.")
            print("  Please close it first: File > Exit, or kill it.")
            print("  (Pass --force / -f to override this check if necessary.)")
            print()
            return 1

    if not getattr(opts, "no_update_check", False):
        check_for_updates()

    # ── Validate paths ──────────────────────────────────────────────────────
    active_db_paths = (
        _existing_paths(*opts.custom_dbs)
        if getattr(opts, "custom_dbs", None)
        else DB_PATHS
    )

    if not active_db_paths and not getattr(opts, "dry_run", False):
        print(f"  ERROR: Database not found at any known Antigravity location:")
        for candidate in _DB_CANDIDATES:
            print(f"    {candidate}")
        print()
        print("  Make sure Antigravity has been installed and opened at least once.")
        return 1

    # ── Discover conversations (multi-folder merge with dedup) ───────────
    active_conv_dirs = (
        opts.custom_conv_dirs
        if getattr(opts, "custom_conv_dirs", None)
        else _ALL_CONV_DIRS
    )
    conv_catalog = _collect_all_conversations(active_conv_dirs)

    if not conv_catalog:
        print("  No conversations found on disk. Nothing to fix.")
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
        print(f"    {folder_name} ({d}): {c} conversation(s)")
    print(f"  Found {len(conversation_ids)} unique conversations across all folders")
    print()

    # ── Preserve existing metadata ──────────────────────────────────────────
    print("  Reading existing metadata from database(s)...")
    for db_path in active_db_paths:
        app_name = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(db_path))))
        print(f"    {app_name}: {db_path}")
    existing_titles, existing_inner_blobs = extract_existing_metadata_from_paths(active_db_paths)
    ws_count = sum(1 for v in existing_inner_blobs.values()
                   if extract_workspace_hint(v))
    print(f"  Found {len(existing_titles)} existing titles to preserve")
    print(f"  Found {ws_count} conversations with workspace metadata")
    print()

    # ── Scan conversations ──────────────────────────────────────────────────
    print("  Scanning conversations (newest first):")
    print("  " + "-" * 58)

    resolved = []  # (cid, title, source, inner_data, has_ws)
    stats = {"brain": 0, "preserved": 0, "conversation": 0, "fallback": 0}
    markers = {"brain": "+", "preserved": "~", "conversation": "=", "fallback": "?"}

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
    print("  Legend: [+] brain  [~] preserved  [=] conversation  [?] fallback  [WS] workspace")
    print(
        f"  Totals: {stats['brain']} brain, {stats['preserved']} preserved, "
        f"{stats['conversation']} conversation, {stats['fallback']} fallback"
    )
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

        auto_mode = getattr(opts, "auto", False)
        if auto_mode:
            choice = "1"
            print("  Auto-assigning workspaces from brain artifacts (--auto/--yes enabled)...")
        else:
            print("  Press Enter or 1: Auto-assign workspaces (recommended)")
            print("  Press 2:          Auto-assign + manually assign the rest")
            print()
            try:
                choice = input("  Your choice: ").strip()
            except (EOFError, OSError):
                choice = "1"

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
    ts_updated = 0

    for cid, title, source, inner_data, has_ws in resolved:
        ws_path = ws_assignments.get(cid)
        conversation_path = conv_catalog.get(cid)
        conversation_mtime = (
            os.path.getmtime(conversation_path)
            if conversation_path and os.path.exists(conversation_path) else None
        )
        source_is_db = bool(
            conversation_path and conversation_path.lower().endswith(".db")
        )
        old_timestamp = extract_timestamp_seconds(inner_data, 3) if inner_data else None

        entry = build_trajectory_entry(
            cid, title, inner_data, ws_path, conversation_mtime, source_is_db
        )
        result_bytes += encode_length_delimited(1, entry)

        if has_ws or ws_path:
            ws_total += 1
        if conversation_mtime and (
            not inner_data
            or (source_is_db and (old_timestamp is None or int(conversation_mtime) > old_timestamp))
            or (not source_is_db and not has_timestamp_fields(inner_data))
        ):
            ts_updated += 1

    print(f"  Workspace: {ws_total} mapped  |  Timestamps added/refreshed: {ts_updated}")
    print()

    # ── Write the rebuilt index to ALL databases (or dry run) ───────────────
    if getattr(opts, "dry_run", False):
        print("  [DRY RUN] Verification successful. Rebuilt payload is valid.")
        print(f"  [DRY RUN] Would update {len(active_db_paths)} database(s) with {len(conversation_ids)} conversation(s).")
        return 0

    encoded = base64.b64encode(result_bytes).decode('utf-8')

    print("  Writing rebuilt index to database(s):")
    saved_backups = []
    backup_target = getattr(opts, "custom_backup_dir", None)
    for db_path in active_db_paths:
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
        print("    1. Make sure Antigravity is fully closed (both Windows and WSL)")
        print("    2. Open Antigravity — conversations should appear sorted by date")
        print("    3. If changes do not appear, restart Antigravity or reboot")
    else:
        print("    1. Make sure Antigravity is fully closed")
        print("    2. Open Antigravity — conversations should appear sorted by date")
        print("    3. If changes do not appear, restart Antigravity or reboot")
    print()
    if saved_backups:
        print("  ROLLBACK: your previous index was saved to")
        print(f"    {_backup_dir(backup_target)}")
        print("  Keep those .txt files if you may want to undo this.")
        print()
    return 0


def run_cli(argv=None):
    """Run the CLI command, handling arguments and exit prompt behavior."""
    if argv is None:
        argv = []

    try:
        opts = parse_args(argv)
    except SystemExit as e:
        return e.code

    try:
        result = main(opts)
    except Exception as error:
        print()
        print("  UNEXPECTED ERROR: " + str(error))
        print("  The repair did not complete. Check any backup path shown above.")
        result = 1

    if not getattr(opts, "non_interactive", False):
        try:
            input("\n  Finished. Press Enter to close...")
        except (EOFError, OSError, KeyboardInterrupt):
            pass

    return result


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))

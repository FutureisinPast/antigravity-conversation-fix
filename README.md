# Antigravity Conversation Fix

Your Antigravity conversation history disappeared? Conversations showing in the wrong order? Titles replaced with placeholder text? Workspace assignments lost? This tool fixes all of that.

## ⚡ Quick Start (Windows)

1. **Close Antigravity** completely (File → Exit or kill from Task Manager)
2. Download **`Antigravity_Conversation_Fix.exe`** from the [Releases](../../releases) page
3. Double-click it — a terminal window will open
4. The tool scans your conversations, rebuilds the index, and shows you the results
5. When prompted for workspace assignment, choose an option:
   - **Press Enter or 1** — auto-assigns workspaces from your brain files *(recommended)*
   - **Press 2** — auto-assigns first, then lets you manually assign any remaining conversations
6. Restart your PC, then open Antigravity — your conversations are back, sorted by date

### Advanced: Auto-Sync Background Daemon (Linux only)

Tired of having to run the script manually? v1.05 includes an **Auto-Sync Daemon** (`antigravity_watcher.py`) perfectly suited for Linux environments.
Once setup, it starts automatically on login, watches for new conversations hidden off by Antigravity, and seamlessly runs a non-interactive synchronization (`--auto`) whenever the Antigravity app is closed.

1. Create a Systemd user service:
   `~/.config/systemd/user/antigravity-sync.service`
2. Enable and start: `systemctl --user enable --now antigravity-sync.service`

> **No Python or developer tools required (for the main exe).** Just download, run, done.

## What It Fixes

| Problem | Fixed? |
|---|---|
| Conversations missing from sidebar | ✅ |
| Conversations in wrong order | ✅ Sorted newest first |
| Placeholder titles instead of real names | ✅ Restores from brain artifacts |
| Titles lost after previous fix attempts | ✅ Preserves existing titles |
| Workspace assignments stripped on rebuild | ✅ Preserves workspace metadata *(v1.01+)* |
| Lost workspace assignments (v1.0 damage) | ✅ Auto-recovers from brain artifacts *(v1.03+)* |
| Missing timestamps causing wrong sort | ✅ Injects timestamps from file dates *(v1.03+)* |
| Remote workspaces (WSL/SSH/Docker) not recognized | ✅ Full `vscode-remote://` support *(v1.04+)* |

## How It Works

Antigravity stores conversation data in two places:

- **Conversation files** (`*.pb`) — stored in your user profile
- **Sidebar index** — a SQLite database in your app data folder

| OS | Conversations | Database |
|---|---|---|
| Windows | `%USERPROFILE%\.gemini\antigravity\conversations\` | `%APPDATA%\antigravity\...\state.vscdb` |
| macOS | `~/.gemini/antigravity/conversations/` | `~/Library/Application Support/antigravity/.../state.vscdb` |
| Linux | `~/.gemini/antigravity/conversations/` | `~/.config/Antigravity/.../state.vscdb` |

When the index gets corrupted, conversations still exist on disk but don't show up in the sidebar. This tool scans your conversation files, sorts them by date, pulls titles from brain artifacts, and writes a clean index back to the database.

**Title resolution priority:**
1. Brain artifact `.md` headings (best source)
2. Titles already in the database (preserved across re-runs)
3. Fallback: `Conversation (date) short-id`

## Output Legend

| Marker | Meaning |
|---|---|
| `[+]` | Title extracted from brain artifact |
| `[~]` | Title preserved from existing database |
| `[?]` | Fallback title (no source available) |
| `[WS]` | Workspace metadata preserved or recovered |

## Changelog

### v1.05
- **New:** **Automated Background Sync** — Added `antigravity_watcher.py` to optionally run as a background service (e.g. systemd). It watches the directories for changes and automatically rebuilds the index (`--auto`) when Antigravity is closed.
- **New:** **Deep Scanning** — Added support for scanning implicit (background task) and backup folders, often doubling the amount of conversations successfully recovered.
- **Fix:** Used exact binary matching (`pgrep -x`) on Linux/macOS to avoid false positive matches where the script would detect itself as "running" and abort.
- **Fix:** Guarded `input()` pauses with process-safe checks to allow truly non-interactive automated executions.

### v1.04
- **New:** **Remote workspace support** — now correctly handles `vscode-remote://` URIs for WSL, SSH, and Docker workspaces. Remote paths are detected during auto-assignment and accepted during manual assignment without local filesystem validation.

### v1.03
- **New:** **Workspace auto-recovery** — scans your brain artifact `.md` files for project paths and automatically re-assigns lost workspace mappings. If you ran v1.0 and lost your workspace assignments, this version can recover most of them automatically.
- **New:** **Workspace assignment menu** — choose between auto-assigning only (Option 1) or auto-assigning plus manual interactive prompts for any remaining unmapped conversations (Option 2). Supports batch assignment (`all`) for quick setup.
- **New:** **Timestamp injection** — injects proper timestamps (created/updated) into conversations that are missing them, ensuring Antigravity sorts everything correctly by date.
- **Fix:** Workspace URIs now properly URL-encode spaces and special characters (e.g. `My Project` → `My%20Project`).
- **Fix:** Cross-platform process detection — Linux/macOS now properly checks if Antigravity is running.

### v1.02
- **New:** Cross-platform support — the Python script now works on **macOS** and **Linux** in addition to Windows. The `.exe` remains Windows-only.

### v1.01
- **Fix:** Workspace assignments are now preserved when rebuilding the index. Previously, running the tool would strip conversations from their assigned workspace.
- **Note:** If you ran v1.0 and lost workspace assignments, those must be manually re-assigned inside Antigravity. v1.01 prevents this from happening on future runs.

### v1.0
- Initial release — restores missing conversations, sorts by date, fixes titles.

## Advanced: Run from Source (Mac / Linux / Windows)

If you prefer running the Python script directly, or if you are on **Mac** or **Linux** (which cannot run `.exe` files):

```bash
python rebuild_conversations.py
```

Requires Python 3.7+ with no external packages. The script automatically detects your operating system and finds the correct `antigravity` folders.

## Safety

- **Automatic backup** — your current index is saved to `trajectorySummaries_backup.txt` before any changes
- **Non-destructive** — conversation files (`*.pb`) are never modified, only the sidebar index is rebuilt
- **Metadata-preserving** — workspace assignments, timestamps, and other internal state are retained *(v1.01+)*
- **Idempotent** — safe to run multiple times

⚠️ Antivirus false positive: The .exe may be flagged only by 2 out of 72 engines on VirusTotal — both low-tier (SecureAge, Bkav). This is a known PyInstaller false positive: the bundler extracts Python to a temp folder at runtime, which triggers generic heuristic rules. All major engines (Windows Defender, Kaspersky, ESET, Bitdefender, Norton, etc.) pass it as clean. The source code is fully open — review it yourself if in doubt, or simply use the .py version.

## FAQ

**Q: Do I really need to restart my PC?**
A: A full restart is the safest way to ensure Antigravity picks up the changes. In most cases, simply closing and reopening Antigravity works too.

**Q: Why do some titles show as "Conversation (Mar 10) abc12345"?**
A: Those conversations don't have brain artifacts, and their original titles weren't in the database. Future re-runs will preserve any titles the app generates going forward.

**Q: Can I run this while Antigravity is open?**
A: The tool will detect if Antigravity is running and warn you. It's recommended to close it first so the app doesn't overwrite your fix when it exits.

**Q: I ran v1.0 and my workspace chats were removed. Can I get them back?**
A: Yes! v1.03+ can auto-recover most workspace assignments by scanning your brain artifact files. When prompted, press Enter or 1 for auto-assignment. If some conversations can't be auto-detected, choose option 2 to manually assign them.

**Q: I use WSL / SSH / Docker remote workspaces. Will this work?**
A: Yes! v1.04+ fully supports `vscode-remote://` URIs. The tool auto-detects remote workspace paths from your brain artifacts and accepts them during manual assignment.

## License

MIT — free to use, share, and modify.

---

**⭐ If this fixed your conversations, please star the repo so others can find it!**

# Antigravity Conversation Fix

Your Antigravity conversation history disappeared? Conversations showing in the wrong order? Titles replaced with placeholder text? Workspace assignments lost? This tool fixes all of that.

##  Quick Start (Windows)

1. **Close Antigravity** completely (File → Exit or kill from Task Manager)
2. Download **`Antigravity_Conversation_Fix.exe`** from the [Releases](../../releases) page
3. Double-click it — a terminal window will open
4. The tool scans your conversations, rebuilds the index, and shows you the results
5. When prompted for workspace assignment, choose an option:
   - **Press Enter or 1** — auto-assigns workspaces from your brain files *(recommended)*
   - **Press 2** — auto-assigns first, then lets you manually assign any remaining conversations
6. Open Antigravity — your conversations are back, sorted by date. A PC restart is normally not required if Antigravity was fully closed; reboot only if the changes do not appear.

> **No Python or developer tools required.** Just download, run, done.

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
| "Antigravity IDE" renamed folder not detected | ✅ Auto-detects both old and new paths *(v1.05+)* |
| Antigravity IDE 2.x `antigravity-ide` data folder | ✅ Auto-detects all naming variants *(v1.05+)* |
| Conversations split across multiple folders after upgrade | ✅ Multi-folder merge with dedup *(v1.06+)* |
| Only one Antigravity variant fixed when both installed | ✅ Writes index to ALL databases *(v1.06+)* |
| New `.db` conversation format not detected | ✅ Supports both `.pb` and `.db` files *(v1.06+)* |
| Placeholder titles despite usable conversation text | ✅ Derives titles from the first `.db` user prompt *(v1.07+)* |
| Recent `.db` messages disappear from the sidebar later | ✅ Refreshes updated timestamps on re-run *(v1.07+)* |
| Running from WSL requires manual file copying | ✅ Native WSL path detection *(v1.05+)* |
| `python` command fails on macOS/Linux | ✅ Auto-detects Python 3, with built-in fallback *(v1.05+)* |

## How It Works

Antigravity stores conversation data in two places:

- **Conversation files** (`*.pb` or `*.db`) — stored in your user profile
- **Sidebar index** — a SQLite database in your app data folder (one per Antigravity variant)

| OS | Conversations | Database |
|---|---|---|
| Windows | `%USERPROFILE%\.gemini\antigravity\` or `antigravity-ide\` | `%APPDATA%\Antigravity IDE\...\state.vscdb` |
| macOS | `~/.gemini/antigravity/` or `antigravity-ide/` | `~/Library/Application Support/Antigravity IDE/.../state.vscdb` |
| Linux | `~/.gemini/antigravity/` or `antigravity-ide/` | `~/.config/Antigravity IDE/.../state.vscdb` |
| WSL | `~/.gemini/antigravity/` or `antigravity-ide/` | Auto-resolved from Windows `%APPDATA%` via `/mnt/c/` |

> **Note:** The tool automatically detects all folder name variants — `antigravity`, `antigravity-ide`, `antigravity-backup`, `Antigravity`, and `Antigravity IDE` — and merges conversations from all locations. Duplicates are automatically removed (newest location wins).

When the index gets corrupted, conversations still exist on disk but don't show up in the sidebar. This tool scans your conversation files, sorts them by date, pulls titles from brain artifacts or derives them from the first prompt in newer conversation databases, and writes a clean index back to the database.

**Title resolution priority:**
1. Titles already in the database (canonical Antigravity titles — preserved across re-runs)
2. Brain artifact `.md` headings (for conversations not yet indexed)
3. A meaningful title derived from the first user prompt in a `.db` conversation
4. Fallback: `Conversation (date) short-id`

## Output Legend

| Marker | Meaning |
|---|---|
| `[+]` | Title extracted from brain artifact |
| `[~]` | Title preserved from existing database |
| `[=]` | Title derived from a `.db` conversation's first user prompt |
| `[?]` | Fallback title (no source available) |
| `[WS]` | Workspace metadata preserved or recovered |

## Changelog

### v1.07
- **Fix:** **Meaningful `.db` titles** — derives a concise title from the first user prompt when neither the existing index nor brain artifacts contain a title, replacing many date-and-ID placeholders.
- **Fix:** **Recent-message visibility** — when an existing `.db` conversation file is newer than its indexed update time, refreshes only the sidebar's updated timestamp. Created time, workspace mapping, and unknown metadata remain unchanged.
- **Safety:** The tool now exits without scanning or writing when Antigravity is running, preventing the app from later overwriting the repaired index.
- **Tests:** Generated SQLite/protobuf fixtures cover title extraction, malformed input, title priority, timestamp refresh, metadata preservation, and the active-process abort.

### v1.06
- **New:** **Multi-folder conversation merge** — scans `antigravity-ide`, `antigravity`, and `antigravity-backup` folders, merges all conversations with deduplication (newest folder wins). Users who upgraded from v1.x to v2.x no longer lose conversations that were only in the old or backup folder.
- **New:** **Multi-database write** — discovers ALL existing Antigravity databases (`Antigravity`, `Antigravity IDE`, `antigravity`) and writes the rebuilt index to every one of them. If you have both the standalone agent and the IDE installed, both get fixed in one run.
- **New:** **`.db` conversation format** — newer Antigravity IDE versions store conversations as `.db` (SQLite) files instead of `.pb` (protobuf). The tool now detects both formats. `.db-shm` and `.db-wal` journal files are automatically ignored.
- **New:** **Cross-folder brain search** — brain artifacts are now searched across all 3 folders, so title resolution and workspace inference work even when brain data is in a different folder than the conversation file.
- **Fix:** Process detection now checks for both `antigravity.exe` and `antigravity ide.exe`.
- **Non-destructive** — conversations are read in-place from all folders. No files are copied or moved.

### v1.05
- **New:** **Antigravity IDE path support** — automatically detects both the old (`Antigravity`) and new (`Antigravity IDE`) folder names across Windows, macOS, and Linux. No manual configuration needed — the tool finds whichever version you have installed.
- **New:** **Smarter workspace matching** — uses Antigravity's own `workspaceStorage` data to accurately match conversations to workspaces, instead of relying solely on path-depth heuristics. Falls back to the old method if no workspace storage data is found.
- **New:** **Native WSL support** — the script now auto-detects WSL and resolves Windows `%APPDATA%` paths directly via `cmd.exe` and `wslpath`. No manual file copying needed — just run the `.py` script from your WSL terminal. Falls back to scanning `/mnt/c/Users/` for Antigravity installations if the primary method fails.
- **New:** **Python 3 auto-fallback** — if accidentally launched with Python 2, the script auto-relaunches itself with `python3`. Also validates minimum version (3.7+) with clear error messages and install instructions.
- **Fix:** Title resolution now correctly preserves canonical Antigravity titles from the database, only using brain artifact headings for conversations that have no existing title.
- **Fix:** WSL process detection now checks both the Windows host (`tasklist.exe`) and Linux processes (`pgrep`).

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
# Windows
python rebuild_conversations.py

# macOS / Linux
python3 rebuild_conversations.py
```

> **Why `python3`?** On macOS 12.3+ and most Linux distros, the `python` command either doesn't exist or may point to an old Python 2 installation. Use `python3` to be safe. If you're unsure which you have, run `python3 --version` in your terminal.

> **Automatic fallback:** If you accidentally run the script with Python 2 (e.g. `python rebuild_conversations.py` on a system where `python` is Python 2), the script will detect this and automatically re-launch itself with `python3`. If Python 3 isn't installed at all, it will print a clear error with install instructions.

Requires Python 3.7+ with no external packages. The script automatically detects your operating system and finds the correct folders (both old and new naming conventions).

### WSL Users

The script natively supports WSL — just run it directly from your WSL terminal:

```bash
python3 rebuild_conversations.py
```

The tool automatically detects WSL, resolves your Windows `%APPDATA%` path, and accesses the Antigravity database on the Windows side. No manual file copying needed.

> **How it works:** The script calls `cmd.exe /c echo %APPDATA%` and converts the result with `wslpath`. If that fails, it scans `/mnt/c/Users/` for folders that have Antigravity installed. Conversations and brain data are read from your Linux home directory (`~/.gemini/antigravity/`).

## Safety

- **Automatic backup** — your current index is saved to `trajectorySummaries_backup.txt` before any changes
- **Non-destructive** — conversation files (`*.pb` and `*.db`) are never modified, only the sidebar index is rebuilt
- **Metadata-preserving** — workspace assignments, timestamps, and other internal state are retained *(v1.01+)*
- **Idempotent** — safe to run multiple times

⚠️ Antivirus false positives: PyInstaller one-file executables may trigger heuristic detections. You can review the source and build workflow, scan the downloaded file, or run the Python source directly.

## FAQ

**Q: Do I really need to restart my PC?**
A: No. If Antigravity was fully closed before the repair, reopen it afterward. Reboot your PC only if the changes do not appear.

**Q: Why do some titles show as "Conversation (Mar 10) abc12345"?**
A: v1.07+ derives a meaningful title from the first user prompt in newer `.db` conversations. A placeholder remains only when no existing index title, brain heading, or usable first prompt is available.

**Q: Why can recent messages disappear from the sidebar again later?**
A: A conversation's `.db` file can be updated after the sidebar index was rebuilt. Re-run v1.07+ with Antigravity fully closed; the tool refreshes that conversation's updated time while preserving its created time and other metadata.

**Q: Can I run this while Antigravity is open?**
A: No. The tool exits without making changes if Antigravity is running, because the app can overwrite the repaired index when it exits.

**Q: I ran v1.0 and my workspace chats were removed. Can I get them back?**
A: Yes! v1.03+ can auto-recover most workspace assignments by scanning your brain artifact files. When prompted, press Enter or 1 for auto-assignment. If some conversations can't be auto-detected, choose option 2 to manually assign them.

**Q: I use WSL / SSH / Docker remote workspaces. Will this work?**
A: Yes! v1.04+ fully supports `vscode-remote://` URIs. v1.05+ also supports running the script natively from inside WSL — no file copying needed.

**Q: I updated Antigravity and the folder changed from "Antigravity" to "Antigravity IDE". Will the tool still work?**
A: Yes! v1.05+ automatically detects both folder names and uses whichever one exists on your system.

## License

MIT — free to use, share, and modify.

---

**⭐ If this fixed your conversations, please star the repo so others can find it!**

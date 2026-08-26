Antigravity Conversation Fix v1.07

💬 Meaningful Titles from `.db` Conversations

When neither the sidebar index nor brain artifacts contain a title, the tool now derives a concise title from the first user prompt stored in a newer `.db` conversation. Existing index titles still have highest priority, followed by brain headings, then the first prompt.

🕒 Recent-Message Visibility

When a `.db` conversation changes after an earlier repair, the tool refreshes that conversation's sidebar update time on the next run. It replaces only the updated-time field when the file is newer, while preserving created time, workspace assignment, and other internal metadata.

🛑 Safer Closed-App Check

The tool now stops without scanning or writing if Antigravity is running. Exact process-name checks avoid mistaking this script or repository path for the app.

What it fixes

• Conversations disappeared from the sidebar
• Conversations in the wrong order
• Date-and-ID placeholder titles when a usable first prompt exists
• Recent `.db` messages disappearing from the sidebar again later
• Workspace assignments and metadata lost during rebuilds
• Conversations split across old, new, and backup data folders

How to use

1. Close Antigravity completely (File → Exit or kill it from Task Manager)
2. Download and double-click `Antigravity_Conversation_Fix.exe`
3. When prompted for workspace assignment, press Enter or 1 for automatic assignment, or press 2 to review any remaining conversations manually
4. Open Antigravity — a PC restart is normally not required; reboot only if the changes do not appear after reopening it

Notes

• No Python or developer tools needed — just download and run
• Automatically backs up your current index before making changes
• Opens conversation files read-only and never modifies them
• Preserves existing titles, created time, workspace assignments, and unknown metadata except for the targeted `.db` updated-time refresh
• Safe to run multiple times
• Windows users can run the executable; macOS and Linux users can run the Python source

⭐ If this helped you, please star the repo so others can find it!

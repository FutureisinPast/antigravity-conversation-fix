@echo off
chcp 65001 >nul 2>&1
echo.
echo  ============================================
echo    Antigravity Conversation Fix
echo    Fixes missing/unordered conversation history
echo  ============================================
echo.
echo  IMPORTANT: Make sure Antigravity is FULLY CLOSED before continuing!
echo.
pause
echo.
python "%~dp0rebuild_conversations.py"
if %errorlevel% neq 0 (
    echo.
    echo  python not found. Trying python3...
    python3 "%~dp0rebuild_conversations.py"
)
echo.
echo  ============================================
echo  If this tool helped you, please star the GitHub repo
echo  so other users can find it too!
echo  ============================================
echo.
pause

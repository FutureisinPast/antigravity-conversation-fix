#!/usr/bin/env bash
# Antigravity Conversation Fix - Linux / WSL / macOS Launcher
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ANSI color codes (if terminal is interactive)
if [ -t 1 ]; then
    BOLD="\033[1m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    CYAN="\033[36m"
    RESET="\033[0m"
else
    BOLD=""
    GREEN=""
    YELLOW=""
    CYAN=""
    RESET=""
fi

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║         Antigravity Conversation Fix                     ║"
echo "  ║         Fixes missing/unordered conversation history     ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# Find Python 3
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    if python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1; then
        PYTHON_BIN="python"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${YELLOW}ERROR: Python 3.7+ is required but was not found in PATH.${RESET}"
    echo "Please install Python 3:"
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install python3"
    echo "  Fedora:        sudo dnf install python3"
    echo "  Arch:          sudo pacman -S python"
    echo "  macOS:         brew install python"
    exit 1
fi

# Forward all arguments to the python script
exec "$PYTHON_BIN" "$SCRIPT_DIR/rebuild_conversations.py" "$@"

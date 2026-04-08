import os
import time
import subprocess
import platform

# Configuration (mirrored from rebuild_conversations.py)
_home = os.path.expanduser("~")
SEARCH_DIRS = [
    os.path.join(_home, ".gemini", "antigravity", "conversations"),
    os.path.join(_home, ".gemini", "antigravity", "implicit"),
    os.path.join(_home, ".gemini", "antigravity_backup", "conversations"),
    os.path.join(_home, ".gemini", "antigravity_backup", "implicit"),
]

FIX_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebuild_conversations.py")
CHECK_INTERVAL = 60  # seconds

def is_antigravity_running():
    """Check if the Antigravity desktop app is running (not our own scripts)."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq antigravity.exe'], capture_output=True, text=True, creationflags=0x08000000)
            return 'antigravity.exe' in result.stdout.lower()
        else:
            # Check for the actual Antigravity app binary, excluding our own scripts
            result = subprocess.run(
                ['pgrep', '-x', 'antigravity'],
                capture_output=True, text=True
            )
            return bool(result.stdout.strip())
    except:
        return False

def get_max_mtime():
    max_mtime = 0
    for d in SEARCH_DIRS:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".pb"):
                    try:
                        max_mtime = max(max_mtime, os.path.getmtime(os.path.join(d, f)))
                    except:
                        pass
    return max_mtime

def main():
    print(f"Antigravity Watcher started. Monitoring {len(SEARCH_DIRS)} directories.", flush=True)
    last_mtime = get_max_mtime()
    
    while True:
        try:
            time.sleep(CHECK_INTERVAL)
            current_mtime = get_max_mtime()
            
            if current_mtime > last_mtime:
                print(f"Change detected ({current_mtime}). Checking if Antigravity is active...", flush=True)
                if not is_antigravity_running():
                    print("Antigravity is closed. Running auto-sync...", flush=True)
                    subprocess.run(["python3", FIX_SCRIPT, "--auto"])
                    last_mtime = current_mtime
                else:
                    print("Antigravity is active. Postponing sync until it closes.", flush=True)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error in watcher: {e}", flush=True)
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

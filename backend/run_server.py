"""Server launcher."""
import os
import sys
from pathlib import Path

# Configure UTF-8 encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import uvicorn


def ensure_ollama_running():
    """Ensure Ollama daemon is active if installed on local system."""
    import socket
    import subprocess
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        res = sock.connect_ex(("127.0.0.1", 11434))
        sock.close()
        if res == 0:
            print(">> Local Ollama daemon is active on port 11434.")
            return

        ollama_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if not ollama_path.exists():
            import shutil
            found = shutil.which("ollama")
            if found:
                ollama_path = Path(found)

        if ollama_path.exists():
            print(f">> Launching Ollama daemon in background ({ollama_path})...")
            subprocess.Popen([str(ollama_path), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print(">> Local Ollama not found. Operating via Groq Cloud fallback.")
    except Exception as e:
        print(f">> Ollama check notice: {e}. Operating via Groq Cloud fallback.")


if __name__ == "__main__":
    ensure_ollama_running()
    print("==================================================")
    print(">> 🧠 HERMES BRAIN AI AUTOMATION ENGINE ACTIVE")
    print("   Brain Hub:   http://localhost:8000/brain")
    print("   Pitch Demo:  http://localhost:8000")
    print("   Search:      http://localhost:8000/search")
    print("   API Docs:    http://localhost:8000/docs")
    print("==================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

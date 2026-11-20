"""Start the API and worker together. Closing the launcher stops both."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("Frontend is missing. Run setup.ps1 first.")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1):
            raise SystemExit(f"Port {args.port} is already serving an app. Close the previous launcher first.")
    except OSError:
        pass
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    settings.data.mkdir(parents=True, exist_ok=True)
    log = (settings.data / "server.log").open("a", encoding="utf-8")
    processes = []
    try:
        for arguments in (["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
                          ["-m", "app.worker"]):
            processes.append(subprocess.Popen([sys.executable, *arguments], cwd=ROOT, stdout=log, stderr=log, creationflags=flags))
        for _ in range(80):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Service stopped. Read data/server.log.")
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=1):
                    break
            except OSError:
                time.sleep(.25)
        else:
            raise RuntimeError("Startup timed out. Read data/server.log.")
        print(f"Clause is running at {url}", flush=True)
        print("Keep this window open. Press Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
        raise RuntimeError("One of the services stopped. Read data/server.log.")
    except KeyboardInterrupt:
        print("Stopping services...")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        log.close()


if __name__ == "__main__":
    main()

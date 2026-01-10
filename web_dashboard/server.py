import subprocess
import sys
from pathlib import Path
from typing import Dict

from flask import Flask, jsonify, send_from_directory

BASE_DIR = Path(__file__).resolve().parents[1]
PYTHON_EXECUTABLE = sys.executable
SCRIPTS = {
    "main": BASE_DIR / "main.py",
    "market": BASE_DIR / "Python Scripts" / "MarketData.py",
}

app = Flask(__name__, static_folder=".")
processes: Dict[str, subprocess.Popen] = {}


def _script_running(name: str) -> bool:
    proc = processes.get(name)
    return bool(proc and proc.poll() is None)


def start_script(name: str):
    if name not in SCRIPTS:
        raise ValueError("Unknown script")
    if _script_running(name):
        return False
    script_path = SCRIPTS[name]
    proc = subprocess.Popen([PYTHON_EXECUTABLE, str(script_path)], cwd=str(BASE_DIR))
    processes[name] = proc
    return True


def stop_script(name: str):
    proc = processes.get(name)
    if not proc:
        return False
    if proc.poll() is not None:
        return False
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


@app.get("/")
def index():
    return send_from_directory(Path(__file__).parent, "index.html")


@app.post("/start/<name>")
def start(name: str):
    try:
        started = start_script(name)
    except ValueError:
        return jsonify({"error": "Unknown script"}), 404
    return jsonify({"name": name, "running": _script_running(name), "started": started})


@app.post("/stop/<name>")
def stop(name: str):
    stopped = stop_script(name)
    return jsonify({"name": name, "stopped": stopped, "running": _script_running(name)})


@app.get("/status")
def status():
    return jsonify({name: _script_running(name) for name in SCRIPTS})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


DEFAULT_TIMEOUT_SECONDS = 10.0


def _timeout_seconds():
    raw_value = os.getenv("PHYLOFLOW_AI_DRY_RUN_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = float(raw_value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS


def _isolated_environment(temporary_directory):
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TEMP": temporary_directory,
        "TMP": temporary_directory,
    }
    for name in ("SYSTEMROOT", "WINDIR"):
        if os.getenv(name):
            environment[name] = os.environ[name]
    return environment


def run_dry_run(code_str, pattern=None, knowledge=None):
    worker_path = Path(__file__).with_name("_dry_run_worker.py").resolve()
    payload = json.dumps(
        {"code": code_str, "pattern": pattern, "knowledge": knowledge},
        ensure_ascii=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="phyloflow_dry_run_") as temporary_directory:
            completed = subprocess.run(
                [sys.executable, "-I", str(worker_path)],
                input=payload,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=temporary_directory,
                env=_isolated_environment(temporary_directory),
                timeout=_timeout_seconds(),
                check=False,
            )
    except subprocess.TimeoutExpired:
        return False, f"Isolated dry run exceeded {_timeout_seconds():g} seconds."
    except OSError as error:
        return False, f"Isolated dry run could not start: {error}"

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no details"
        return False, f"Isolated dry run process exited with code {completed.returncode}: {detail}"
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, "Isolated dry run returned an invalid response."
    return bool(response.get("ok")), response.get("result")

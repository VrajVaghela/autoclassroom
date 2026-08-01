"""
Optional local execution of generated code, to capture real program output.

Disabled by default. The code being run was written by an LLM from an
assignment document we do not control, so running it is the user's explicit
choice (settings -> "Run generated code"). When disabled, callers fall back to
the model's predicted output.
"""

import os
import subprocess
import sys
from collections import namedtuple

# Interpreter per file extension. Anything not listed is never executed.
RUNNERS = {
    ".py": [sys.executable],
    ".js": ["node"],
}

# status is why the run ended, which decides what a caller can do about it:
#   "ok"          the program ran and exited cleanly
#   "failed"      the program ran and exited non-zero — the code is at fault
#   "timeout"     the program never finished — the code is probably at fault
#   "unavailable" we could not run it at all (no interpreter, unknown type)
RunResult = namedtuple("RunResult", "command output ok status")


def can_run(filename):
    return os.path.splitext(filename)[1].lower() in RUNNERS


def run_file(directory, filename, timeout=20):
    """
    Execute one generated file and return a RunResult.

    Never raises: a failure to run is reported as output text so it can still
    be shown in the report.
    """
    ext = os.path.splitext(filename)[1].lower()
    argv = RUNNERS.get(ext)
    if not argv:
        return RunResult(
            filename, f"[skipped: no runner configured for {ext} files]", False, "unavailable"
        )

    cmd = argv + [filename]
    display = " ".join([os.path.basename(argv[0])] + [filename])

    try:
        proc = subprocess.run(
            cmd,
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return RunResult(display, f"[skipped: {argv[0]} not found on PATH]", False, "unavailable")
    except subprocess.TimeoutExpired:
        return RunResult(display, f"[timed out after {timeout}s]", False, "timeout")
    except OSError as e:
        return RunResult(display, f"[could not run: {e}]", False, "unavailable")

    output = (proc.stdout or "") + (proc.stderr or "")
    if not output.strip():
        output = f"(no output; exit code {proc.returncode})"
    ok = proc.returncode == 0
    return RunResult(display, output.rstrip(), ok, "ok" if ok else "failed")

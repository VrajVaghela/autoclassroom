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

# Interpreter per file extension. Anything not listed is never executed.
RUNNERS = {
    ".py": [sys.executable],
    ".js": ["node"],
}


def can_run(filename):
    return os.path.splitext(filename)[1].lower() in RUNNERS


def run_file(directory, filename, timeout=20):
    """
    Execute one generated file and return (command, combined_output, ok).

    Never raises: a failure to run is reported as output text so it can still
    be shown in the report.
    """
    ext = os.path.splitext(filename)[1].lower()
    argv = RUNNERS.get(ext)
    if not argv:
        return (filename, f"[skipped: no runner configured for {ext} files]", False)

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
        return (display, f"[skipped: {argv[0]} not found on PATH]", False)
    except subprocess.TimeoutExpired:
        return (display, f"[timed out after {timeout}s]", False)
    except OSError as e:
        return (display, f"[could not run: {e}]", False)

    output = (proc.stdout or "") + (proc.stderr or "")
    if not output.strip():
        output = f"(no output; exit code {proc.returncode})"
    return (display, output.rstrip(), proc.returncode == 0)

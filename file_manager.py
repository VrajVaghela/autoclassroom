"""
Writes a generated solution to the user's chosen output folder.

The output folder comes from config (settings panel), not a hardcoded path.
Source files are written before documents so that, when local execution is
enabled, a report's screenshots can show real captured output.
"""

import os
import re

import config
from artifacts import write_artifact
from artifacts.notebook import execute_notebook
from artifacts.runner import can_run, run_file

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_folder_name(name):
    """Make an assignment title safe to use as a single folder name."""
    cleaned = _INVALID.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if cleaned.lower() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}_folder"
    return cleaned[:120] or "Assignment"


def _unique_dir(base_path):
    """Never overwrite a previous run: Title, Title (2), Title (3)..."""
    if not os.path.exists(base_path):
        return base_path
    for n in range(2, 100):
        candidate = f"{base_path} ({n})"
        if not os.path.exists(candidate):
            return candidate
    return base_path


def _capture_real_output(target_dir, code_files, timeout):
    """
    Run the generated source files and return {filename: (command, output)}.

    Only called when the user has enabled execution in settings.
    """
    captured = {}
    for rel in code_files:
        if not can_run(rel):
            continue
        command, output, _ok = run_file(target_dir, rel.replace("/", os.sep), timeout)
        captured[rel] = (command, output)
    return captured


def _apply_real_output(artifact, captured):
    """
    Replace predicted screenshot output with captured output where we have it.

    Matches a screenshot block to a run by the filename mentioned in its
    command; falls back to the first captured run when there is only one.
    """
    blocks = artifact.get("blocks")
    if not isinstance(blocks, list) or not captured:
        return

    for block in blocks:
        if not isinstance(block, dict) or (block.get("type") or "").lower() != "screenshot":
            continue
        command = block.get("command") or ""
        match = None
        for rel, (real_cmd, output) in captured.items():
            if os.path.basename(rel) in command:
                match = (real_cmd, output)
                break
        if match is None and len(captured) == 1:
            match = next(iter(captured.values()))
        if match:
            block["command"], block["output"] = match


def save_solution(assignment_title, solution, cfg=None):
    """
    Write every artifact in a solution and return a report of what happened.

    Returns {"dir": path, "files": [rel, ...], "notes": [str, ...]}.
    """
    cfg = cfg if cfg is not None else config.load()
    artifacts = solution.get("artifacts") or []
    if not artifacts:
        raise ValueError("There were no files to save.")

    output_root = cfg.get("output_dir") or config.DEFAULT_OUTPUT_DIR
    target_dir = _unique_dir(os.path.join(output_root, sanitize_folder_name(assignment_title)))
    os.makedirs(target_dir, exist_ok=True)

    notes = []
    written = []

    # Source files first: documents may need their captured output.
    code_first = sorted(
        enumerate(artifacts, start=1),
        key=lambda pair: 0 if (pair[1].get("kind") or "").lower() == "code" else 1,
    )

    code_files = []
    notebooks = []
    documents = []

    for index, artifact in code_first:
        kind = (artifact.get("kind") or "").lower()
        if kind in ("docx", "pdf"):
            documents.append((index, artifact))
            continue
        try:
            result = write_artifact(target_dir, artifact, index, assignment_title)
        except Exception as e:
            notes.append(f"Could not write {artifact.get('filename') or 'a file'}: {e}")
            continue
        written.append(result["filename"])
        if result["kind"] == "code":
            code_files.append(result["filename"])
        elif result["kind"] == "notebook":
            notebooks.append(result)

    captured = {}
    if cfg.get("run_code") and code_files:
        timeout = int(cfg.get("run_timeout") or 20)
        try:
            captured = _capture_real_output(target_dir, code_files, timeout)
        except Exception as e:
            notes.append(f"Could not run generated code: {e}")
        if captured:
            notes.append(f"Captured real output from {len(captured)} file(s).")

    if cfg.get("run_code"):
        for result in notebooks:
            ok, message = execute_notebook(
                result["path"], int(cfg.get("run_timeout") or 20) * 3
            )
            if not ok:
                notes.append(message)

    for index, artifact in documents:
        if captured:
            _apply_real_output(artifact, captured)
        try:
            result = write_artifact(target_dir, artifact, index, assignment_title)
        except Exception as e:
            notes.append(f"Could not write {artifact.get('filename') or 'a document'}: {e}")
            continue
        written.append(result["filename"])

    if not written:
        raise ValueError("Every file failed to write. " + " ".join(notes))

    return {"dir": target_dir, "files": written, "notes": notes}

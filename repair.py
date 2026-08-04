"""
The generate -> run -> repair loop.

Every other model call in AutoClassroom is one-shot: ask, parse, write. This
module is the one place that feeds the world back to the model. It runs the
generated program, and when the program fails it hands the source and the
traceback back and asks for a fix, re-running after each attempt until the
program works or the attempt budget runs out.

It only does anything when the user has enabled "Run generated code" — without
execution there is no error to react to. A repair is kept only if it makes the
program work: when the attempts run out the model's original file is put back,
so the code in the report always matches the file on disk and the output beside
it, and a repair pass can never leave a solution worse than the first one.
"""

import os
import re

import config
import providers
from artifacts.notebook import (
    execute_notebook,
    notebook_code_cells,
    notebook_errors,
    patch_notebook_cell,
)
from artifacts.runner import can_run, run_file

# Failures worth sending back to the model. A missing interpreter or a file type
# we have no runner for is our limitation, not a bug in the generated code.
REPAIRABLE = ("failed", "timeout")

# A repair is capped well below the limit config accepts, so a mis-set value
# can't turn one assignment into dozens of model calls.
MAX_ATTEMPTS = 5

# Notebooks start a kernel and run every cell, so they get a longer leash than
# a single script.
NOTEBOOK_TIMEOUT_FACTOR = 3

MAX_SOURCE_CHARS = 24_000
MAX_OUTPUT_CHARS = 4_000
MAX_CELL_CONTEXT_CHARS = 6_000

REPAIR_SYSTEM_PROMPT = """You fix a student's program that failed when it was \
run.

You are given one source file, the command that ran it, and everything the \
program printed — including the error it died on. Work out the real cause and \
return the corrected file.

Rules:
- Reply with ONLY the full corrected contents of the file. No explanation, no \
commentary, no markdown fences, no diff.
- Fix the cause. Never delete the failing feature, stub it out, swallow the \
error in a bare try/except, or print a hardcoded answer instead of computing it.
- Keep solving the same problem: same language, same algorithm, and the same \
file, function and class names the assignment asked for.
- The program runs from the folder holding the generated files, so it must not \
depend on files that are not there — build any data it needs in the code.
- Use only the standard library and packages the file already imports \
successfully. If an import is what failed, rewrite that part without it.
- Keep the file as plain as it already is. Do not add comments, extra prints, \
docstrings, error handling or features while fixing it.
- Change as little as possible. This is a fix, not a rewrite."""

NOTEBOOK_REPAIR_SYSTEM_PROMPT = """You fix the one failing cell of a student's \
Jupyter notebook.

You are given the notebook's earlier code cells for context, the source of the \
cell that raised, and its traceback. Work out the real cause and return that \
one cell, corrected.

Rules:
- Reply with ONLY the full corrected source of that single cell. No \
explanation, no commentary, no markdown fences, no cell numbering.
- Fix the cause. Never delete the failing work, stub it out, swallow the error \
in a bare try/except, or hardcode the answer.
- The earlier cells have already run, so names they defined are available. Do \
not repeat their code, and do not rename anything later cells rely on.
- Use only the standard library and packages the notebook already imports \
successfully. Do not depend on data files that are not there.
- Keep the cell as plain as it already is. Do not add comments, extra prints \
or error handling while fixing it.
- Change as little as possible. This is a fix, not a rewrite."""

# Programs are run with stdin closed, so anything interactive dies here.
_STDIN_ERRORS = re.compile(
    r"EOFError|EOF when reading a line|StdinNotImplementedError", re.IGNORECASE
)

_FENCE = re.compile(r"```[A-Za-z0-9_+#.-]*[ \t]*\r?\n(.*?)```", re.DOTALL)


def attempts_for(cfg):
    """How many repair rounds the user has allowed, clamped to something sane."""
    raw = cfg.get("repair_attempts", config.DEFAULTS["repair_attempts"])
    try:
        return max(0, min(MAX_ATTEMPTS, int(raw)))
    except (TypeError, ValueError):
        return 0


def wants_input(output):
    """
    True when a program failed only because nothing was typed at it.

    Assignments often ask for an interactive program, and we run with stdin
    closed, so the first input() call raises. That is the harness's limit, not
    a bug — "fixing" it would mean hardcoding away the interactivity the
    assignment asked for, so these failures are left alone.
    """
    return bool(_STDIN_ERRORS.search(output or ""))


def _extract_code(text):
    """Pull the file body out of a reply that may still be wrapped in a fence."""
    body = (text or "").strip()
    if not body:
        return None

    if body.startswith("```"):
        # One fenced block wrapping the whole reply. Cut the opening line and
        # the final fence rather than matching pairs, so a file that itself
        # contains ``` — a script printing markdown — survives intact.
        body = body.split("\n", 1)[1] if "\n" in body else ""
        end = body.rfind("```")
        if end != -1:
            body = body[:end]
    else:
        blocks = _FENCE.findall(body)
        if blocks:
            body = max(blocks, key=len)

    body = body.strip("\r\n")
    if not body.strip():
        return None
    return body.rstrip() + "\n"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write(path, source):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)


def _head(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n[...]"


def _tail(text, limit):
    """Keep the end of a program's output — that is where the traceback is."""
    text = text or ""
    return text if len(text) <= limit else "[...]\n" + text[-limit:]


def _last_line(output):
    """The most useful single line of a traceback is its last one."""
    lines = [line.strip() for line in (output or "").strip().splitlines() if line.strip()]
    return lines[-1][:160] if lines else "no output"


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _repair_prompt(rel, source, failure, attempt, attempts, context):
    parts = []
    if context:
        parts.append(f"This file is part of: {context}")
    if attempt > 1:
        parts.append(
            f"This is fix attempt {attempt} of {attempts}. The file below is the "
            f"previous attempt, and it still fails — try a different approach."
        )
    parts.append(f"File: {rel}")
    parts.append(f"Ran with: {failure.command}")
    if failure.status == "timeout":
        parts.append(
            "The program never finished and was killed. It is most likely stuck "
            "in a loop that never ends or waiting on something that never comes."
        )
    parts.append(f"\nCurrent contents of {rel}:\n\n{_head(source, MAX_SOURCE_CHARS)}")
    parts.append(
        f"\nWhat it printed, including the error:\n\n{_tail(failure.output, MAX_OUTPUT_CHARS)}"
    )
    parts.append(f"\nReturn the corrected full contents of {rel}, and nothing else.")
    return "\n".join(parts)


def _ask(system, user, cfg):
    """One model call, returning the code it replied with (or None)."""
    return _extract_code(providers.complete(system, user, cfg=cfg))


def _repair_file(target_dir, rel, timeout, attempts, cfg, context, notes):
    """
    Run one file, handing each failure back to the model until it works.

    Returns (final_result, (original_source, repaired_source) or None). The
    result is the run whose output belongs in the report: the successful one if
    a fix landed, otherwise the original failure.
    """
    native = rel.replace("/", os.sep)
    path = os.path.join(target_dir, native)

    first = run_file(target_dir, native, timeout)
    if first.ok or not attempts or first.status not in REPAIRABLE:
        return first, None

    if wants_input(first.output):
        notes.append(f"{rel} waits for typed input, so it could not be run to completion.")
        return first, None

    original = _read(path)
    if original is None:
        return first, None

    failure = first
    used = 0
    for attempt in range(1, attempts + 1):
        source = _read(path) or original
        try:
            fixed = _ask(
                REPAIR_SYSTEM_PROMPT,
                _repair_prompt(rel, source, failure, attempt, attempts, context),
                cfg,
            )
        except providers.ProviderError as e:
            notes.append(f"Could not ask the model to fix {rel}: {e}")
            break

        # No reply, or the same file back again: another round would ask the
        # same question and get the same answer.
        if not fixed or fixed.strip() == source.strip():
            break

        try:
            _write(path, fixed)
        except OSError as e:
            notes.append(f"Could not write the repaired {rel}: {e}")
            break
        used = attempt

        failure = run_file(target_dir, native, timeout)
        if failure.ok:
            notes.append(f"{rel} failed to run and was repaired in {_plural(attempt, 'attempt')}.")
            return failure, (original, fixed)
        if failure.status not in REPAIRABLE or wants_input(failure.output):
            break

    if used:
        try:
            _write(path, original)
        except OSError as e:
            notes.append(f"Could not restore the original {rel}: {e}")
        notes.append(
            f"{rel} still fails after {_plural(used, 'repair attempt')} "
            f"({_last_line(first.output)}); kept the original."
        )
    else:
        notes.append(f"{rel} fails to run and could not be repaired ({_last_line(first.output)}).")
    return first, None


def run_and_repair(target_dir, code_files, cfg, context=""):
    """
    Run every runnable generated file, repairing the ones that fail.

    Returns (captured, repairs, notes):
      captured {rel: (command, output)} — what each file printed, for the
                                          report's screenshots
      repairs  {rel: (original, fixed)} — files whose contents we changed
      notes    user-facing lines for the extension's status area
    """
    timeout = int(cfg.get("run_timeout") or 20)
    attempts = attempts_for(cfg)

    captured, repairs, notes = {}, {}, []
    for rel in code_files:
        if not can_run(rel):
            continue
        result, repair = _repair_file(
            target_dir, rel, timeout, attempts, cfg, context, notes
        )
        captured[rel] = (result.command, result.output)
        if repair:
            repairs[rel] = repair
    return captured, repairs, notes


def _notebook_prompt(path, index, source, error, attempt, attempts, context):
    earlier = [s for i, s in notebook_code_cells(path) if i < index and s.strip()]
    parts = []
    if context:
        parts.append(f"This notebook is part of: {context}")
    if attempt > 1:
        parts.append(
            f"This is fix attempt {attempt} of {attempts}. The cell below is the "
            f"previous attempt, and it still raises — try a different approach."
        )
    parts.append(f"Notebook: {os.path.basename(path)}")
    if earlier:
        parts.append(
            "\nEarlier code cells, already run, for context only:\n\n"
            + _tail("\n\n# --- next cell ---\n\n".join(earlier), MAX_CELL_CONTEXT_CHARS)
        )
    parts.append(f"\nThe cell that raised:\n\n{_head(source, MAX_SOURCE_CHARS)}")
    parts.append(f"\nIts traceback:\n\n{_tail(error, MAX_OUTPUT_CHARS)}")
    parts.append("\nReturn the corrected source of that cell, and nothing else.")
    return "\n".join(parts)


def repair_notebook(path, cfg, context=""):
    """
    Execute a notebook and fix the cells that raise, one attempt at a time.

    Notebooks are executed with allow_errors on, so a broken cell leaves a
    traceback sitting in the deliverable. Each round fixes the first cell still
    raising and re-runs the whole notebook, since a fix upstream changes
    everything after it.

    Returns notes for the extension. The notebook is left executed either way;
    if the repairs did not reduce the number of failing cells, the original
    sources are put back and it is run once more so the outputs are honest.
    """
    name = os.path.basename(path)
    timeout = int(cfg.get("run_timeout") or 20) * NOTEBOOK_TIMEOUT_FACTOR
    attempts = attempts_for(cfg)

    ok, message = execute_notebook(path, timeout)
    if not ok:
        return [message]

    baseline = len(notebook_errors(path))
    if not baseline:
        return []
    if not attempts:
        return [f"{name}: {_plural(baseline, 'cell')} raised an error."]

    original = {}
    for attempt in range(1, attempts + 1):
        errors = notebook_errors(path)
        if not errors:
            break
        index, source, error = errors[0]

        try:
            fixed = _ask(
                NOTEBOOK_REPAIR_SYSTEM_PROMPT,
                _notebook_prompt(path, index, source, error, attempt, attempts, context),
                cfg,
            )
        except providers.ProviderError as e:
            return _notebook_notes(name, path, baseline, original, timeout,
                                   [f"Could not ask the model to fix {name}: {e}"])

        if not fixed or fixed.strip() == source.strip():
            break
        original.setdefault(index, source)
        # _extract_code ends a file with a newline; cell sources don't carry one.
        if not patch_notebook_cell(path, index, fixed.rstrip("\n")):
            break

        ok, message = execute_notebook(path, timeout)
        if not ok:
            return _notebook_notes(name, path, baseline, original, timeout, [message])

    return _notebook_notes(name, path, baseline, original, timeout, [])


def _notebook_notes(name, path, baseline, original, timeout, notes):
    """Judge the repair round, rolling back if it did not help, and report."""
    remaining = len(notebook_errors(path))

    if original and remaining >= baseline:
        for index, source in original.items():
            patch_notebook_cell(path, index, source)
        execute_notebook(path, timeout)
        notes.append(
            f"{name}: {_plural(baseline, 'cell')} raised an error and the repairs "
            f"did not help; kept the original."
        )
    elif original and remaining:
        notes.append(
            f"{name}: repaired {baseline - remaining} of {_plural(baseline, 'failing cell')}."
        )
    elif original:
        notes.append(f"{name}: repaired {_plural(baseline, 'failing cell')}.")
    elif remaining:
        notes.append(f"{name}: {_plural(remaining, 'cell')} raised an error.")
    return notes

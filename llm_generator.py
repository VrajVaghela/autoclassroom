"""
Turns assignment instructions into a complete set of deliverables.

The model is asked to decide *what* the assignment wants handed in — a
notebook, a Word report with output screenshots, a PDF, plain source files, or
several of these — and to produce the full contents of each. This module owns
the prompt and the JSON parsing; artifacts/ owns turning the result into files.
"""

import json
import re

import config
import providers

SYSTEM_PROMPT = """You are an expert teaching assistant who completes student \
laboratory and programming assignments end to end.

You will be given an assignment's title and its full instructions. Produce the \
COMPLETE solution AND the exact deliverable files the assignment asks for.

Decide the deliverables from the instructions:
- Asks for a Jupyter/Colab notebook, or is a data-science/ML task -> a "notebook".
- Asks for a lab report, write-up, documentation, or screenshots of output -> a "docx".
- Explicitly asks for a PDF -> a "pdf".
- Asks only for code/a program/a script -> one or more "code" files.
- If it asks for several things (e.g. code AND a report), emit one artifact each.
When the instructions are ambiguous, produce the working code plus a docx lab \
report containing the code and its output.

Reply with ONLY a JSON object in exactly this shape, and no other text:

{
  "summary": "one sentence on what you produced",
  "language": "python",
  "artifacts": [
    {
      "kind": "code",
      "filename": "main.py",
      "content": "full source text of the file"
    },
    {
      "kind": "notebook",
      "filename": "lab1.ipynb",
      "title": "Lab 1: Title",
      "cells": [
        {"type": "markdown", "source": "## Aim\\nWhat this does."},
        {"type": "code", "source": "print(2+2)", "output": "4\\n"}
      ]
    },
    {
      "kind": "docx",
      "filename": "lab1_report.docx",
      "title": "Lab 1: Title",
      "blocks": [
        {"type": "heading", "text": "Aim", "level": 1},
        {"type": "paragraph", "text": "Prose text."},
        {"type": "list", "items": ["step one", "step two"], "ordered": true},
        {"type": "code", "text": "print('Hello')", "language": "python"},
        {"type": "screenshot", "command": "python main.py",
         "output": "exact console output the program prints",
         "caption": "Program output"},
        {"type": "table", "headers": ["Input", "Output"], "rows": [["2", "4"]]},
        {"type": "pagebreak"}
      ]
    }
  ]
}

Rules:
- "pdf" artifacts use the same "blocks" structure as "docx".
- A "screenshot" block is rendered as a terminal-window image. Put the program's
  real expected console output in "output" — this is how the report shows results.
- A lab report should normally contain: Aim, Theory/Description, Code, Output
  (as a screenshot block), and Conclusion.
- Write complete, correct, runnable code. No placeholders, no "TODO", no elisions.
- Include every file needed to run the solution (e.g. input data files).
- Use the assignment's required language, filenames and function/class names when
  it specifies them.
- Output MUST be valid JSON: escape newlines inside strings as \\n and quotes as \\".
"""

MAX_INSTRUCTION_CHARS = 120_000


def _strip_code_fence(text):
    fence = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fence.group(1) if fence else text


def _extract_json(text):
    """
    Pull the JSON object out of a model reply.

    Tries the whole string, then a fenced block, then the widest {...} span, then
    a brace-matched scan that ignores braces inside strings.
    """
    if not text:
        raise ValueError("model returned an empty response")

    candidates = []
    stripped = text.strip()
    candidates.append(stripped)

    unfenced = _strip_code_fence(stripped)
    if unfenced != stripped:
        candidates.append(unfenced)

    for source in list(candidates):
        start, end = source.find("{"), source.rfind("}")
        if 0 <= start < end:
            candidates.append(source[start:end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"artifacts": parsed}
        except json.JSONDecodeError:
            continue

    # Brace matching, string-aware, for replies with trailing prose.
    source = _strip_code_fence(stripped)
    start = source.find("{")
    if start >= 0:
        depth, in_string, escaped = 0, False, False
        for i, ch in enumerate(source[start:], start):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(source[start:i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break

    raise ValueError("could not find valid JSON in the model's response")


def _legacy_artifacts(parsed):
    """
    Accept the old shape ([{filename, content}, ...]) as plain code files.

    Keeps working if a model ignores the artifact schema and returns the simple
    file list the previous version of this project used.
    """
    items = parsed.get("artifacts")
    if items is None:
        items = parsed.get("files")
    if not isinstance(items, list):
        return []

    artifacts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("filename"):
            continue
        if "kind" not in item and ("content" in item or "text" in item):
            item = dict(item, kind="code")
        artifacts.append(item)
    return artifacts


def generate_solution(assignment_title, instructions, cfg=None):
    """
    Ask the configured model for the full set of deliverables.

    Returns {"summary": str, "language": str, "artifacts": [...]}.
    Raises providers.ProviderError or ValueError with a user-facing message.
    """
    if not (instructions or "").strip():
        raise ValueError("The assignment has no instructions or attachments to read.")

    cfg = cfg if cfg is not None else config.load()

    body = instructions.strip()
    if len(body) > MAX_INSTRUCTION_CHARS:
        body = body[:MAX_INSTRUCTION_CHARS] + "\n\n[instructions truncated]"

    user_prompt = (
        f"Assignment title: {assignment_title}\n\n"
        f"Assignment instructions and attached material:\n\n{body}"
    )

    raw = providers.complete(SYSTEM_PROMPT, user_prompt, cfg=cfg)
    parsed = _extract_json(raw)

    artifacts = _legacy_artifacts(parsed)
    if not artifacts:
        raise ValueError("The model did not return any deliverable files.")

    return {
        "summary": (parsed.get("summary") or "").strip(),
        "language": (parsed.get("language") or "python").strip().lower(),
        "artifacts": artifacts,
    }

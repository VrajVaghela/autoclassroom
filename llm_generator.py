"""
Turns assignment instructions into a complete set of deliverables.

An assignment usually holds several questions, and each one is its own
deliverable, so generation runs in two passes: split the instructions into
questions, then solve each question in its own model call and namespace its
files (q1_*, q2_*, ...). One call per question also keeps a long assignment
from being cut off by the reply token limit.

The model is asked to decide *what* the assignment wants handed in — a
notebook, a Word report with output screenshots, a PDF, plain source files, or
several of these — and to produce the full contents of each. This module owns
the prompts and the JSON parsing; artifacts/ owns turning the result into files.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

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
- ONE FILE PER QUESTION, ALWAYS. If the assignment contains more than one
  question, problem, exercise or program, emit a separate "code" artifact for
  each one — q1_<name>.py, q2_<name>.py, and so on (use the assignment's own
  filenames when it gives them). This is not optional, and it applies even when
  the questions are tiny or closely related.
- Never put two questions in one file: no single file with all the answers
  separated by comments, no "menu-driven" program that runs each question from a
  choice, no one file that calls every question's function.
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

SPLIT_SYSTEM_PROMPT = """You split a student's assignment into the separate \
questions it contains, so each one can be solved and handed in as its own file.

You will be given an assignment's title and its full instructions. Identify \
every distinct question, problem, task, exercise or program the student has to \
produce.

Reply with ONLY a JSON object in exactly this shape, and no other text:

{
  "report_style": "combined",
  "questions": [
    {
      "number": 1,
      "title": "short title for this question",
      "instructions": "the full text of this question, plus any shared data, \
constraints or setup it needs to be solved on its own"
    }
  ]
}

Rules:
- SPLIT BY DEFAULT. Each question becomes its own file, so returning one entry
  for an assignment that really holds several is the worst possible answer.
  Return a single entry ONLY when the whole assignment is one single program.
- One entry per question the student must hand in. Treat each of these as its
  own question, whether or not the assignment numbers them:
    * a numbered or lettered item ("Q1.", "2.", "(iii)", "Program 4", "Task B")
    * each separate "Write a program to ..." / "Implement ..." / "Create ..."
      sentence or bullet
    * each row of a table of programs, and each practical/experiment in a list
- Sub-parts of one question (a, b, c) stay together in that question unless each
  sub-part is clearly its own standalone program.
- Never merge two questions into one entry, and never invent questions that the
  instructions do not ask for.
- Keep the original wording of each question, and copy in any shared context
  (data files, table of inputs, required language) that the question needs.
- "report_style" is "combined" when the assignment asks for ONE report or
  document covering all questions, "separate" when each question needs its own
  document, and "none" when no write-up is asked for.
- Output MUST be valid JSON: escape newlines inside strings as \\n and quotes as \\".
"""

MAX_INSTRUCTION_CHARS = 120_000

# How much of the whole assignment to repeat as context in a per-question call.
MAX_CONTEXT_CHARS = 6_000

# A split claiming more than this is almost certainly the model over-splitting
# prose into "questions"; anything past the cap is dropped.
MAX_QUESTIONS = 25

# Questions are independent HTTP calls, so a few can be in flight at once.
MAX_PARALLEL_QUESTIONS = 4

# Numbered items that usually mark the start of a new question. Used only to
# notice that the model under-split an assignment, never to split it ourselves.
_QUESTION_MARKERS = (
    re.compile(
        r"^\s*(?:q|question|problem|prob|exercise|ex|program|task|part|practical|aim)"
        r"\s*[-.:)]?\s*(\d{1,2})\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^\s*(\d{1,2})\s*[.)]\s+\S", re.MULTILINE),
    re.compile(r"^\s*\((\d{1,2})\)\s+\S", re.MULTILINE),
)


def count_question_markers(text):
    """
    Rough count of numbered questions in an assignment.

    A list only counts when its numbering starts at 1 or 2, which keeps prose
    like "see section 7" and mid-question steps from inflating the count.
    """
    best = 0
    for pattern in _QUESTION_MARKERS:
        numbers = {int(n) for n in pattern.findall(text)}
        if numbers and min(numbers) <= 2:
            best = max(best, len(numbers))
    return best


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


def split_questions(assignment_title, instructions, cfg=None, hint=None):
    """
    Break an assignment into its individual questions.

    Returns (questions, report_style) where questions is a list of
    {"number": int, "title": str, "instructions": str}. Returns an empty list
    when the split fails — the caller then treats the assignment as one task.
    """
    cfg = cfg if cfg is not None else config.load()

    user_prompt = (
        f"Assignment title: {assignment_title}\n\n"
        + (f"{hint}\n\n" if hint else "")
        + f"Assignment instructions and attached material:\n\n{instructions}"
    )
    parsed = _extract_json(providers.complete(SPLIT_SYSTEM_PROMPT, user_prompt, cfg=cfg))

    report_style = (parsed.get("report_style") or "combined").strip().lower()
    if report_style not in ("combined", "separate", "none"):
        report_style = "combined"

    questions = []
    for item in parsed.get("questions") or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("instructions") or item.get("text") or "").strip()
        if not text:
            continue
        questions.append({
            "number": len(questions) + 1,
            "title": (item.get("title") or f"Question {len(questions) + 1}").strip(),
            "instructions": text,
        })
        if len(questions) >= MAX_QUESTIONS:
            break

    return questions, report_style


def _prefix_filename(filename, number):
    """
    Namespace a question's file so questions never overwrite each other.

    "main.py" -> "q1_main.py"; "src/util.py" -> "src/q1_util.py". A name the
    model already tagged with this question's number is left alone.
    """
    raw = (filename or "").replace("\\", "/")
    head, _, leaf = raw.rpartition("/")
    leaf = leaf.strip() or "solution.txt"
    if re.match(rf"^q(?:uestion)?[ _-]?{number}(?![0-9])", leaf, re.IGNORECASE):
        return raw
    return f"{head}/q{number}_{leaf}" if head else f"q{number}_{leaf}"


def _solve_question(assignment_title, question, total, context, report_style, cfg):
    """Run one model call for one question and return its normalized artifacts."""
    number = question["number"]

    guidance = [
        f"This is question {number} of {total} in the assignment. Produce the "
        f"deliverables for THIS question only — the other questions are being "
        f"handled by separate calls, so do not solve or mention them.",
        f'Name every file you emit with the prefix "q{number}_" '
        f'(for example q{number}_main.py) unless the assignment specifies exact '
        f"filenames, in which case use the names it gives.",
    ]
    if report_style == "combined":
        guidance.append(
            "The assignment wants a single report covering every question. Emit "
            "this question's write-up as a docx artifact holding only this "
            "question's sections; it will be merged into the full report."
        )
    elif report_style == "none":
        guidance.append(
            "The assignment does not ask for a report or write-up, so emit only "
            "the code or notebook files it asks for."
        )

    user_prompt = (
        f"Assignment title: {assignment_title}\n\n"
        + "\n".join(guidance)
        + f"\n\nQuestion {number} — {question['title']}:\n\n{question['instructions']}"
        + f"\n\nFull assignment text, for context only:\n\n{context}"
    )

    parsed = _extract_json(providers.complete(SYSTEM_PROMPT, user_prompt, cfg=cfg))
    artifacts = _legacy_artifacts(parsed)
    if not artifacts:
        raise ValueError("no deliverable files were returned")

    for artifact in artifacts:
        artifact["filename"] = _prefix_filename(artifact.get("filename"), number)
        artifact.setdefault("title", f"Question {number}: {question['title']}")

    return {
        "summary": (parsed.get("summary") or "").strip(),
        "language": (parsed.get("language") or "").strip().lower(),
        "artifacts": artifacts,
    }


def _merge_reports(assignment_title, solved):
    """
    Fold the per-question documents into one report for the whole assignment.

    Returns (merged_artifact_or_None, remaining_artifacts). Each question keeps
    its own heading and starts on a new page.
    """
    documents = []
    remaining = []
    for question, result in solved:
        for artifact in result["artifacts"]:
            ext = os.path.splitext(artifact.get("filename") or "")[1].lower()
            kind = (artifact.get("kind") or "").lower()
            if (ext in (".docx", ".pdf") or kind in ("docx", "pdf")) and artifact.get("blocks"):
                documents.append((question, artifact))
            else:
                remaining.append(artifact)

    if len(documents) < 2:
        return None, remaining + [a for _q, a in documents]

    kind = "pdf" if all(
        (a.get("kind") or "").lower() == "pdf"
        or os.path.splitext(a.get("filename") or "")[1].lower() == ".pdf"
        for _q, a in documents
    ) else "docx"

    blocks = []
    for index, (question, artifact) in enumerate(documents):
        if index:
            blocks.append({"type": "pagebreak"})
        blocks.append({
            "type": "heading",
            "text": f"Question {question['number']}: {question['title']}",
            "level": 1,
        })
        blocks.extend(b for b in artifact["blocks"] if isinstance(b, dict))

    merged = {
        "kind": kind,
        "filename": f"report.{kind}",
        "title": assignment_title,
        "blocks": blocks,
    }
    return merged, remaining


def _generate_per_question(assignment_title, body, questions, report_style, cfg):
    """Solve every question in its own call and combine the results."""
    total = len(questions)
    context = body if len(body) <= MAX_CONTEXT_CHARS else body[:MAX_CONTEXT_CHARS] + "\n[...]"

    def solve(question):
        return _solve_question(
            assignment_title, question, total, context, report_style, cfg
        )

    workers = min(MAX_PARALLEL_QUESTIONS, total)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda q: _safe_solve(solve, q), questions))

    notes = []
    solved = []
    for question, (result, error) in zip(questions, outcomes):
        if error:
            notes.append(f"Question {question['number']} ({question['title']}) failed: {error}")
        else:
            solved.append((question, result))

    if not solved:
        raise ValueError(
            "Every question failed to generate. " + " ".join(notes)
        )

    artifacts = []
    if report_style == "combined":
        merged, remaining = _merge_reports(assignment_title, solved)
        artifacts.extend(remaining)
        if merged:
            artifacts.append(merged)
    else:
        for _question, result in solved:
            artifacts.extend(result["artifacts"])

    language = next(
        (r["language"] for _q, r in solved if r["language"]), "python"
    )
    return {
        "summary": f"Solved {len(solved)} of {total} question(s) in this assignment.",
        "language": language,
        "artifacts": artifacts,
        "questions": total,
        "notes": notes,
    }


def _safe_solve(solve, question):
    """Run one question's call, turning a failure into a note instead of a crash."""
    try:
        return solve(question), None
    except (providers.ProviderError, ValueError) as e:
        return None, str(e)
    except Exception as e:  # a broken reply for one question must not sink the rest
        return None, f"{type(e).__name__}: {e}"


def _generate_whole(assignment_title, body, cfg):
    """Single call for the whole assignment — used when there is one question."""
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
        "questions": 1,
        "notes": [],
    }


def generate_solution(assignment_title, instructions, cfg=None):
    """
    Ask the configured model for the full set of deliverables.

    Splits the assignment into questions first and solves each one separately,
    so every question gets its own file(s). Falls back to a single call when the
    assignment holds one task or the split fails.

    Returns {"summary", "language", "artifacts", "questions", "notes"}.
    Raises providers.ProviderError or ValueError with a user-facing message.
    """
    if not (instructions or "").strip():
        raise ValueError("The assignment has no instructions or attachments to read.")

    cfg = cfg if cfg is not None else config.load()

    body = instructions.strip()
    if len(body) > MAX_INSTRUCTION_CHARS:
        body = body[:MAX_INSTRUCTION_CHARS] + "\n\n[instructions truncated]"

    split_note = None
    try:
        questions, report_style = split_questions(assignment_title, body, cfg=cfg)

        # The model sometimes hands back one lump for an assignment that plainly
        # lists several questions. Ask once more, pointing at what we counted.
        expected = count_question_markers(body)
        if expected > max(len(questions), 1):
            hint = (
                f"This assignment appears to contain about {expected} separate "
                f"questions. Split it into every question it lists — one entry "
                f"per question — and do not return fewer than the assignment has."
            )
            try:
                retry, retry_style = split_questions(
                    assignment_title, body, cfg=cfg, hint=hint
                )
            except (providers.ProviderError, ValueError):
                retry, retry_style = [], report_style  # keep the first split
            if len(retry) > len(questions):
                questions, report_style = retry, retry_style
            elif len(questions) <= 1:
                split_note = (
                    f"Found about {expected} question(s) in the instructions, but the "
                    f"model kept the assignment as one task."
                )
    except (providers.ProviderError, ValueError) as e:
        questions, report_style = [], "combined"
        split_note = f"Could not split the assignment into questions ({e}); solved it as one task."

    if len(questions) > 1:
        print(f"Assignment has {len(questions)} question(s); generating one at a time.")
        solution = _generate_per_question(
            assignment_title, body, questions, report_style, cfg
        )
    else:
        solution = _generate_whole(assignment_title, body, cfg)

    if split_note:
        solution["notes"].insert(0, split_note)
    return solution

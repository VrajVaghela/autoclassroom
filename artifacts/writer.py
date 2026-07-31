"""
Dispatches one artifact spec to the right renderer.

An artifact is whatever the assignment asked to be handed in:
  {"kind": "notebook", "filename": "lab1.ipynb", "cells": [...]}
  {"kind": "docx",     "filename": "report.docx", "title": "...", "blocks": [...]}
  {"kind": "pdf",      "filename": "report.pdf",  "title": "...", "blocks": [...]}
  {"kind": "code",     "filename": "main.py",     "content": "..."}
"""

import os
import re

from .document import write_docx, write_pdf
from .notebook import write_notebook

ARTIFACT_KINDS = ("code", "notebook", "docx", "pdf")

EXTENSION_KIND = {
    ".ipynb": "notebook",
    ".docx": "docx",
    ".pdf": "pdf",
}

_UNSAFE = re.compile(r'[<>:"|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_name(name, fallback="file.txt"):
    """Strip a single path component down to something safe to create."""
    name = _UNSAFE.sub("_", (name or "").strip().replace("\\", "/"))
    name = name.rsplit("/", 1)[-1].strip(" .")
    if not name:
        return fallback
    stem, ext = os.path.splitext(name)
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"{stem}_file"
    return f"{stem[:120]}{ext[:12]}"


def safe_relpath(raw, fallback="file.txt"):
    """
    Keep subdirectories the model asked for, but never escape the target dir.

    "src/main.py" is preserved; "../../etc/passwd" and "C:/Windows/x" are not.
    """
    raw = (raw or "").replace("\\", "/")
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part == "." or part == "..":
            continue
        if re.fullmatch(r"[A-Za-z]:", part):  # drive letter
            continue
        parts.append(part)
    if not parts:
        return fallback

    dirs = [sanitize_name(p, "sub") for p in parts[:-1]][:4]
    leaf = sanitize_name(parts[-1], fallback)
    return "/".join(dirs + [leaf])


def infer_kind(artifact):
    """Trust the extension over a mislabeled 'kind' — the filename is the deliverable."""
    kind = (artifact.get("kind") or "").lower().strip()
    ext = os.path.splitext(artifact.get("filename") or "")[1].lower()

    if ext in EXTENSION_KIND:
        return EXTENSION_KIND[ext]
    if kind in ARTIFACT_KINDS:
        # A docx/pdf/notebook kind with a plain extension can't be rendered as
        # that format; write it as a source file instead of guessing.
        return "code" if kind in ("docx", "pdf", "notebook") else kind
    return "code"


def _default_name(kind, index):
    return {
        "notebook": f"notebook_{index}.ipynb",
        "docx": f"report_{index}.docx",
        "pdf": f"report_{index}.pdf",
    }.get(kind, f"file_{index}.txt")


def write_artifact(target_dir, artifact, index=1, assignment_title=""):
    """
    Render one artifact into target_dir.

    Returns a dict describing what was written:
      {"filename": ..., "path": ..., "kind": ..., "note": optional}
    """
    kind = infer_kind(artifact)
    rel = safe_relpath(artifact.get("filename"), _default_name(kind, index))
    path = os.path.join(target_dir, rel.replace("/", os.sep))

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

    title = artifact.get("title") or assignment_title
    assets_dir = os.path.join(target_dir, "assets")
    result = {"filename": rel, "path": path, "kind": kind}

    if kind == "notebook":
        write_notebook(
            path,
            title,
            artifact.get("cells") or artifact.get("blocks") or [],
            language=(artifact.get("language") or "python").lower(),
        )

    elif kind == "docx":
        write_docx(path, title, artifact.get("blocks") or [], assets_dir)

    elif kind == "pdf":
        write_pdf(path, title, artifact.get("blocks") or [], assets_dir)

    else:
        content = artifact.get("content")
        if content is None:
            content = artifact.get("text") or ""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(str(content))

    return result

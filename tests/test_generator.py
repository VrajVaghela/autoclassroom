"""Tests for the model-reply JSON extraction and artifact normalization."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_generator as gen  # noqa: E402

PLAN = {
    "summary": "Bubble sort with a report.",
    "language": "python",
    "artifacts": [
        {"kind": "code", "filename": "main.py", "content": "print('hi')\n"},
        {"kind": "docx", "filename": "report.docx", "title": "Lab 1",
         "blocks": [{"type": "heading", "text": "Aim", "level": 1}]},
    ],
}


def test_plain_json():
    assert gen._extract_json(json.dumps(PLAN))["summary"] == PLAN["summary"]


def test_fenced_json():
    text = "```json\n" + json.dumps(PLAN) + "\n```"
    assert len(gen._extract_json(text)["artifacts"]) == 2


def test_prose_before_and_after():
    text = "Sure! Here is the solution:\n\n" + json.dumps(PLAN) + "\n\nLet me know if you need changes."
    assert gen._extract_json(text)["language"] == "python"


def test_braces_inside_strings_do_not_confuse_matcher():
    plan = {"artifacts": [{"kind": "code", "filename": "a.py",
                           "content": "d = {'k': {'n': 1}}\nprint('}')\n"}]}
    text = "Here you go:\n" + json.dumps(plan) + "\nDone."
    out = gen._extract_json(text)
    assert out["artifacts"][0]["content"].endswith("print('}')\n")


def test_bare_array_reply_is_wrapped():
    text = json.dumps([{"filename": "x.py", "content": "x=1"}])
    assert gen._extract_json(text)["artifacts"][0]["filename"] == "x.py"


def test_empty_reply_raises():
    with pytest.raises(ValueError):
        gen._extract_json("")


def test_unparseable_reply_raises():
    with pytest.raises(ValueError):
        gen._extract_json("I cannot help with that request.")


def test_legacy_file_list_becomes_code_artifacts():
    parsed = {"files": [{"filename": "a.py", "content": "a=1"},
                        {"filename": "b.py", "content": "b=2"}]}
    arts = gen._legacy_artifacts(parsed)
    assert [a["kind"] for a in arts] == ["code", "code"]


def test_legacy_skips_entries_without_filename():
    parsed = {"artifacts": [{"content": "orphan"}, "junk", None,
                            {"filename": "ok.py", "content": "x"}]}
    arts = gen._legacy_artifacts(parsed)
    assert len(arts) == 1 and arts[0]["filename"] == "ok.py"


def test_declared_kind_is_preserved():
    parsed = {"artifacts": [{"kind": "notebook", "filename": "l.ipynb", "cells": []}]}
    assert gen._legacy_artifacts(parsed)[0]["kind"] == "notebook"


def test_generate_solution_happy_path(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete",
                        lambda *a, **k: "```json\n" + json.dumps(PLAN) + "\n```")
    out = gen.generate_solution("Lab 1", "Write a bubble sort.", cfg={})
    assert out["summary"] == PLAN["summary"]
    assert len(out["artifacts"]) == 2


def test_generate_solution_sends_instructions(monkeypatch):
    seen = {}

    def fake(system, user, **kwargs):
        seen["system"], seen["user"] = system, user
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    gen.generate_solution("Lab 7", "Sort an array using quicksort.", cfg={})
    assert "Lab 7" in seen["user"]
    assert "quicksort" in seen["user"]
    assert "JSON" in seen["system"]


def test_generate_solution_rejects_empty_instructions():
    with pytest.raises(ValueError, match="no instructions"):
        gen.generate_solution("Lab 1", "   ", cfg={})


def test_generate_solution_rejects_empty_artifacts(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete",
                        lambda *a, **k: json.dumps({"summary": "none", "artifacts": []}))
    with pytest.raises(ValueError, match="did not return any deliverable"):
        gen.generate_solution("Lab 1", "Do a thing.", cfg={})


def test_long_instructions_are_truncated(monkeypatch):
    seen = {}

    def fake(system, user, **kwargs):
        seen["user"] = user
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    gen.generate_solution("Big", "x" * (gen.MAX_INSTRUCTION_CHARS + 5000), cfg={})
    assert "[instructions truncated]" in seen["user"]
    assert len(seen["user"]) < gen.MAX_INSTRUCTION_CHARS + 2000

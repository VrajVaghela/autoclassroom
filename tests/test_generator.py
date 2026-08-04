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


TWO_QUESTIONS = {
    "layout": "per_question",
    "report_style": "combined",
    "questions": [
        {"number": 1, "title": "Bubble sort", "instructions": "Write bubble sort."},
        {"number": 2, "title": "Binary search", "instructions": "Write binary search."},
    ],
}


def _fake_two_question_provider(calls=None):
    """A provider that answers the planning call, then one call per question."""

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(TWO_QUESTIONS)
        number = 2 if "question 2 of" in user else 1
        if calls is not None:
            calls.append(user)
        return json.dumps({
            "summary": f"Answer {number}",
            "language": "python",
            "artifacts": [
                {"kind": "code", "filename": "main.py", "content": f"# q{number}\n"},
                {"kind": "docx", "filename": "report.docx",
                 "blocks": [{"type": "heading", "text": f"Aim {number}", "level": 2}]},
            ],
        })

    return fake


def test_each_question_gets_its_own_files(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete", _fake_two_question_provider())
    out = gen.generate_solution("Lab 3", "Q1 bubble sort. Q2 binary search.", cfg={})

    code = [a["filename"] for a in out["artifacts"] if a["kind"] == "code"]
    assert code == ["q1_main.py", "q2_main.py"]
    assert out["questions"] == 2


def test_each_question_is_asked_separately(monkeypatch):
    calls = []
    monkeypatch.setattr(gen.providers, "complete", _fake_two_question_provider(calls))
    gen.generate_solution("Lab 3", "Q1 bubble sort. Q2 binary search.", cfg={})

    assert len(calls) == 2
    assert any("Write bubble sort." in c for c in calls)
    assert any("Write binary search." in c for c in calls)


def test_combined_report_is_merged_into_one_document(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete", _fake_two_question_provider())
    out = gen.generate_solution("Lab 3", "Q1 bubble sort. Q2 binary search.", cfg={})

    docs = [a for a in out["artifacts"] if a["kind"] == "docx"]
    assert len(docs) == 1
    headings = [b["text"] for b in docs[0]["blocks"] if b["type"] == "heading"]
    assert headings == ["Question 1: Bubble sort", "Aim 1",
                        "Question 2: Binary search", "Aim 2"]


def test_separate_report_style_keeps_one_document_per_question(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(dict(TWO_QUESTIONS, report_style="separate"))
        number = 2 if "question 2 of" in user else 1
        return json.dumps({"artifacts": [
            {"kind": "docx", "filename": "report.docx",
             "blocks": [{"type": "heading", "text": f"Aim {number}", "level": 1}]},
        ]})

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 3", "Q1. Q2.", cfg={})
    assert [a["filename"] for a in out["artifacts"]] == ["q1_report.docx", "q2_report.docx"]


def test_one_failing_question_does_not_sink_the_others(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(TWO_QUESTIONS)
        if "question 2 of" in user:
            raise gen.providers.ProviderError("rate limited")
        return json.dumps({"artifacts": [
            {"kind": "code", "filename": "main.py", "content": "x=1"}]})

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 3", "Q1. Q2.", cfg={})
    assert [a["filename"] for a in out["artifacts"]] == ["q1_main.py"]
    assert any("Question 2" in note and "rate limited" in note for note in out["notes"])


def test_failed_plan_falls_back_to_one_call(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            raise gen.providers.ProviderError("model is down")
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 1", "Write a bubble sort.", cfg={})
    assert len(out["artifacts"]) == 2
    assert any("Could not work out" in note for note in out["notes"])


def test_single_question_assignment_is_not_prefixed(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps({"layout": "per_question",
                               "questions": [{"number": 1, "title": "Only",
                                              "instructions": "Do the thing."}]})
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 1", "Do the thing.", cfg={})
    assert [a["filename"] for a in out["artifacts"]] == ["main.py", "report.docx"]


NOTEBOOK_ASSIGNMENT = (
    "Using the attached dataset, complete the following in a single notebook:\n"
    "1. Load the data and describe it.\n"
    "2. Plot the distribution of each column.\n"
    "3. Train a linear regression model and report its score.\n"
)

NOTEBOOK_PLAN = {
    "layout": "single",
    "report_style": "none",
    "questions": [
        {"number": 1, "title": "Load", "instructions": "Load the data."},
        {"number": 2, "title": "Plot", "instructions": "Plot the columns."},
        {"number": 3, "title": "Model", "instructions": "Train a model."},
    ],
}

ONE_NOTEBOOK = {
    "summary": "Notebook covering all three parts.",
    "language": "python",
    "artifacts": [
        {"kind": "notebook", "filename": "lab.ipynb", "title": "Lab",
         "cells": [{"type": "code", "source": "print(1)", "output": "1\n"}]},
    ],
}


def test_single_layout_produces_one_deliverable_for_every_question(monkeypatch):
    solve_calls = []

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(NOTEBOOK_PLAN)
        solve_calls.append(user)
        return json.dumps(ONE_NOTEBOOK)

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("DS Lab 2", NOTEBOOK_ASSIGNMENT, cfg={})

    # One call, one file, no q1_/q2_ prefixes.
    assert len(solve_calls) == 1
    assert [a["filename"] for a in out["artifacts"]] == ["lab.ipynb"]
    assert out["questions"] == 3


def test_single_layout_call_is_told_about_every_question(monkeypatch):
    seen = {}

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(NOTEBOOK_PLAN)
        seen["user"] = user
        return json.dumps(ONE_NOTEBOOK)

    monkeypatch.setattr(gen.providers, "complete", fake)
    gen.generate_solution("DS Lab 2", NOTEBOOK_ASSIGNMENT, cfg={})

    assert "3 questions" in seen["user"]
    assert "Plot" in seen["user"]
    assert "one file per question" in seen["user"]


def test_single_layout_is_not_re_split_when_questions_are_numbered(monkeypatch):
    plans = []

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            plans.append(user)
            return json.dumps(dict(NOTEBOOK_PLAN, questions=NOTEBOOK_PLAN["questions"][:1]))
        return json.dumps(ONE_NOTEBOOK)

    monkeypatch.setattr(gen.providers, "complete", fake)
    gen.generate_solution("DS Lab 2", NOTEBOOK_ASSIGNMENT, cfg={})
    assert len(plans) == 1  # no "you missed some questions" retry


def test_single_layout_falls_back_to_per_question_when_one_call_fails(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps(NOTEBOOK_PLAN)
        if "This is question" not in user:  # the one-call attempt
            return "I could not fit that into one reply."
        return json.dumps({"artifacts": [
            {"kind": "code", "filename": "main.py", "content": "x=1"}]})

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("DS Lab 2", NOTEBOOK_ASSIGNMENT, cfg={})

    assert [a["filename"] for a in out["artifacts"]] == [
        "q1_main.py", "q2_main.py", "q3_main.py"]
    assert any("file per question instead" in note for note in out["notes"])


def test_plan_defaults_to_a_single_deliverable(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete",
                        lambda *a, **k: json.dumps({"questions": []}))
    assert gen.plan_assignment("Lab", "text", cfg={})[2] == "single"


def test_plan_rejects_an_unknown_layout(monkeypatch):
    monkeypatch.setattr(gen.providers, "complete",
                        lambda *a, **k: json.dumps({"layout": "whatever"}))
    assert gen.plan_assignment("Lab", "text", cfg={})[2] == "single"


def test_plan_skips_blank_questions_and_renumbers():
    parsed = {"questions": [{"title": "a", "instructions": "first"},
                            {"title": "empty", "instructions": "  "},
                            {"title": "b", "instructions": "second"}]}
    assert [q["number"] for q in _plan_from(parsed)] == [1, 2]
    assert [q["title"] for q in _plan_from(parsed)] == ["a", "b"]


def _plan_from(parsed):
    """Run plan_assignment against a canned reply."""
    original = gen.providers.complete
    gen.providers.complete = lambda *a, **k: json.dumps(parsed)
    try:
        return gen.plan_assignment("Lab", "text", cfg={})[0]
    finally:
        gen.providers.complete = original


def test_plan_caps_runaway_question_lists():
    parsed = {"questions": [{"title": f"q{i}", "instructions": f"do {i}"}
                            for i in range(gen.MAX_QUESTIONS + 10)]}
    assert len(_plan_from(parsed)) == gen.MAX_QUESTIONS


FOUR_LISTED = (
    "Complete the following:\n"
    "1. Write a program to print the factorial of a number.\n"
    "2. Write a program to reverse a string.\n"
    "3. Write a program to check for a palindrome.\n"
    "4. Write a program to sum a list.\n"
)


@pytest.mark.parametrize("text, expected", [
    (FOUR_LISTED, 4),
    ("Q1. Do this.\nQ2. Do that.\nQ3. Do the other.\n", 3),
    ("Program 1: sort\nProgram 2: search\n", 2),
    ("(1) first\n(2) second\n", 2),
    ("Write a bubble sort program.", 0),
    ("Follow these steps:\nSee section 7.\nRefer to page 12.\n", 0),
])
def test_count_question_markers(text, expected):
    assert gen.count_question_markers(text) == expected


def test_under_split_is_retried_with_a_hint(monkeypatch):
    prompts = []

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            prompts.append(user)
            if len(prompts) == 1:  # first try: lumps everything into one
                return json.dumps({"layout": "per_question", "questions": [
                    {"title": "All of it", "instructions": "Do all four programs."}]})
            return json.dumps({"layout": "per_question", "report_style": "none",
                               "questions": [
                                   {"title": f"Program {i}",
                                    "instructions": f"Write program {i}."}
                                   for i in range(1, 5)]})
        return json.dumps({"artifacts": [
            {"kind": "code", "filename": "main.py", "content": "x=1"}]})

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 6", FOUR_LISTED, cfg={})

    assert len(prompts) == 2
    assert "about 4 separate questions" in prompts[1]
    assert out["questions"] == 4
    assert [a["filename"] for a in out["artifacts"]] == [
        "q1_main.py", "q2_main.py", "q3_main.py", "q4_main.py"]


def test_stubborn_single_split_is_reported(monkeypatch):
    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            return json.dumps({"layout": "per_question", "questions": [
                {"title": "All of it", "instructions": "Do all four programs."}]})
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 6", FOUR_LISTED, cfg={})
    assert any("kept the assignment as one task" in note for note in out["notes"])


def test_retry_failure_keeps_the_first_split(monkeypatch):
    calls = []

    def fake(system, user, **kwargs):
        if system is gen.PLAN_SYSTEM_PROMPT:
            calls.append(user)
            if len(calls) == 2:
                raise gen.providers.ProviderError("rate limited")
            return json.dumps({"layout": "per_question", "questions": [
                {"title": "A", "instructions": "Do A."},
                {"title": "B", "instructions": "Do B."}]})
        return json.dumps({"artifacts": [
            {"kind": "code", "filename": "main.py", "content": "x=1"}]})

    monkeypatch.setattr(gen.providers, "complete", fake)
    out = gen.generate_solution("Lab 6", FOUR_LISTED, cfg={})
    assert out["questions"] == 2


@pytest.mark.parametrize("name, number, expected", [
    ("main.py", 1, "q1_main.py"),
    ("src/util.py", 2, "src/q2_util.py"),
    ("q3_answer.py", 3, "q3_answer.py"),
    ("Q4-report.docx", 4, "Q4-report.docx"),
    ("question2.c", 2, "question2.c"),
    ("", 5, "q5_solution.txt"),
])
def test_prefix_filename(name, number, expected):
    assert gen._prefix_filename(name, number) == expected


def test_long_instructions_are_truncated(monkeypatch):
    seen = {}

    def fake(system, user, **kwargs):
        seen["user"] = user
        return json.dumps(PLAN)

    monkeypatch.setattr(gen.providers, "complete", fake)
    gen.generate_solution("Big", "x" * (gen.MAX_INSTRUCTION_CHARS + 5000), cfg={})
    assert "[instructions truncated]" in seen["user"]
    assert len(seen["user"]) < gen.MAX_INSTRUCTION_CHARS + 2000

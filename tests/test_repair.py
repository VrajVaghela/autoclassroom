"""Tests for the run -> repair loop, with the model faked out."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers  # noqa: E402
import repair  # noqa: E402

WORKING = "print('WORKS')\n"
BROKEN = "raise ValueError('boom')\n"
SLOW = "while True:\n    pass\n"
INTERACTIVE = "name = input('Name: ')\nprint(name)\n"


def _cfg(**over):
    cfg = {"run_timeout": 15, "repair_attempts": 2}
    cfg.update(over)
    return cfg


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the provider call with a scripted list of replies."""
    calls = []

    def install(*replies):
        queue = list(replies)

        def complete(system, user, **kwargs):
            calls.append({"system": system, "user": user})
            if not queue:
                raise AssertionError("the loop asked for more fixes than expected")
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(providers, "complete", complete)
        return calls

    install.calls = calls
    return install


def _write(tmp_path, name, source):
    (tmp_path / name).write_text(source, encoding="utf-8")
    return name


# ------------------------------------------------------------------ extraction

def test_extract_code_unwraps_a_fence():
    assert repair._extract_code("```python\nx = 1\n```") == "x = 1\n"
    assert repair._extract_code("Here you go:\n```\nx = 1\n```\nDone.") == "x = 1\n"


def test_extract_code_passes_bare_source_through():
    assert repair._extract_code("x = 1") == "x = 1\n"
    assert repair._extract_code("   \n ") is None
    assert repair._extract_code("") is None


def test_extract_code_keeps_fences_inside_the_file():
    reply = "```python\nprint('```')\nx = 1\n```"
    assert "x = 1" in repair._extract_code(reply)


def test_wants_input_spots_a_closed_stdin():
    assert repair.wants_input("EOFError: EOF when reading a line")
    assert not repair.wants_input("ValueError: boom")


def test_attempts_are_clamped():
    assert repair.attempts_for({"repair_attempts": 99}) == repair.MAX_ATTEMPTS
    assert repair.attempts_for({"repair_attempts": -3}) == 0
    assert repair.attempts_for({"repair_attempts": "two"}) == 0
    assert repair.attempts_for({}) == 2  # the configured default


# ----------------------------------------------------------------------- files

def test_working_code_is_never_sent_to_the_model(tmp_path, fake_model):
    fake_model()  # any call at all raises
    _write(tmp_path, "main.py", WORKING)
    captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert "WORKS" in captured["main.py"][1]
    assert repairs == {}
    assert notes == []


def test_failing_code_is_repaired_and_rerun(tmp_path, fake_model):
    calls = fake_model(f"```python\n{WORKING}```")
    _write(tmp_path, "main.py", BROKEN)
    captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert "WORKS" in captured["main.py"][1]
    assert "boom" not in captured["main.py"][1]
    assert (tmp_path / "main.py").read_text() == WORKING
    assert repairs["main.py"] == (BROKEN, WORKING)
    assert any("repaired in 1 attempt" in n for n in notes)
    # The model saw the source and the traceback it has to work from.
    assert "raise ValueError" in calls[0]["user"]
    assert "boom" in calls[0]["user"]


def test_second_attempt_gets_the_new_error(tmp_path, fake_model):
    still_broken = "raise TypeError('second')\n"
    calls = fake_model(still_broken, WORKING)
    _write(tmp_path, "main.py", BROKEN)
    _captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert repairs["main.py"] == (BROKEN, WORKING)
    assert any("repaired in 2 attempts" in n for n in notes)
    assert "second" in calls[1]["user"], "the retry must show the error it just caused"
    assert "attempt 2 of 2" in calls[1]["user"]


def test_giving_up_restores_the_original_file(tmp_path, fake_model):
    fake_model("raise TypeError('one')\n", "raise TypeError('two')\n")
    _write(tmp_path, "main.py", BROKEN)
    captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert (tmp_path / "main.py").read_text() == BROKEN
    assert repairs == {}
    # The reported output is the original failure, matching the restored file.
    assert "boom" in captured["main.py"][1]
    assert any("still fails after 2 repair attempts" in n for n in notes)


def test_repairs_off_leaves_the_failure_alone(tmp_path, fake_model):
    fake_model()
    _write(tmp_path, "main.py", BROKEN)
    captured, repairs, notes = repair.run_and_repair(
        str(tmp_path), ["main.py"], _cfg(repair_attempts=0)
    )

    assert "boom" in captured["main.py"][1]
    assert (repairs, notes) == ({}, [])


def test_interactive_program_is_not_rewritten(tmp_path, fake_model):
    fake_model()  # asking the model at all would fail the test
    _write(tmp_path, "main.py", INTERACTIVE)
    _captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert (tmp_path / "main.py").read_text() == INTERACTIVE
    assert repairs == {}
    assert any("waits for typed input" in n for n in notes)


def test_a_timed_out_program_is_repairable(tmp_path, fake_model):
    calls = fake_model(WORKING)
    _write(tmp_path, "main.py", SLOW)
    captured, repairs, _notes = repair.run_and_repair(
        str(tmp_path), ["main.py"], _cfg(run_timeout=2)
    )

    assert "never finished" in calls[0]["user"]
    assert "WORKS" in captured["main.py"][1]
    assert repairs["main.py"][1] == WORKING


def test_provider_failure_is_reported_not_raised(tmp_path, fake_model):
    fake_model(providers.ProviderError("rate limited"))
    _write(tmp_path, "main.py", BROKEN)
    captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert (tmp_path / "main.py").read_text() == BROKEN
    assert repairs == {}
    assert "boom" in captured["main.py"][1]
    assert any("rate limited" in n for n in notes)


def test_an_unchanged_reply_stops_the_loop(tmp_path, fake_model):
    calls = fake_model(BROKEN, WORKING)
    _write(tmp_path, "main.py", BROKEN)
    _captured, repairs, _notes = repair.run_and_repair(str(tmp_path), ["main.py"], _cfg())

    assert len(calls) == 1, "the same file back means another round asks the same question"
    assert repairs == {}


def test_unrunnable_files_are_skipped(tmp_path, fake_model):
    fake_model()
    _write(tmp_path, "notes.txt", "not code")
    captured, repairs, notes = repair.run_and_repair(str(tmp_path), ["notes.txt"], _cfg())

    assert (captured, repairs, notes) == ({}, {}, [])


def test_each_file_is_repaired_independently(tmp_path, fake_model):
    fake_model(WORKING)
    _write(tmp_path, "good.py", "print('FINE')\n")
    _write(tmp_path, "bad.py", BROKEN)
    captured, repairs, _notes = repair.run_and_repair(
        str(tmp_path), ["good.py", "bad.py"], _cfg()
    )

    assert "FINE" in captured["good.py"][1]
    assert "WORKS" in captured["bad.py"][1]
    assert list(repairs) == ["bad.py"]


def test_context_reaches_the_prompt(tmp_path, fake_model):
    calls = fake_model(WORKING)
    _write(tmp_path, "main.py", BROKEN)
    repair.run_and_repair(str(tmp_path), ["main.py"], _cfg(), context="Lab 3 — Sorting")

    assert "Lab 3 — Sorting" in calls[0]["user"]


# ------------------------------------------------------------------- notebooks

@pytest.fixture
def fake_kernel(monkeypatch):
    """
    Stand in for nbclient: any cell whose source says BOOM records a traceback.

    Real execution needs a kernel and seconds per run; the loop's job is to
    decide what to do about a failing cell, and that is what this exercises.
    """
    import nbformat
    from nbformat.v4 import new_output

    runs = []

    def execute(path, timeout=60):
        runs.append(path)
        with open(path, "r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
        for cell in nb.cells:
            if cell.cell_type != "code":
                continue
            if "BOOM" in cell.source:
                cell.outputs = [new_output(
                    "error", ename="ValueError", evalue="boom",
                    traceback=["\x1b[0;31mValueError\x1b[0m: boom"],
                )]
            else:
                cell.outputs = [new_output("stream", name="stdout", text="ok\n")]
        with open(path, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)
        return (True, "notebook executed")

    monkeypatch.setattr(repair, "execute_notebook", execute)
    return runs


def _notebook(tmp_path, *sources):
    from artifacts.notebook import write_notebook

    path = str(tmp_path / "lab.ipynb")
    write_notebook(path, "Lab", [{"type": "code", "source": s} for s in sources])
    return path


def _sources(path):
    from artifacts.notebook import notebook_code_cells

    return [source for _index, source in notebook_code_cells(path)]


def test_notebook_cell_is_repaired(tmp_path, fake_model, fake_kernel):
    calls = fake_model("total = 1 + 1")
    path = _notebook(tmp_path, "x = 1", "BOOM")
    notes = repair.repair_notebook(path, _cfg())

    assert _sources(path) == ["x = 1", "total = 1 + 1"]
    assert any("repaired 1 failing cell" in n for n in notes)
    # The prompt carries the traceback, stripped of its terminal colours.
    assert "ValueError: boom" in calls[0]["user"]
    assert "\x1b[" not in calls[0]["user"]
    # Earlier cells go along as context so the fix can use what they defined.
    assert "x = 1" in calls[0]["user"]


def test_notebook_rolls_back_when_repairs_do_not_help(tmp_path, fake_model, fake_kernel):
    fake_model("BOOM again", "BOOM once more")
    path = _notebook(tmp_path, "BOOM")
    notes = repair.repair_notebook(path, _cfg())

    assert _sources(path) == ["BOOM"], "the model's original cell must come back"
    assert any("did not help" in n for n in notes)
    assert fake_kernel[-1] == path, "the rolled-back notebook is executed again"


def test_notebook_repairs_off_only_reports(tmp_path, fake_model, fake_kernel):
    fake_model()
    path = _notebook(tmp_path, "BOOM")
    notes = repair.repair_notebook(path, _cfg(repair_attempts=0))

    assert _sources(path) == ["BOOM"]
    assert any("1 cell raised an error" in n for n in notes)


def test_clean_notebook_says_nothing(tmp_path, fake_model, fake_kernel):
    fake_model()
    path = _notebook(tmp_path, "x = 1")
    assert repair.repair_notebook(path, _cfg()) == []

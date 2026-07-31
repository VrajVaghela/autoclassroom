"""Tests for folder naming and full solution saving."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_manager as fm  # noqa: E402


def test_sanitize_folder_name():
    assert fm.sanitize_folder_name("Lab 1: Sorting") == "Lab 1_ Sorting"
    assert fm.sanitize_folder_name("a/b\\c") == "a_b_c"
    assert fm.sanitize_folder_name("  spaced  out  ") == "spaced out"
    assert fm.sanitize_folder_name("") == "Assignment"
    assert fm.sanitize_folder_name("trailing.") == "trailing"
    assert fm.sanitize_folder_name("con").endswith("_folder")
    assert len(fm.sanitize_folder_name("x" * 300)) <= 120


def test_unique_dir_never_overwrites(tmp_path):
    base = str(tmp_path / "Lab 1")
    assert fm._unique_dir(base) == base
    os.makedirs(base)
    assert fm._unique_dir(base) == base + " (2)"
    os.makedirs(base + " (2)")
    assert fm._unique_dir(base) == base + " (3)"


def _cfg(tmp_path, **over):
    cfg = {"output_dir": str(tmp_path), "run_code": False, "run_timeout": 10}
    cfg.update(over)
    return cfg


def test_save_solution_writes_all_kinds(tmp_path):
    solution = {
        "summary": "s", "language": "python",
        "artifacts": [
            {"kind": "code", "filename": "main.py", "content": "print('Hello')\n"},
            {"kind": "notebook", "filename": "lab.ipynb",
             "cells": [{"type": "code", "source": "print(1)", "output": "1\n"}]},
            {"kind": "docx", "filename": "report.docx", "title": "Lab 1",
             "blocks": [{"type": "screenshot", "command": "python main.py",
                         "output": "predicted\n"}]},
            {"kind": "pdf", "filename": "report.pdf",
             "blocks": [{"type": "paragraph", "text": "hi"}]},
        ],
    }
    res = fm.save_solution("Lab 1: Intro", solution, cfg=_cfg(tmp_path))
    assert sorted(res["files"]) == ["lab.ipynb", "main.py", "report.docx", "report.pdf"]
    out = tmp_path / "Lab 1_ Intro"
    for name in ["main.py", "lab.ipynb", "report.docx", "report.pdf"]:
        assert (out / name).exists(), name


def test_save_solution_second_run_gets_new_folder(tmp_path):
    solution = {"artifacts": [{"kind": "code", "filename": "a.py", "content": "x=1"}]}
    first = fm.save_solution("Lab 2", solution, cfg=_cfg(tmp_path))
    second = fm.save_solution("Lab 2", solution, cfg=_cfg(tmp_path))
    assert first["dir"] != second["dir"]
    assert second["dir"].endswith("(2)")


def test_save_solution_respects_configured_output_dir(tmp_path):
    custom = tmp_path / "somewhere" / "else"
    solution = {"artifacts": [{"kind": "code", "filename": "a.py", "content": "x=1"}]}
    res = fm.save_solution("Lab 3", solution, cfg=_cfg(custom))
    assert str(custom) in res["dir"]
    assert (custom / "Lab 3" / "a.py").exists()


def test_one_bad_artifact_does_not_lose_the_rest(tmp_path, monkeypatch):
    real = fm.write_artifact

    def flaky(target_dir, artifact, index=1, title=""):
        if artifact.get("filename") == "boom.docx":
            raise RuntimeError("renderer exploded")
        return real(target_dir, artifact, index, title)

    monkeypatch.setattr(fm, "write_artifact", flaky)
    solution = {"artifacts": [
        {"kind": "code", "filename": "good.py", "content": "x=1"},
        {"kind": "docx", "filename": "boom.docx", "blocks": []},
    ]}
    res = fm.save_solution("Lab 4", solution, cfg=_cfg(tmp_path))
    assert res["files"] == ["good.py"]
    assert any("boom.docx" in n for n in res["notes"])


def test_all_artifacts_failing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "write_artifact",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    with pytest.raises(ValueError, match="Every file failed"):
        fm.save_solution("Lab 5", {"artifacts": [{"kind": "code", "filename": "a.py"}]},
                         cfg=_cfg(tmp_path))


def test_empty_solution_raises(tmp_path):
    with pytest.raises(ValueError, match="no files to save"):
        fm.save_solution("Lab 6", {"artifacts": []}, cfg=_cfg(tmp_path))


def test_run_code_disabled_keeps_predicted_output(tmp_path):
    solution = {"artifacts": [
        {"kind": "code", "filename": "main.py", "content": "print('REAL OUTPUT')\n"},
        {"kind": "docx", "filename": "r.docx",
         "blocks": [{"type": "screenshot", "command": "python main.py",
                     "output": "PREDICTED"}]},
    ]}
    fm.save_solution("Lab 7", solution, cfg=_cfg(tmp_path, run_code=False))
    # The predicted text is what got rendered; nothing was executed.
    assert solution["artifacts"][1]["blocks"][0]["output"] == "PREDICTED"


def test_run_code_enabled_substitutes_real_output(tmp_path):
    solution = {"artifacts": [
        {"kind": "code", "filename": "main.py", "content": "print('REAL OUTPUT')\n"},
        {"kind": "docx", "filename": "r.docx",
         "blocks": [{"type": "screenshot", "command": "python main.py",
                     "output": "PREDICTED"}]},
    ]}
    res = fm.save_solution("Lab 8", solution, cfg=_cfg(tmp_path, run_code=True))
    block = solution["artifacts"][1]["blocks"][0]
    assert "REAL OUTPUT" in block["output"]
    assert "PREDICTED" not in block["output"]
    assert any("Captured real output" in n for n in res["notes"])


def test_run_code_captures_failure_text(tmp_path):
    solution = {"artifacts": [
        {"kind": "code", "filename": "main.py", "content": "raise ValueError('boom')\n"},
        {"kind": "docx", "filename": "r.docx",
         "blocks": [{"type": "screenshot", "command": "python main.py", "output": "x"}]},
    ]}
    fm.save_solution("Lab 9", solution, cfg=_cfg(tmp_path, run_code=True))
    assert "boom" in solution["artifacts"][1]["blocks"][0]["output"]

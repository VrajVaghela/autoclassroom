"""Tests for path safety and artifact rendering."""

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from artifacts.writer import infer_kind, safe_relpath, write_artifact  # noqa: E402


def test_relpath_keeps_subdirs():
    assert safe_relpath("main.py") == "main.py"
    assert safe_relpath("src/main.py") == "src/main.py"


def test_relpath_blocks_traversal():
    for raw in ["../../etc/passwd", "..\\..\\x.py", "./../../../y.py"]:
        out = safe_relpath(raw)
        assert ".." not in out, (raw, out)
        assert not os.path.isabs(out), (raw, out)


def test_relpath_strips_drive_letters():
    out = safe_relpath("C:/Windows/system32/evil.dll")
    assert out == "Windows/system32/evil.dll", out
    assert ":" not in out


def test_relpath_caps_depth():
    assert safe_relpath("a/b/c/d/e/f/g.py").count("/") <= 4


def test_relpath_windows_reserved_and_empty():
    assert safe_relpath("con.txt").startswith("con_file")
    assert safe_relpath("") == "file.txt"
    assert safe_relpath("we<ir>d|name?.py") == "we_ir_d_name_.py"


def test_infer_kind_prefers_extension():
    assert infer_kind({"kind": "code", "filename": "r.docx"}) == "docx"
    assert infer_kind({"filename": "r.pdf"}) == "pdf"
    assert infer_kind({"filename": "l.ipynb"}) == "notebook"
    # docx kind with a plain extension can't render as docx -> source file
    assert infer_kind({"kind": "docx", "filename": "notes.txt"}) == "code"
    assert infer_kind({"kind": "bogus", "filename": "x"}) == "code"


def test_write_code_artifact(tmp_path):
    res = write_artifact(str(tmp_path), {"kind": "code", "filename": "src/main.py",
                                        "content": "print('hi')\n"})
    assert res["kind"] == "code"
    written = tmp_path / "src" / "main.py"
    assert written.read_text() == "print('hi')\n"


def test_write_code_artifact_escape_contained(tmp_path):
    write_artifact(str(tmp_path), {"kind": "code", "filename": "../../escaped.py",
                                   "content": "x = 1"})
    assert not (tmp_path.parent.parent / "escaped.py").exists()
    assert (tmp_path / "escaped.py").exists()


def test_write_notebook(tmp_path):
    import nbformat
    res = write_artifact(str(tmp_path), {
        "kind": "notebook", "filename": "lab1.ipynb", "title": "Lab 1",
        "cells": [
            {"type": "markdown", "source": "## Aim\nAdd numbers."},
            {"type": "code", "source": "print(2 + 2)", "output": "4\n"},
        ],
    })
    nb = nbformat.read(res["path"], as_version=4)
    assert nb.cells[0].cell_type == "markdown"
    assert "Lab 1" in nb.cells[0].source
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert code[0].outputs[0].text == "4\n"
    nbformat.validate(nb)


def test_write_docx_with_screenshot(tmp_path):
    res = write_artifact(str(tmp_path), {
        "kind": "docx", "filename": "report.docx", "title": "Lab Report",
        "blocks": [
            {"type": "heading", "text": "Aim", "level": 1},
            {"type": "paragraph", "text": "Demonstrate output capture."},
            {"type": "list", "items": ["one", "two"], "ordered": True},
            {"type": "code", "text": "print('Hello')", "language": "python"},
            {"type": "screenshot", "command": "python main.py",
             "output": "Hello\n", "caption": "Program output"},
            {"type": "table", "headers": ["Input", "Output"], "rows": [[1, 2], [3, 4]]},
            {"type": "pagebreak"},
        ],
    })
    assert os.path.getsize(res["path"]) > 5000
    with zipfile.ZipFile(res["path"]) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert media, "screenshot image was not embedded in the docx"
    assert (tmp_path / "assets" / "output_01.png").exists()


def test_write_pdf_with_screenshot(tmp_path):
    res = write_artifact(str(tmp_path), {
        "kind": "pdf", "filename": "report.pdf", "title": "Lab Report",
        "blocks": [
            {"type": "heading", "text": "Aim", "level": 1},
            {"type": "paragraph", "text": "Angle brackets <b> & ampersands must not break."},
            {"type": "code", "text": "x = 1 < 2"},
            {"type": "screenshot", "command": "python main.py", "output": "Hello\n"},
            {"type": "table", "headers": ["A", "B"], "rows": [["<x>", "&y"]]},
        ],
    })
    with open(res["path"], "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert os.path.getsize(res["path"]) > 2000


def test_malformed_blocks_are_skipped(tmp_path):
    res = write_artifact(str(tmp_path), {
        "kind": "docx", "filename": "r.docx",
        "blocks": ["not a dict", None, {"type": "nonsense"}, {"type": "paragraph", "text": "ok"}],
    })
    assert os.path.exists(res["path"])


def test_empty_artifacts_still_write(tmp_path):
    for spec in [{"kind": "docx", "filename": "e.docx", "blocks": []},
                 {"kind": "pdf", "filename": "e.pdf", "blocks": []},
                 {"kind": "notebook", "filename": "e.ipynb", "cells": []},
                 {"kind": "code", "filename": "e.py"}]:
        res = write_artifact(str(tmp_path), spec)
        assert os.path.exists(res["path"]), spec

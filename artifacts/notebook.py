"""
Jupyter notebook (.ipynb) generation.

The LLM supplies a cell list:
  {"type": "markdown", "source": "..."}
  {"type": "code", "source": "...", "output": "expected stdout"}

Code cells carry their expected output so the notebook reads as if it had been
run. When local execution is enabled the notebook is executed for real and the
recorded outputs are replaced with genuine ones.
"""

import os
import re

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

# Jupyter tracebacks are coloured; the escape codes are noise in a prompt.
_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _cells(spec):
    cells = []
    exec_count = 0
    for item in spec or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source") or item.get("text") or ""
        if (item.get("type") or "code").lower() == "markdown":
            cells.append(new_markdown_cell(source))
            continue

        exec_count += 1
        cell = new_code_cell(source, execution_count=exec_count)
        output = item.get("output")
        if output:
            cell.outputs = [new_output("stream", name="stdout", text=str(output))]
        cells.append(cell)
    return cells


def write_notebook(path, title, cells_spec, kernel="python3", language="python"):
    nb = new_notebook()
    nb.cells = []
    if title:
        nb.cells.append(new_markdown_cell(f"# {title}"))
    nb.cells.extend(_cells(cells_spec))
    if not nb.cells:
        nb.cells.append(new_markdown_cell("# Empty notebook"))

    nb.metadata.update({
        "kernelspec": {
            "display_name": "Python 3" if language == "python" else language,
            "language": language,
            "name": kernel,
        },
        "language_info": {"name": language},
    })

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    return path


def execute_notebook(path, timeout=60):
    """
    Run the notebook in place so outputs are real. Returns (ok, message).

    Requires nbclient + a kernel; treated as optional since a notebook with
    predicted outputs is still a valid deliverable.
    """
    try:
        from nbclient import NotebookClient
    except ImportError:
        return (False, "nbclient not installed; kept predicted outputs")

    try:
        with open(path, "r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
        client = NotebookClient(
            nb, timeout=timeout, kernel_name="python3", allow_errors=True
        )
        client.execute(cwd=os.path.dirname(os.path.abspath(path)))
        with open(path, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)
        return (True, "notebook executed")
    except Exception as e:
        return (False, f"notebook execution failed: {type(e).__name__}: {e}")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return nbformat.read(f, as_version=4)


def notebook_errors(path):
    """
    Cells that raised on the last execution: [(index, source, error_text), ...].

    execute_notebook runs with allow_errors=True, so a notebook that blew up in
    cell 3 still writes successfully — the failure only shows up here, in the
    recorded outputs.
    """
    try:
        nb = _read(path)
    except (OSError, ValueError, nbformat.reader.NotJSONError):
        return []

    found = []
    for index, cell in enumerate(nb.cells):
        if cell.get("cell_type") != "code":
            continue
        for output in cell.get("outputs") or []:
            if output.get("output_type") != "error":
                continue
            text = "\n".join(output.get("traceback") or []) or (
                f"{output.get('ename')}: {output.get('evalue')}"
            )
            found.append((index, cell.get("source") or "", _ANSI.sub("", text).strip()))
            break
    return found


def notebook_code_cells(path):
    """Every code cell in order, as [(index, source), ...]."""
    try:
        nb = _read(path)
    except (OSError, ValueError, nbformat.reader.NotJSONError):
        return []
    return [
        (i, c.get("source") or "")
        for i, c in enumerate(nb.cells)
        if c.get("cell_type") == "code"
    ]


def patch_notebook_cell(path, index, source):
    """Swap one cell's source and drop its stale outputs. Returns True if written."""
    try:
        nb = _read(path)
    except (OSError, ValueError, nbformat.reader.NotJSONError):
        return False
    if not 0 <= index < len(nb.cells):
        return False

    cell = nb.cells[index]
    cell["source"] = source
    cell["outputs"] = []
    cell["execution_count"] = None
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    return True

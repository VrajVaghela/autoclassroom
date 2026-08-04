"""
End-to-end check against a real running server.

Boots server.py in-process on a spare port, stubs only the two things that
touch the outside world (Google Classroom and the LLM), then drives the same
HTTP calls the extension makes and inspects the files that land on disk.

    python tests/e2e_check.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import providers  # noqa: E402
import server  # noqa: E402

PORT = 5099
BASE = f"http://127.0.0.1:{PORT}"
HEAD = {"X-AutoClassroom-Client": "e2e", "Content-Type": "application/json"}

ASSIGNMENT = """
Lab 4: Bubble Sort

Write a Python program that sorts a list of integers using bubble sort.
Submit your code and a lab report containing the aim, the code, a screenshot
of the output, and a conclusion.
"""

MODEL_REPLY = json.dumps({
    "summary": "Bubble sort implementation with a lab report.",
    "language": "python",
    "artifacts": [
        {
            "kind": "code",
            "filename": "bubble_sort.py",
            "content": (
                "def bubble_sort(values):\n"
                "    items = list(values)\n"
                "    for i in range(len(items)):\n"
                "        for j in range(len(items) - i - 1):\n"
                "            if items[j] > items[j + 1]:\n"
                "                items[j], items[j + 1] = items[j + 1], items[j]\n"
                "    return items\n\n\n"
                "if __name__ == '__main__':\n"
                "    data = [5, 2, 9, 1, 7]\n"
                "    print('Unsorted:', data)\n"
                "    print('Sorted:  ', bubble_sort(data))\n"
            ),
        },
        {
            "kind": "docx",
            "filename": "lab4_report.docx",
            "title": "Lab 4: Bubble Sort",
            "blocks": [
                {"type": "heading", "text": "Aim", "level": 1},
                {"type": "paragraph", "text": "To sort integers using bubble sort."},
                {"type": "heading", "text": "Code", "level": 1},
                {"type": "code", "text": "print('see bubble_sort.py')"},
                {"type": "heading", "text": "Output", "level": 1},
                {"type": "screenshot", "command": "python bubble_sort.py",
                 "output": "PREDICTED OUTPUT", "caption": "Program output"},
                {"type": "table", "headers": ["Case", "Complexity"],
                 "rows": [["Best", "O(n)"], ["Worst", "O(n^2)"]]},
                {"type": "heading", "text": "Conclusion", "level": 1},
                {"type": "paragraph", "text": "Bubble sort works but is O(n^2)."},
            ],
        },
        {
            "kind": "notebook",
            "filename": "lab4.ipynb",
            "title": "Lab 4: Bubble Sort",
            "cells": [
                {"type": "markdown", "source": "## Aim\nSort integers."},
                {"type": "code", "source": "print(sorted([5, 2, 9]))", "output": "[2, 5, 9]\n"},
            ],
        },
        {
            "kind": "pdf",
            "filename": "lab4_report.pdf",
            "title": "Lab 4: Bubble Sort",
            "blocks": [
                {"type": "heading", "text": "Aim", "level": 1},
                {"type": "paragraph", "text": "Handles <angle> & ampersand safely."},
            ],
        },
    ],
})

TWO_QUESTIONS = """
Lab 5: Two Programs

Q1. Write a Python program that prints the factorial of 5.
Q2. Write a Python program that prints a reversed string.

Submit a single lab report covering both questions.
"""

PLAN_REPLY = json.dumps({
    "layout": "per_question",
    "report_style": "combined",
    "questions": [
        {"number": 1, "title": "Factorial", "instructions": "Print the factorial of 5."},
        {"number": 2, "title": "Reverse", "instructions": "Print a reversed string."},
    ],
})


def _question_reply(number, name, code, aim):
    return json.dumps({
        "summary": f"Question {number}",
        "language": "python",
        "artifacts": [
            {"kind": "code", "filename": f"q{number}_{name}.py", "content": code},
            {"kind": "docx", "filename": f"q{number}_report.docx", "blocks": [
                {"type": "heading", "text": "Aim", "level": 2},
                {"type": "paragraph", "text": aim},
                {"type": "screenshot", "command": f"python q{number}_{name}.py",
                 "output": "PREDICTED OUTPUT", "caption": "Program output"},
            ]},
        ],
    })


NOTEBOOK_ASSIGNMENT = """
Lab 7: Data Analysis

1. Load the dataset and print how many rows it has.
2. Print the mean of the first column.
3. Print the largest value in the second column.

Submit a single Colab notebook (.ipynb). Do not submit separate files.
"""

NOTEBOOK_PLAN_REPLY = json.dumps({
    "layout": "single",
    "report_style": "none",
    "questions": [
        {"number": 1, "title": "Load", "instructions": "Load the dataset."},
        {"number": 2, "title": "Mean", "instructions": "Print the column mean."},
        {"number": 3, "title": "Max", "instructions": "Print the largest value."},
    ],
})

ONE_NOTEBOOK_REPLY = json.dumps({
    "summary": "One notebook covering all three parts.",
    "language": "python",
    "artifacts": [
        {
            "kind": "notebook",
            "filename": "lab7.ipynb",
            "title": "Lab 7: Data Analysis",
            "cells": [
                {"type": "markdown", "source": "## 1. Load the data"},
                {"type": "code", "source": "data = [[1, 2], [3, 8]]\nprint(len(data))",
                 "output": "2\n"},
                {"type": "markdown", "source": "## 2. Mean of the first column"},
                {"type": "code", "source": "print(sum(r[0] for r in data) / len(data))",
                 "output": "2.0\n"},
                {"type": "markdown", "source": "## 3. Largest value in the second column"},
                {"type": "code", "source": "print(max(r[1] for r in data))", "output": "8\n"},
            ],
        },
    ],
})

BROKEN_ASSIGNMENT = """
Lab 6: Broken Program

Write a Python program that prints a greeting. Submit the code and a report
containing the code listing and a screenshot of the output.
"""

BROKEN_CODE = "print(greeting_that_was_never_defined)\n"
FIXED_CODE = "print('FIXED OUTPUT')\n"

BROKEN_REPLY = json.dumps({
    "summary": "A program that does not run.",
    "language": "python",
    "artifacts": [
        {"kind": "code", "filename": "greet.py", "content": BROKEN_CODE},
        {"kind": "docx", "filename": "lab6_report.docx", "title": "Lab 6", "blocks": [
            {"type": "heading", "text": "Code", "level": 1},
            {"type": "code", "text": BROKEN_CODE},
            {"type": "screenshot", "command": "python greet.py",
             "output": "PREDICTED OUTPUT", "caption": "Program output"},
        ]},
    ],
})


def repairing_model(system, user, **kwargs):
    """Stub provider that writes code that crashes, then fixes it when asked."""
    import repair

    if system is repair.REPAIR_SYSTEM_PROMPT:
        return f"```python\n{FIXED_CODE}```"
    return BROKEN_REPLY


def two_question_model(system, user, **kwargs):
    """Stub provider for the multi-question run: plan first, then one per question."""
    import llm_generator

    if system is llm_generator.PLAN_SYSTEM_PROMPT:
        return PLAN_REPLY
    if "question 2 of" in user:
        return _question_reply(2, "reverse", "print('dlrow olleh'[::-1])\n",
                               "To reverse a string.")
    return _question_reply(1, "factorial", "print(120)\n", "To compute 5!.")


solve_calls = []


def one_notebook_model(system, user, **kwargs):
    """Stub provider for an assignment handed in as one notebook."""
    import llm_generator

    if system is llm_generator.PLAN_SYSTEM_PROMPT:
        return NOTEBOOK_PLAN_REPLY
    if system is llm_generator.SYSTEM_PROMPT:
        solve_calls.append(user)
    return ONE_NOTEBOOK_REPLY


failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main():
    workdir = tempfile.mkdtemp(prefix="autoclassroom-e2e-")
    cfg_path = os.path.join(workdir, "config.json")
    output_dir = os.path.join(workdir, "solutions")

    # Point config at a throwaway file so real settings are untouched.
    config.CONFIG_PATH = cfg_path
    config.CONFIG_DIR = workdir
    config.DEFAULT_OUTPUT_DIR = output_dir

    # Stub the two external dependencies.
    server.get_assignment_details = lambda course_id, work_id: ("Lab 4: Bubble Sort", ASSIGNMENT)
    providers.complete = lambda system, user, **kwargs: MODEL_REPLY

    thread = threading.Thread(
        target=lambda: server.app.run(host="127.0.0.1", port=PORT, debug=False,
                                      use_reloader=False),
        daemon=True,
    )
    thread.start()

    for _ in range(60):
        try:
            requests.get(f"{BASE}/health", headers=HEAD, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        print("server never came up")
        return 1

    try:
        print("\n1. Auth guard")
        unguarded = requests.get(f"{BASE}/health", timeout=5)
        check("request without client header is rejected", unguarded.status_code == 403,
              f"got {unguarded.status_code}")
        check("request with client header is accepted",
              requests.get(f"{BASE}/health", headers=HEAD, timeout=5).status_code == 200)

        print("\n2. Settings")
        resp = requests.post(f"{BASE}/settings", headers=HEAD, timeout=10, json={
            "output_dir": output_dir,
            "provider": "openai",
            "models": {"openai": "gpt-4o"},
            "api_keys": {"openai": "sk-e2e-secret-key-1234567890"},
            "run_code": True,
        })
        check("settings saved", resp.status_code == 200, resp.text[:200])

        body = requests.get(f"{BASE}/settings", headers=HEAD, timeout=5)
        check("raw key never returned", "sk-e2e-secret-key-1234567890" not in body.text)
        entry = [p for p in body.json()["providers"] if p["key"] == "openai"][0]
        check("key is masked", entry["masked_key"] == "sk-e********7890", entry["masked_key"])
        check("output dir applied", body.json()["output_dir"] == output_dir)
        check("run_code applied", body.json()["run_code"] is True)

        print("\n3. Full assignment run")
        run = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=180,
                            json={"courseId": "123", "courseWorkId": "456"})
        check("run succeeded", run.status_code == 200, run.text[:300])
        if run.status_code != 200:
            return 1
        data = run.json()
        check("four files written", len(data["files"]) == 4, str(data["files"]))
        check("summary returned", bool(data["summary"]))

        target = data["dir"]
        print(f"     output: {target}")

        print("\n4. Generated files")
        for name in ["bubble_sort.py", "lab4_report.docx", "lab4.ipynb", "lab4_report.pdf"]:
            path = os.path.join(target, name)
            check(f"{name} exists", os.path.exists(path))
            if os.path.exists(path):
                check(f"{name} is non-trivial", os.path.getsize(path) > 200,
                      f"{os.path.getsize(path)} bytes")

        docx_path = os.path.join(target, "lab4_report.docx")
        if os.path.exists(docx_path):
            with zipfile.ZipFile(docx_path) as z:
                media = [n for n in z.namelist() if n.startswith("word/media/")]
                doc_xml = z.read("word/document.xml").decode("utf-8", "ignore")
            check("screenshot embedded in docx", len(media) == 1, str(media))
            check("report headings present", "Conclusion" in doc_xml)
            check("table rendered", "Complexity" in doc_xml)

        pdf_path = os.path.join(target, "lab4_report.pdf")
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                check("pdf has a valid header", f.read(5) == b"%PDF-")

        nb_path = os.path.join(target, "lab4.ipynb")
        if os.path.exists(nb_path):
            import nbformat
            nb = nbformat.read(nb_path, as_version=4)
            nbformat.validate(nb)
            check("notebook is valid nbformat", True)
            code_cells = [c for c in nb.cells if c.cell_type == "code"]
            check("notebook has a code cell with output",
                  code_cells and bool(code_cells[0].outputs))

        print("\n5. Real output capture (run_code was on)")
        shot = os.path.join(target, "assets", "output_01.png")
        check("screenshot png written", os.path.exists(shot))
        check("real output replaced prediction",
              any("Captured real output" in n for n in data["notes"]),
              str(data["notes"]))

        print("\n6. Repeat run does not overwrite")
        again = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=180,
                              json={"courseId": "123", "courseWorkId": "456"})
        check("second run used a new folder",
              again.status_code == 200 and again.json()["dir"] != target,
              again.text[:200])

        print("\n7. Multi-question assignment -> one file per question")
        original_details = server.get_assignment_details
        original_complete = providers.complete
        server.get_assignment_details = lambda c, w: ("Lab 5: Two Programs", TWO_QUESTIONS)
        providers.complete = two_question_model
        try:
            multi = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=180,
                                  json={"courseId": "123", "courseWorkId": "789"})
            check("multi-question run succeeded", multi.status_code == 200, multi.text[:300])
            if multi.status_code == 200:
                info = multi.json()
                check("two questions detected", info.get("questions") == 2, str(info.get("questions")))
                names = sorted(info["files"])
                check("one code file per question",
                      names[:2] == ["q1_factorial.py", "q2_reverse.py"], str(names))
                check("reports merged into one document",
                      len([n for n in names if n.endswith(".docx")]) == 1, str(names))

                merged = os.path.join(info["dir"], "report.docx")
                if os.path.exists(merged):
                    with zipfile.ZipFile(merged) as z:
                        xml = z.read("word/document.xml").decode("utf-8", "ignore")
                    check("merged report covers question 1", "Question 1: Factorial" in xml)
                    check("merged report covers question 2", "Question 2: Reverse" in xml)
        finally:
            server.get_assignment_details = original_details
            providers.complete = original_complete

        print("\n8. Assignment asking for one file -> one file, not one per question")
        original_details = server.get_assignment_details
        original_complete = providers.complete
        server.get_assignment_details = lambda c, w: ("Lab 7: Data Analysis",
                                                      NOTEBOOK_ASSIGNMENT)
        providers.complete = one_notebook_model
        solve_calls.clear()
        try:
            single = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=180,
                                   json={"courseId": "123", "courseWorkId": "321"})
            check("single-deliverable run succeeded", single.status_code == 200,
                  single.text[:300])
            if single.status_code == 200:
                info = single.json()
                names = sorted(info["files"])
                check("one notebook, no per-question files",
                      [n for n in names if n.endswith(".ipynb")] == ["lab7.ipynb"], str(names))
                check("nothing was prefixed q1_/q2_",
                      not any(n.startswith("q") and n[1:2].isdigit() for n in names),
                      str(names))
                check("solved in a single model call", len(solve_calls) == 1,
                      f"{len(solve_calls)} call(s)")
                check("the call listed every question",
                      solve_calls and "3 questions" in solve_calls[0])
                check("all three questions reported", info.get("questions") == 3,
                      str(info.get("questions")))
        finally:
            server.get_assignment_details = original_details
            providers.complete = original_complete

        print("\n9. Code that fails is repaired and the report follows")
        original_details = server.get_assignment_details
        original_complete = providers.complete
        server.get_assignment_details = lambda c, w: ("Lab 6: Broken Program",
                                                      BROKEN_ASSIGNMENT)
        providers.complete = repairing_model
        try:
            settings = requests.post(f"{BASE}/settings", headers=HEAD, timeout=10,
                                     json={"repair_attempts": 1})
            check("repair_attempts applied",
                  settings.status_code == 200
                  and settings.json()["settings"]["repair_attempts"] == 1,
                  settings.text[:200])

            fix = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=180,
                                json={"courseId": "123", "courseWorkId": "999"})
            check("repair run succeeded", fix.status_code == 200, fix.text[:300])
            if fix.status_code == 200:
                info = fix.json()
                check("repair reported to the user",
                      any("repaired" in n for n in info["notes"]), str(info["notes"]))

                source = os.path.join(info["dir"], "greet.py")
                if os.path.exists(source):
                    with open(source, encoding="utf-8") as f:
                        check("the saved file is the working one", f.read() == FIXED_CODE)

                report = os.path.join(info["dir"], "lab6_report.docx")
                if os.path.exists(report):
                    with zipfile.ZipFile(report) as z:
                        xml = z.read("word/document.xml").decode("utf-8", "ignore")
                    check("report lists the repaired code", "FIXED OUTPUT" in xml)
                    check("report no longer lists the broken code",
                          "greeting_that_was_never_defined" not in xml)
        finally:
            server.get_assignment_details = original_details
            providers.complete = original_complete
            requests.post(f"{BASE}/settings", headers=HEAD, timeout=10,
                          json={"repair_attempts": 2})

        print("\n10. Error handling")
        bad = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=10, json={})
        check("missing ids -> 400", bad.status_code == 400)

        original = providers.complete
        providers.complete = lambda *a, **k: "I refuse to answer."
        try:
            broken = requests.post(f"{BASE}/process_assignment", headers=HEAD, timeout=30,
                                   json={"courseId": "1", "courseWorkId": "2"})
            check("unparseable model reply -> 422", broken.status_code == 422,
                  f"got {broken.status_code}: {broken.text[:150]}")
        finally:
            providers.complete = original

        def refuse(provider, cfg=None):
            raise providers.ProviderError("HTTP 401: invalid key")

        original_test = providers.test
        providers.test = refuse
        try:
            t = requests.post(f"{BASE}/test_provider", headers=HEAD, timeout=10,
                              json={"provider": "openai"})
            check("failed connection test reports cleanly",
                  t.status_code == 200 and t.json()["success"] is False)
        finally:
            providers.test = original_test

        print("\n" + "=" * 60)
        if failures:
            print(f"{len(failures)} check(s) FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("All end-to-end checks passed.")
        return 0

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

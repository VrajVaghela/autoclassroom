"""
Native folder picker, run as a subprocess by the server.

tkinter needs to own its process's main thread, which a Flask request thread is
not. Prints the chosen path to stdout, or nothing if the user cancels.

    python folder_picker.py [initial_dir]
"""

import os
import sys


def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    if initial and not os.path.isdir(initial):
        initial = ""

    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        print("tkinter is not available in this Python install.", file=sys.stderr)
        return 1

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    try:
        path = filedialog.askdirectory(
            title="Choose where AutoClassroom saves your solutions",
            initialdir=initial or os.path.expanduser("~"),
            mustexist=False,
        )
    finally:
        root.destroy()

    if not path:
        print("cancelled", file=sys.stderr)
        return 0

    print(os.path.normpath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())

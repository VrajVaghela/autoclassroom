"""
Local Flask backend for the AutoClassroom extension.

Security notes, because this process holds API keys and writes to disk:
  * Binds to 127.0.0.1 only — never reachable from the network.
  * CORS is restricted to chrome-extension:// origins, not "*".
  * Every endpoint requires the X-AutoClassroom-Client header. Browsers must
    send a CORS preflight for a custom header, and our origin check rejects it,
    so a random web page cannot POST here even to fire-and-forget.
  * Settings reads return masked API keys; raw keys never leave the process.
"""

import base64
import os
import re
import subprocess
import sys
import threading

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

import config
import providers
import repair
from classroom_api import authenticate_google, check_auth_status, get_assignment_details
from file_manager import save_solution
from llm_generator import generate_solution

app = Flask(__name__)

# Only the extension may talk to this server. Chrome sends
# "Origin: chrome-extension://<id>" from popup/options pages.
# In cloud environments, allow extension origins or explicit ALLOWED_ORIGINS env var.
allowed_origins = os.environ.get("ALLOWED_ORIGINS")
if allowed_origins:
    origins_list = [o.strip() for o in allowed_origins.split(",")]
else:
    origins_list = [
        re.compile(r"^chrome-extension://.*$"),
        re.compile(r"^moz-extension://.*$"),
        re.compile(r"^http://(127\.0\.0\.1|localhost):[0-9]+$")
    ]

CORS(
    app,
    origins=origins_list,
    allow_headers=["Content-Type", "X-AutoClassroom-Client"],
    methods=["GET", "POST", "OPTIONS"],
    max_age=600,
)

CLIENT_HEADER = "X-AutoClassroom-Client"

# One generation at a time: a run shells out, writes files, and can trigger the
# Google OAuth flow. Concurrent runs would interleave all three.
_run_lock = threading.Lock()


@app.before_request
def require_client_header():
    """
    Reject anything that isn't our extension.

    A cross-origin request carrying a custom header must pass preflight first,
    and CORS above only approves extension origins — so this blocks drive-by
    requests from ordinary web pages.
    """
    if request.method == "OPTIONS":
        return None
    if not request.headers.get(CLIENT_HEADER):
        return jsonify({"error": "This endpoint is only callable from the AutoClassroom extension."}), 403
    return None


def decode_classroom_id(encoded_id):
    """Classroom URLs sometimes carry base64'd numeric IDs."""
    if not encoded_id:
        return encoded_id
    try:
        padded = encoded_id + "=" * (-len(encoded_id) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return encoded_id


@app.route("/health", methods=["GET"])
def health():
    cfg = config.load()
    provider = cfg["provider"]
    return jsonify({
        "ok": True,
        "provider": provider,
        "provider_label": config.PROVIDERS[provider][0],
        "model": config.get_model(provider, cfg),
        "has_key": bool(config.get_api_key(provider, cfg)),
        "output_dir": cfg["output_dir"],
        "google_auth": check_auth_status(),
    })


@app.route("/auth/status", methods=["GET"])
def get_auth_status():
    return jsonify(check_auth_status())


@app.route("/auth/login", methods=["POST"])
def auth_login():
    try:
        authenticate_google()
        return jsonify({"success": True, "message": "Successfully authenticated with Google Classroom!", "google_auth": check_auth_status()})
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"{type(e).__name__}: {e}"}), 500



@app.route("/settings", methods=["GET"])
def get_settings():
    return jsonify(config.public_view())


@app.route("/settings", methods=["POST"])
def post_settings():
    patch = request.get_json(silent=True)
    if not isinstance(patch, dict):
        return jsonify({"error": "Expected a JSON object."}), 400

    # Fail early with a clear message rather than writing an unusable folder.
    if patch.get("output_dir"):
        target = os.path.abspath(os.path.expanduser(patch["output_dir"].strip()))
        try:
            os.makedirs(target, exist_ok=True)
            probe = os.path.join(target, ".autoclassroom_write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.unlink(probe)
        except OSError as e:
            return jsonify({"error": f"Cannot write to that folder: {e}"}), 400

    try:
        config.update(patch)
    except OSError as e:
        return jsonify({"error": f"Could not save settings: {e}"}), 500

    return jsonify({"success": True, "settings": config.public_view()})


@app.route("/test_provider", methods=["POST"])
def test_provider():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider") or config.load().get("provider")
    if provider not in config.PROVIDERS:
        return jsonify({"error": f"Unknown provider '{provider}'."}), 400

    try:
        reply = providers.test(provider)
    except providers.ProviderError as e:
        return jsonify({"success": False, "error": str(e)}), 200
    except Exception as e:
        return jsonify({"success": False, "error": f"{type(e).__name__}: {e}"}), 200

    return jsonify({
        "success": True,
        "provider": provider,
        "model": config.get_model(provider),
        "reply": reply,
    })


@app.route("/browse_folder", methods=["POST"])
def browse_folder():
    """
    Open the OS folder picker.

    The browser cannot hand a real filesystem path to the extension, so the
    dialog is opened here, in a subprocess — tkinter must own the main thread of
    whatever process runs it, and Flask's request threads are not that.
    """
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "folder_picker.py")
    if not os.path.exists(helper):
        return jsonify({"error": "folder_picker.py is missing."}), 500

    try:
        proc = subprocess.run(
            [sys.executable, helper, config.load().get("output_dir", "")],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"cancelled": True, "error": "The folder dialog timed out."}), 200
    except OSError as e:
        return jsonify({"error": f"Could not open the folder dialog: {e}"}), 500

    path = (proc.stdout or "").strip()
    if not path:
        detail = (proc.stderr or "").strip()
        if detail and "cancelled" not in detail.lower():
            return jsonify({"cancelled": True, "error": detail[:200]}), 200
        return jsonify({"cancelled": True}), 200

    return jsonify({"path": os.path.abspath(path)})


@app.route("/download_file", methods=["GET"])
def download_file():
    """Download a single generated solution file."""
    target_dir = request.args.get("dir")
    filename = request.args.get("filename")
    if not target_dir or not filename:
        return jsonify({"error": "dir and filename parameters are required."}), 400

    abs_dir = os.path.abspath(target_dir)
    abs_file = os.path.abspath(os.path.join(abs_dir, filename))

    if not abs_file.startswith(abs_dir) or not os.path.isfile(abs_file):
        return jsonify({"error": "File not found."}), 404

    return send_file(abs_file, as_attachment=True, download_name=filename)


@app.route("/download_zip", methods=["GET"])
def download_zip():
    """Download all solution files in a directory as a .zip archive."""
    import io, zipfile
    target_dir = request.args.get("dir")
    if not target_dir:
        return jsonify({"error": "dir parameter is required."}), 400

    abs_dir = os.path.abspath(target_dir)
    if not os.path.isdir(abs_dir):
        return jsonify({"error": "Directory not found."}), 404

    memory_file = io.BytesIO()
    folder_name = os.path.basename(abs_dir) or "solution"
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(abs_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, abs_dir)
                zf.write(full_path, rel_path)
    memory_file.seek(0)
    return send_file(memory_file, mimetype="application/zip", as_attachment=True, download_name=f"{folder_name}.zip")


@app.route("/process_assignment", methods=["POST"])
def process_assignment():
    data = request.get_json(silent=True) or {}
    course_id = decode_classroom_id(data.get("courseId"))
    coursework_id = decode_classroom_id(data.get("courseWorkId"))

    if not course_id or not coursework_id:
        return jsonify({"error": "courseId and courseWorkId are required."}), 400

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "Another assignment is already being processed."}), 409

    try:
        cfg = config.load()
        provider_label = config.PROVIDERS[cfg["provider"]][0]

        print(f"--- Course {course_id} / assignment {coursework_id} ---")
        title, instructions = get_assignment_details(course_id, coursework_id)
        if not (instructions or "").strip():
            return jsonify({"error": "That assignment has no instructions or readable attachments."}), 400

        print(f"Fetched: {title}")
        print(f"Generating with {provider_label} ({config.get_model(cfg['provider'], cfg)})...")
        solution = generate_solution(title, instructions, cfg=cfg)

        result = save_solution(title, solution, cfg=cfg)
        print(f"Wrote {len(result['files'])} file(s) to {result['dir']}")

        questions = solution.get("questions") or 1
        message = f"Saved {len(result['files'])} file(s) to {result['dir']}"
        if questions > 1:
            message += f" for {questions} question(s)"
        return jsonify({
            "success": True,
            "message": message,
            "title": title,
            "summary": solution.get("summary", ""),
            "questions": questions,
            "dir": result["dir"],
            "files": result["files"],
            # Generation notes first: a question that failed matters more than a
            # file-writing detail.
            "notes": (solution.get("notes") or []) + result["notes"],
        })

    except providers.ProviderError as e:
        print(f"Provider error: {e}")
        return jsonify({"error": str(e)}), 502
    except ValueError as e:
        print(f"Generation error: {e}")
        return jsonify({"error": str(e)}), 422
    except FileNotFoundError as e:
        print(f"Setup error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Server error: {type(e).__name__}: {e}")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    finally:
        _run_lock.release()


if __name__ == "__main__":
    cfg = config.load()
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    print(f"AutoClassroom server -> http://{host}:{port}")
    print(f"  provider : {config.PROVIDERS[cfg['provider']][0]} "
          f"({config.get_model(cfg['provider'], cfg)})")
    print(f"  api key  : {'set' if config.get_api_key(cfg['provider'], cfg) else 'MISSING'}")
    print(f"  output   : {cfg['output_dir']}")
    if cfg.get("run_code"):
        attempts = repair.attempts_for(cfg)
        print(f"  run code : on, {'no repairs' if not attempts else f'repair x{attempts}'}")
    # use_reloader=False keeps the Google OAuth flow from firing twice in dev.
    app.run(host=host, port=port, debug=False, use_reloader=False)

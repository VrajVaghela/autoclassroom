# AutoClassroom 🚀

AutoClassroom reads a Google Classroom assignment, works out what it asks you
to hand in, generates the complete solution, and saves the finished files to a
folder you choose.

## ✨ Features

- **Google Classroom integration** — pulls the assignment title, description,
  and the text of attached Docs and PDFs.
- **Complete solutions, not just code** — produces the actual deliverables the
  assignment asks for: Jupyter notebooks (`.ipynb`), Word lab reports
  (`.docx`) with output screenshots, PDFs, and source files.
- **The files the assignment asks for** — a task that says "submit a single
  `.ipynb`" gets one notebook covering every question, while a lab sheet listing
  independent programs gets `q1_*.py`, `q2_*.py`, … one per question. If the
  assignment wants a single write-up, the per-question sections are merged into
  one report.
- **Code that looks like yours** — plain, direct solutions that answer exactly
  what was asked, without heavy comments, banner prints or extra features.
- **Bring your own AI** — Gemini, OpenAI, Anthropic Claude, OpenRouter, Groq,
  xAI Grok, or any OpenAI-compatible endpoint. Switch providers from the
  extension.
- **Your folder, your choice** — set the output location from the extension's
  settings, with a native folder picker.
- **Real output capture (optional)** — run the generated code locally so the
  report's screenshots show genuine program output.

## 🛠️ Project structure

| File | Role |
| --- | --- |
| `server.py` | Local Flask API the extension talks to |
| `config.py` | Settings store (output folder, provider, API keys) |
| `providers.py` | One `complete()` call across all supported AI providers |
| `llm_generator.py` | Works out what the assignment wants handed in, and generates it |
| `file_manager.py` | Writes a solution into the chosen output folder |
| `repair.py` | Runs the generated code and asks the AI to fix what fails |
| `artifacts/` | Renderers for notebooks, Word/PDF reports, screenshots |
| `classroom_api.py` | Google Classroom + Drive access |
| `folder_picker.py` | Native folder dialog, run as a subprocess |
| `extension/` | Chrome extension (popup + settings panel) |

## 🚀 Getting started

### Prerequisites

- Python 3.10+
- A Google Cloud project with the Classroom API enabled, and its
  `credentials.json` in the project root
- An API key for at least one AI provider

### Setup

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Add your Google credentials**

   Place `credentials.json` in the project root. The first run opens a browser
   to authorize access and writes `token.json`.

3. **Start the server**

   ```bash
   python server.py
   ```

   It binds to `127.0.0.1:5000` and prints the provider, model, and output
   folder it will use.

4. **Load the extension**

   Open `chrome://extensions/`, enable **Developer mode**, click
   **Load unpacked**, and select the `extension/` folder.

5. **Configure it**

   Click the extension, then the ⚙️ gear icon:

   - **Save solutions to** — pick your output folder (**Browse…** opens a
     native dialog).
   - **AI provider** and **Model** — choose which AI writes your solutions.
   - **API key** — pasted keys are stored locally in `config.json`
     (gitignored). **Test connection** verifies the key before you rely on it.
   - **Run generated code** and **Repair attempts** — see the notes below.

### Using it

Open a Google Classroom assignment, click the extension, and press
**Solve this assignment**. The generated files are listed in the popup and
written to a folder named after the assignment. Re-running never overwrites an
earlier run — it creates `Assignment (2)`, `(3)`, and so on.

## ⚙️ Configuration reference

Settings live in `config.json`, written by the extension. API keys may instead
come from environment variables (see `.env.example`); a key saved in settings
takes precedence over the environment.

| Setting | Default | Meaning |
| --- | --- | --- |
| `output_dir` | `./lab` | Where solutions are written |
| `provider` | `gemini` | Active AI provider |
| `models` | per-provider default | Model override per provider |
| `api_keys` | — | Per-provider keys |
| `custom_base_url` | — | Endpoint for the `custom` provider |
| `run_code` | `false` | Execute generated code (see below) |
| `run_timeout` | `20` | Seconds before a run is killed |
| `repair_attempts` | `2` | Times a failing program is sent back to be fixed |

### About "Run generated code"

Lab reports usually want screenshots of a program running. With this **off**
(the default), screenshots render the model's *predicted* output. With it
**on**, AutoClassroom executes the generated program on your machine and
screenshots what it really printed.

That is genuinely more useful — and it means code written by an AI from a
document you didn't write gets executed locally. It is off by default and
opt-in for that reason. Only turn it on for assignments you trust.

### About "Repair attempts"

Running the code also means finding out when it doesn't work, so with execution
on the generator stops being a single pass. A program that crashes is handed
back to the model with its own source and the traceback, and the fix is run
again — up to `repair_attempts` times (`0` turns this off). Notebooks are
repaired the same way, one failing cell per round, re-running the whole
notebook after each fix.

Two rules keep this from making things worse:

- **A repair is kept only if it works.** When the attempts run out, the model's
  original file is restored, so a report never quotes code that was never run.
- **Programs waiting on typed input are left alone.** They run with stdin
  closed, so an interactive assignment dies on its first `input()`. That's this
  tool's limitation, not a bug in the code, and "fixing" it would mean
  hardcoding away the interactivity the assignment asked for.

When a repair does land, the report's code listing is updated to match the file
that actually ran.

## 🔒 Notes on security

- The server listens on `127.0.0.1` only.
- Cross-origin requests are restricted to `chrome-extension://` origins, and
  every request must carry an `X-AutoClassroom-Client` header — so an ordinary
  web page cannot reach the server even to fire-and-forget.
- Reading settings returns **masked** API keys; raw keys never leave the
  process.
- `config.json`, `.env`, `token.json`, and `credentials.json` are gitignored.

## 🧪 Tests

```bash
python -m pytest tests/ -q          # unit tests
python tests/e2e_check.py           # full pipeline against a live server
```

## 📜 License

MIT

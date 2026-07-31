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
- **A file per question** — the assignment is split into its questions and each
  one is solved in its own call, so you get `q1_*.py`, `q2_*.py`, … instead of
  everything crammed into one file. If the assignment wants a single write-up,
  the per-question sections are merged into one report.
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
| `llm_generator.py` | Splits an assignment into questions and solves each one |
| `file_manager.py` | Writes a solution into the chosen output folder |
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
   - **Run generated code** — see the note below.

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

### About "Run generated code"

Lab reports usually want screenshots of a program running. With this **off**
(the default), screenshots render the model's *predicted* output. With it
**on**, AutoClassroom executes the generated program on your machine and
screenshots what it really printed.

That is genuinely more useful — and it means code written by an AI from a
document you didn't write gets executed locally. It is off by default and
opt-in for that reason. Only turn it on for assignments you trust.

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

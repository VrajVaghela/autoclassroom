# AutoClassroom 🚀

AutoClassroom is a powerful automation tool designed to bridge the gap between Google Classroom assignments and your local development environment. It automatically fetches assignment instructions, uses an LLM (Large Language Model) to generate the necessary code, and saves it directly to your disk.

## ✨ Features

- **Google Classroom Integration**: Seamlessly fetch assignment titles and instructions.
- **AI-Powered Code Generation**: Uses advanced LLMs to interpret instructions and write code.
- **Local File Management**: Automatically organizes and saves generated code to your local machine.
- **Chrome Extension**: Easily trigger the process directly from the Google Classroom UI.

## 🛠️ Project Structure

- `server.py`: Flask backend that coordinates fetching, generating, and saving.
- `classroom_api.py`: Handles OAuth2 authentication and communication with the Google Classroom API.
- `llm_generator.py`: Manages interactions with the LLM (e.g., Gemini) for code generation.
- `file_manager.py`: Utilities for directory creation and file saving.
- `extension/`: Chrome extension to interact with Google Classroom.

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Google Cloud Project with Classroom API enabled.
- `credentials.json` from Google Cloud Console.
- LLM API Key (configured in `.env`).

### Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/vraj/autoclassroom.git
    cd autoclassroom
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure environment**:
    - Place your `credentials.json` in the root directory.
    - Create a `.env` file with your API keys (see `.env.example`).

4.  **Run the server**:
    ```bash
    python server.py
    ```

5.  **Install the extension**:
    - Open Chrome and go to `chrome://extensions/`.
    - Enable "Developer mode".
    - Click "Load unpacked" and select the `extension/` folder.

## 📜 License

MIT License

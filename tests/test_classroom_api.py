"""Tests for Google Classroom API authentication and path resolution."""

import os
import pytest
import classroom_api


def test_credentials_path_resolution():
    assert os.path.isabs(classroom_api.CREDENTIALS_PATH)
    assert os.path.isabs(classroom_api.TOKEN_PATH)
    assert classroom_api.CREDENTIALS_PATH.endswith("credentials.json")
    assert classroom_api.TOKEN_PATH.endswith("token.json")


def test_missing_credentials_raises_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(classroom_api, "CREDENTIALS_PATH", str(tmp_path / "nonexistent_credentials.json"))
    monkeypatch.setattr(classroom_api, "TOKEN_PATH", str(tmp_path / "nonexistent_token.json"))
    monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_CREDENTIALS", raising=False)

    with pytest.raises(FileNotFoundError) as exc_info:
        classroom_api.authenticate_google()

    assert "Missing 'credentials.json'" in str(exc_info.value)


def test_env_credentials_populates_file(monkeypatch, tmp_path):
    creds_file = tmp_path / "credentials.json"
    token_file = tmp_path / "token.json"
    monkeypatch.setattr(classroom_api, "CREDENTIALS_PATH", str(creds_file))
    monkeypatch.setattr(classroom_api, "TOKEN_PATH", str(token_file))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", '{"installed": {"client_id": "test"}}')

    # Mock InstalledAppFlow to avoid opening a real browser during test
    class DummyFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            class DummyCreds:
                valid = True
                def to_json(self):
                    return '{"token": "dummy"}'
            class DummyFlowInst:
                def run_local_server(self, port=0):
                    return DummyCreds()
            return DummyFlowInst()

    class DummyService:
        pass

    monkeypatch.setattr(classroom_api, "InstalledAppFlow", DummyFlow)
    monkeypatch.setattr(classroom_api, "build", lambda service, version, credentials: DummyService())

    classroom, drive = classroom_api.authenticate_google()
    assert creds_file.exists()
    assert '{"installed": {"client_id": "test"}}' in creds_file.read_text()

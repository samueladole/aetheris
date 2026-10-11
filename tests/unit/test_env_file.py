"""Tests for local secret-file loading (`.env`).

The constitution sanctions credentials from *"environment variables or local
secret files that are git-ignored"*. `.env` is git-ignored, so these tests pin
the behaviour that makes that path usable — most importantly the precedence,
because a stale `.env` silently overriding an exported credential is a bug that
presents as an auth failure and is very hard to trace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings, _load_env_file

PROVIDER_KEYS = [
    "FRED_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "TIINGO_API_KEY",
]


@pytest.fixture()
def clean_env(monkeypatch):
    """Remove every provider key and the env-file override before each test."""
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("AETHERIS_ENV_FILE", raising=False)
    yield
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestEnvFileLoading:
    """Test env file loading."""
    def test_loads_credentials_from_dotenv(self, tmp_path, clean_env, monkeypatch):
        """Test that loads credentials from dotenv."""
        env_file = tmp_path / ".env"
        env_file.write_text("FRED_API_KEY=from-file\n", encoding="utf-8")
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(env_file))

        settings = Settings.load()
        assert settings.fred_api_key == "from-file"

    def test_quoted_values_are_unquoted(self, tmp_path, clean_env, monkeypatch):
        """A quoted .env value must not arrive with the quotes attached."""
        env_file = tmp_path / ".env"
        env_file.write_text('FINNHUB_API_KEY="quoted-key"\n', encoding="utf-8")
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(env_file))

        settings = Settings.load()
        assert settings.finnhub_api_key == "quoted-key"

    def test_all_five_providers_load(self, tmp_path, clean_env, monkeypatch):
        """Test that all five providers load."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(f"{key}=value-{i}" for i, key in enumerate(PROVIDER_KEYS)) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(env_file))

        settings = Settings.load()
        creds = settings.provider_credentials()
        assert set(creds) == {"fred", "alpha_vantage", "finnhub", "fmp", "tiingo"}


class TestPrecedence:
    """Test precedence."""
    def test_real_environment_wins_over_the_file(
        self, tmp_path, clean_env, monkeypatch
    ):
        """The safety property: an exported key must never be shadowed by .env."""
        env_file = tmp_path / ".env"
        env_file.write_text("FRED_API_KEY=from-file\n", encoding="utf-8")
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(env_file))
        monkeypatch.setenv("FRED_API_KEY", "from-environment")

        settings = Settings.load()
        assert settings.fred_api_key == "from-environment", (
            "a .env value overrode an explicitly exported credential"
        )


class TestAbsence:
    """Test absence."""
    def test_missing_file_is_silent(self, tmp_path, clean_env, monkeypatch):
        """A missing .env is a normal state, not an error."""
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(tmp_path / "nope.env"))
        settings = Settings.load()
        assert settings.fred_api_key is None

    def test_default_path_is_used_when_unset(self, tmp_path, clean_env, monkeypatch):
        """Test that default path is used when unset."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FMP_API_KEY=cwd-file\n", encoding="utf-8")

        settings = Settings.load()
        assert settings.fmp_api_key == "cwd-file"

    def test_loader_never_raises(self, tmp_path, clean_env, monkeypatch):
        """A malformed secret file must not crash the CLI."""
        monkeypatch.setenv("AETHERIS_ENV_FILE", str(tmp_path / ".env"))
        (tmp_path / ".env").write_bytes(b"\xff\xfe not valid utf-8 \xff")
        _load_env_file()  # must not raise


class TestGitignore:
    """Test gitignore."""
    def test_dotenv_is_gitignored(self):
        """The safety property that makes .env usable at all."""
        repo = Path(__file__).resolve().parents[2]
        text = (repo / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in text

    def test_example_is_tracked(self):
        """Test that example is tracked."""
        repo = Path(__file__).resolve().parents[2]
        assert (repo / ".env.example").is_file()

    def test_toml_secret_path_is_ignored(self):
        """The TOML config path can hold credentials too."""
        repo = Path(__file__).resolve().parents[2]
        text = (repo / ".gitignore").read_text(encoding="utf-8")
        assert "aetheris.toml" in text


class TestRedaction:
    """Test redaction."""
    def test_credentials_never_appear_in_redacted_output(self):
        """Test that credentials never appear in redacted output."""
        settings = Settings(fred_api_key="super-secret-value")
        redacted = settings.redacted()
        assert "super-secret-value" not in str(redacted)
        assert redacted["fred_api_key"] == "***set***"

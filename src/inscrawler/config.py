"""Shared configuration parsing and normalized application options."""

from __future__ import annotations

import os

import inscrawler.paths as app_paths


def _read_dotenv_file(path: str = app_paths.ENV_FILE) -> dict:
    """Read a simple KEY=VALUE .env file (no quotes/exports)."""
    env_vars = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    except Exception:
        env_vars = {}
    return env_vars


APP_GITHUB_URL = "https://github.com/syy674998887/inscrawler"

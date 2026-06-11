"""TOML 설정 로더."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = PACKAGE_DIR / "config.toml"


class Config:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else DEFAULT_PATH
        with open(p, "rb") as f:
            return cls(tomllib.load(f), p)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data_dir(self) -> Path:
        raw = Path(str(self.get("paths.data_dir", "data")))
        return raw if raw.is_absolute() else PACKAGE_DIR / raw

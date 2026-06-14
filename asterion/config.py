"""TOML 설정 로더 + 로컬 오버레이.

config.toml은 사람이 손으로 관리하는 기본값(주석 포함)이라 런타임에 덮어쓰지
않는다. SYSTEM 탭에서 바꾼 ProgID/URL 같은 기기별 설정은 같은 폴더의
config.local.json 오버레이에 저장하고, 로드 시 toml 위에 깊은 병합한다.
즉 toml은 읽기 전용, 변경분은 오버레이로만 — 주석이 보존된다.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = PACKAGE_DIR / "config.toml"


def _deep_merge(base: dict, overlay: dict) -> None:
    """overlay를 base에 in-place 깊은 병합 (dict끼리는 재귀, 그 외는 덮어씀)."""
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _set_nested(node: dict, parts: list[str], value: Any) -> None:
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


class Config:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path
        self.overlay_path = path.parent / "config.local.json"
        self._overlay: dict[str, Any] = {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else DEFAULT_PATH
        with open(p, "rb") as f:
            cfg = cls(tomllib.load(f), p)
        cfg._load_overlay()
        return cfg

    def _load_overlay(self) -> None:
        if self.overlay_path.exists():
            try:
                self._overlay = json.loads(self.overlay_path.read_text("utf-8"))
            except (OSError, ValueError):
                self._overlay = {}
            else:
                _deep_merge(self.data, self._overlay)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        """런타임 설정 변경 — in-memory data와 오버레이 파일 둘 다 갱신."""
        parts = dotted.split(".")
        _set_nested(self.data, parts, value)
        _set_nested(self._overlay, parts, value)
        tmp = self.overlay_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._overlay, ensure_ascii=False, indent=2),
                       "utf-8")
        tmp.replace(self.overlay_path)  # 원자적 교체

    @property
    def data_dir(self) -> Path:
        raw = Path(str(self.get("paths.data_dir", "data")))
        return raw if raw.is_absolute() else PACKAGE_DIR / raw

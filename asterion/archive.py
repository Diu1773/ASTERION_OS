"""Archive Recovery — 파일↔DB 정합성 (로드맵 §9.3, "핵심 5" 중 마지막).

DB의 Frame 레코드와 디스크의 FITS 파일이 어긋나는 걸 대조한다:
  · missing       — DB엔 있는데 파일이 사라짐(삭제·이동)
  · untracked     — 디스크엔 있는데 DB에 없음(외부 캡처·DB 손실)
  · no_file_path  — astropy 없이 저장돼 통계만 있고 파일 경로가 빈 레코드
  · checksum      — (deep) sha256 무결성: 첫 deep 스캔이 채우고, 이후 deep은 변조 검출

v1은 '진단(scan)'이 중심 — 보고 + deep 시 checksum 채움/검증. 미등록 재등록·누락
복구 액션은 보고 위에 얹는다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .core.ontology import Db, Frame


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class ArchiveRecovery:
    def __init__(self, db: Db, frames_dir):
        self.db = db
        self.frames_dir = Path(frames_dir)

    def scan(self, *, deep: bool = False, limit: int = 500) -> dict[str, Any]:
        """파일↔DB 대조 보고. deep=True면 sha256까지(느림 — 큰 FITS 다수면 무거움)."""
        def _rows(s):
            return [(f.id, f.file_path, f.checksum)
                    for f in s.query(Frame.id, Frame.file_path,
                                     Frame.checksum).all()]
        rows = self.db.query(_rows)

        missing: list[dict] = []
        mismatch: list[dict] = []
        no_file = 0
        ok = 0
        to_store: dict[int, str] = {}
        tracked: set[str] = set()

        for fid, fp, chk in rows:
            if not fp:
                no_file += 1
                continue
            p = Path(fp)
            tracked.add(str(p.resolve()) if p.exists() else fp)
            if not p.exists():
                missing.append({"id": fid, "file_path": fp})
                continue
            ok += 1
            if deep:
                digest = _sha256(p)
                if chk and chk != digest:
                    mismatch.append({"id": fid, "file_path": fp})
                elif not chk:
                    to_store[fid] = digest

        # untracked — 디스크 FITS 중 DB에 없는 것
        untracked: list[str] = []
        if self.frames_dir.exists():
            for ext in ("*.fits", "*.fit"):
                for f in self.frames_dir.rglob(ext):
                    if str(f.resolve()) not in tracked:
                        untracked.append(str(f))
                        if len(untracked) >= limit:
                            break

        # deep — 누락 checksum 채우기(다음 스캔부터 변조 검출 가능)
        stored = 0
        if to_store:
            def _upd(s):
                for fid, dig in to_store.items():
                    row = s.get(Frame, fid)
                    if row is not None:
                        row.checksum = dig
            self.db.update(_upd)
            stored = len(to_store)

        return {
            "deep": deep, "total_db": len(rows), "ok": ok,
            "no_file_path": no_file,
            "missing_count": len(missing), "missing": missing[:limit],
            "untracked_count": len(untracked), "untracked": untracked,
            "checksum_mismatch_count": len(mismatch),
            "checksum_mismatch": mismatch, "checksum_stored": stored,
        }

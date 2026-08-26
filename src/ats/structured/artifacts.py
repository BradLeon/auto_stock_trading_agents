"""Content-addressed storage for structured raw responses and query slices."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredBlob:
    blob_id: str
    content_hash: str
    relative_path: str
    bytes: int
    created: bool


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def encode(payload: bytes | str | dict | list) -> bytes:
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    def put(self, payload: bytes | str | dict | list, *, suffix: str = ".bin") -> StoredBlob:
        body = self.encode(payload)
        digest = hashlib.sha256(body).hexdigest()
        safe_suffix = suffix if suffix.startswith(".") and suffix[1:].isalnum() else ".bin"
        relative = Path(digest[:2]) / f"{digest}{safe_suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            created = True
        return StoredBlob(blob_id=digest, content_hash=digest,
                          relative_path=str(relative), bytes=len(body), created=created)

    def read(self, relative_path: str) -> bytes:
        target = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root not in target.parents:
            raise ValueError("artifact path escapes configured root")
        return target.read_bytes()

    def usage(self) -> dict[str, int]:
        files = [path for path in self.root.rglob("*") if path.is_file()]
        return {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}


def default_artifact_root() -> Path:
    from ..config import REPO_ROOT

    return Path(os.environ.get(
        "ATS_STRUCTURED_ARTIFACT_ROOT", REPO_ROOT / "var" / "structured_artifacts"))

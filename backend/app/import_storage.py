"""Private local storage adapter for staged import files.

The adapter contract keeps API/worker code independent of local paths. A shared
object-storage adapter can replace this implementation when multiple app hosts
are introduced; local storage is supported for a single-machine deployment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings


class LocalImportStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or get_settings().upload_dir).resolve() / 'imports'

    def _target(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError('Invalid private import storage key')
        return target

    def write(self, key: str, source: BinaryIO, max_bytes: int) -> tuple[int, bytes]:
        target = self._target(key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:os.chmod(target.parent,0o700)
        except OSError:pass
        total = 0
        digest = __import__('hashlib').sha256()
        created = False
        try:
            with target.open('xb') as output:
                created = True
                while chunk := source.read(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError('Maximum import size exceeded')
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            try:os.chmod(target,0o600)
            except OSError:pass
            return total, digest.digest()
        except Exception:
            # A failed exclusive open does not own an existing object. Retrying
            # a key must never delete a previously stored private document.
            if created:
                target.unlink(missing_ok=True)
            raise

    def read(self, key: str) -> bytes:
        return self._target(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._target(key).is_file()

    def delete(self, key: str) -> None:
        target = self._target(key)
        if target.is_file():
            target.unlink()
        parent = target.parent
        while parent != self.root and self.root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def get_import_storage() -> LocalImportStorage:
    return LocalImportStorage()

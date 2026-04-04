"""Save uploaded Flask file to a temp path."""
from __future__ import annotations

import os
import tempfile


def save_upload_to_temp(file_storage, *, suffix: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix="anomaly_upload_")
    path = os.path.join(tmpdir, file_storage.filename)
    if not file_storage.filename:
        path = os.path.join(tmpdir, f"upload{suffix}")
    file_storage.save(path)
    return path

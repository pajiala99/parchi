"""Replays saved AnalyzeExpense-shaped responses, keyed by the file's SHA-256.

The fixtures in ``samples/textract/`` are written in the documented
AnalyzeExpense response shape for the sample documents (they are simulated,
not recorded from AWS). Uploading a sample offline therefore exercises exactly
the same parsing code that runs on real Textract output in the cloud.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .. import storage
from ..extraction.model import Extraction
from ..extraction.textract import parse_analyze_expense
from . import PermanentError

DEFAULT_DIR = Path(__file__).resolve().parents[4] / "samples" / "textract"


class ReplayEngine:
    name = "replay"

    def __init__(self, directory: str | os.PathLike[str] | None = None) -> None:
        self.directory = Path(directory or os.environ.get("REPLAY_DIR") or DEFAULT_DIR)

    def extract(self, bucket: str, key: str, content_type: str) -> Extraction:
        digest = hashlib.sha256(storage.read_object(bucket, key)).hexdigest()
        index_file = self.directory / "index.json"
        index = json.loads(index_file.read_text()) if index_file.exists() else {}
        name = index.get(digest)
        if not name:
            raise PermanentError(
                "The offline replay engine only knows the sample documents in samples/. "
                "Install Tesseract to read your own files offline, or deploy to use Textract."
            )
        response = json.loads((self.directory / name).read_text())
        return parse_analyze_expense(response, engine=self.name)

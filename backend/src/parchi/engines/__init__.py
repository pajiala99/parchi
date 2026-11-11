"""OCR engines behind one interface.

* ``textract``  - Amazon Textract AnalyzeExpense (the deployed default)
* ``tesseract`` - open-source OCR + heuristics; free, runs offline
* ``replay``    - returns saved AnalyzeExpense-shaped responses for the sample
                  documents, so the Textract code path runs with no AWS account
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..config import settings
from ..extraction.model import Extraction


class PermanentError(Exception):
    """The document itself cannot be processed; retrying will not help."""


class Engine(Protocol):
    name: str

    def extract(self, bucket: str, key: str, content_type: str) -> Extraction: ...


_CUSTOM: dict[str, Callable[[], Engine]] = {}


def register(name: str, factory: Callable[[], Engine]) -> None:
    """Plug in another engine (tests use this to inject fakes)."""
    _CUSTOM[name] = factory


def get_engine(name: str | None = None) -> Engine:
    choice = (name or settings().ocr_engine).lower()
    if choice in _CUSTOM:
        return _CUSTOM[choice]()
    if choice == "textract":
        from .textract_engine import TextractEngine

        return TextractEngine()
    if choice == "tesseract":
        from .tesseract_engine import TesseractEngine

        return TesseractEngine()
    if choice == "replay":
        from .replay_engine import ReplayEngine

        return ReplayEngine()
    raise ValueError(f"unknown OCR engine {choice!r}")

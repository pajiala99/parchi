"""Tesseract OCR engine: free and offline, for local development and cost-free demos.

Needs the ``tesseract`` binary (``brew install tesseract`` / ``apt install
tesseract-ocr``). Pillow improves results (orientation, upscaling) and
pypdfium2 adds PDF support; both are optional imports so the Lambda package
stays dependency-free.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict

from .. import storage
from ..extraction.heuristics import Line, parse_lines
from ..extraction.model import Extraction
from . import PermanentError

TARGET_WIDTH = 1800


def lines_from_tsv(tsv: str) -> list[Line]:
    """Group Tesseract's word-level TSV into lines with a mean confidence."""
    grouped: OrderedDict[tuple[str, ...], list[tuple[str, float]]] = OrderedDict()
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = (row.get("text") or "").strip()
        if row.get("level") != "5" or not word:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        try:
            confidence = max(0.0, float(row.get("conf") or 0)) / 100.0
        except ValueError:
            confidence = 0.0
        grouped.setdefault(key, []).append((word, confidence))
    lines = []
    for words in grouped.values():
        text = " ".join(word for word, _ in words)
        lines.append(Line(text, round(sum(c for _, c in words) / len(words), 4)))
    return lines


class TesseractEngine:
    name = "tesseract"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or os.environ.get("TESSERACT_BIN") or shutil.which("tesseract")

    def extract(self, bucket: str, key: str, content_type: str) -> Extraction:
        if not self.binary:
            raise PermanentError("Tesseract is not installed (brew install tesseract).")
        return self.extract_bytes(storage.read_object(bucket, key), content_type)

    def extract_bytes(self, data: bytes, content_type: str) -> Extraction:
        if not self.binary:
            raise PermanentError("Tesseract is not installed (brew install tesseract).")
        if not data:
            raise PermanentError("The uploaded file is empty.")
        if content_type == "application/pdf":
            lines, pages = self._pdf(data)
        else:
            lines, pages = self._image(data), 1
        if not lines:
            raise PermanentError("No text could be read from this file. Try a sharper, well-lit photo.")
        return parse_lines(lines, engine=self.name, pages=pages)

    def _pdf(self, data: bytes) -> tuple[list[Line], int]:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise PermanentError("Reading PDFs offline needs pypdfium2 (pip install pypdfium2).") from exc
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PermanentError("The PDF could not be opened.") from exc
        pages = len(pdf)
        if pages == 0:
            raise PermanentError("The PDF has no pages.")
        page = pdf[0]  # like Textract's synchronous API: the first page only
        text = page.get_textpage().get_text_range()
        if len(text.strip()) >= 40:  # a digital PDF: its text layer beats OCR
            return [Line(row, 0.99) for row in text.splitlines() if row.strip()], pages
        image = page.render(scale=300 / 72).to_pil()
        return self._ocr_pil(image), pages

    def _image(self, data: bytes) -> list[Line]:
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:
            return self._ocr_file(data, ".img")
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise PermanentError("The file is not a readable image.") from exc
        return self._ocr_pil(image)

    def _ocr_pil(self, image) -> list[Line]:
        from PIL import Image, ImageOps

        image = ImageOps.exif_transpose(image).convert("L")
        if image.width < TARGET_WIDTH:
            ratio = TARGET_WIDTH / image.width
            image = image.resize((TARGET_WIDTH, int(image.height * ratio)), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return self._ocr_file(buffer.getvalue(), ".png")

    def _ocr_file(self, data: bytes, suffix: str) -> list[Line]:
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(data)
            handle.flush()
            try:
                result = subprocess.run(
                    [self.binary or "tesseract", handle.name, "stdout", "--psm", "6", "-l", "eng", "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                raise PermanentError(f"Tesseract could not read the file: {exc.stderr.strip()[:200]}") from exc
        return lines_from_tsv(result.stdout)

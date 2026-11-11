"""OCR engines: Textract (stubbed), Tesseract (real binary, if installed) and replay."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from decimal import Decimal

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from conftest import BUCKET
from fakes import pharmacy_response
from parchi import aws, engines
from parchi.engines import PermanentError
from parchi.engines.replay_engine import ReplayEngine
from parchi.engines.tesseract_engine import TesseractEngine
from parchi.engines.textract_engine import TextractEngine


@pytest.fixture
def textract(monkeypatch):
    client = boto3.client("textract", region_name="ap-south-1")
    monkeypatch.setattr(aws, "textract", lambda: client)
    with Stubber(client) as stub:
        yield stub


def test_textract_reads_straight_from_s3(textract):
    textract.add_response(
        "analyze_expense",
        pharmacy_response(),
        expected_params={"Document": {"S3Object": {"Bucket": "parchi-test-uploads", "Name": "uploads/k.png"}}},
    )
    extraction = TextractEngine().extract("parchi-test-uploads", "uploads/k.png", "image/png")
    assert extraction.engine == "textract" and extraction.total == Decimal("607.42")


def test_textract_document_errors_are_permanent(textract):
    textract.add_client_error("analyze_expense", service_error_code="UnsupportedDocumentException")
    with pytest.raises(PermanentError, match="single-page PDF"):
        TextractEngine().extract("parchi-test-uploads", "uploads/k.png", "application/pdf")


def test_textract_throttling_is_not_permanent(textract):
    textract.add_client_error("analyze_expense", service_error_code="ProvisionedThroughputExceededException")
    with pytest.raises(ClientError):
        TextractEngine().extract("parchi-test-uploads", "uploads/k.png", "image/png")


def test_engine_selection(monkeypatch):
    assert isinstance(engines.get_engine("textract"), TextractEngine)
    assert isinstance(engines.get_engine("tesseract"), TesseractEngine)
    assert isinstance(engines.get_engine("replay"), ReplayEngine)
    with pytest.raises(ValueError):
        engines.get_engine("magic")


def test_replay_engine(cloud, tmp_path):
    data = b"sample receipt bytes"
    cloud.s3.put_object(Bucket=BUCKET, Key="uploads/x.png", Body=data)
    (tmp_path / "pharmacy.json").write_text(json.dumps(pharmacy_response()))
    (tmp_path / "index.json").write_text(json.dumps({hashlib.sha256(data).hexdigest(): "pharmacy.json"}))
    extraction = ReplayEngine(tmp_path).extract(BUCKET, "uploads/x.png", "image/png")
    assert extraction.engine == "replay" and extraction.vendor == "SANJEEVANI MEDICOS"
    cloud.s3.put_object(Bucket=BUCKET, Key="uploads/y.png", Body=b"something else")
    with pytest.raises(PermanentError, match="sample documents"):
        ReplayEngine(tmp_path).extract(BUCKET, "uploads/y.png", "image/png")


def test_tesseract_missing_binary_is_permanent():
    engine = TesseractEngine()
    engine.binary = None
    with pytest.raises(PermanentError, match="not installed"):
        engine.extract("parchi-test-uploads", "uploads/k.png", "image/png")


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
def test_tesseract_reads_a_rendered_receipt(cloud):
    from PIL import Image, ImageDraw, ImageFont

    lines = [
        "SHARMA GENERAL STORE",
        "Bill No: GS/1142  Date: 05/11/2026",
        "Sugar 1kg 1 48.00 48.00",
        "Grand Total Rs. 48.00",
    ]
    image = Image.new("RGB", (900, 60 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
        except OSError:
            font = ImageFont.load_default(size=34)
    for i, text in enumerate(lines):
        draw.text((30, 20 + 60 * i), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    cloud.s3.put_object(Bucket=BUCKET, Key="uploads/r.png", Body=buffer.getvalue())

    extraction = TesseractEngine().extract(BUCKET, "uploads/r.png", "image/png")
    assert extraction.total == Decimal("48.00")
    assert extraction.document_date == "2026-11-05"
    assert extraction.invoice_number == "GS/1142"


def test_tesseract_rejects_non_images(cloud):
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract is not installed")
    cloud.s3.put_object(Bucket=BUCKET, Key="uploads/bad.png", Body=b"definitely not a png")
    with pytest.raises(PermanentError):
        TesseractEngine().extract(BUCKET, "uploads/bad.png", "image/png")

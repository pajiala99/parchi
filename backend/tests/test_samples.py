"""Every sample bill's simulated Textract response must parse to its ground truth."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from parchi.extraction import summarize
from parchi.extraction.textract import parse_analyze_expense

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
TRUTH = json.loads((SAMPLES / "truth.json").read_text())
INDEX = json.loads((SAMPLES / "textract" / "index.json").read_text())


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_replayed_sample_matches_truth(name):
    digest = hashlib.sha256((SAMPLES / name).read_bytes()).hexdigest()
    assert digest in INDEX, f"{name} changed; rerun local/make_samples.py"
    response = json.loads((SAMPLES / "textract" / INDEX[digest]).read_text())
    got = summarize(parse_analyze_expense(response, engine="replay"))
    expected = TRUTH[name]
    assert got["docType"] == expected["docType"]
    assert got["vendor"] == expected["vendor"]
    assert got["documentDate"] == expected["documentDate"]
    assert got["invoiceNumber"] == expected["invoiceNumber"]
    assert got["total"] == Decimal(expected["total"])
    assert got["tax"] == (Decimal(expected["tax"]) if expected["tax"] else None)
    assert all(check["ok"] for check in got["checks"]), got["checks"]

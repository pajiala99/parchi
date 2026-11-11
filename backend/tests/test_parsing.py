"""Unit tests for the pure parsing helpers: money, dates, GSTIN, classification, ids, keys."""

from __future__ import annotations

from decimal import Decimal

import pytest

from parchi import notify, storage
from parchi.auth import is_valid_key, owner_from_key
from parchi.extraction.amounts import detect_currency, find_amounts, parse_amount, parse_quantity
from parchi.extraction.classify import FEE_RECEIPT, INVOICE, MEDICAL_BILL, OTHER, classify
from parchi.extraction.dates import find_dates, parse_date
from parchi.extraction.gstin import checksum_char, find_gstins, is_valid_gstin
from parchi.ids import id_timestamp_ms, is_valid_id, new_id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Grand Total ₹ 1,23,456.50", "123456.50"),
        ("Rs. 450/-", "450"),
        ("Rs.450.00", "450.00"),
        ("INR 1,240.00", "1240.00"),
        ("$12.99", "12.99"),
        ("1240.5", "1240.5"),
        ("Total: 999", "999"),
        ("1,24O.00", "1240.00"),  # OCR read a zero as the letter O
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text", ["Date 08/11/2026", "at 13:42", "GST 18%", "650mg", "INV2291", "Ph 9876543210", "Session 2026-27", ""]
)
def test_things_that_are_not_money(text):
    assert find_amounts(text) == []


def test_amount_helpers():
    assert parse_amount("Qty 2 Amount 64.00") == Decimal("64.00")
    assert parse_amount(None) is None
    assert parse_amount("no money here") is None
    (suffixed,) = find_amounts("450/-")
    assert suffixed.currency == "INR"  # the /- suffix is an Indian convention
    (bare,) = find_amounts("Qty 3")
    assert not bare.marked
    assert detect_currency("Paid Rs 100 only") == "INR"
    assert detect_currency("₹500") == "INR"
    assert detect_currency("Total $5.00") == "USD"
    assert detect_currency("nothing") is None
    assert parse_quantity("2 x") == Decimal("2")
    assert parse_quantity(None) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Date: 08/11/2026", "2026-11-08"),
        ("Bill dt 8-11-26", "2026-11-08"),
        ("2026-11-08", "2026-11-08"),
        ("8th November, 2026", "2026-11-08"),
        ("08-Nov-2026", "2026-11-08"),
        ("Nov 8, 2026", "2026-11-08"),
        ("11/25/2026", "2026-11-25"),  # day-first impossible, so month-first
        ("14.07.2026", "2026-07-14"),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["8 Market Road 2026", "Exp 08/27", "31/02/2026", "01/01/1999", "Session 2026-27"])
def test_not_dates(text):
    assert find_dates(text) == []


def test_dates_in_reading_order():
    hits = find_dates("Mfg 01/03/2026 Exp 28/02/2028 Date 08/11/2026", max_year=2030)
    assert [iso for iso, _, _ in hits] == ["2026-03-01", "2028-02-28", "2026-11-08"]


def test_gstin():
    assert is_valid_gstin("27AAPFU0939F1ZV")
    assert not is_valid_gstin("27AAPFU0939F1ZX")
    assert is_valid_gstin("27 aapfu 0939 f1zv")
    assert not is_valid_gstin("40AAPFU0939F1Z" + checksum_char("40AAPFU0939F1Z"))  # no state 40
    assert not is_valid_gstin(None)
    assert find_gstins("GSTIN 03AABCS1234K1ZQ buyer 03BCDPN4410Q1ZL") == ["03AABCS1234K1ZQ", "03BCDPN4410Q1ZL"]


PHARMACY = """SANJEEVANI MEDICOS
GST INVOICE   D.L. No: PB-PTA-20B-1187
Patient: Aarav Sharma  Dr. R. Mehta
Paracetamol 650 Tab B2341 Exp 08/27
Cetirizine 10mg Tab  Batch CT220"""
FEE = """NORTHFIELD INSTITUTE OF TECHNOLOGY
Fee Receipt - Academic Session 2026-27
Student Name: Aarav Sharma   Roll No: 1024XXXXXX
Semester 5  Tuition Fee  Hostel Fee  Examination Fee"""
INVOICE_TEXT = """KAVERI CLOUD SUPPLIES PVT LTD
TAX INVOICE   Invoice No: KCS/26-27/0423
Bill To: Patiala Robotics Club   Place of Supply: Punjab (03)
HSN 8471  Taxable Value  Due Date 18 Nov 2026"""
CANTEEN = """THE CORNER CANTEEN
Masala Chai 2 40.00
Aloo Paratha 2 120.00
Thank you, visit again"""


@pytest.mark.parametrize(
    ("text", "expected"), [(PHARMACY, MEDICAL_BILL), (FEE, FEE_RECEIPT), (INVOICE_TEXT, INVOICE), (CANTEEN, OTHER)]
)
def test_classify(text, expected):
    result = classify(text)
    assert result.doc_type == expected
    assert 0 < result.confidence <= 1


def test_classification_explains_itself():
    result = classify(PHARMACY)
    assert "tablets" in result.signals and "drug licence" in result.signals
    assert result.scores[MEDICAL_BILL] > result.scores[INVOICE]  # "GST INVOICE" doesn't win
    assert classify(CANTEEN).signals == []
    assert classify("Bill 450.00", vendor="City Care Hospital").doc_type == MEDICAL_BILL


def test_ids_and_keys():
    first, second = new_id(1_700_000_000_000), new_id(1_700_000_000_001)
    assert first < second and is_valid_id(first) and len(first) == 26
    assert id_timestamp_ms(first) == 1_700_000_000_000
    assert not is_valid_id("not-an-id") and not is_valid_id("Z" * 26)
    assert is_valid_key("A" * 43) and not is_valid_key("short") and not is_valid_key(None)
    assert len(owner_from_key("A" * 43)) == 32 and owner_from_key("A" * 43) != owner_from_key("B" * 43)


@pytest.mark.parametrize(
    ("name", "content_type", "expected"),
    [
        ("My Bill (1).JPG", "image/jpeg", "My_Bill_1.jpg"),
        ("../../etc/passwd.png", "image/png", "passwd.png"),
        ("receipt", "image/png", "receipt.png"),
        ("photo.png", "image/jpeg", "photo.jpg"),
        ("....", "image/png", "document.png"),
    ],
)
def test_safe_file_name(name, content_type, expected):
    assert storage.safe_file_name(name, content_type) == expected


def test_object_keys_round_trip():
    owner, document_id = "0" * 32, new_id()
    key = storage.object_key(owner, document_id, "bill.png", "image/png")
    assert storage.parse_object_key(key) == (owner, document_id, "bill.png")
    assert storage.parse_object_key("elsewhere/x.png") is None
    assert storage.parse_object_key(f"uploads/{owner}/{document_id}/a/b.png") is None
    assert storage.parse_object_key(f"uploads/NOT-HEX/{document_id}/b.png") is None


def test_email_text():
    assert notify.money(Decimal("123456.5")) == "Rs. 1,23,456.50"
    assert notify.money(Decimal("1234567.891")) == "Rs. 12,34,567.89"
    assert notify.money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert notify.money(None) == "-"
    document = {
        "documentId": new_id(),
        "fileName": "bill.png",
        "status": "COMPLETED",
        "docType": "MEDICAL_BILL",
        "vendor": "Śrī Sanjeevani Medicos & Surgical Agencies (Leela Bhawan Branch, Patiala)" * 2,
        "total": Decimal("607.42"),
        "confidence": Decimal("0.97"),
        "needsReview": True,
        "reviewReasons": ["date not found"],
    }
    subject, body = notify.compose(document)
    assert subject.isascii() and len(subject) < 100
    assert "Rs. 607.42" in body and "date not found" in body and f"/#/doc/{document['documentId']}" in body
    subject, body = notify.compose({"documentId": new_id(), "fileName": "x.jpg", "status": "FAILED", "error": "blurry"})
    assert "could not read" in subject and "blurry" in body

"""Textract AnalyzeExpense normalisation, raw-OCR heuristics and the cross-checks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fakes import PHARMACY_GSTIN, PHARMACY_LINES, expense_response, field, item, pharmacy_response
from parchi.engines.tesseract_engine import lines_from_tsv
from parchi.extraction.heuristics import Line, parse_lines
from parchi.extraction.model import Extraction, LineItem
from parchi.extraction.summary import summarize
from parchi.extraction.textract import parse_analyze_expense

# ---------------------------------------------------------------- Textract


def test_pharmacy_bill_from_textract():
    ex = parse_analyze_expense(pharmacy_response())
    assert ex.vendor == "SANJEEVANI MEDICOS"  # the high-confidence candidate, not "GST INVOICE"
    assert ex.document_date == "2026-11-08"
    assert ex.invoice_number == "SM/2611/0457"  # "No:" prefix stripped
    assert ex.total == Decimal("607.42")  # "Grand Total", not the "Total Qty: 12" candidate
    assert ex.subtotal == Decimal("578.50")
    assert ex.tax == Decimal("28.92")  # CGST + SGST added up
    assert ex.vendor_gstin == PHARMACY_GSTIN
    assert ex.receiver == "Aarav Sharma"
    assert ex.currency == "INR"
    assert len(ex.line_items) == 5
    assert ex.line_items[0] == LineItem("Paracetamol 650 Tab", Decimal("2"), Decimal("32.00"), Decimal("64.00"))
    assert {"label": "D.L. No:", "value": "PB-PTA-20B-1187"} in ex.other_fields
    assert {"label": "Vendor address", "value": "Shop 12, Leela Bhawan Market, Patiala 147001"} in ex.other_fields
    assert ex.text.splitlines()[0] == "SANJEEVANI MEDICOS"
    assert 0.9 < ex.field_confidence["total"] <= 1


def test_total_falls_back_to_amount_paid():
    ex = parse_analyze_expense(expense_response([field("AMOUNT_PAID", "₹1,91,200.00", confidence=90)]))
    assert ex.total == Decimal("191200.00")
    assert ex.field_confidence["total"] < 0.9


def test_unlabelled_totals_prefer_confidence_then_amount():
    ex = parse_analyze_expense(
        expense_response([field("TOTAL", "100.00", confidence=80), field("TOTAL", "250.00", confidence=95)])
    )
    assert ex.total == Decimal("250.00")


def test_total_tax_label_wins_over_parts():
    ex = parse_analyze_expense(
        expense_response(
            [
                field("TAX", "45.00", label="CGST 9%"),
                field("TAX", "45.00", label="SGST 9%"),
                field("TAX", "90.00", label="Total Tax"),
            ]
        )
    )
    assert ex.tax == Decimal("90.00")


def test_identical_tax_lines_are_not_double_counted():
    ex = parse_analyze_expense(
        expense_response([field("TAX", "18.00", label="IGST"), field("TAX", "18.00", label="IGST")])
    )
    assert ex.tax == Decimal("18.00")


def test_order_date_fallback_and_expense_row():
    ex = parse_analyze_expense(
        expense_response(
            [field("ORDER_DATE", "3 Nov 2026", confidence=90)],
            items=[item(EXPENSE_ROW="USB-C Hub 7-in-1 1 2,499.00", PRICE="2,499.00")],
        )
    )
    assert ex.document_date == "2026-11-03"
    assert ex.field_confidence["documentDate"] < 0.9
    assert ex.line_items[0].description == "USB-C Hub 7-in-1"


def test_empty_response():
    ex = parse_analyze_expense({"ExpenseDocuments": []})
    assert ex.total is None and ex.vendor is None and ex.text == "" and ex.line_items == []


# -------------------------------------------------------- OCR heuristics

FEE_OCR = """NORTHFIELD INSTITUTE OF TECHNOLOGY
Bhadson Road, Patiala - 147004
FEE RECEIPT
Receipt No: NIT/FR/2026/08817 Date: 14-Jul-2026
Student Name: Aarav Sharma Roll No: 1024XXXXXX
Programme: B.E. Computer Engineering, Semester 5
Particulars Amount
Tuition Fee 1,25,000.00
Hostel & Mess Fee 58,500.00
Examination Fee 4,500.00
Library & Lab Fee 3,200.00
Total Amount Paid Rs. 1,91,200.00
Rupees One Lakh Ninety One Thousand Two Hundred Only"""


def test_fee_receipt_from_ocr_text():
    ex = parse_lines(FEE_OCR.splitlines())
    assert ex.vendor == "NORTHFIELD INSTITUTE OF TECHNOLOGY"
    assert ex.document_date == "2026-07-14"
    assert ex.invoice_number == "NIT/FR/2026/08817"
    assert ex.total == Decimal("191200.00")
    assert ex.receiver == "Aarav Sharma"
    assert [i.description for i in ex.line_items] == [
        "Tuition Fee",
        "Hostel & Mess Fee",
        "Examination Fee",
        "Library & Lab Fee",
    ]
    assert sum(i.amount for i in ex.line_items) == ex.total


def test_pharmacy_bill_from_ocr_text():
    ex = parse_lines(PHARMACY_LINES)
    assert ex.vendor == "SANJEEVANI MEDICOS"
    assert ex.invoice_number == "SM/2611/0457"
    assert ex.document_date == "2026-11-08"
    assert ex.total == Decimal("607.42")  # not "Total Qty: 12", not the sub total
    assert ex.subtotal == Decimal("578.50")
    assert ex.tax == Decimal("28.92")
    assert ex.vendor_gstin == PHARMACY_GSTIN
    assert ex.receiver == "Aarav Sharma"
    first = ex.line_items[0]
    assert (first.description, first.quantity, first.unit_price, first.amount) == (
        "Paracetamol 650 Tab",
        Decimal("2"),
        Decimal("32.00"),
        Decimal("64.00"),
    )
    assert len(ex.line_items) == 5


def test_total_value_on_the_next_line_and_fallback():
    ex = parse_lines(["SHOP", "Grand Total", "₹ 450.00"])
    assert ex.total == Decimal("450.00")
    fallback = parse_lines(["SHOP", "Chai Rs. 40.00", "Samosa Rs. 30.00"])
    assert fallback.total == Decimal("40.00") and fallback.field_confidence["total"] == 0.5


def test_ocr_confidence_flows_into_fields():
    ex = parse_lines([Line("CITY CARE CLINIC", 0.62), Line("Net Payable Rs. 700.00", 0.58)])
    assert ex.field_confidence["vendor"] < 0.7 and ex.field_confidence["total"] < 0.7


def test_tesseract_tsv_lines():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t96\tGrand",
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t90\tTotal",
        "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t80\t450.00",
        "4\t1\t1\t1\t2\t0\t0\t0\t10\t10\t-1\t",
    ]
    lines = lines_from_tsv("\n".join([header, *rows]))
    assert [(line.text, line.confidence) for line in lines] == [("Grand Total", 0.93), ("450.00", 0.8)]


# ------------------------------------------------------------ the checks


def _extraction(**overrides) -> Extraction:
    base = {
        "engine": "test",
        "text": "Sanjeevani Medicos pharmacy tablets",
        "vendor": "Sanjeevani Medicos",
        "document_date": "2026-11-08",
        "total": Decimal("607.42"),
        "subtotal": Decimal("578.50"),
        "tax": Decimal("28.92"),
        "vendor_gstin": PHARMACY_GSTIN,
        "field_confidence": {"vendor": 0.99, "documentDate": 0.99, "total": 0.99},
    }
    return Extraction(**{**base, **overrides})


def test_clean_document_needs_no_review():
    result = summarize(_extraction(), today=date(2026, 11, 11))
    assert result["needsReview"] is False and result["reviewReasons"] == []
    assert {c["name"]: c["ok"] for c in result["checks"]} == {
        "gstin_checksum": True,
        "totals_reconcile": True,
        "date_plausible": True,
    }
    assert result["confidence"] == 0.99 and result["docType"] == "MEDICAL_BILL"


def test_failed_checks_flag_the_document():
    result = summarize(
        _extraction(total=Decimal("6070.42"), vendor_gstin="03AABCS1234K1ZX", document_date="2001-01-01"),
        today=date(2026, 11, 11),
    )
    failed = {c["name"] for c in result["checks"] if not c["ok"]}
    assert failed == {"gstin_checksum", "totals_reconcile", "date_plausible"}
    assert result["needsReview"] and len(result["reviewReasons"]) == 3


def test_missing_fields_lower_confidence():
    result = summarize(_extraction(total=None, subtotal=None, field_confidence={"vendor": 0.9, "documentDate": 0.9}))
    assert "total not found" in result["reviewReasons"]
    assert result["confidence"] == round(0.9 * 0.75, 4)


def test_line_items_sum_check():
    items = [LineItem("a", amount=Decimal("100")), LineItem("b", amount=Decimal("80"))]
    ok = summarize(_extraction(subtotal=None, tax=Decimal("0"), total=Decimal("180"), line_items=items))
    assert {c["name"]: c["ok"] for c in ok["checks"]}["line_items_sum"] is True
    bad = summarize(_extraction(subtotal=None, tax=None, total=Decimal("500"), line_items=items))
    assert {c["name"]: c["ok"] for c in bad["checks"]}["line_items_sum"] is False

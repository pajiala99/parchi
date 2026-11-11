"""Generate the sample documents, their ground truth and simulated Textract responses.

    python local/make_samples.py

Writes to samples/:
  *.png / *.jpg / *.pdf     rendered bills (every name, number and GSTIN is fictional)
  truth.json                the correct field values, for local/evaluate.py
  textract/*.json           SIMULATED AnalyzeExpense responses in the documented
                            response shape (written by this script, not recorded from
                            AWS), used by the offline "replay" engine
  textract/index.json       sha256 of each file -> its simulated response
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
FONTS = Path("/System/Library/Fonts/Supplemental")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (FONTS / name, Path("/usr/share/fonts/truetype/dejavu") / "DejaVuSans.ttf"):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


# --------------------------------------------------------------- the bills


@dataclass
class Bill:
    name: str
    kind: str  # png | jpg | pdf
    width: int
    regular: str
    bold: str
    size: int
    rows: list[tuple] = field(default_factory=list)
    truth: dict = field(default_factory=dict)
    summary: list[tuple] = field(default_factory=list)  # (TYPE, value, label)
    items: list[dict] = field(default_factory=list)


def fee_receipt() -> Bill:
    bill = Bill("fee-receipt", "png", 1240, "Georgia.ttf", "Georgia Bold.ttf", 30)
    fees = [("Tuition Fee", "1,25,000.00"), ("Hostel & Mess Fee", "58,500.00"),
            ("Examination Fee", "4,500.00"), ("Library & Lab Fee", "3,200.00")]  # fmt: skip
    bill.rows = [
        ("title", "NORTHFIELD INSTITUTE OF TECHNOLOGY"),
        ("center", "Bhadson Road, Patiala, Punjab 147004"),
        ("gap",),
        ("center_bold", "FEE RECEIPT"),
        ("pair", "Receipt No: NIT/FR/2026/08817", "Date: 14-Jul-2026"),
        ("text", "Student Name: Aarav Sharma"),
        ("text", "Enrolment No: NIT2403217"),
        ("text", "Programme: B.E. Computer Engineering, Semester 5"),
        ("text", "Academic Session: 2026-27"),
        ("rule",),
        ("pair_bold", "Particulars", "Amount (Rs.)"),
        *[("pair", label, amount) for label, amount in fees],
        ("rule",),
        ("pair_bold", "Total Amount Paid", "Rs. 1,91,200.00"),
        ("text", "Rupees One Lakh Ninety One Thousand Two Hundred Only"),
        ("gap",),
        ("text", "Payment Mode: NEFT    UTR: NIT26071498812"),
        ("small", "This is a computer generated receipt and does not need a signature."),
    ]
    bill.truth = {"docType": "FEE_RECEIPT", "vendor": "NORTHFIELD INSTITUTE OF TECHNOLOGY",
                  "documentDate": "2026-07-14", "invoiceNumber": "NIT/FR/2026/08817",
                  "total": "191200.00", "tax": None}  # fmt: skip
    bill.summary = [
        ("VENDOR_NAME", "NORTHFIELD INSTITUTE OF TECHNOLOGY", None),
        ("VENDOR_ADDRESS", "Bhadson Road, Patiala, Punjab 147004", None),
        ("INVOICE_RECEIPT_ID", "NIT/FR/2026/08817", "Receipt No:"),
        ("INVOICE_RECEIPT_DATE", "14-Jul-2026", "Date:"),
        ("RECEIVER_NAME", "Aarav Sharma", "Student Name:"),
        ("TOTAL", "Rs. 1,91,200.00", "Total Amount Paid"),
        ("OTHER", "NEFT", "Payment Mode:"),
    ]
    bill.items = [{"ITEM": label, "PRICE": amount} for label, amount in fees]
    return bill


PHARMACY_ITEMS = [
    ("Paracetamol 650 Tab", "B2341", "08/27", "2", "32.00", "64.00"),
    ("Azithromycin 500 Tab", "AZ771", "05/27", "1", "118.50", "118.50"),
    ("Cetirizine 10mg Tab", "CT220", "11/27", "3", "18.00", "54.00"),
    ("Pantoprazole 40 Tab", "PN915", "02/28", "2", "96.00", "192.00"),
    ("Vitamin D3 60K Cap", "VD301", "09/27", "4", "37.50", "150.00"),
]


def pharmacy_bill(name: str = "pharmacy-bill", kind: str = "png") -> Bill:
    bill = Bill(name, kind, 860, "Courier New.ttf", "Courier New Bold.ttf", 25)
    bill.rows = [
        ("title", "SANJEEVANI MEDICOS"),
        ("center", "Shop 12, Leela Bhawan Market, Patiala 147001"),
        ("center", "Ph: 0175-2201234"),
        ("center", "GSTIN: 03AABCS1234K1ZQ"),
        ("center", "D.L. No: PB-PTA-20B-1187"),
        ("center_bold", "GST INVOICE"),
        ("pair", "Bill No: SM/2611/0457", "Date: 08/11/2026"),
        ("text", "Patient: Aarav Sharma"),
        ("text", "Dr. R. Mehta"),
        ("rule",),
        ("mono", "Item                 Batch Exp   Qty   Rate  Amount"),
        ("rule",),
        *[("mono", f"{d:<20} {b:<5} {e:<5} {q:>3} {r:>6} {a:>7}") for d, b, e, q, r, a in PHARMACY_ITEMS],
        ("rule",),
        ("pair", "Total Qty: 12", ""),
        ("pair", "Sub Total", "578.50"),
        ("pair", "CGST @ 2.5%", "14.46"),
        ("pair", "SGST @ 2.5%", "14.46"),
        ("pair_bold", "Grand Total", "Rs. 607.42"),
        ("gap",),
        ("center", "Get well soon!"),
    ]
    bill.truth = {"docType": "MEDICAL_BILL", "vendor": "SANJEEVANI MEDICOS", "documentDate": "2026-11-08",
                  "invoiceNumber": "SM/2611/0457", "total": "607.42", "tax": "28.92"}  # fmt: skip
    bill.summary = [
        ("VENDOR_NAME", "SANJEEVANI MEDICOS", None),
        ("VENDOR_ADDRESS", "Shop 12, Leela Bhawan Market, Patiala 147001", None),
        ("VENDOR_PHONE", "0175-2201234", "Ph:"),
        ("VENDOR_GST_NUMBER", "03AABCS1234K1ZQ", "GSTIN:"),
        ("INVOICE_RECEIPT_ID", "SM/2611/0457", "Bill No:"),
        ("INVOICE_RECEIPT_DATE", "08/11/2026", "Date:"),
        ("RECEIVER_NAME", "Aarav Sharma", "Patient:"),
        ("TOTAL", "12", "Total Qty:"),
        ("SUBTOTAL", "578.50", "Sub Total"),
        ("TAX", "14.46", "CGST @ 2.5%"),
        ("TAX", "14.46", "SGST @ 2.5%"),
        ("TOTAL", "Rs. 607.42", "Grand Total"),
        ("OTHER", "PB-PTA-20B-1187", "D.L. No:"),
    ]
    bill.items = [{"ITEM": d, "QUANTITY": q, "UNIT_PRICE": r, "PRICE": a} for d, _, _, q, r, a in PHARMACY_ITEMS]
    return bill


INVOICE_ITEMS = [
    ("1", "Single Board Computer Kit 8GB", "8471", "2", "7,450.00", "14,900.00"),
    ("2", "USB-C Hub 7-in-1", "8473", "1", "2,499.00", "2,499.00"),
    ("3", "Micro Servo Motor 9g", "8501", "10", "85.00", "850.00"),
]


def tax_invoice() -> Bill:
    bill = Bill("tax-invoice", "png", 1300, "Arial.ttf", "Arial Bold.ttf", 26)
    bill.rows = [
        ("title", "KAVERI CLOUD SUPPLIES PVT. LTD."),
        ("center", "Plot 44, Phase 8 Industrial Area, Mohali, Punjab 160071"),
        ("center", "GSTIN: 03AAFCK7781M1Z2"),
        ("center_bold", "TAX INVOICE"),
        ("pair", "Invoice No: KCS/26-27/0423", "Invoice Date: 03 Nov 2026"),
        ("text", "Bill To: Patiala Robotics Club, GSTIN: 03BCDPN4410Q1ZL"),
        ("pair", "Place of Supply: Punjab (03)", "Due Date: 18 Nov 2026"),
        ("rule",),
        ("cols_bold", ["#", "Description", "HSN", "Qty", "Rate", "Amount"]),
        *[("cols", list(row)) for row in INVOICE_ITEMS],
        ("rule",),
        ("pair", "Taxable Value", "18,249.00"),
        ("pair", "CGST @ 9%", "1,642.41"),
        ("pair", "SGST @ 9%", "1,642.41"),
        ("pair_bold", "Grand Total", "Rs. 21,533.82"),
        ("text", "Rupees Twenty One Thousand Five Hundred Thirty Three and Eighty Two Paise Only"),
        ("small", "Payment terms: 15 days. Interest at 18% p.a. on overdue amounts."),
    ]
    bill.truth = {"docType": "INVOICE", "vendor": "KAVERI CLOUD SUPPLIES PVT. LTD.", "documentDate": "2026-11-03",
                  "invoiceNumber": "KCS/26-27/0423", "total": "21533.82", "tax": "3284.82"}  # fmt: skip
    bill.summary = [
        ("VENDOR_NAME", "KAVERI CLOUD SUPPLIES PVT. LTD.", None),
        ("VENDOR_GST_NUMBER", "03AAFCK7781M1Z2", "GSTIN:"),
        ("INVOICE_RECEIPT_ID", "KCS/26-27/0423", "Invoice No:"),
        ("INVOICE_RECEIPT_DATE", "03 Nov 2026", "Invoice Date:"),
        ("DUE_DATE", "18 Nov 2026", "Due Date:"),
        ("RECEIVER_NAME", "Patiala Robotics Club", "Bill To:"),
        ("SUBTOTAL", "18,249.00", "Taxable Value"),
        ("TAX", "1,642.41", "CGST @ 9%"),
        ("TAX", "1,642.41", "SGST @ 9%"),
        ("TOTAL", "Rs. 21,533.82", "Grand Total"),
        ("PAYMENT_TERMS", "15 days", "Payment terms:"),
    ]
    bill.items = [{"ITEM": d, "PRODUCT_CODE": h, "QUANTITY": q, "UNIT_PRICE": r, "PRICE": a}
                  for _, d, h, q, r, a in INVOICE_ITEMS]  # fmt: skip
    return bill


def canteen_receipt() -> Bill:
    bill = Bill("canteen-receipt", "png", 760, "Courier New.ttf", "Courier New Bold.ttf", 26)
    bill.rows = [
        ("title", "THE CORNER CANTEEN"),
        ("center", "Block C, Campus Road, Patiala"),
        ("pair", "Token No: 0427", "05-11-2026 13:42"),
        ("rule",),
        ("mono", "Masala Chai      2 x 20.00   40.00"),
        ("mono", "Aloo Paratha     2 x 60.00  120.00"),
        ("mono", "Sweet Lassi      1 x 50.00   50.00"),
        ("rule",),
        ("pair_bold", "TOTAL", "Rs. 210.00"),
        ("center", "Thank you, visit again!"),
    ]
    bill.truth = {"docType": "OTHER", "vendor": "THE CORNER CANTEEN", "documentDate": "2026-11-05",
                  "invoiceNumber": "0427", "total": "210.00", "tax": None}  # fmt: skip
    bill.summary = [
        ("VENDOR_NAME", "THE CORNER CANTEEN", None),
        ("INVOICE_RECEIPT_ID", "0427", "Token No:"),
        ("INVOICE_RECEIPT_DATE", "05-11-2026", None),
        ("TOTAL", "Rs. 210.00", "TOTAL"),
    ]
    bill.items = [
        {"ITEM": "Masala Chai", "QUANTITY": "2", "UNIT_PRICE": "20.00", "PRICE": "40.00"},
        {"ITEM": "Aloo Paratha", "QUANTITY": "2", "UNIT_PRICE": "60.00", "PRICE": "120.00"},
        {"ITEM": "Sweet Lassi", "QUANTITY": "1", "UNIT_PRICE": "50.00", "PRICE": "50.00"},
    ]
    return bill


def clinic_bill() -> Bill:
    bill = Bill("clinic-bill", "pdf", 1240, "Arial.ttf", "Arial Bold.ttf", 28)
    tests = [("Consultation Fee", "600.00"), ("Blood Sugar (Fasting)", "150.00"), ("Complete Blood Count", "350.00")]
    bill.rows = [
        ("title", "CITY CARE CLINIC"),
        ("center", "22 Mall Road, Patiala 147001"),
        ("center", "Dr. Neha Bansal, MBBS, MD (Medicine)"),
        ("center_bold", "OPD RECEIPT"),
        ("pair", "Receipt No: CCC/OPD/5521", "Date: 09/11/2026"),
        ("pair", "Patient: Aarav Sharma", "UHID: CC-88213"),
        ("rule",),
        ("pair_bold", "Particulars", "Amount"),
        *[("pair", label, amount) for label, amount in tests],
        ("rule",),
        ("pair_bold", "Total Amount", "Rs. 1,100.00"),
        ("text", "Paid by UPI"),
    ]
    bill.truth = {"docType": "MEDICAL_BILL", "vendor": "CITY CARE CLINIC", "documentDate": "2026-11-09",
                  "invoiceNumber": "CCC/OPD/5521", "total": "1100.00", "tax": None}  # fmt: skip
    bill.summary = [
        ("VENDOR_NAME", "CITY CARE CLINIC", None),
        ("INVOICE_RECEIPT_ID", "CCC/OPD/5521", "Receipt No:"),
        ("INVOICE_RECEIPT_DATE", "09/11/2026", "Date:"),
        ("RECEIVER_NAME", "Aarav Sharma", "Patient:"),
        ("TOTAL", "Rs. 1,100.00", "Total Amount"),
    ]
    bill.items = [{"ITEM": label, "PRICE": amount} for label, amount in tests]
    return bill


# --------------------------------------------------------------- rendering


def row_text(row: tuple) -> str:
    kind = row[0]
    if kind in ("pair", "pair_bold"):
        return f"{row[1]}  {row[2]}".strip()
    if kind in ("cols", "cols_bold"):
        return " ".join(row[1])
    if kind in ("rule", "gap"):
        return ""
    return row[1]


def render(bill: Bill) -> Image.Image:
    regular, bold = font(bill.regular, bill.size), font(bill.bold, bill.size)
    title, small = font(bill.bold, int(bill.size * 1.35)), font(bill.regular, int(bill.size * 0.8))
    line_h, margin = int(bill.size * 1.7), 60
    height = margin * 2 + line_h * (len(bill.rows) + 2)
    image = Image.new("RGB", (bill.width, height), (252, 251, 247))
    draw = ImageDraw.Draw(image)
    y = margin
    right = bill.width - margin
    columns = [margin, margin + 50, margin + int(bill.width * 0.48), margin + int(bill.width * 0.58)]
    for row in bill.rows:
        kind = row[0]
        if kind == "title":
            draw.text((bill.width / 2, y), row[1], font=title, fill="black", anchor="ma")
            y += int(line_h * 1.3)
            continue
        if kind in ("center", "center_bold"):
            face = bold if kind == "center_bold" else regular
            draw.text((bill.width / 2, y), row[1], font=face, fill="black", anchor="ma")
        elif kind in ("text", "mono"):
            draw.text((margin, y), row[1], font=regular, fill="black")
        elif kind == "small":
            draw.text((margin, y), row[1], font=small, fill=(60, 60, 60))
        elif kind in ("pair", "pair_bold"):
            face = bold if kind == "pair_bold" else regular
            draw.text((margin, y), row[1], font=face, fill="black")
            draw.text((right, y), row[2], font=face, fill="black", anchor="ra")
        elif kind in ("cols", "cols_bold"):
            face = bold if kind == "cols_bold" else regular
            number, description, hsn, qty, rate, amount = row[1]
            for x, text in zip(columns, (number, description, hsn, qty), strict=True):
                draw.text((x, y), text, font=face, fill="black")
            draw.text((right - int(bill.width * 0.17), y), rate, font=face, fill="black", anchor="ra")
            draw.text((right, y), amount, font=face, fill="black", anchor="ra")
        elif kind == "rule":
            draw.line((margin, y + line_h // 2, right, y + line_h // 2), fill=(90, 90, 90), width=2)
        y += line_h
    return image.crop((0, 0, bill.width, y + margin))


def phone_photo(image: Image.Image, seed: int = 7) -> Image.Image:
    """Make a clean scan look like a slightly tilted, soft, noisy phone photo."""
    rng = random.Random(seed)
    photo = image.rotate(-1.6, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(205, 200, 190))
    photo = photo.filter(ImageFilter.GaussianBlur(0.9))
    pixels = photo.load()
    width, height = photo.size
    for _ in range(width * height // 25):
        x, y = rng.randrange(width), rng.randrange(height)
        shade = rng.randint(-40, 40)
        r, g, b = pixels[x, y]
        pixels[x, y] = (max(0, min(255, r + shade)), max(0, min(255, g + shade)), max(0, min(255, b + shade)))
    gradient = Image.linear_gradient("L").resize((width, height)).point(lambda v: 255 - v // 5)
    return Image.composite(photo, Image.new("RGB", photo.size, (150, 145, 135)), gradient)


# ----------------------------------------------- simulated Textract output


def simulated_textract(bill: Bill, confidence: float) -> dict:
    """AnalyzeExpense-shaped response for the bill (simulated, not recorded from AWS)."""

    def detection(text: str, offset: float = 0.0) -> dict:
        return {"Text": text, "Confidence": round(confidence - offset, 2)}

    summary = []
    for kind, value, label in bill.summary:
        entry = {
            "Type": {"Text": kind, "Confidence": round(confidence, 2)},
            "ValueDetection": detection(value),
            "PageNumber": 1,
        }
        if label:
            entry["LabelDetection"] = detection(label, 0.5)
        if kind in ("TOTAL", "SUBTOTAL", "TAX"):
            entry["Currency"] = {"Code": "INR", "Confidence": 92.0}
        summary.append(entry)
    items = [
        {
            "LineItemExpenseFields": [
                {"Type": {"Text": kind, "Confidence": round(confidence, 2)}, "ValueDetection": detection(text, 1.0)}
                for kind, text in item.items()
            ]
        }
        for item in bill.items
    ]
    lines = [text for text in (row_text(row) for row in bill.rows) if text]
    return {
        "_comment": "SIMULATED AnalyzeExpense response generated by local/make_samples.py; not recorded from AWS.",
        "DocumentMetadata": {"Pages": 1},
        "ExpenseDocuments": [
            {
                "ExpenseIndex": 1,
                "SummaryFields": summary,
                "LineItemGroups": [{"LineItemGroupIndex": 1, "LineItems": items}],
                "Blocks": [
                    {"BlockType": "LINE", "Id": f"{bill.name}-{i}", "Text": text, "Confidence": round(confidence, 2)}
                    for i, text in enumerate(lines)
                ],
            }
        ],
    }


def encode(image: Image.Image, kind: str) -> bytes:
    buffer = io.BytesIO()
    if kind == "jpg":
        image.save(buffer, format="JPEG", quality=72)
    elif kind == "pdf":
        image.convert("RGB").save(buffer, format="PDF", resolution=150.0)  # image-only, like a scan
    else:
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> None:
    (OUT / "textract").mkdir(parents=True, exist_ok=True)
    bills = [fee_receipt(), pharmacy_bill(), tax_invoice(), canteen_receipt(), clinic_bill()]
    photo = pharmacy_bill("pharmacy-bill-photo", "jpg")
    truth, index = {}, {}
    for bill in [*bills, photo]:
        image = render(bill)
        if bill is photo:
            image = phone_photo(image)
        data = encode(image, bill.kind)
        file_name = f"{bill.name}.{bill.kind}"
        (OUT / file_name).write_bytes(data)
        truth[file_name] = bill.truth
        fixture = f"{bill.name}.json"
        response = simulated_textract(bill, 91.5 if bill is photo else 98.6)
        (OUT / "textract" / fixture).write_text(json.dumps(response, indent=2, ensure_ascii=False) + "\n")
        index[hashlib.sha256(data).hexdigest()] = fixture
        print(f"wrote samples/{file_name} ({len(data) // 1024} KB)")
    (OUT / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    (OUT / "textract" / "index.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main()

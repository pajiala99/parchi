"""Field-level accuracy of the offline Tesseract engine on the sample bills.

    python local/evaluate.py            # prints a Markdown table, writes samples/evaluation.json

The samples are synthetic (rendered by make_samples.py), so treat the numbers as a
smoke test of the heuristics, not a benchmark. The Textract path cannot be scored
here: that needs an AWS account (its parser is unit-tested on AnalyzeExpense-shaped
responses instead).
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from parchi.engines import PermanentError  # noqa: E402
from parchi.engines.tesseract_engine import TesseractEngine  # noqa: E402
from parchi.extraction import summarize  # noqa: E402

FIELDS = ("docType", "vendor", "documentDate", "invoiceNumber", "total", "tax")


def same(field: str, expected, actual) -> bool:
    if expected is None:
        return actual is None
    if actual is None:
        return False
    if field in ("total", "tax"):
        return Decimal(str(expected)) == Decimal(str(actual))
    if field == "vendor":
        return " ".join(str(expected).upper().split()) == " ".join(str(actual).upper().split())
    return str(expected) == str(actual)


def main() -> None:
    truth = json.loads((ROOT / "samples" / "truth.json").read_text())
    engine = TesseractEngine()
    rows, results = [], {}
    hits = dict.fromkeys(FIELDS, 0)
    for name, expected in truth.items():
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        started = time.perf_counter()
        try:
            got = summarize(engine.extract_bytes((ROOT / "samples" / name).read_bytes(), content_type))
        except PermanentError as exc:
            got = {"error": str(exc)}
        elapsed = round((time.perf_counter() - started) * 1000)
        marks = {field: same(field, expected[field], got.get(field)) for field in FIELDS}
        for field, ok in marks.items():
            hits[field] += ok
        results[name] = {"expected": expected, "got": {f: got.get(f) for f in FIELDS}, "ms": elapsed}
        cells = " | ".join("✓" if marks[f] else f"✗ `{got.get(f)}`" for f in FIELDS)
        rows.append(f"| {name} | {cells} | {elapsed} |")

    total = len(truth)
    print("| Sample | " + " | ".join(FIELDS) + " | ms |")
    print("|---" * (len(FIELDS) + 2) + "|")
    print("\n".join(rows))
    print("| **Accuracy** | " + " | ".join(f"**{hits[f]}/{total}**" for f in FIELDS) + " | |")
    overall = sum(hits.values()) / (total * len(FIELDS))
    print(f"\nOverall field accuracy: {overall:.0%} ({sum(hits.values())}/{total * len(FIELDS)})")
    out = ROOT / "samples" / "evaluation.json"
    out.write_text(json.dumps({"overall": round(overall, 4), "fields": hits, "samples": results}, indent=2, default=str))


if __name__ == "__main__":
    main()

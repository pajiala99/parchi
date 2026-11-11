"""Explainable document classifier: fee receipt, medical bill, invoice, or other.

Weighted keyword evidence instead of a model: every decision comes with the
signals that produced it, which the dashboard shows ("medical bill because:
pharmacy, tablets, batch no., patient").
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

FEE_RECEIPT = "FEE_RECEIPT"
MEDICAL_BILL = "MEDICAL_BILL"
INVOICE = "INVOICE"
OTHER = "OTHER"
DOC_TYPES = (FEE_RECEIPT, MEDICAL_BILL, INVOICE, OTHER)

# (pattern, weight, label shown to the user)
RULES: dict[str, list[tuple[str, float, str]]] = {
    FEE_RECEIPT: [
        (r"\bfees?\s+receipt\b", 5, "fee receipt"),
        (r"\btuition\b", 3, "tuition"),
        (r"\bsemester\b", 3, "semester"),
        (r"\bacademic\b", 2, "academic"),
        (r"\buniversity\b", 2.5, "university"),
        (r"\binstitute\b", 2, "institute"),
        (r"\bcollege\b", 2.5, "college"),
        (r"\bschool\b", 2, "school"),
        (r"\bhostel\b", 2, "hostel"),
        (r"\badmission\b", 2, "admission"),
        (r"\bexamination\b|\bexam\s+fee", 2, "examination"),
        (r"\broll\s*(?:no|number)\b", 3, "roll no."),
        (r"\benrol(?:l)?ment\b", 2.5, "enrolment"),
        (r"\bstudent\b", 2.5, "student"),
        (r"\bprogramme\b|\bprogram\b|\bcourse\b", 1.5, "programme"),
        (r"\bfees?\b", 1.5, "fee"),
        (r"\bb\.?\s?tech\b|\bb\.\s?e\.|\bm\.?\s?tech\b|\bmba\b", 1.5, "degree"),
    ],
    MEDICAL_BILL: [
        (r"\bpharmacy\b|\bpharma\b", 3, "pharmacy"),
        (r"\bchemists?\b|\bdruggists?\b", 3, "chemist"),
        (r"\bmedicos?\b|\bmedical\s+(?:store|stores|hall|centre|center)\b", 3, "medical store"),
        (r"\bhospital\b", 3, "hospital"),
        (r"\bclinic\b|\bpolyclinic\b", 3, "clinic"),
        (r"\bdiagnostics?\b|\bpathology\b|\blaboratory\b", 2.5, "diagnostics"),
        (r"\bdr\.?\s+[a-z]", 1.5, "doctor's name"),
        (r"\bdoctor\b|\bconsultant\b|\bphysician\b", 2, "doctor"),
        (r"\bpatient\b", 3, "patient"),
        (r"\btab(?:let)?s?\b", 2, "tablets"),
        (r"\bcap(?:sule)?s?\b", 1.5, "capsules"),
        (r"\bsyrup\b|\binj(?:ection)?\b|\bointment\b|\bdrops\b", 2, "syrup / injection"),
        (r"\b\d+\s?mg\b", 2, "dosage (mg)"),
        (r"\bbatch\b", 1.5, "batch no."),
        (r"\bexp(?:iry)?\b", 1, "expiry"),
        (r"\bd\.?\s?l\.?\s*no\b|\bdrug\s+lic", 3, "drug licence"),
        (r"\bconsultation\b|\bopd\b|\bipd\b|\buhid\b", 3, "consultation"),
        (r"\bprescription\b|\brx\b", 2, "prescription"),
        (r"\bmrp\b", 1, "MRP"),
    ],
    INVOICE: [
        (r"\btax\s+invoice\b", 5, "tax invoice"),
        (r"\binvoice\b", 3, "invoice"),
        (r"\bhsn\b|\bsac\b", 2.5, "HSN/SAC codes"),
        (r"\bbill(?:ed)?\s+to\b|\bship(?:ped)?\s+to\b|\bbuyer\b", 2.5, "bill to / ship to"),
        (r"\bplace\s+of\s+supply\b", 3, "place of supply"),
        (r"\bpo\s*(?:no|number|#)|\bpurchase\s+order\b", 2, "purchase order"),
        (r"\bdue\s+date\b|\bpayment\s+terms\b", 2.5, "payment terms"),
        (r"\btaxable\s+(?:value|amount)\b", 2, "taxable value"),
        (r"\breverse\s+charge\b|\be-?way\s+bill\b|\birn\b", 2.5, "GST compliance fields"),
        (r"\bigst\b", 1, "IGST"),
        (r"\bproforma\b", 2, "proforma"),
    ],
}
_COMPILED = {
    doc_type: [(re.compile(p, re.IGNORECASE), w, label) for p, w, label in rules] for doc_type, rules in RULES.items()
}
THRESHOLD = 4.0  # below this much evidence a document is "other"
TEMPERATURE = 3.0


@dataclass
class Classification:
    doc_type: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def classify(text: str, vendor: str | None = None) -> Classification:
    vendor = vendor or ""
    scores: dict[str, float] = {}
    evidence: dict[str, list[tuple[float, str]]] = {}
    for doc_type, rules in _COMPILED.items():
        total = 0.0
        hits: list[tuple[float, str]] = []
        for pattern, weight, label in rules:
            in_text = len(pattern.findall(text))
            in_vendor = len(pattern.findall(vendor))
            if in_text or in_vendor:
                # The shop's own name ("City Care Hospital") is stronger evidence than a stray word.
                contribution = weight * min(in_text, 2) + weight * 1.5 * min(in_vendor, 1)
                total += contribution
                hits.append((contribution, label))
        scores[doc_type] = round(total, 2)
        evidence[doc_type] = sorted(hits, reverse=True)

    best = max(scores, key=lambda key: scores[key])
    logits = {**scores, OTHER: THRESHOLD}
    peak = max(logits.values())
    weights = {k: math.exp((v - peak) / TEMPERATURE) for k, v in logits.items()}
    norm = sum(weights.values())
    if scores[best] < THRESHOLD:
        return Classification(OTHER, round(weights[OTHER] / norm, 4), [], scores)
    signals = []
    for _, label in evidence[best]:
        if label not in signals:
            signals.append(label)
    return Classification(best, round(weights[best] / norm, 4), signals[:6], scores)

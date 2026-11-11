"""Amazon Textract AnalyzeExpense, reading the object straight from S3 (no download)."""

from __future__ import annotations

from botocore.exceptions import ClientError

from .. import aws
from ..extraction.model import Extraction
from ..extraction.textract import parse_analyze_expense
from . import PermanentError

# Errors caused by the document itself. Everything else (throttling, timeouts,
# service errors, missing permissions) is retried and ends in the DLQ if it persists.
PERMANENT = {
    "UnsupportedDocumentException": "Textract can't read this file. Upload a JPEG or PNG photo, or a single-page PDF.",
    "BadDocumentException": "The file looks damaged, or it isn't a readable image or PDF.",
    "DocumentTooLargeException": "The document is too large for Textract (10 MB, 10,000 px per side at most).",
    "InvalidS3ObjectException": "The uploaded file could not be found in S3.",
    "InvalidParameterException": "Textract rejected the file (it may be empty or have more than one page).",
}


class TextractEngine:
    name = "textract"

    def extract(self, bucket: str, key: str, content_type: str) -> Extraction:
        try:
            response = aws.textract().analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in PERMANENT:
                raise PermanentError(PERMANENT[code]) from exc
            raise
        return parse_analyze_expense(response, engine=self.name)

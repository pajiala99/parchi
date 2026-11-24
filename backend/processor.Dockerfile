# The processor Lambda as a container image, so it can carry the Tesseract OCR binary.
# Built by `sam build` (context: repository root) for arm64 Lambda.
#
# AWS's own Python base image (Amazon Linux 2023) has no Tesseract package, so this uses
# Debian's, plus the AWS Lambda runtime interface client (awslambdaric).
FROM python:3.13-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

# boto3 for AWS calls, Pillow and pypdfium2 for image clean-up and PDFs.
RUN pip install --no-cache-dir awslambdaric boto3 pillow pypdfium2

WORKDIR /var/task
COPY backend/src/ ./
# Saved Textract responses for the sample bills (OcrEngine=replay).
COPY samples/textract/ ./parchi/engines/replay_samples/

ENV TESSERACT_BIN=/usr/bin/tesseract
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["parchi.handlers.processor.handler"]

# Parchi — serverless, event-driven document processing on AWS

*Parchi* (पर्ची) is Hindi for a slip or receipt. Upload a fee receipt, medical bill or invoice: it goes straight to S3 with a presigned POST, the S3 event lands in SQS, a Lambda worker reads it with Amazon Textract (AnalyzeExpense), checks and files the fields in DynamoDB, and SNS emails a confirmation. No server runs at any point.

UCS531 Cloud Computing project, Thapar Institute of Engineering & Technology. Team: Hardik Pajiala, Shikhar Goel, Rumnik Dhillon.

- `template.yaml` — AWS SAM: 4 Lambdas, HTTP API, S3 + CloudFront, SQS + DLQ, DynamoDB, SNS, per-function IAM roles, alarms, dashboard. Passes cfn-lint and `sam validate --lint`; `sam build` succeeds. **Not deployed yet.**
- Offline: `.venv/bin/python local/devstack.py` then `cd frontend && npm run dev` (moto emulates AWS; Tesseract does the OCR).
- Tests: `.venv/bin/pytest` (117 passing, 93% coverage, incl. an HTTP end-to-end run) and `cd frontend && npm test` (11 passing).
- Deploy: `sam build && sam deploy --guided`, then sync `frontend/dist` to the `SiteBucketName` output.

Sample bills and their Textract responses in `samples/` are synthetic; all names and GSTINs are fictional.

[LinkedIn](https://linkedin.com/in/hardik-pajiala) · [Portfolio](https://pajiala99.github.io) · [GitHub](https://github.com/pajiala99)

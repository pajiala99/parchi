# Demo and viva guide

Parchi runs fully offline for the demo: the **same Lambda code** that would run on AWS, against an emulator of S3, SQS, SNS and DynamoDB (moto), with Tesseract doing the OCR instead of Textract. No AWS account and no internet needed (apart from the web fonts).

## Before the viva (once, on the demo laptop)

```bash
git clone https://github.com/pajiala99/parchi && cd parchi
brew install tesseract          # real OCR; Windows/Linux: install tesseract-ocr
scripts/demo.sh                 # first run sets everything up (2–3 minutes)
```

Needs Python 3.12+ and Node.js 20+. After the first run it starts in a few seconds and opens **http://localhost:3001**. `Ctrl+C` stops it. Each start begins with an empty ledger.

Keep a real bill or receipt photo on the laptop too (a canteen bill, a pharmacy receipt): reading a bill the examiners didn't expect is the most convincing moment.

## The 5-minute demo

| # | Do | Say |
|---|---|---|
| 1 | Open **How it works**. | "Every box is a managed AWS service. Nothing runs between requests: no EC2, no containers. The whole stack is code in `template.yaml`, about 45 resources, deployed with one command." Walk through the numbered arrows. |
| 2 | Back to **Ledger**. Open **Settings**, enter an email, close it. | "Notifications go through one SNS topic for all users. Each subscription carries a filter policy, so you only get your own emails." |
| 3 | Click **Tax invoice** under "Try a sample", then **Pharmacy bill (photo)**. Point at the **Live pipeline** strip. | "The browser asked our API for a presigned URL and uploaded straight to S3. S3 fired an event into SQS, a Lambda worker picked it up, ran OCR, checked the numbers and filed it in DynamoDB. Under a second." |
| 4 | Open the pharmacy bill. | "Vendor, date, GST, line items, each with its OCR confidence. It's classified as a medical bill, and it shows why: pharmacy, tablets, drug licence. The checks: subtotal plus CGST plus SGST equals the total." |
| 5 | Scroll to **Pipeline** on the same page. | "Timestamps from each stage: how long it waited in the queue, how long OCR took, when it was filed and emailed." |
| 6 | **Correct fields** → change the total → Save. | "Humans stay in the loop: anything that fails a check is flagged for review, and corrections are recorded." |
| 7 | Drag in your own real bill. | Let it be read live. If a field is off: "that's why the checks and the review flag exist." |
| 8 | **Settings** → Outbox. | "Offline, SNS can't send real email, so the emulator's mailbox shows exactly what would have gone out." |
| 9 | Show `template.yaml` and the CI badge on GitHub. | "118 automated tests, including an end-to-end run over HTTP through the emulator. CI runs them on every push." |

## Questions you are likely to get

**Why SQS between S3 and Lambda, when S3 can call Lambda directly?**
Textract accepts only a few requests per second. The queue absorbs bursts, retries failures with a delay, and after three failed tries moves the event to a dead-letter queue (alarmed) instead of losing it. Lambda's event source mapping scales workers with the queue, capped at five.

**What if the same message is delivered twice?** (SQS is at-least-once.)
The worker claims a document with a DynamoDB conditional write and a 120-second lease. A duplicate finds it filed or in progress and is skipped. This is tested.

**What if one file in a batch is corrupt?**
Partial batch responses (`ReportBatchItemFailures`): only that message is retried. A file that can never be read is marked failed at once, with a reason, and the user is emailed.

**Why doesn't the file go through the API?**
API Gateway caps payload size and Lambda bills for the time spent streaming bytes. A presigned POST lets S3 itself enforce the file type, the size limit (10 MB) and the exact key.

**How is it secured?**
Private buckets with encryption and TLS-only policies; CloudFront is the only reader of the site bucket; one least-privilege IAM role per function; presigned URLs expire in 5 minutes; API throttling and daily upload caps. Users are separated by a random workspace key whose hash scopes every query; production would add Cognito.

**What does it cost?**
About $10 per 1,000 documents, ~99% of it Textract. Idle cost is effectively zero, versus ~$15 a month for even a small always-on server.

**Why isn't it deployed?**
Answer honestly: the template validates and builds, and `scripts/deploy.sh` deploys it in one command, but the AWS Academy labs we had are per-lesson sandboxes that allow only that lesson's services and delete resources when the lab ends. With a Learner Lab or a personal account it deploys as-is. The offline mode runs the identical handler code.

**What is "serverless" here, exactly?**
Function-as-a-Service (Lambda) for all the logic, plus fully managed services (S3, SQS, DynamoDB, SNS, Textract) for everything else. AWS provisions, scales and patches; we pay per request.

## If something goes wrong during the demo

- **Port already in use:** `scripts/demo.sh --port 3005`.
- **A bill reads badly:** use the sample bills; they are known to read correctly.
- **No Tesseract on the demo laptop:** the sample bills still work (they replay simulated Textract responses).
- **Backup:** screenshots in `docs/screenshots/`.

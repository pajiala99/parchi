# Deploying Parchi

## What you need

- An AWS account (a normal account on the free tier, or AWS Academy Learner Lab)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), configured (`aws configure` or SSO)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Python 3.13 (for `sam build`) and Node.js 20+ (for the dashboard)

Check credentials with `aws sts get-caller-identity`.

## Deploy

```bash
scripts/deploy.sh -e you@example.com
```

The script:

1. `sam build` packages the four Lambda functions (no third-party dependencies; boto3 ships with the runtime).
2. `sam deploy` creates stack `parchi-dev` in `ap-south-1`: S3 buckets, SQS + DLQ, DynamoDB, SNS, HTTP API, CloudFront, IAM roles, alarms, dashboard and (because `-e` was given) a $5 monthly budget alert.
3. Builds the dashboard and syncs it to the site bucket (hashed assets cached for a year, `index.html` re-checked on every visit), then invalidates CloudFront.
4. Prints the app URL (`https://<id>.cloudfront.net`) and the CloudWatch dashboard URL.

The first deploy takes 5–10 minutes, most of it CloudFront. Confirm the SNS email AWS sends to the alert address.

Options: `-s <stage>` for a second copy (e.g. `prod`), `-r <region>`, `--stack-only`, `--site-only`. Everything else is a template parameter (`sam deploy --parameter-overrides`):

| Parameter | Default | Meaning |
|---|---|---|
| `DailyUploadLimitPerWorkspace` | 20 | uploads one browser workspace may make per day |
| `DailyUploadLimitGlobal` | 200 | uploads the whole deployment may make per day (Textract cost cap) |
| `MaxUploadMB` | 10 | largest file (Textract's synchronous limit) |
| `ProcessorMaxConcurrency` | 5 | most worker Lambdas running at once |
| `UploadRetentionDays` | 0 | delete uploads after N days (0 keeps them) |
| `AlertEmail` / `MonthlyBudgetUsd` | — / 5 | alarm and budget emails |
| `ExistingLambdaRoleArn` | — | use one existing role instead of creating roles |

## AWS Academy Learner Lab

Learner Lab accounts cannot create IAM roles and only allow `us-east-1` and `us-west-2`. Use the lab's `LabRole`:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
scripts/deploy.sh -r us-east-1 --lambda-role arn:aws:iam::$ACCOUNT:role/LabRole
```

Budgets may be unavailable there; leave `-e` off if the deploy fails on `MonthlyBudget`.

## Check that it works

1. Open the app URL and upload a sample bill ("Try a sample").
2. Watch the live pipeline strip; the document should be filed within a few seconds.
3. In Settings, subscribe an email, confirm the AWS email, upload again: a confirmation arrives.
4. In the AWS console, the CloudWatch dashboard `parchi-dev-<region>` shows documents processed, latency and queue depth; the Lambda logs are JSON lines.

## Troubleshooting

- **Upload fails with a CORS error:** open the dashboard from the CloudFront URL (or `http://localhost:5173` in development); those are the only origins the upload bucket allows.
- **Documents stay "in queue":** check the worker's logs (`/aws/lambda/parchi-dev-ProcessorFunction-*`) and the dead-letter queue. `AccessDeniedException` from Textract means the region doesn't offer it or the role lacks `textract:AnalyzeExpense`.
- **"Textract can't read this file":** the synchronous API rejects multi-page PDFs; upload one page.
- **Dashboard shows an old version:** CloudFront invalidations take a minute or two.

## Remove everything

```bash
scripts/teardown.sh            # empties both buckets, then sam delete
```

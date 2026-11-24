#!/usr/bin/env bash
# Deploy Parchi: the SAM stack first, then the dashboard build to S3 + CloudFront.
#
#   scripts/deploy.sh                                  # stage "dev" in ap-south-1
#   scripts/deploy.sh -s prod -r us-east-1 -e me@example.com
#   scripts/deploy.sh --lambda-role arn:aws:iam::123456789012:role/LabRole   # AWS Academy
#   scripts/deploy.sh --site-only                      # re-publish the dashboard only
#
# Needs the AWS CLI, the SAM CLI, Node.js and working AWS credentials.
set -euo pipefail

STAGE="dev"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
ALERT_EMAIL=""
LAMBDA_ROLE=""
DO_STACK=1
DO_SITE=1

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  -s, --stage NAME        deployment name, part of every resource name (default: dev)
  -r, --region REGION     AWS region (default: $AWS_REGION, else ap-south-1)
  -e, --alert-email ADDR  receive alarms and the monthly budget alert
      --lambda-role ARN   run every function with this role instead of creating roles
      --stack-only        deploy the backend, leave the dashboard as it is
      --site-only         publish the dashboard to an already deployed stack
  -h, --help              show this help
EOF
}

die() {
  echo "deploy: $*" >&2
  exit 1
}

need_value() {
  [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s | --stage) need_value "$@"; STAGE="$2"; shift 2 ;;
    -r | --region) need_value "$@"; REGION="$2"; shift 2 ;;
    -e | --alert-email) need_value "$@"; ALERT_EMAIL="$2"; shift 2 ;;
    --lambda-role) need_value "$@"; LAMBDA_ROLE="$2"; shift 2 ;;
    --stack-only) DO_SITE=0; shift ;;
    --site-only) DO_STACK=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ "$STAGE" =~ ^[a-z0-9-]{1,10}$ ]] || die "stage must match ^[a-z0-9-]{1,10}$ (got '$STAGE')"
[[ $DO_STACK -eq 1 || $DO_SITE -eq 1 ]] || die "--stack-only and --site-only cannot be combined"

STACK="parchi-$STAGE"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for tool in aws sam; do
  command -v "$tool" >/dev/null || die "$tool is not installed"
done
if [[ $DO_SITE -eq 1 ]]; then
  command -v npm >/dev/null || die "npm is not installed"
fi
aws sts get-caller-identity --region "$REGION" >/dev/null || die "no working AWS credentials"

stack_output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

if [[ $DO_STACK -eq 1 ]]; then
  echo "==> Building and deploying stack $STACK in $REGION"
  overrides=("Stage=$STAGE")
  [[ -n "$ALERT_EMAIL" ]] && overrides+=("AlertEmail=$ALERT_EMAIL")
  [[ -n "$LAMBDA_ROLE" ]] && overrides+=("ExistingLambdaRoleArn=$LAMBDA_ROLE")
  sam build
  sam deploy --stack-name "$STACK" --s3-prefix "$STACK" --region "$REGION" \
    --parameter-overrides "${overrides[@]}"
fi

if [[ $DO_SITE -eq 1 ]]; then
  site_bucket="$(stack_output SiteBucketName)"
  distribution="$(stack_output DistributionId)"
  [[ -n "$site_bucket" && "$site_bucket" != "None" ]] || die "stack $STACK has no SiteBucketName output; deploy it first"
  [[ -n "$distribution" && "$distribution" != "None" ]] || die "stack $STACK has no DistributionId output"

  echo "==> Building the dashboard"
  (cd frontend && npm ci && npm run build)

  echo "==> Publishing to s3://$site_bucket"
  # Vite fingerprints everything under assets/, so those files can be cached for a year;
  # the rest (index.html, samples, favicon) must be re-checked on every visit.
  aws s3 sync frontend/dist "s3://$site_bucket" --region "$REGION" --delete \
    --exclude "assets/*" --cache-control "no-cache"
  aws s3 sync frontend/dist/assets "s3://$site_bucket/assets" --region "$REGION" --delete \
    --cache-control "public, max-age=31536000, immutable"
  aws cloudfront create-invalidation --distribution-id "$distribution" --paths "/*" \
    --query "Invalidation.Id" --output text
fi

echo
echo "App:       $(stack_output AppUrl)"
echo "Dashboard: $(stack_output DashboardUrl)"
if [[ -n "$ALERT_EMAIL" && $DO_STACK -eq 1 ]]; then
  echo "Confirm the subscription email sent to $ALERT_EMAIL to start receiving alarms."
fi

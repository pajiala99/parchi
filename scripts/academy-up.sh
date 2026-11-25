#!/usr/bin/env bash
# Bring Parchi up in an AWS Academy sandbox, which deletes everything when a lab session ends.
# Deploys the whole stack, points the Vercel site at the new CloudFront address and checks it.
#
#   1. Start the lab, copy AWS Details -> AWS CLI, then: pbpaste > ~/.aws/credentials
#   2. scripts/academy-up.sh            (about 10 minutes; needs Docker Desktop running)
set -euo pipefail
cd "$(dirname "$0")/.."

REGION=us-east-1
VERCEL_SITE=https://parchi-theta.vercel.app
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.docker/run/docker.sock}"

# A paste that lost its first character leaves "default]"; fix the header so the CLI can read the file.
sed -i '' '1s/^default\]/[default]/' "$HOME/.aws/credentials" 2>/dev/null || true

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "==> Account $ACCOUNT, region $REGION"
docker info >/dev/null 2>&1 || { echo "Docker is not running: open Docker Desktop and try again." >&2; exit 1; }

scripts/deploy.sh --region "$REGION" --lambda-role "arn:aws:iam::${ACCOUNT}:role/LabRole" \
  --ocr-engine tesseract --extra-origin "$VERCEL_SITE"

APP=$(aws cloudformation describe-stacks --region "$REGION" --stack-name parchi-dev \
  --query "Stacks[0].Outputs[?OutputKey=='AppUrl'].OutputValue" --output text)
HOST=${APP#https://}
HOST=${HOST%/}
echo "==> Pointing Vercel at $HOST"
sed -i '' -E "s#https://[a-z0-9]+\.cloudfront\.net#https://${HOST}#" vercel.json
sed -i '' -E "s#[a-z0-9]+\.cloudfront\.net#${HOST}#g" README.md
npx --yes vercel deploy --prod --yes >/dev/null

echo "==> Checking $VERCEL_SITE/api/health"
for _ in 1 2 3 4 5 6; do
  if curl -fs -m 15 "$VERCEL_SITE/api/health" | grep -q '"ok": true'; then
    echo "Live: $VERCEL_SITE  (and $APP)"
    exit 0
  fi
  sleep 10
done
echo "The API did not answer yet; CloudFront can take a few more minutes. Try $APP/api/health." >&2
exit 1

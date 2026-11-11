#!/usr/bin/env bash
# Delete a deployed Parchi stack and everything in it (uploads, documents, dashboard).
#
#   scripts/teardown.sh                 # stage "dev" in ap-south-1
#   scripts/teardown.sh -s prod -r us-east-1
#
# CloudFormation cannot delete non-empty buckets, so both buckets are emptied first.
# This cannot be undone.
set -euo pipefail

STAGE="dev"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s | --stage) STAGE="$2"; shift 2 ;;
    -r | --region) REGION="$2"; shift 2 ;;
    -y | --yes) YES=1; shift ;;
    -h | --help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "teardown: unknown option $1" >&2; exit 1 ;;
  esac
done

STACK="parchi-$STAGE"
command -v aws >/dev/null || { echo "teardown: the AWS CLI is not installed" >&2; exit 1; }
command -v sam >/dev/null || { echo "teardown: the SAM CLI is not installed" >&2; exit 1; }

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

uploads="$(output UploadBucketName)"
site="$(output SiteBucketName)"
[[ -n "$uploads$site" ]] || { echo "teardown: stack $STACK not found in $REGION" >&2; exit 1; }

if [[ $YES -ne 1 ]]; then
  read -r -p "Delete stack $STACK in $REGION, all uploaded files and all filed documents? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "Cancelled."; exit 0; }
fi

for bucket in "$uploads" "$site"; do
  if [[ -n "$bucket" && "$bucket" != "None" ]]; then
    echo "==> Emptying s3://$bucket"
    aws s3 rm "s3://$bucket" --recursive --region "$REGION" --only-show-errors
  fi
done

echo "==> Deleting stack $STACK"
sam delete --stack-name "$STACK" --region "$REGION" --no-prompts
echo "Done. The SAM deployment bucket (aws-sam-cli-managed-default) is shared and was left in place."

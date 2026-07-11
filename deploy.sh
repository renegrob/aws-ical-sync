#!/usr/bin/env bash
# Deploys the hockey-calendar-sync Lambda + daily EventBridge schedule.
# Prerequisites:
#   - AWS CLI configured (aws configure) with a user/role that can create
#     IAM roles, Lambda functions, and EventBridge rules.
#   - You have already created a Google service account key and put it
#     into the SSM parameter (see step 3 below) BEFORE running this script.
#
# Usage:
#   ./deploy.sh
#
# Re-running this script after making code changes will update the
# existing function (it's idempotent).

set -euo pipefail

# ---- Config: edit these ----------------------------------------------
FUNCTION_NAME="hockey-calendar-sync"
REGION="eu-central-1"                     # pick a region close to you
SSM_PARAM_NAME="/hockey-sync/google-service-account"
SCHEDULE_EXPRESSION="cron(0 5 * * ? *)"   # 05:00 UTC daily - edit as needed
ROLE_NAME="hockey-calendar-sync-role"

# Define the iCal feeds to sync in a JSON array.
# Use unique `uid_prefix` for each feed to prevent event deletion conflicts.
SYNC_CONFIGS='[
  {
    "ical_url": "https://app.myice.hockey/api/players/ical/50553/113",
    "calendar_id": "primary",
    "uid_prefix": "hockey1-"
  }
]'
# ------------------------------------------------------------------------

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "== 1/6 Packaging Lambda =="
PROJECT_DIR="$(pwd)"
BUILD_DIR=$(mktemp -d -t aws-ical-sync-build-XXXXXX)
trap 'rm -rf "$BUILD_DIR"' EXIT

if command -v uv &> /dev/null; then
  echo "Using uv for packaging..."
  uv pip install -r requirements.txt -t "$BUILD_DIR" --quiet --only-binary :all: --python-platform manylinux2014_x86_64 --python-version 3.12
else
  echo "uv not found, falling back to pip..."
  pip install -r requirements.txt -t "$BUILD_DIR" --quiet --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.12 2>/dev/null \
    || pip install -r requirements.txt -t "$BUILD_DIR" --quiet
fi

cp lambda_function.py "$BUILD_DIR"/
(cd "$BUILD_DIR" && zip -r "${PROJECT_DIR}/function.zip" . -q)
echo "Package size: $(du -h function.zip | cut -f1)"


echo "== 2/6 Creating/updating IAM role =="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file://trust-policy.json >/dev/null
  echo "Role created, waiting for IAM propagation..."
  sleep 10
fi
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "hockey-calendar-sync-policy" \
  --policy-document file://lambda-policy.json >/dev/null

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo "== 3/6 Checking SSM parameter exists =="
if ! aws ssm get-parameter --name "$SSM_PARAM_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: SSM parameter $SSM_PARAM_NAME not found in $REGION."
  echo "Create it first with:"
  echo "  aws ssm put-parameter --name \"$SSM_PARAM_NAME\" --type SecureString \\"
  echo "    --value file://service-account-key.json --region $REGION"
  exit 1
fi

echo "== 4/6 Creating/updating Lambda function =="
export SYNC_CONFIGS SSM_PARAM_NAME
ENV_JSON=$(python3 -c "import json, os; print(json.dumps({'Variables': {'SYNC_CONFIGS': os.environ['SYNC_CONFIGS'], 'SERVICE_ACCOUNT_PARAM': os.environ['SSM_PARAM_NAME']}}))")

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb://function.zip \
    --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "$ENV_JSON" \
    --region "$REGION" >/dev/null
else
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler lambda_function.handler \
    --timeout 30 \
    --memory-size 256 \
    --zip-file fileb://function.zip \
    --environment "$ENV_JSON" \
    --region "$REGION" >/dev/null
fi

echo "== 5/6 Creating daily EventBridge schedule =="
aws events put-rule \
  --name "${FUNCTION_NAME}-daily" \
  --schedule-expression "$SCHEDULE_EXPRESSION" \
  --region "$REGION" >/dev/null

FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Configuration.FunctionArn' --output text)

aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id "${FUNCTION_NAME}-eventbridge" \
  --action "lambda:InvokeFunction" \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${FUNCTION_NAME}-daily" \
  --region "$REGION" >/dev/null 2>&1 || echo "(permission already exists, skipping)"

aws events put-targets \
  --rule "${FUNCTION_NAME}-daily" \
  --targets "Id"="1","Arn"="$FUNCTION_ARN" \
  --region "$REGION" >/dev/null

echo "== 6/6 Done =="
echo "Function: $FUNCTION_ARN"
echo "Schedule: $SCHEDULE_EXPRESSION"
echo ""
echo "Test it manually with:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --log-type Tail out.json && cat out.json"

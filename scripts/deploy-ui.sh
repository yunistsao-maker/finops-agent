#!/usr/bin/env bash
# Build web/ against the deployed stack and publish it to the Amplify app (manual deployment, no Git connection).
#   scripts/deploy-ui.sh                     # stack AgentCore-FinopsAgent-default, branch main
#   STACK=... BRANCH=... scripts/deploy-ui.sh
set -euo pipefail
STACK=${STACK:-AgentCore-FinopsAgent-default}
BRANCH=${BRANCH:-main}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
REGION=${AWS_REGION:-us-east-1}

out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?starts_with(OutputKey,'FinopsUi$1')].OutputValue | [0]" --output text
}

APP_ID=$(out AmplifyAppId)
cat > "$ROOT/web/.env.production" <<ENV
VITE_REGION=$REGION
VITE_USER_POOL_ID=$(out UserPoolId)
VITE_USER_POOL_CLIENT_ID=$(out UserPoolClientId)
VITE_IDENTITY_POOL_ID=$(out IdentityPoolId)
VITE_RUNTIME_ARN=$(out RuntimeArn)
VITE_GUEST_ACCESS=$(out GuestAccess)
VITE_GUEST_ROLE_ARN=$(out GuestRoleArn)
ENV

(cd "$ROOT/web" && npm ci --no-audit --no-fund && npm run build)

ZIP=$(mktemp -d)/web.zip
(cd "$ROOT/web/dist" && zip -qr "$ZIP" .)
read -r JOB URL < <(aws amplify create-deployment --region "$REGION" --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query '[jobId, zipUploadUrl]' --output text)
curl -sSf -X PUT -H 'Content-Type: application/zip' --upload-file "$ZIP" "$URL"
aws amplify start-deployment --region "$REGION" --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB" >/dev/null

echo "deploying job $JOB ..."
while :; do
  STATUS=$(aws amplify get-job --region "$REGION" --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB" \
    --query 'job.summary.status' --output text)
  case "$STATUS" in
    SUCCEED) break ;;
    FAILED|CANCELLED) echo "deployment $STATUS" >&2; exit 1 ;;
  esac
  sleep 5
done
echo "live: $(out WebUrl)"

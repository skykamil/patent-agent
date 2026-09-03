#!/usr/bin/env bash
set -euo pipefail
AWS_REGION="eu-central-1"
OPENAI_PARAM="/patent-agent/openai-api-key"
EPO_KEY_PARAM="/patent-agent/epo-consumer-key"
EPO_SECRET_PARAM="/patent-agent/epo-consumer-secret"
DATABASE_PATH="/data/logs_db.db"
IMAGE_NAME="patent-agent"
CONTAINER_NAME="patent-agent-api"
get_ssm_parameter() {
local parameter_name="$1"
aws ssm get-parameter \
--name "$parameter_name" \
--with-decryption \
--region "$AWS_REGION" \
--query 'Parameter.Value' \
--output text
}
export OPENAI_API_KEY="$(get_ssm_parameter "$OPENAI_PARAM")"
export EPO_CONSUMER_KEY="$(get_ssm_parameter "$EPO_KEY_PARAM")"
export EPO_CONSUMER_SECRET="$(get_ssm_parameter "$EPO_SECRET_PARAM")"
cd "$(dirname "$0")/.."
git pull --ff-only
sudo docker build -t "$IMAGE_NAME" .
if sudo docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
sudo docker rm -f "$CONTAINER_NAME"
fi
sudo --preserve-env=OPENAI_API_KEY,EPO_CONSUMER_KEY,EPO_CONSUMER_SECRET docker run -d \
--name "$CONTAINER_NAME" \
--restart unless-stopped \
-p 127.0.0.1:8000:8000 \
-v /data:/data \
-e DATABASE_PATH="$DATABASE_PATH" \
-e OPENAI_API_KEY \
-e EPO_CONSUMER_KEY \
-e EPO_CONSUMER_SECRET \
"$IMAGE_NAME"

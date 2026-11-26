#!/usr/bin/env bash
set -euo pipefail

readonly IMAGE="westcott/fact-validator:local"
draft=""
research=""
args=()

while (($#)); do
  case "$1" in
    --draft|--research)
      if (($# < 2)); then
        echo "fact-validator-docker: missing value for $1" >&2
        exit 64
      fi
      option="$1"
      value="$2"
      shift 2
      case "$option" in
        --draft) draft="$value"; args+=(--draft /input/draft.md) ;;
        --research) research="$value"; args+=(--research /input/research.json) ;;
      esac
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$draft" || -z "$research" ]]; then
  echo "fact-validator-docker: --draft and --research are required" >&2
  exit 64
fi

draft="$(realpath -e -- "$draft")"
research="$(realpath -e -- "$research")"
run_dir="$(dirname -- "$draft")"
if [[ "$(basename -- "$draft")" != "draft.md" \
   || "$(basename -- "$research")" != "research.json" \
   || "$(dirname -- "$research")" != "$run_dir" ]]; then
  echo "fact-validator-docker: inputs must be draft.md and research.json in one run directory" >&2
  exit 64
fi
if [[ "$run_dir" == *,* ]]; then
  echo "fact-validator-docker: run directory path cannot contain a comma" >&2
  exit 64
fi

if [[ -z "${WESTCOTT_TAVILY_API_KEY:-}" || -z "${WESTCOTT_FACT_CHECK_OPENAI_API_KEY:-}" ]]; then
  echo "fact-validator-docker: dedicated Tavily and OpenAI credentials are required" >&2
  exit 2
fi

# Runner intentionally does not inherit arbitrary DOCKER_HOST/DOCKER_CONTEXT.
# Rootless Docker's standard per-user socket is selected explicitly instead.
export DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
security_options="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null || true)"
if [[ "$security_options" != *rootless* ]]; then
  echo "fact-validator-docker: a rootless Docker daemon is required" >&2
  exit 2
fi

# Search results are never fetched directly by the validator. Network is needed
# only for the fixed Tavily/OpenAI HTTPS APIs. Apply host/proxy egress allowlists
# if the deployment requires network-layer destination restriction.
exec docker run --rm --pull=never \
  --network bridge \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 64 \
  --memory 512m \
  --cpus 1.0 \
  --ulimit nofile=256:256 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777 \
  --mount "type=bind,src=${run_dir},dst=/input,readonly" \
  --user 0:0 \
  --env WESTCOTT_TAVILY_API_KEY \
  --env WESTCOTT_FACT_CHECK_OPENAI_API_KEY \
  "$IMAGE" \
  "${args[@]}"

#!/usr/bin/env bash
# Prepare a local LM Studio for pkpipe's live tests and evals (tests/README.md, docs/evaluation.md).
#
# Keeps the model under test and the judge model loaded at the same time, with no idle TTL and with parallel
# predictions, so live tests and `pkpipe eval --concurrency 4` never wait for a model swap ("Model unloaded"
# HTTP 400 under contention) and LM Studio serves several requests at once.
#
#   scripts/lmstudio-test-setup.sh            # load / reload the two models as needed, then show `lms ps`
#   scripts/lmstudio-test-setup.sh --check    # only report; exit 1 when something is not as wanted
#   scripts/lmstudio-test-setup.sh --force    # also reload a model that is busy generating right now
#
# Environment: PKPIPE_MODEL_NAME (default qwen3.5-9b-mlx), PKPIPE_JUDGE_MODEL (default google/gemma-4-12b),
# PKPIPE_LMSTUDIO_PARALLEL (default 4), PKPIPE_LMSTUDIO_CONTEXT (context length; default: LM Studio's),
# LMS (path of the lms CLI; default: lms on PATH, else ~/.lmstudio/bin/lms).
#
# Only these two models are touched: other loaded models stay loaded, nothing is downloaded or deleted, and no
# other LM Studio setting changes. A model is reloaded only when it is loaded with a TTL or with fewer
# parallel predictions than wanted (a reload drops requests in flight: run this when no test is running).
set -euo pipefail

MODELS=("${PKPIPE_MODEL_NAME:-qwen3.5-9b-mlx}" "${PKPIPE_JUDGE_MODEL:-google/gemma-4-12b}")
PARALLEL="${PKPIPE_LMSTUDIO_PARALLEL:-4}"
CONTEXT="${PKPIPE_LMSTUDIO_CONTEXT:-}"
CHECK=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --force) FORCE=1 ;;
    -h | --help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

LMS="${LMS:-$(command -v lms || true)}"
[[ -z "$LMS" && -x "$HOME/.lmstudio/bin/lms" ]] && LMS="$HOME/.lmstudio/bin/lms"
if [[ -z "$LMS" ]]; then
  echo "lms CLI not found: install LM Studio and run it once (it installs ~/.lmstudio/bin/lms)" >&2
  exit 1
fi

if ! "$LMS" server status 2>&1 | grep -q "running"; then
  if ((CHECK)); then
    echo "LM Studio server is not running" >&2
    exit 1
  fi
  "$LMS" server start
fi

# state <model>: "absent", or "<status> <ttl_ms|none> <parallel>" of the instance whose identifier is <model>
state() {
  "$LMS" ps --json | python3 -c '
import json, sys
want = sys.argv[1]
for m in json.load(sys.stdin):
    if m.get("identifier") == want:
        ttl = m.get("ttlMs")
        print(m.get("status", "?"), "none" if ttl is None else ttl, m.get("parallel") or 1)
        break
else:
    print("absent")
' "$1"
}

load() {
  local args=("$1" --identifier "$1" --parallel "$PARALLEL" --yes)  # no --ttl: stays loaded
  [[ -n "$CONTEXT" ]] && args+=(--context-length "$CONTEXT")
  "$LMS" load "${args[@]}"
}

problems=0
for model in "${MODELS[@]}"; do
  read -r status ttl parallel <<<"$(state "$model")"
  if [[ "$status" == "absent" ]]; then
    echo "$model: not loaded"
    if ((CHECK)); then problems=$((problems + 1)); else load "$model"; fi
    continue
  fi
  if [[ "$ttl" == "none" && "$parallel" -ge "$PARALLEL" ]]; then
    echo "$model: ok (no TTL, parallel $parallel)"
    continue
  fi
  echo "$model: loaded with TTL ${ttl} ms / parallel ${parallel}; wanted no TTL / parallel ${PARALLEL}"
  if ((CHECK)); then
    problems=$((problems + 1))
    continue
  fi
  if [[ "$status" != "idle" && "$FORCE" -eq 0 ]]; then
    echo "$model is $status (a test or eval is using it): rerun when idle, or with --force" >&2
    problems=$((problems + 1))
    continue
  fi
  "$LMS" unload "$model"
  load "$model"
done

"$LMS" ps
exit $((problems > 0))

#!/usr/bin/env bash
# Prepare a local LM Studio for pkpipe's live tests and evals (tests/README.md, docs/evaluation.md).
#
# Keeps the model under test and the judge model loaded at the same time, with no idle TTL and with parallel
# predictions, so live tests and `pkpipe eval --concurrency 4` never wait for a model swap ("Model unloaded"
# HTTP 400 under contention) and LM Studio serves several requests at once. Both get a bounded context
# (32768 tokens, the most a models.json profile lets a prompt use) instead of the models' 262144-token
# default, which caps the KV caches of two models x 4 parallel slots on a 32 GB machine that already runs
# close to full swap (a stalled LM Studio times out judge requests: unjudged eval cases).
#
#   scripts/lmstudio-test-setup.sh            # load / reload the two models as needed, then show `lms ps`
#   scripts/lmstudio-test-setup.sh --check    # only report; exit 1 when something is not as wanted
#   scripts/lmstudio-test-setup.sh --force    # also reload a model that is busy generating right now
#
# Environment: PKPIPE_MODEL_NAME (default qwen3.5-9b-mlx), PKPIPE_JUDGE_MODEL (default google/gemma-4-12b),
# PKPIPE_LMSTUDIO_PARALLEL (default 4), PKPIPE_LMSTUDIO_CONTEXT (context length; default 32768; empty:
# the model's own default), LMS (path of the lms CLI; default: lms on PATH, else ~/.lmstudio/bin/lms).
#
# Headless (recommended on a busy machine): quit the LM Studio app (its window, renderer and GPU helper cost
# memory the models need) and let the llmster daemon serve instead. This script starts it when nothing serves:
# `lms daemon up` (the daemon), then `lms server start --bind 127.0.0.1` (the OpenAI-compatible server on
# localhost only, port 1234 unless another was used last); it warns while the app window is open. Stop with
# `lms server stop` and `lms daemon down` (docs/installation.md).
#
# LM Link: a model served by another device (the judge on a second machine, `lms link status`) is loaded and
# configured on that device. The script never loads, unloads or reloads it; it only reports it, warns when it
# runs fewer parallel predictions than wanted (lower `pkpipe eval --concurrency` to match) and fails --check only
# when it is not loaded at all.
#
# Only these two models are touched: other loaded models stay loaded, nothing is downloaded or deleted, and no
# other LM Studio setting changes. A model is reloaded only when it is loaded with a TTL, with fewer
# parallel predictions or with another context length than wanted (a reload drops requests in flight: run
# this when no test is running).
set -euo pipefail

MODELS=("${PKPIPE_MODEL_NAME:-qwen3.5-9b-mlx}" "${PKPIPE_JUDGE_MODEL:-google/gemma-4-12b}")
PARALLEL="${PKPIPE_LMSTUDIO_PARALLEL:-4}"
CONTEXT="${PKPIPE_LMSTUDIO_CONTEXT-32768}"
CHECK=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --force) FORCE=1 ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

LMS="${LMS:-$(command -v lms || true)}"
[[ -z "$LMS" && -x "$HOME/.lmstudio/bin/lms" ]] && LMS="$HOME/.lmstudio/bin/lms"
if [[ -z "$LMS" ]]; then
  echo "lms CLI not found: install LM Studio and run it once (it installs ~/.lmstudio/bin/lms)" >&2
  exit 1
fi

# The LM Studio app with its window (not the headless daemon, which runs the same binary --run-as-service)
if ps -axo command= | grep -E '/LM Studio\.app/Contents/MacOS/LM Studio( |$)' | grep -qv -- '--run-as-service'; then
  echo "note: the LM Studio app is open; for headless mode quit it and rerun (lms daemon up)" >&2
fi

has_daemon() { "$LMS" daemon --help >/dev/null 2>&1; }  # lms 0.4+: the llmster daemon

if ! "$LMS" server status 2>&1 | grep -q "server is running"; then  # not "... is not running"
  if ((CHECK)); then
    echo "LM Studio server is not running (start it: lms daemon up && lms server start --bind 127.0.0.1)" >&2
    exit 1
  fi
  if has_daemon && ! "$LMS" daemon status 2>&1 | grep -q "is running"; then
    "$LMS" daemon up
  fi
  "$LMS" server start --bind 127.0.0.1
fi

# state <model>: "absent", or "<status> <ttl_ms|none> <parallel> <context> <device|local>" of the instance
# <model> (device: the LM Link device identifier of a model served by another machine)
state() {
  "$LMS" ps --json | python3 -c '
import json, sys
want = sys.argv[1]
for m in json.load(sys.stdin):
    if m.get("identifier") == want:
        ttl = m.get("ttlMs")
        print(m.get("status", "?"), "none" if ttl is None else ttl, m.get("parallel") or 1,
              m.get("contextLength") or 0, m.get("deviceIdentifier") or "local")
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
  read -r status ttl parallel context device <<<"$(state "$model")"
  if [[ "$status" != "absent" && "$device" != "local" ]]; then
    echo "$model: served by LM Link device $device (parallel $parallel, context $context; managed there)"
    if [[ "$parallel" -lt "$PARALLEL" ]]; then
      echo "note: $model runs $parallel parallel prediction(s) on its device; use pkpipe eval" \
        "--concurrency $parallel or reload it there with --parallel $PARALLEL" >&2
    fi
    continue
  fi
  if [[ "$status" == "absent" ]]; then
    echo "$model: not loaded"
    if ((CHECK)); then problems=$((problems + 1)); else load "$model"; fi
    continue
  fi
  if [[ "$ttl" == "none" && "$parallel" -ge "$PARALLEL" && (-z "$CONTEXT" || "$context" == "$CONTEXT") ]]; then
    echo "$model: ok (no TTL, parallel $parallel, context $context)"
    continue
  fi
  echo "$model: loaded with TTL ${ttl} ms / parallel ${parallel} / context ${context};" \
    "wanted no TTL / parallel ${PARALLEL} / context ${CONTEXT:-any}"
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

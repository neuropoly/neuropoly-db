#!/usr/bin/env bash
set -euo pipefail

# Production smoke test for gateway subpath auth flow.
# Defaults can be overridden from the environment:
#   NB_GATEWAY_PUBLIC_ORIGIN=https://data.neuro.polymtl.ca
#   NB_GATEWAY_BASE_PATH=/bagel
#   NB_GATEWAY_INSECURE_TLS=true

ORIGIN="${NB_GATEWAY_PUBLIC_ORIGIN:-https://localhost:9010}"
BASE_PATH="${NB_GATEWAY_BASE_PATH:-/bagel}"
INSECURE_TLS="${NB_GATEWAY_INSECURE_TLS:-true}"
TIMEOUT="${NB_GATEWAY_SMOKE_TIMEOUT:-20}"

if [[ "${BASE_PATH}" != /* ]]; then
	BASE_PATH="/${BASE_PATH}"
fi

if [[ "${BASE_PATH}" != "/" ]]; then
	BASE_PATH="${BASE_PATH%/}"
fi

if ! command -v curl >/dev/null 2>&1; then
	echo "FAIL: curl is required"
	exit 1
fi

CURL_OPTS=(--silent --show-error --connect-timeout "${TIMEOUT}" --max-time "${TIMEOUT}")
if [[ "${INSECURE_TLS}" == "true" ]]; then
	CURL_OPTS+=(--insecure)
fi

PASS=0
FAIL=0

request_headers() {
	local url="$1"
	curl "${CURL_OPTS[@]}" -D - -o /dev/null "$url"
}

status_code() {
	awk 'toupper($1) ~ /^HTTP\// { code=$2 } END { print code }'
}

location_header() {
	awk 'BEGIN{IGNORECASE=1} /^Location:/ { sub(/^Location:[[:space:]]*/, "", $0); gsub(/\r$/, "", $0); print; exit }'
}

record_pass() {
	echo "PASS: $1"
	PASS=$((PASS + 1))
}

record_fail() {
	echo "FAIL: $1"
	FAIL=$((FAIL + 1))
}

echo "Gateway smoke test"
echo "  origin: ${ORIGIN}"
echo "  base_path: ${BASE_PATH}"
echo

# 1) Canonical base path redirect: /bagel -> /bagel/
base_no_slash_headers="$(request_headers "${ORIGIN}${BASE_PATH}")"
base_no_slash_status="$(printf '%s\n' "${base_no_slash_headers}" | status_code)"
base_no_slash_location="$(printf '%s\n' "${base_no_slash_headers}" | location_header)"

if [[ "${base_no_slash_status}" == "302" && "${base_no_slash_location}" == "${BASE_PATH}/" ]]; then
	record_pass "${BASE_PATH} redirects to ${BASE_PATH}/"
else
	record_fail "${BASE_PATH} should return 302 to ${BASE_PATH}/ (got status=${base_no_slash_status}, location=${base_no_slash_location:-<none>})"
fi

# 2) UI entrypoint should either be authenticated content (200) or redirect to prefixed oauth2 sign-in.
ui_headers="$(request_headers "${ORIGIN}${BASE_PATH}/")"
ui_status="$(printf '%s\n' "${ui_headers}" | status_code)"
ui_location="$(printf '%s\n' "${ui_headers}" | location_header)"

if [[ "${ui_status}" == "200" ]]; then
	record_pass "${BASE_PATH}/ returns 200 (already authenticated)"
elif [[ "${ui_status}" == "302" && "${ui_location}" == "${BASE_PATH}/oauth2/sign_in"* ]]; then
	record_pass "${BASE_PATH}/ redirects to prefixed oauth2 sign-in"
else
	record_fail "${BASE_PATH}/ should return 200 or 302 to ${BASE_PATH}/oauth2/sign_in (got status=${ui_status}, location=${ui_location:-<none>})"
fi

# 3) OAuth callback endpoint must exist under the same base path and not fall through to another root app.
callback_headers="$(request_headers "${ORIGIN}${BASE_PATH}/oauth2/callback")"
callback_status="$(printf '%s\n' "${callback_headers}" | status_code)"

if [[ "${callback_status}" == "302" || "${callback_status}" == "400" || "${callback_status}" == "401" ]]; then
	record_pass "${BASE_PATH}/oauth2/callback is handled by oauth2-proxy (status ${callback_status})"
else
	record_fail "${BASE_PATH}/oauth2/callback should be handled (expected 302/400/401, got ${callback_status})"
fi

# 4) API path should stay under base path; unauthenticated clients should get 401.
api_headers="$(request_headers "${ORIGIN}${BASE_PATH}/query")"
api_status="$(printf '%s\n' "${api_headers}" | status_code)"

if [[ "${api_status}" == "401" || "${api_status}" == "200" || "${api_status}" == "405" ]]; then
	record_pass "${BASE_PATH}/query is routed through gateway auth path (status ${api_status})"
else
	record_fail "${BASE_PATH}/query returned unexpected status ${api_status}"
fi

echo
echo "Summary: PASS=${PASS} FAIL=${FAIL}"

if [[ "${FAIL}" -gt 0 ]]; then
	exit 1
fi

#!/usr/bin/env bash
# shellcheck disable=SC2016  # literal backticks/${key} in single quotes are intended
# Benchmark every class of network call npdb makes to NeuroData
# (REST API, HTTPS git, SSH git-annex) and report which ones are lagging.
#
# Usage: scripts/benchmark_neurodata.sh [options]   (see -h)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

RUNS=10
TIMEOUT=30
DATASETS_CSV=""
DATASETS_FILE=""
OUTDIR=""
PROBE_GROUPS="net,ssh,api,git,annex,npdb"
KEEP=0
VERBOSE=0
SSL_VERIFY=true
ANNEX_FILE=""
DRY_RUN=0
AGGREGATE_ONLY=""
ORG="datasets"

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Times NeuroData calls used by npdb. Each run picks a random dataset from the
list to avoid server/proxy caching. Every call is bounded by a timeout and is
never retried, so the numbers show raw latency.

Every failure is classified (AUTH, TLS, NOT_FOUND, SERVER_ERROR, DNS, CONNECT,
TIMEOUT, OTHER) and printed with a hint and the tail of its log. AUTH and TLS
failures are fatal: the benchmark stops at once (no repeated calls), writes a
partial report and exits with code 3 (AUTH) or 4 (TLS).

Options:
  -n RUNS            Repetitions per call (default: $RUNS)
  -t SECONDS         Timeout per call (default: $TIMEOUT)
  -d "ds1,ds2,..."   Comma-separated dataset repos under '$ORG/' (default: whole-spine)
  -D FILE            File with one dataset per line ('#' comments allowed)
  -o DIR             Output directory (default: ./tmp/gitea-bench-<timestamp>)
  -g GROUPS          Comma-separated groups: net,ssh,api,git,annex,npdb (default: all)
  -k                 Keep per-run clones (default: deleted)
  -v                 Verbose: print each command (redacted), HTTP codes and
                     20 log lines per failure. Repeat (-vv) to also trace
                     curl/git/ssh traffic and print full logs of every call.
  --no-verify-ssl    Disable TLS verification, like 'npdb --no-verify-ssl' (default: verify)
  --annex-file GLOB  Annexed file (glob) to fetch in annex_get_small
                     (default: first annexed file < 1MiB)
  --dry-run          Print the commands that would run (secrets redacted); no network
  --aggregate-only F Rebuild summary/report from an existing samples.tsv; no network
  -h, --help         Show this help

Credentials come from the environment or ./.env / <repo>/.env:
  NP_GITEA_APP_URL, NP_GITEA_APP_USER, NP_GITEA_APP_TOKEN

Outputs (in DIR): samples.tsv, summary.tsv, report.md, logs/

Exit codes: 0 done, 2 bad usage/config, 3 authentication failure, 4 TLS failure.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n) RUNS=$2; shift 2 ;;
        -t) TIMEOUT=$2; shift 2 ;;
        -d) DATASETS_CSV=$2; shift 2 ;;
        -D) DATASETS_FILE=$2; shift 2 ;;
        -o) OUTDIR=$2; shift 2 ;;
        -g) PROBE_GROUPS=$2; shift 2 ;;
        -k) KEEP=1; shift ;;
        -v) VERBOSE=$((VERBOSE + 1)); shift ;;
        -vv) VERBOSE=$((VERBOSE + 2)); shift ;;
        --no-verify-ssl|--no-ssl-verify) SSL_VERIFY=false; shift ;;
        --annex-file) ANNEX_FILE=$2; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --aggregate-only) AGGREGATE_ONLY=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo "-n must be a positive integer" >&2; exit 2; }
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "-t must be a positive integer" >&2; exit 2; }

OUTDIR=${OUTDIR:-./tmp/gitea-bench-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUTDIR/logs"
SAMPLES="$OUTDIR/samples.tsv"

# Probe -> npdb call site, used in the report.
declare -A SITE=(
    [dns]="network baseline"
    [https_dns]="network baseline (curl time_namelookup)"
    [https_connect]="network baseline (TCP connect)"
    [https_tls]="network baseline (TLS handshake, every API/git HTTPS call)"
    [https_ttfb]="network baseline (time to first byte on /)"
    [https_total]="network baseline (GET /)"
    [ssh_handshake]="SSH transport used by git-annex (annex_get)"
    [api_version]="REST baseline: GET /api/v1/version"
    [api_org]="OrganizationMixin._fetch_organization (every DataNeuroPolyMTL init)"
    [api_repos_page1]="OrganizationMixin._fetch_repositories (one page)"
    [api_repos_all]="OrganizationMixin.datasets (all pages, re-fetched on every access)"
    [api_repo]="GET /api/v1/repos/$ORG/<ds>"
    [ls_remote_annex]="cli.helpers.repo_has_git_annex (per repo in 'download')"
    [ls_remote_head]="cheap alternative to GitProvider.get_main_branch_head_commit"
    [clone_shallow_blobless]="GitProvider.clone_sparse / DataNeuroPolyMTL.clone_repository(light)"
    [sparse_checkout]="GitProvider.clone_sparse (sparse-checkout + lazy blob fetch)"
    [fetch_annex_branch]="GiteaProvider.annex_get step 1 (runs twice per repo)"
    [annex_init]="GiteaProvider.annex_get step 3"
    [annex_merge]="GiteaProvider.annex_get step 5 (local control)"
    [annex_get_small]="GiteaProvider.annex_get step 6 (git-annex-shell over SSH)"
    [npdb_init]="GiteaProviderFactory.create_from_env (py-gitea org + client)"
    [npdb_datasets]="DataNeuroPolyMTL.datasets (py-gitea repo listing)"
)

# --------------------------------------------------------------------------
# Aggregation / reporting (no network)
# --------------------------------------------------------------------------
aggregate() {
    local samples=$1 outdir=$2
    local summary="$outdir/summary.tsv" report="$outdir/report.md"

    # Timeouts contribute their recorded lower-bound duration to the statistics.
    {
        printf 'probe\tn_ok\tn_fail\tn_timeout\tn_skipped\tmin\tmedian\tp90\tmax\tmean\tfail_reasons\n'
        tail -n +2 "$samples" | sort -t $'\t' -k2,2 -k5,5g | awk -F'\t' '
            function reasons(   r, s) {
                s = ""
                for (r in rc) s = s (s == "" ? "" : ",") r ":" rc[r]
                return s == "" ? "-" : s
            }
            function flush(   med, p90, i, mean) {
                if (probe == "") return
                if (n > 0) {
                    med = (n % 2) ? v[(n + 1) / 2] : (v[n / 2] + v[n / 2 + 1]) / 2
                    i = int(0.9 * n + 0.999999); if (i < 1) i = 1; if (i > n) i = n
                    p90 = v[i]; mean = sum / n
                    printf "%s\t%d\t%d\t%d\t%d\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%s\n", probe, nok, nf, nt, ns, v[1], med, p90, v[n], mean, reasons()
                } else {
                    printf "%s\t0\t%d\t%d\t%d\t-\t-\t-\t-\t-\t%s\n", probe, nf, nt, ns, reasons()
                }
            }
            {
                if ($2 != probe) { flush(); probe = $2; n = 0; nok = 0; nf = 0; nt = 0; ns = 0; sum = 0; delete v; delete rc }
                if ($4 == "OK" || $4 == "TIMEOUT") { v[++n] = $5; sum += $5 }
                if ($4 == "OK") { nok++; next }
                if ($4 == "SKIPPED") { ns++; next }
                if ($4 == "TIMEOUT") nt++; else nf++
                r = ($4 == "TIMEOUT") ? "TIMEOUT" : "OTHER"
                if (match($6, /reason=[A-Z_]+/)) r = substr($6, RSTART + 7, RLENGTH - 7)
                rc[r]++
            }
            END { flush() }' | sort -t $'\t' -k7,7gr
    } > "$summary"

    local site_file="$outdir/.sites.tsv"
    : > "$site_file"
    for p in "${!SITE[@]}"; do printf '%s\t%s\n' "$p" "${SITE[$p]}" >> "$site_file"; done

    {
        echo "# NeuroData benchmark report"
        echo
        if [[ -f "$outdir/abort.txt" ]]; then
            echo '## Aborted'
            echo
            cat "$outdir/abort.txt"
            echo
        fi
        if [[ -f "$outdir/env.txt" ]]; then
            echo '## Environment'
            echo
            sed 's/^/- /' "$outdir/env.txt"
            echo
        fi
        echo '## Summary (seconds, OK and TIMEOUT samples, sorted by median)'
        echo
        echo '| Probe | OK | Fail | Timeout | Skipped | Min | Median | p90 | Max | Mean | Failure reasons | npdb call site |'
        echo '|---|---|---|---|---|---|---|---|---|---|---|---|'
        awk -F'\t' 'NR == FNR { site[$1] = $2; next }
            FNR > 1 { printf "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n",
                      $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, site[$1] }' "$site_file" "$summary"
        echo
        echo '## Top offenders'
        echo
        awk -F'\t' 'FNR > 1 && $7 != "-" && n < 5 { printf "%d. `%s` — median %ss, p90 %ss\n", ++n, $1, $7, $8 }' "$summary"
        echo
        echo '## Failures'
        echo
        echo '| Probe | Reason | Count | First log |'
        echo '|---|---|---|---|'
        tail -n +2 "$samples" | awk -F'\t' '
            $4 == "FAIL" || $4 == "TIMEOUT" {
                r = ($4 == "TIMEOUT") ? "TIMEOUT" : "OTHER"
                if (match($6, /reason=[A-Z_]+/)) r = substr($6, RSTART + 7, RLENGTH - 7)
                k = $2 " | " r
                if (!(k in c)) { order[++m] = k; lg = "-"; if (match($6, /log=[^ ]+/)) lg = "logs/" substr($6, RSTART + 4, RLENGTH - 4); first[k] = lg }
                c[k]++
            }
            END {
                if (m == 0) print "| - | none | 0 | - |"
                for (i = 1; i <= m; i++) printf "| %s | %d | %s |\n", order[i], c[order[i]], first[order[i]]
            }'
        echo
        echo '## Median by dataset (seconds)'
        echo
        echo '| Probe | Dataset | OK | Timeout | Median |'
        echo '|---|---|---|---|---|'
        tail -n +2 "$samples" | awk -F'\t' '($4 == "OK" || $4 == "TIMEOUT") && $3 != "-"' | sort -t $'\t' -k2,2 -k3,3 -k5,5g | awk -F'\t' '
            function flush(   med) {
                if (key == "") return
                med = (n % 2) ? v[(n + 1) / 2] : (v[n / 2] + v[n / 2 + 1]) / 2
                printf "| %s | %s | %d | %d | %.3f |\n", p, d, nok, nt, med
            }
            { k = $2 SUBSEP $3; if (k != key) { flush(); key = k; p = $2; d = $3; n = 0; nok = 0; nt = 0; delete v } v[++n] = $5; if ($4 == "OK") nok++; else nt++ }
            END { flush() }'
        echo
        echo 'Notes:'
        echo '- TIMEOUT samples use the configured timeout as a lower-bound duration, not the actual completion time. Summary and dataset statistics include these values; other failures and skipped calls are excluded.'
        echo '- npdb wraps every git call in a 3x tenacity retry with 1-10 s backoff (and a 3600 s timeout), so a slow or flaky call here costs much more in real npdb runs.'
        echo '- `annex_get` runs twice per repo in `download` (raw + derivatives), so `fetch_annex_branch` and `annex_init` count twice.'
        echo '- `gitea2bagel` calls `get_main_branch_head_commit`, which does a whole extra sparse clone; compare `clone_shallow_blobless + sparse_checkout` with `ls_remote_head`.'
        echo
        echo 'Raw samples: `samples.tsv`. Per-call stderr: `logs/`.'
    } > "$report"
    rm -f "$site_file"

    echo
    column -t -s $'\t' "$summary"
    echo
    echo "Report:  $report"
    echo "Summary: $summary"
    echo "Samples: $samples"
}

if [[ -n "$AGGREGATE_ONLY" ]]; then
    [[ -f "$AGGREGATE_ONLY" ]] || { echo "No such samples file: $AGGREGATE_ONLY" >&2; exit 2; }
    if [[ "$(realpath "$AGGREGATE_ONLY")" != "$(realpath -m "$SAMPLES")" ]]; then
        cp "$AGGREGATE_ONLY" "$SAMPLES"
    fi
    aggregate "$SAMPLES" "$OUTDIR"
    exit 0
fi

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
load_env_file() {
    local f=$1
    [[ -f "$f" ]] || return 0
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(NP_GITEA_APP_[A-Z_]+)=(.*)$ ]] || continue
        key=${BASH_REMATCH[2]}
        val=${BASH_REMATCH[3]}
        val=${val%\"}; val=${val#\"}; val=${val%\'}; val=${val#\'}
        [[ -z "${!key:-}" && -n "$val" ]] && export "$key=$val"
    done < "$f"
}
load_env_file "./.env"
load_env_file "$ROOT/.env"

if (( DRY_RUN )); then
    : "${NP_GITEA_APP_URL:=https://data.neuro.polymtl.ca}"
    : "${NP_GITEA_APP_USER:=<user>}"
    : "${NP_GITEA_APP_TOKEN:=<token>}"
fi
for v in NP_GITEA_APP_URL NP_GITEA_APP_USER NP_GITEA_APP_TOKEN; do
    [[ -n "${!v:-}" ]] || { echo "Missing $v (set it or add it to .env)" >&2; exit 2; }
done

URL=$NP_GITEA_APP_URL
[[ "$URL" == *"://"* ]] || URL="https://$URL"
PROTO=${URL%%://*}
HOST=${URL#*://}; HOST=${HOST%%/*}
BASE="$PROTO://$HOST"
SSH_HOST=${HOST%%:*}
USER_=$NP_GITEA_APP_USER
TOKEN=$NP_GITEA_APP_TOKEN
BASIC=$(printf '%s:%s' "$USER_" "$TOKEN" | base64 | tr -d '\n')

CURL_SSL=()
[[ "$SSL_VERIFY" == false ]] && CURL_SSL=(-k)
CURL_V=()
(( VERBOSE >= 2 )) && CURL_V=(-v)
SSH_V=""
(( VERBOSE >= 2 )) && SSH_V="-v"

VERIFY_ENV_IGNORED=0
case "${NP_GITEA_APP_VERIFY_SSL:-}" in
    0|false|False|FALSE|no|No) [[ "$SSL_VERIFY" == true ]] && VERIFY_ENV_IGNORED=1 ;;
esac

DATASETS=()
if [[ -n "$DATASETS_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=${line%%#*}; line=$(echo "$line" | xargs)
        [[ -n "$line" ]] && DATASETS+=("$line")
    done < "$DATASETS_FILE"
fi
if [[ -n "$DATASETS_CSV" ]]; then
    IFS=',' read -r -a _ds <<< "$DATASETS_CSV"
    for d in "${_ds[@]}"; do d=$(echo "$d" | xargs); [[ -n "$d" ]] && DATASETS+=("$d"); done
fi
(( ${#DATASETS[@]} )) || DATASETS=("whole-spine")

has_group() { [[ ",$PROBE_GROUPS," == *",$1,"* ]]; }

PYTHON=python3
[[ -x "$ROOT/.venv/bin/python" ]] && PYTHON="$ROOT/.venv/bin/python"

export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/echo SSH_ASKPASS=/bin/echo GCM_INTERACTIVE=never
export GIT_SSH_COMMAND="ssh ${SSH_V:+$SSH_V }-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=$TIMEOUT"
if (( VERBOSE >= 2 )); then
    # git redacts Authorization headers in traces by default (GIT_TRACE_REDACT=1).
    export GIT_TRACE=1 GIT_TRACE_CURL=1 GIT_TRACE_CURL_NO_DATA=1
fi
GIT=(git -c "http.extraHeader=Authorization: Basic $BASIC" -c "http.sslVerify=$SSL_VERIFY")
AUTH_HDR=(-H "Authorization: token $TOKEN")

WORK=$(mktemp -d "${TMPDIR:-/tmp}/gitea-bench.XXXXXX")
cleanup() { (( KEEP )) || rm -rf "$WORK"; }
trap cleanup EXIT

redact() {
    local args=()
    (( ${#TOKEN} >= 6 )) && args+=(-e "s|$TOKEN|<token>|g")
    args+=(-e "s|$BASIC|<basic-auth>|g")
    sed "${args[@]}"
}

# --------------------------------------------------------------------------
# Timing primitives, failure classification
# --------------------------------------------------------------------------
printf 'run\tprobe\tdataset\tstatus\tseconds\textra\n' > "$SAMPLES"

RUN=0
DS="-"
OK_RCS="0"      # exit codes considered success for the next probe
EXTRA=""        # optional extra column for the next probe
REASON=""
HINT=""

# Tiny curl wrapper so API probes log the HTTP status and error body.
API_HELPER="$WORK/api_get.sh"
cat > "$API_HELPER" <<'HELPER'
#!/usr/bin/env bash
# api_get.sh MODE URL [curl args...]
#   one  : single GET, logs http_code
#   show : single GET, also logs the start of the body
#   all  : follows &page=N until an empty JSON array, logs pages=N
set -u
mode=$1 url=$2; shift 2
body=$(mktemp); trap 'rm -f "$body"' EXIT
fetch() {
    local u=$1 rc=0; shift
    code=$(curl -sS -o "$body" -w '%{http_code}' "$@" "$u") || rc=$?
    echo "GET $u -> http_code=${code:-000} curl_rc=$rc bytes=$(wc -c < "$body")"
    (( rc == 0 )) || return "$rc"
    if (( 10#${code:-0} >= 400 )); then
        echo "body: $(head -c 800 "$body")"
        return 22
    fi
}
case "$mode" in
    one) fetch "$url" "$@" ;;
    show) fetch "$url" "$@" && echo "body: $(head -c 400 "$body")" ;;
    all)
        page=1
        while :; do
            fetch "$url&page=$page" "$@" || exit $?
            grep -Eq '^[[:space:]]*\[[[:space:]]*\][[:space:]]*$' "$body" && break
            page=$((page + 1))
            (( page > 1000 )) && { echo "more than 1000 pages, giving up"; exit 1; }
        done
        echo "pages=$((page - 1))"
        ;;
esac
HELPER
chmod +x "$API_HELPER"

record() {  # probe status seconds [extra]
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$RUN" "$1" "$DS" "$2" "$3" "${4:-}" >> "$SAMPLES"
}

now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.3f", b - a }'; }
log_path() { printf '%s/logs/%03d_%s.log' "$OUTDIR" "$RUN" "$1"; }
log_has() { grep -Eiq -- "$1" "$2" 2>/dev/null; }

TLS_RE='SSL certificate problem|server certificate verification failed|certificate verify failed|CERTIFICATE_VERIFY_FAILED|SSLError|unable to get local issuer|self[- ]signed certificate|certificate has expired|SSL_ERROR|gnutls_handshake|TLS connect error|wrong version number'
AUTH_RE='http_code=40[13]|HTTP/[0-9.]+ 40[13]|returned error: 40[13]|Received status code: 40[13]|Authentication failed|could not read (Username|Password)|terminal prompts disabled|Permission denied \(publickey|Host key verification failed|Cannot find key|invalid (username|credentials|token)|token is required|user does not exist|Forbidden|Unauthorized'
NOT_FOUND_RE='http_code=404|HTTP/[0-9.]+ 404|returned error: 404|Received status code: 404|NotFoundException|not found|does not appear to be a git repository'
SERVER_RE='http_code=5[0-9]{2}|HTTP/[0-9.]+ 5[0-9]{2}|returned error: 5[0-9]{2}|Received status code: 5[0-9]{2}|Bad Gateway|Gateway Time-?out|Service Unavailable|Internal Server Error'
DNS_RE='Could not resolve host|Name or service not known|Temporary failure in name resolution|NameResolutionError|getaddrinfo'
CONNECT_RE='Connection refused|Connection timed out|Connection reset|Connection closed|No route to host|Network is unreachable|Failed to connect|Operation timed out|ConnectTimeout|ConnectionError|Max retries exceeded|early EOF|RPC failed|unexpected disconnect'

# classify NAME RC LOG -> sets REASON and HINT.
classify() {
    local name=$1 rc=$2 log=$3
    REASON=OTHER
    HINT="unrecognised error, read the log"
    if (( rc == 124 || rc == 137 )); then
        REASON=TIMEOUT
        HINT="no answer within ${TIMEOUT}s (raise -t to measure the real latency)"
    elif (( rc == 127 )); then
        REASON=MISSING_TOOL
        HINT="a required command is not installed"
    elif log_has "$TLS_RE" "$log" || { [[ "$name" =~ ^(https_phases|api_|preflight) ]] && (( rc == 35 || rc == 51 || rc == 58 || rc == 60 )); }; then
        REASON=TLS
        if [[ "$SSL_VERIFY" == true ]]; then
            HINT="server certificate not trusted: re-run with --no-verify-ssl"
            (( VERIFY_ENV_IGNORED )) && HINT+=" (NP_GITEA_APP_VERIFY_SSL=false in .env is ignored, as in npdb)"
        else
            HINT="TLS handshake failed even without certificate verification (proxy/VPN issue?)"
        fi
    elif log_has "$AUTH_RE" "$log"; then
        REASON=AUTH
        if log_has 'Cannot find key' "$log"; then
            HINT="ssh offered a key that Gitea no longer knows (deleted from your account, server authorized_keys stale): pin the right key with IdentityFile + IdentitiesOnly yes for $SSH_HOST in ~/.ssh/config (check: ssh -vT git@$SSH_HOST)"
        elif log_has 'publickey|Host key verification failed' "$log"; then
            HINT="SSH key for git@$SSH_HOST rejected or host key changed: check ssh-agent and ~/.ssh/config (try: ssh -vT git@$SSH_HOST)"
        else
            HINT="credentials rejected (HTTP 401/403): check NP_GITEA_APP_USER / NP_GITEA_APP_TOKEN and that the token can read the '$ORG' org and its repositories"
        fi
    elif log_has "$NOT_FOUND_RE" "$log"; then
        REASON=NOT_FOUND
        HINT="check the dataset name '$DS'; Gitea also answers 404 for repositories you are not allowed to read"
    elif log_has "$SERVER_RE" "$log"; then
        REASON=SERVER_ERROR
        HINT="server-side error (overloaded server or reverse-proxy timeout)"
    elif [[ "$name" == dns ]] || log_has "$DNS_RE" "$log" || { [[ "$name" =~ ^(https_phases|api_|preflight) ]] && (( rc == 6 )); }; then
        REASON=DNS
        HINT="cannot resolve $SSH_HOST (VPN down?)"
    elif log_has "$CONNECT_RE" "$log" || { [[ "$name" =~ ^(https_phases|api_|preflight) ]] && (( rc == 7 )); }; then
        REASON=CONNECT
        HINT="connection to $SSH_HOST failed or was cut (VPN, firewall, overloaded server?)"
    fi
}

is_fatal() { [[ "$REASON" == AUTH || "$REASON" == TLS ]]; }

fail_details() {  # LOG
    local log=$1 n=5
    (( VERBOSE >= 1 )) && n=20
    printf '      -> %s: %s\n' "$REASON" "$HINT"
    if [[ -s "$log" ]]; then
        printf '      -> log: %s\n' "$log"
        if (( VERBOSE >= 2 )); then
            sed 's/^/         | /' "$log"
        else
            grep -v '^[[:space:]]*$' "$log" | tail -n "$n" | sed 's/^/         | /'
        fi
    else
        printf '      -> (no output)\n'
    fi
}

# fatal NAME LOG -> writes a partial report and exits; the failing call is not repeated.
fatal() {
    local name=$1 log=$2 code=3
    [[ "$REASON" == TLS ]] && code=4
    {
        echo "**$REASON failure** in \`$name\` (run $RUN, dataset \`$DS\`). The benchmark stopped so the failing call is not repeated."
        echo
        echo "- Hint: $HINT"
        echo "- Log: \`logs/$(basename "$log")\`"
        echo
        echo '```'
        grep -v '^[[:space:]]*$' "$log" 2>/dev/null | tail -n 20
        echo '```'
    } > "$OUTDIR/abort.txt"
    {
        echo
        echo "ERROR: $REASON failure in '$name' (run $RUN, dataset $DS). Aborting so the call is not repeated."
        echo "  Hint: $HINT"
        echo "  Log:  $log"
    } >&2
    aggregate "$SAMPLES" "$OUTDIR"
    exit "$code"
}

# finish NAME RC LOG SECONDS -> records the sample and prints details; returns 0 on success.
finish() {
    local name=$1 rc=$2 log=$3 secs=$4 status extra code pages
    code=$(grep -o 'http_code=[0-9]*' "$log" 2>/dev/null | tail -n 1 || true)
    pages=$(grep -o '^pages=[0-9]*' "$log" 2>/dev/null | tail -n 1 || true)
    extra="rc=$rc${code:+ $code}${pages:+ $pages}${EXTRA:+ $EXTRA}"
    if [[ " $OK_RCS " == *" $rc "* ]]; then status=OK
    elif (( rc == 124 || rc == 137 )); then status=TIMEOUT; secs=$TIMEOUT
    else status=FAIL; fi
    OK_RCS="0"; EXTRA=""

    if [[ "$status" == OK ]]; then
        record "$name" OK "$secs" "$extra"
        local shown=""
        (( VERBOSE >= 1 )) && shown="  [$extra]"
        printf '  run %-3s %-24s %-8s %8ss  %s%s\n' "$RUN" "$name" OK "$secs" "$DS" "$shown"
        if (( VERBOSE >= 2 )) && [[ -s "$log" ]]; then sed 's/^/         | /' "$log"; fi
        return 0
    fi

    classify "$name" "$rc" "$log"
    record "$name" "$status" "$secs" "$extra reason=$REASON log=$(basename "$log")"
    printf '  run %-3s %-24s %-8s %8ss  %s\n' "$RUN" "$name" "$status" "$secs" "$DS"
    fail_details "$log"
    is_fatal && fatal "$name" "$log"
    return 1
}

show_cmd() { (( VERBOSE >= 1 )) && printf '    $ %s\n' "$*" | redact; return 0; }

# probe NAME CMD... -> times CMD under `timeout`, records a sample, returns 0 on success.
probe() {
    local name=$1; shift
    local log
    log=$(log_path "$name")
    if (( DRY_RUN )); then
        printf '[dry-run] run=%s ds=%s %-24s %s\n' "$RUN" "$DS" "$name" "$*" | redact
        OK_RCS="0"; EXTRA=""
        return 0
    fi
    show_cmd "$@"
    local t0 t1 rc=0
    t0=$(now)
    timeout --kill-after=5 "$TIMEOUT" "$@" > "$log.tmp" 2>&1 || rc=$?
    t1=$(now)
    redact < "$log.tmp" > "$log"; rm -f "$log.tmp"
    finish "$name" "$rc" "$log" "$(elapsed "$t0" "$t1")"
}

skip() {  # WHY PROBE...
    local why=$1; shift
    (( DRY_RUN )) && return 0
    for p in "$@"; do
        record "$p" SKIPPED 0 "$why"
        printf '  run %-3s %-24s %-8s           (%s)\n' "$RUN" "$p" SKIPPED "$why"
    done
}

# --------------------------------------------------------------------------
# Probe groups
# --------------------------------------------------------------------------
API_OPTS=("${CURL_SSL[@]}" "${CURL_V[@]}" "${AUTH_HDR[@]}")

# One authenticated call before the runs: stops at once on bad TLS or credentials.
preflight() {
    local name=preflight log rc=0 t0 t1
    log=$(log_path "$name")
    if (( DRY_RUN )); then
        printf '[dry-run] preflight %s\n' "$API_HELPER show $BASE/api/v1/user ${API_OPTS[*]}" | redact
        return 0
    fi
    echo "Preflight: GET $BASE/api/v1/user (TLS + token check)"
    show_cmd "$API_HELPER" show "$BASE/api/v1/user" "${API_OPTS[@]}"
    t0=$(now)
    timeout --kill-after=5 "$TIMEOUT" "$API_HELPER" show "$BASE/api/v1/user" "${API_OPTS[@]}" > "$log.tmp" 2>&1 || rc=$?
    t1=$(now)
    redact < "$log.tmp" > "$log"; rm -f "$log.tmp"
    if (( rc == 0 )); then
        local login
        login=$(grep -oE '"login": *"[^"]*"' "$log" | head -n 1 | sed -E 's/.*: *"([^"]*)"/\1/' || true)
        echo "  OK  authenticated as '${login:-?}' in $(elapsed "$t0" "$t1")s"
        if [[ -n "$login" && "$login" != "$USER_" ]]; then
            echo "  note: token belongs to '$login' but NP_GITEA_APP_USER='$USER_' (git HTTPS basic auth sends both)"
        fi
        (( VERBOSE >= 2 )) && sed 's/^/         | /' "$log"
        return 0
    fi
    classify "$name" "$rc" "$log"
    echo "  FAIL preflight after $(elapsed "$t0" "$t1")s"
    fail_details "$log"
    is_fatal && fatal "$name" "$log"
    echo "  continuing: per-call results will show where it breaks"
}

probe_https_phases() {
    local name=https_phases log rc=0 out
    local cmd=(curl -sS -o /dev/null "${CURL_SSL[@]}" "${CURL_V[@]}"
        -w '%{time_namelookup} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total} %{http_code}'
        "$BASE/")
    if (( DRY_RUN )); then probe "$name" "${cmd[@]}"; return 0; fi
    log=$(log_path "$name")
    show_cmd "${cmd[@]}"
    out=$(timeout --kill-after=5 "$TIMEOUT" "${cmd[@]}" 2> "$log.tmp") || rc=$?
    redact < "$log.tmp" > "$log"; rm -f "$log.tmp"
    echo "write-out: $out" >> "$log"
    if (( rc == 0 )); then
        local t_dns t_con t_tls t_ttfb t_tot code
        read -r t_dns t_con t_tls t_ttfb t_tot code <<< "$out"
        record https_dns OK "$t_dns" "http=$code"
        record https_connect OK "$(elapsed "$t_dns" "$t_con")" "http=$code"
        record https_tls OK "$(awk -v a="$t_con" -v b="$t_tls" 'BEGIN{printf "%.3f", (b>0 ? b-a : 0)}')" "http=$code"
        record https_ttfb OK "$(awk -v a="$t_tls" -v b="$t_ttfb" -v c="$t_con" 'BEGIN{s=(a>0?a:c); printf "%.3f", b-s}')" "http=$code"
        record https_total OK "$t_tot" "http=$code"
        printf '  run %-3s %-24s %-8s %8.3fs  (dns %.3f, tls-done %.3f, ttfb %.3f, http %s)\n' "$RUN" https_total OK "$t_tot" "$t_dns" "$t_tls" "$t_ttfb" "$code"
        if (( VERBOSE >= 2 )); then sed 's/^/         | /' "$log"; fi
        return 0
    fi
    local st=FAIL secs=0
    if (( rc == 124 || rc == 137 )); then st=TIMEOUT; secs=$TIMEOUT; fi
    classify "$name" "$rc" "$log"
    for p in https_dns https_connect https_tls https_ttfb https_total; do
        record "$p" "$st" "$secs" "rc=$rc reason=$REASON log=$(basename "$log")"
    done
    printf '  run %-3s %-24s %-8s %8ss\n' "$RUN" https_total "$st" "$secs"
    fail_details "$log"
    is_fatal && fatal "$name" "$log"
    return 0
}

probe_net() {
    probe dns getent hosts "$SSH_HOST" || true
    probe_https_phases
}

probe_ssh() {
    # Gitea answers `ssh -T` with a greeting and exit code 0 or 1 when auth succeeds.
    OK_RCS="0 1"
    probe ssh_handshake ssh ${SSH_V:+"$SSH_V"} -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout="$TIMEOUT" "git@$SSH_HOST" || true
}

probe_api() {
    probe api_version "$API_HELPER" one "$BASE/api/v1/version" "${API_OPTS[@]}" || true
    probe api_org "$API_HELPER" one "$BASE/api/v1/orgs/$ORG" "${API_OPTS[@]}" || true
    probe api_repos_page1 "$API_HELPER" one "$BASE/api/v1/orgs/$ORG/repos?page=1&limit=50" "${API_OPTS[@]}" || true
    probe api_repos_all "$API_HELPER" all "$BASE/api/v1/orgs/$ORG/repos?limit=50" "${API_OPTS[@]}" || true
    probe api_repo "$API_HELPER" one "$BASE/api/v1/repos/$ORG/$DS" "${API_OPTS[@]}" || true
}

probe_git_annex() {
    local url="$BASE/$ORG/$DS.git"
    local dest="$WORK/run$RUN-$DS"

    if has_group git || has_group annex; then
        probe ls_remote_annex "${GIT[@]}" ls-remote --heads "$url" refs/heads/git-annex || true
        has_group git && { probe ls_remote_head "${GIT[@]}" ls-remote "$url" HEAD || true; }

        if ! probe clone_shallow_blobless "${GIT[@]}" clone --filter=blob:none --no-checkout --depth=1 "$url" "$dest"; then
            skip "clone failed" sparse_checkout fetch_annex_branch annex_init annex_merge annex_get_small
            return
        fi
        probe sparse_checkout bash -c '
            set -e; g=("${@:2}")
            "${g[@]}" -C "$1" sparse-checkout init --no-cone
            "${g[@]}" -C "$1" sparse-checkout set /dataset_description.json /participants.tsv
            "${g[@]}" -C "$1" checkout' _ "$dest" "${GIT[@]}" || true

        if ! probe fetch_annex_branch "${GIT[@]}" -C "$dest" fetch origin \
            refs/heads/git-annex:refs/remotes/origin/git-annex; then
            skip "git-annex branch fetch failed" annex_init annex_merge annex_get_small
            return
        fi
    fi

    has_group annex || return 0

    (( DRY_RUN )) || git -C "$dest" remote set-url origin "git@$SSH_HOST:$ORG/$DS.git"
    if ! probe annex_init git -C "$dest" annex init "gitea-bench"; then
        skip "annex init failed" annex_merge annex_get_small
        return
    fi
    (( DRY_RUN )) || git -C "$dest" config remote.origin.annex-ignore false
    if ! probe annex_merge git -C "$dest" annex merge; then
        skip "annex merge failed" annex_get_small
        return
    fi

    local key=""
    if (( DRY_RUN )); then
        key="<key>"
    else
        local sel=(--smallerthan=1MiB)
        [[ -n "$ANNEX_FILE" ]] && sel=(--include="$ANNEX_FILE")
        key=$(timeout "$TIMEOUT" git -C "$dest" annex find --branch=HEAD "${sel[@]}" \
            --format='${key}\n' 2>/dev/null | head -n 1) || true
    fi
    if [[ -z "$key" ]]; then
        skip "no annexed file matched" annex_get_small
        return
    fi
    EXTRA="key=$key"
    probe annex_get_small git -C "$dest" annex get --from=origin --key="$key" || true
}

NPDB_PY='
import os, time
from npdb.factories import GiteaProviderFactory
verify = os.environ.get("BENCH_SSL_VERIFY", "true") == "true"
t0 = time.perf_counter()
m = GiteaProviderFactory.create_from_env(ssl_verify=verify)
t1 = time.perf_counter()
n = len(m.datasets)
t2 = time.perf_counter()
print(f"BENCH {t1 - t0:.3f} {t2 - t1:.3f} {n}")
'

probe_npdb() {
    if (( DRY_RUN )); then
        printf '[dry-run] run=%s %-24s %s -c <GiteaProviderFactory.create_from_env(); m.datasets>\n' "$RUN" npdb_init "$PYTHON"
        return
    fi
    local name=npdb_init rc=0 log line t0 t1
    log=$(log_path "$name")
    show_cmd "$PYTHON -c <GiteaProviderFactory.create_from_env(); m.datasets>"
    t0=$(now)
    BENCH_SSL_VERIFY=$SSL_VERIFY timeout --kill-after=5 "$TIMEOUT" "$PYTHON" -c "$NPDB_PY" > "$log.tmp" 2>&1 || rc=$?
    t1=$(now)
    redact < "$log.tmp" > "$log"; rm -f "$log.tmp"
    line=$(grep -E '^BENCH [0-9.]+ [0-9.]+ [0-9]+$' "$log" | tail -n 1 || true)
    if (( rc == 0 )) && [[ -n "$line" ]]; then
        local _ t_init t_ds n
        read -r _ t_init t_ds n <<< "$line"
        record npdb_init OK "$t_init" "includes tenacity retries"
        record npdb_datasets OK "$t_ds" "repos=$n"
        printf '  run %-3s %-24s %-8s %8ss  (datasets re-fetch %ss, %s repos)\n' "$RUN" npdb_init OK "$t_init" "$t_ds" "$n"
        if (( VERBOSE >= 2 )); then sed 's/^/         | /' "$log"; fi
        return 0
    fi
    (( rc == 0 )) && rc=1
    finish "$name" "$rc" "$log" "$(elapsed "$t0" "$t1")" || true
    skip "npdb_init failed" npdb_datasets
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
{
    echo "date: $(date -Is)"
    echo "server: $BASE (ssh: git@$SSH_HOST)"
    echo "user: $USER_ (token: ${#TOKEN} chars)"
    echo "org: $ORG"
    echo "datasets: ${DATASETS[*]}"
    echo "runs: $RUNS, timeout: ${TIMEOUT}s, groups: $PROBE_GROUPS, ssl_verify: $SSL_VERIFY, verbosity: $VERBOSE"
    echo "client host: $(hostname)"
    echo "git: $(git --version 2>/dev/null || echo n/a)"
    echo "git-annex: $(git annex version --raw 2>/dev/null || echo n/a)"
    echo "curl: $(curl --version 2>/dev/null | head -n 1 || echo n/a)"
    echo "python: $($PYTHON --version 2>&1 || echo n/a)"
} > "$OUTDIR/env.txt"

echo "Benchmarking $BASE — $RUNS run(s), ${TIMEOUT}s timeout, datasets: ${DATASETS[*]}"
echo "  user: $USER_, token: ${#TOKEN} chars, ssl_verify: $SSL_VERIFY, groups: $PROBE_GROUPS, logs: $OUTDIR/logs"
if (( VERIFY_ENV_IGNORED )); then
    echo "  warning: NP_GITEA_APP_VERIFY_SSL=false is set but ignored (as in npdb); pass --no-verify-ssl to disable TLS verification" >&2
fi
(( DRY_RUN )) && echo "(dry run: no network calls)"

if has_group api || has_group git || has_group annex || has_group npdb; then
    preflight
fi

for ((RUN = 1; RUN <= RUNS; RUN++)); do
    DS=${DATASETS[RANDOM % ${#DATASETS[@]}]}
    echo "Run $RUN/$RUNS — dataset: $DS"
    has_group net && probe_net
    has_group ssh && probe_ssh
    has_group api && probe_api
    if has_group git || has_group annex; then probe_git_annex; fi
    has_group npdb && probe_npdb
    (( KEEP )) || rm -rf "$WORK/run$RUN-$DS"
done

if (( DRY_RUN )); then
    echo "Dry run complete; nothing recorded."
    exit 0
fi

aggregate "$SAMPLES" "$OUTDIR"

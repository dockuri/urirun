# urirun — Bash SDK helpers for building urirun.bindings.v2 documents.
# Connectors are often shell tools; this keeps them pure shell.
URIRUN_BINDINGS_VERSION="urirun.bindings.v2"

# urirun_command <scheme> <target> <route> <inputSchema-json> <argv-json> [connector]
# Echoes one "uri": {binding} JSON member (no surrounding braces, no trailing comma).
urirun_command() {
  local scheme="$1" target="$2" route="$3" schema="$4" argv="$5" conn="${6:-}"
  local uri="${scheme}://${target}/${route}"
  printf '"%s":{"uri":"%s","kind":"command","adapter":"argv-template","inputSchema":%s,"argv":%s,"meta":{"connector":"%s"},"policy":{"allowExecute":true}}' \
    "$uri" "$uri" "$schema" "$argv" "$conn"
}

# urirun_document <members-json>  -> the full bindings.v2 document
urirun_document() {
  printf '{"version":"%s","bindings":{%s}}\n' "$URIRUN_BINDINGS_VERSION" "$1"
}

# uri <full-uri> [json-or-jsonl]
# Universal shell helper to invoke a dockuri URI process with inline JSON/JSONL or stdin.
uri() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys, urirun; raise SystemExit(urirun.uri_cli(sys.argv[1:]))' "$@"
  else
    command uri "$@"
  fi
}


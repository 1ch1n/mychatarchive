#!/usr/bin/env bash
#
# Record a demo of the MyChatArchive Hermes plugin.
#
# Prerequisites:
#   - hermes-agent installed with the mychatarchive plugin
#   - mychatarchive package installed (pip install git+https://github.com/1ch1n/mychatarchive)
#   - A populated archive.db (mychatarchive sync && mychatarchive embed)
#   - HERMES_HOME set to your Hermes data directory
#
# Usage:
#   chmod +x demo/record-demo.sh
#   ./demo/record-demo.sh
#
# Output:
#   demo/demo-transcript.md (overwritten with fresh run)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="${SCRIPT_DIR}/demo-transcript.md"

echo "=== MyChatArchive Hermes Plugin Demo ==="
echo ""

# Check prerequisites
if ! command -v hermes &> /dev/null; then
    echo "ERROR: hermes not found in PATH"
    exit 1
fi

if ! python -c "import mychatarchive" 2>/dev/null; then
    echo "ERROR: mychatarchive package not installed"
    exit 1
fi

# Show plugin status
echo "--- Plugin Status ---"
hermes mychatarchive status 2>&1
echo ""

# Run a single-turn conversation that exercises the recall tools
echo "--- Running recall query ---"
QUERY="Recall what I've discussed about wanting to be an entrepreneur"

# Use hermes in single-shot mode with the query
# The -z flag sends a prompt and exits after one response
hermes -z "$QUERY" 2>&1 | tee "${SCRIPT_DIR}/raw-output.txt"

echo ""
echo "--- Demo complete ---"
echo "Raw output saved to: ${SCRIPT_DIR}/raw-output.txt"
echo "Edit demo/demo-transcript.md with the results."

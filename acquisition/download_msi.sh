#!/usr/bin/env bash
#
# download_msi.sh — Fetch the SLS2 driver/firmware MSI from Microsoft.
#
# One-shot: caches to raw/SurfaceLaptopStudio2_*.msi. Re-running is a no-op
# if the SHA256 matches the one in acquisition/IDENTIFY.md.
#
# Requires: curl
# Optional: sha256sum (for verification)
#
# Microsoft uses Akamai bot protection. A real browser User-Agent is required.

set -euo pipefail

OUT_DIR="$(dirname "$0")/../raw"
mkdir -p "$OUT_DIR"

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
PAGE="https://www.microsoft.com/en-us/download/details.aspx?id=105610"

EXPECTED_SHA="7f01b70e0e88995636374e72b70af0157f5e360269e6fda0aa192d785957f41d"

# 1. Resolve the real .msi download URL (Microsoft serves a details page; the
#    .msi is one of several .download.microsoft.com URLs in the HTML).
echo "[+] Resolving MSI URL from $PAGE …"
URL=$(curl -sL -A "$UA" "$PAGE" \
        | grep -oE 'https://download\.microsoft\.com/[^"]*\.msi' \
        | head -1 || true)

if [[ -z "$URL" ]]; then
    echo "[!] Could not resolve MSI URL. Microsoft may have changed the page layout."
    echo "    Try opening the page in a browser and copying the link manually."
    exit 1
fi

echo "[+] Resolved: $URL"

# 2. Pick output filename based on what we got.
OUT="$OUT_DIR/$(basename "$URL")"

if [[ -f "$OUT" ]]; then
    CUR=$(sha256sum "$OUT" | awk '{print $1}')
    if [[ "$CUR" == "$EXPECTED_SHA" ]]; then
        echo "[=] $OUT already present with correct SHA256. Skipping."
        exit 0
    else
        echo "[!] Existing file has SHA256=$CUR, expected $EXPECTED_SHA."
        echo "    Removing and re-downloading."
        rm -f "$OUT"
    fi
fi

# 3. Download.
echo "[+] Downloading to $OUT …"
curl -sL -A "$UA" -o "$OUT" "$URL"

# 4. Verify.
CUR=$(sha256sum "$OUT" | awk '{print $1}')
if [[ "$CUR" != "$EXPECTED_SHA" ]]; then
    echo "[!] Downloaded file has SHA256=$CUR, expected $EXPECTED_SHA."
    echo "    Microsoft may have shipped a newer build; re-record in IDENTIFY.md"
    echo "    before continuing."
    exit 1
fi

echo "[+] OK. SHA256 matches."
ls -lh "$OUT"

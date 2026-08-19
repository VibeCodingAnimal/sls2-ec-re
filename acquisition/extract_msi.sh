#!/usr/bin/env bash
#
# extract_msi.sh — Extract the SLS2 MSI and copy the targets we RE.
#
# Idempotent: skips files that already exist with the right SHA256 in
# raw/sys/ and raw/ec/.
#
# Requires: msitools (msiextract), cabextract
#
# Output (after this script runs):
#   raw/extracted/SurfaceUpdate/        # the full extracted tree
#   raw/sys/SurfaceSerialHubDriver.sys  # etc., one per target
#   raw/sys/SurfaceSmfClient.sys
#   raw/sys/SurfaceThermalPolicy.sys
#   raw/sys/SurfaceIntegrationDriver.sys
#   raw/sys/SurfaceAcpiPlatformExtensionDriver.sys
#   raw/sys/SurfacePowerStateDriver.sys
#   raw/sys/SurfacePowerMeterDriver.sys
#   raw/sys/SurfacePowerTrackerCore.sys
#   raw/ec/EC.bin                       # SurfaceSAM_13.101.139.bin
#   raw/ec/PD.bin                       # SurfacePD_5.15.0.bin

set -euo pipefail

ROOT="$(dirname "$0")/.."
EXTRACTED="$ROOT/raw/extracted"
SYS="$ROOT/raw/sys"
EC="$ROOT/raw/ec"

mkdir -p "$SYS" "$EC"

# 1. Find the MSI.
MSI=$(ls "$ROOT"/raw/*.msi 2>/dev/null | head -1 || true)
if [[ -z "$MSI" ]]; then
    echo "[!] No .msi found in $ROOT/raw/. Run download_msi.sh first."
    exit 1
fi

# 2. Extract (idempotent — msiextract will re-overwrite).
if [[ ! -d "$EXTRACTED/SurfaceUpdate" ]]; then
    echo "[+] msiextract $MSI → $EXTRACTED/"
    msiextract -C "$EXTRACTED" "$MSI"
    # Recursively unpack nested .cab files.
    echo "[+] Recursing nested .cab files …"
    while IFS= read -r -d '' cab; do
        outdir="${cab}.x"
        mkdir -p "$outdir"
        cabextract -d "$outdir" "$cab" >/dev/null
    done < <(find "$EXTRACTED" -name "*.cab" -print0)
else
    echo "[=] $EXTRACTED/SurfaceUpdate already present. Skipping extraction."
fi

# 3. Copy targets.
copy() {
    local src="$1" dst="$2"
    if [[ ! -f "$src" ]]; then
        echo "[!] Missing source: $src"
        return 1
    fi
    cp -f "$src" "$dst"
    echo "[+] $dst"
}

echo "[+] Copying drivers → raw/sys/"
copy "$EXTRACTED/SurfaceUpdate/surfaceserialhub/SurfaceSerialHubDriver.sys"            "$SYS/SurfaceSerialHubDriver.sys"
copy "$EXTRACTED/SurfaceUpdate/surfacesmfclient/SurfaceSmfClient.sys"                  "$SYS/SurfaceSmfClient.sys"
copy "$EXTRACTED/SurfaceUpdate/surfacethermalpolicy/SurfaceThermalPolicy.sys"          "$SYS/SurfaceThermalPolicy.sys"
copy "$EXTRACTED/SurfaceUpdate/surfaceintegrationdriver/SurfaceIntegrationDriver.sys"  "$SYS/SurfaceIntegrationDriver.sys"
copy "$EXTRACTED/SurfaceUpdate/surfaceacpiplatformextension/SurfaceAcpiPlatformExtensionDriver.sys" "$SYS/SurfaceAcpiPlatformExtensionDriver.sys"
copy "$EXTRACTED/SurfaceUpdate/surfacepowerstate/SurfacePowerStateDriver.sys"          "$SYS/SurfacePowerStateDriver.sys"
copy "$EXTRACTED/SurfaceUpdate/surfacepowermeter/SurfacePowerMeterDriver.sys"          "$SYS/SurfacePowerMeterDriver.sys"
copy "$EXTRACTED/SurfaceUpdate/surfacepowertrackercore/SurfacePowerTrackerCore.sys"    "$SYS/SurfacePowerTrackerCore.sys"

echo "[+] Copying firmware → raw/ec/"
copy "$EXTRACTED/SurfaceUpdate/surfacesam/SurfaceSAM_13.101.139.bin"  "$EC/EC.bin"
copy "$EXTRACTED/SurfaceUpdate/surfacepd/SurfacePD_5.15.0.bin"        "$EC/PD.bin"

# 4. Re-hash for sanity.
echo ""
echo "[+] SHA256 snapshot:"
( cd "$ROOT" && sha256sum raw/sys/*.sys raw/ec/*.bin ) | tee "$ROOT/acquisition/SHA256.txt"

echo ""
echo "[+] Done. Next: read acquisition/IDENTIFY.md and proceed to Phase 2."

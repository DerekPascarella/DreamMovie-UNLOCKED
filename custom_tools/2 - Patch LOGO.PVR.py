#!/usr/bin/env python3
# 2 - Patch LOGO.PVR.py
# Written by Derek Pascarella (ateam)
#
# Patches LOGO.PVR into a previously-patched DreamMovie CDI (in-place).
# Expects LOGO.PVR and a "DreamMovie (Patched *.cdi" in this folder.
#
# LOGO.PVR is a 512x512 ARGB4444 twiddled PVR texture, 524320 bytes.
# In the CDI it's found by matching GBIX+PVRT headers with pixel format
# 0x03 and data format 0x01. BACK.PVR uses the same format but comes
# first on disc (alphabetical ISO 9660 ordering), so we take the second
# match.
#
# EDC/ECC is left stale. Run "4 - Fix ECC.py" when done.

import os
import sys
import glob

SECTOR_SIZE = 2336
DATA_PER_SECTOR = 2048
SUBHEADER_SIZE = 8
EXPECTED_SIZE = 524320
NUM_SECTORS = 257  # ceil(524320 / 2048)

# PVR header bytes that identify LOGO.PVR
PIXEL_FORMAT = 0x03  # ARGB4444
DATA_FORMAT = 0x01   # twiddled
SIBLING_NAME = "BACK.PVR"  # same format, comes first on disc


def find_patched_cdi(script_dir):
    """Locate the most recent patched CDI in the script's directory."""
    pattern = os.path.join(script_dir, "DreamMovie (Patched *).cdi")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not matches:
        print("Error: No patched CDI found. Run '1 - Patch MSL.OUT.py' first.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"  Note: {len(matches)} patched CDIs found, using most recent")
    return matches[-1]


def find_pvr_candidates(cdi):
    """
    Search the CDI for GBIX+PVRT entries matching our pixel/data format,
    512x512 dimensions, and valid Mode 2 Form 1 sector structure across
    all 257 sectors. Returns a list of sector-start offsets.
    """
    candidates = []
    pos = 0
    while True:
        pos = cdi.find(b"GBIX", pos)
        if pos == -1:
            break

        if pos < SUBHEADER_SIZE or pos + 32 > len(cdi):
            pos += 1
            continue

        # PVRT signature sits at GBIX+16
        if cdi[pos + 16 : pos + 20] != b"PVRT":
            pos += 1
            continue

        # Pixel format and data format at GBIX+24
        if cdi[pos + 24] != PIXEL_FORMAT or cdi[pos + 25] != DATA_FORMAT:
            pos += 1
            continue

        # 512x512 dimensions (LE 16-bit each) at GBIX+28
        if cdi[pos + 28 : pos + 32] != b"\x00\x02\x00\x02":
            pos += 1
            continue

        # Validate Mode 2 Form 1 sub-header before this data
        sub = pos - SUBHEADER_SIZE
        if cdi[sub + 2] != 0x08 or cdi[sub + 6] != 0x08:
            pos += 1
            continue

        # All 257 sectors must have valid sub-headers
        valid = True
        for i in range(NUM_SECTORS):
            sec = sub + i * SECTOR_SIZE
            if sec + SECTOR_SIZE > len(cdi):
                valid = False
                break
            if cdi[sec + 2] != 0x08 or cdi[sec + 6] != 0x08:
                valid = False
                break

        if valid:
            candidates.append(sub)
        pos += 1

    return candidates


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    pvr_path = os.path.join(script_dir, "LOGO.PVR")
    if not os.path.isfile(pvr_path):
        print(f"Error: LOGO.PVR not found in {script_dir}")
        sys.exit(1)

    pvr_size = os.path.getsize(pvr_path)
    if pvr_size != EXPECTED_SIZE:
        print(f"Error: LOGO.PVR is {pvr_size} bytes, expected {EXPECTED_SIZE}")
        sys.exit(1)

    cdi_path = find_patched_cdi(script_dir)
    print(f"LOGO.PVR: {pvr_size:,} bytes")
    print(f"Target CDI: {os.path.basename(cdi_path)}")
    print()

    with open(pvr_path, "rb") as f:
        pvr = f.read()

    with open(cdi_path, "rb") as f:
        cdi = bytearray(f.read())

    # Locate LOGO.PVR in the CDI
    print("Searching for LOGO.PVR in CDI...")
    candidates = find_pvr_candidates(cdi)

    if len(candidates) == 0:
        print("Error: No matching GBIX+PVRT entries found")
        sys.exit(1)
    elif len(candidates) == 1:
        print(f"  Warning: only 1 match (expected 2: {SIBLING_NAME} + LOGO.PVR)")
        sector_start = candidates[0]
    else:
        print(f"  Found {len(candidates)} matches, selecting #2 as LOGO.PVR")
        sector_start = candidates[1]

    data_start = sector_start + SUBHEADER_SIZE
    print(f"  LOGO.PVR at CDI offset {data_start} (sector start: {sector_start})")

    # Sanity check
    if cdi[data_start : data_start + 4] != b"GBIX":
        print("Error: GBIX header not where expected")
        sys.exit(1)

    # Patch each sector's data area
    print(f"\nPatching {NUM_SECTORS} sectors...")
    for i in range(NUM_SECTORS):
        src_start = i * DATA_PER_SECTOR
        src_end = min(src_start + DATA_PER_SECTOR, EXPECTED_SIZE)
        chunk = pvr[src_start:src_end]

        dst = sector_start + i * SECTOR_SIZE + SUBHEADER_SIZE
        cdi[dst : dst + len(chunk)] = chunk

    print(f"  Wrote {EXPECTED_SIZE:,} bytes across {NUM_SECTORS} sectors")

    # Write back in-place
    print(f"\nWriting: {os.path.basename(cdi_path)}")
    with open(cdi_path, "wb") as f:
        f.write(cdi)

    # Spot-check verification
    print("\nVerifying...")
    ok = True

    if cdi[data_start : data_start + 32] != pvr[:32]:
        print("  First sector (GBIX+PVRT header): MISMATCH")
        ok = False
    else:
        print("  First sector (GBIX+PVRT header): OK")

    mid = NUM_SECTORS // 2
    src_off = mid * DATA_PER_SECTOR
    dst_off = sector_start + mid * SECTOR_SIZE + SUBHEADER_SIZE
    if cdi[dst_off : dst_off + 16] != pvr[src_off : src_off + 16]:
        print(f"  Mid sector ({mid}): MISMATCH")
        ok = False
    else:
        print(f"  Mid sector ({mid}): OK")

    tail_src = EXPECTED_SIZE - 16
    tail_sector = tail_src // DATA_PER_SECTOR
    tail_in_sector = tail_src % DATA_PER_SECTOR
    tail_dst = sector_start + tail_sector * SECTOR_SIZE + SUBHEADER_SIZE + tail_in_sector
    if cdi[tail_dst : tail_dst + 16] != pvr[tail_src:]:
        print("  Last bytes: MISMATCH")
        ok = False
    else:
        print("  Last bytes: OK")

    if ok:
        print("\nDone. Next: run '3 - Patch FLAG.PVR.py'")
    else:
        print("\nWARNING: Verification failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# 1 - Patch MSL.OUT.py
# Written by Derek Pascarella (ateam)
#
# Patches MSL.OUT into a vanilla DreamMovie CDI disc image.
# Expects MSL.OUT and DreamMovie.cdi in the same folder as this script.
# Output: "DreamMovie (Patched YYYY-MM-DD-HH-MM-SS).cdi"
#
# CDI sector layout (Mode 2 Form 1, headerless):
#   2336 bytes/sector = 8 sub-header + 2048 data + 280 EDC/ECC
#
# MSL.OUT starts with "SDRV" magic. We locate it in the CDI by matching
# the SDRV signature plus neighboring bytes against the actual MSL.OUT
# content, then confirming the Mode 2 Form 1 sub-header structure around
# it. Each 2048-byte chunk of MSL.OUT replaces the data portion of one
# CDI sector, leaving sub-headers and EDC/ECC regions untouched.
#
# EDC/ECC is left stale after patching. Run "4 - Fix ECC.py" when done.

import os
import sys
from datetime import datetime

SECTOR_SIZE = 2336
DATA_PER_SECTOR = 2048
SUBHEADER_SIZE = 8


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    msl_path = os.path.join(script_dir, "MSL.OUT")
    cdi_path = os.path.join(script_dir, "DreamMovie.cdi")

    if not os.path.isfile(msl_path):
        print(f"Error: MSL.OUT not found in {script_dir}")
        sys.exit(1)
    if not os.path.isfile(cdi_path):
        print(f"Error: DreamMovie.cdi not found in {script_dir}")
        sys.exit(1)

    # Read source files into memory
    with open(msl_path, "rb") as f:
        msl = f.read()
    print(f"MSL.OUT: {len(msl):,} bytes")

    with open(cdi_path, "rb") as f:
        cdi = bytearray(f.read())
    print(f"DreamMovie.cdi: {len(cdi):,} bytes")
    print()

    # Search for SDRV signature in the CDI
    print("Searching for MSL.OUT in CDI...")
    sector_start = None
    pos = 0
    while True:
        pos = cdi.find(b"SDRV", pos)
        if pos == -1:
            break

        # Need room for a sub-header before data
        if pos < SUBHEADER_SIZE:
            pos += 1
            continue

        # Validate Mode 2 Form 1 sub-header (8 bytes before data area)
        sub_off = pos - SUBHEADER_SIZE
        if cdi[sub_off + 2] != 0x08 or cdi[sub_off + 6] != 0x08:
            pos += 1
            continue

        # Match bytes 4-15 of data against MSL.OUT to confirm identity
        if cdi[pos + 4 : pos + 16] == msl[4:16]:
            sector_start = sub_off
            print(f"  Found at CDI offset {pos} (sector start: {sector_start})")
            break

        pos += 1

    if sector_start is None:
        print("Error: Could not locate MSL.OUT in CDI")
        sys.exit(1)

    # Patch each sector's data area with the corresponding MSL.OUT chunk
    num_sectors = (len(msl) + DATA_PER_SECTOR - 1) // DATA_PER_SECTOR
    print(f"Patching {num_sectors} sectors...")

    for i in range(num_sectors):
        src_start = i * DATA_PER_SECTOR
        src_end = min(src_start + DATA_PER_SECTOR, len(msl))
        chunk = msl[src_start:src_end]

        dst = sector_start + i * SECTOR_SIZE + SUBHEADER_SIZE
        cdi[dst : dst + len(chunk)] = chunk

    print(f"  Wrote {len(msl):,} bytes across {num_sectors} sectors")

    # Build output filename with current timestamp
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    out_name = f"DreamMovie (Patched {ts}).cdi"
    out_path = os.path.join(script_dir, out_name)

    print(f"\nWriting: {out_name}")
    with open(out_path, "wb") as f:
        f.write(cdi)
    print(f"  {len(cdi):,} bytes written")

    # Spot-check first, middle, and last sectors
    print("\nVerifying...")
    ok = True

    # First sector
    off = sector_start + SUBHEADER_SIZE
    if cdi[off : off + 16] != msl[:16]:
        print("  First sector: MISMATCH")
        ok = False
    else:
        print("  First sector: OK")

    # Middle sector
    mid = num_sectors // 2
    src_off = mid * DATA_PER_SECTOR
    dst_off = sector_start + mid * SECTOR_SIZE + SUBHEADER_SIZE
    if cdi[dst_off : dst_off + 16] != msl[src_off : src_off + 16]:
        print(f"  Mid sector ({mid}): MISMATCH")
        ok = False
    else:
        print(f"  Mid sector ({mid}): OK")

    # Last bytes
    tail_src = len(msl) - 16
    tail_sector = tail_src // DATA_PER_SECTOR
    tail_in_sector = tail_src % DATA_PER_SECTOR
    tail_dst = sector_start + tail_sector * SECTOR_SIZE + SUBHEADER_SIZE + tail_in_sector
    if cdi[tail_dst : tail_dst + 16] != msl[tail_src : tail_src + 16]:
        print("  Last bytes: MISMATCH")
        ok = False
    else:
        print("  Last bytes: OK")

    if ok:
        print(f"\nDone. Output: {out_name}")
        print("Next: run '2 - Patch LOGO.PVR.py'")
    else:
        print("\nWARNING: Verification failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

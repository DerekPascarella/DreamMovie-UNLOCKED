#!/usr/bin/env python3
# 4 - Fix ECC.py
# Written by Derek Pascarella (ateam)
#
# Recalculates EDC/ECC for all modified Mode 2 Form 1 sectors in a
# patched DreamMovie CDI. Operates in-place on the most recent
# "DreamMovie (Patched *.cdi" found in this folder.
#
# Run this after all other patching steps (MSL.OUT, LOGO.PVR, FLAG.PVR).
# Only sectors whose stored EDC doesn't match the data get recomputed,
# so unmodified sectors are left alone.
#
# CDI sector layout (Mode 2 Form 1, 2336 bytes, headerless):
#   0x000  8     Sub-header (4 bytes repeated twice)
#   0x008  2048  User data
#   0x808  4     EDC (CRC-32 over 0x000..0x807)
#   0x80C  172   ECC P-parity (Reed-Solomon)
#   0x8B8  104   ECC Q-parity (Reed-Solomon)
#
# EDC polynomial: 0xD8018001 (reflected)
# ECC field: GF(2^8), primitive polynomial 0x11D
# For Mode 2 Form 1, the missing 4-byte sector address is treated as
# all zeros for ECC computation (per ECMA-130).
#
# Algorithm reference: ecm.c by Neill Corlett (GPL v3)

import os
import sys
import glob
import struct

SECTOR_SIZE = 2336

# -- Lookup tables, filled by init_luts() --
ecc_f_lut = [0] * 256
ecc_b_lut = [0] * 256
edc_lut = [0] * 256


def init_luts():
    """Build GF(2^8) and CRC-32 lookup tables (matches ecm.c)."""
    for i in range(256):
        j = ((i << 1) ^ (0x11D if (i & 0x80) else 0)) & 0xFF
        ecc_f_lut[i] = j
        ecc_b_lut[i ^ j] = i

        edc = i
        for _ in range(8):
            edc = ((edc >> 1) ^ (0xD8018001 if (edc & 1) else 0)) & 0xFFFFFFFF
        edc_lut[i] = edc


# -- EDC (CRC-32) --

def edc_compute(src, size):
    """CRC-32 over src[0:size]."""
    edc = 0
    for i in range(size):
        edc = ((edc >> 8) ^ edc_lut[(edc ^ src[i]) & 0xFF]) & 0xFFFFFFFF
    return edc


# -- ECC (Reed-Solomon product code) --

ZERO_ADDR = bytes(4)


def ecc_writepq(address, data, major_count, minor_count, major_mult,
                minor_inc, output, out_offset):
    """Compute and write P or Q parity bytes."""
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for minor in range(minor_count):
            if index < 4:
                temp = address[index]
            else:
                temp = data[index - 4]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ecc_f_lut[ecc_a]
        ecc_a = ecc_b_lut[ecc_f_lut[ecc_a] ^ ecc_b]
        output[out_offset + major] = ecc_a
        output[out_offset + major + major_count] = ecc_a ^ ecc_b


def ecc_checkpq(address, data, major_count, minor_count, major_mult,
                minor_inc, ecc, ecc_offset):
    """Verify P or Q parity. Returns True if OK."""
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for minor in range(minor_count):
            if index < 4:
                temp = address[index]
            else:
                temp = data[index - 4]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ecc_f_lut[ecc_a]
        ecc_a = ecc_b_lut[ecc_f_lut[ecc_a] ^ ecc_b]
        if (ecc[ecc_offset + major] != ecc_a or
                ecc[ecc_offset + major + major_count] != (ecc_a ^ ecc_b)):
            return False
    return True


# -- Sector helpers --

def is_mode2_form1(data, offset):
    """Check whether the sector at offset is Mode 2 Form 1."""
    if offset + SECTOR_SIZE > len(data):
        return False
    # Sub-header: first 4 bytes must match the repeated copy
    if (data[offset] != data[offset + 4] or
            data[offset + 1] != data[offset + 5] or
            data[offset + 2] != data[offset + 6] or
            data[offset + 3] != data[offset + 7]):
        return False
    # Submode: bit 3 set (data), bit 5 clear (Form 1)
    return (data[offset + 2] & 0x28) == 0x08


def fix_sector(sector):
    """
    Recompute EDC and ECC for one 2336-byte sector (bytearray).
    Returns True if anything changed.
    """
    new_edc = edc_compute(sector, 0x808)
    old_edc = struct.unpack_from('<I', sector, 0x808)[0]

    if new_edc == old_edc:
        return False

    struct.pack_into('<I', sector, 0x808, new_edc)

    # P parity: 172 bytes at 0x80C
    ecc_writepq(ZERO_ADDR, sector, 86, 24, 2, 86, sector, 0x80C)
    # Q parity: 104 bytes at 0x8B8 (reads the P parity we just wrote)
    ecc_writepq(ZERO_ADDR, sector, 52, 43, 86, 88, sector, 0x8B8)

    return True


def verify_sector(sector):
    """Returns (edc_ok, ecc_p_ok, ecc_q_ok)."""
    edc_ok = (edc_compute(sector, 0x808) ==
              struct.unpack_from('<I', sector, 0x808)[0])
    ecc_p_ok = ecc_checkpq(ZERO_ADDR, sector, 86, 24, 2, 86, sector, 0x80C)
    ecc_q_ok = ecc_checkpq(ZERO_ADDR, sector, 52, 43, 86, 88, sector, 0x8B8)
    return edc_ok, ecc_p_ok, ecc_q_ok


# -- Data track detection --

def find_data_track_alignment(data):
    """
    Figure out the byte alignment of Mode 2 Form 1 sectors in the file.
    Tries all 2336 possible alignments, sampling up to 100 sectors each.
    Returns the best alignment, or -1 if nothing looks right.
    """
    file_size = len(data)
    best_alignment = -1
    best_count = 0

    for alignment in range(SECTOR_SIZE):
        total_sectors = (file_size - alignment) // SECTOR_SIZE
        if total_sectors < 10:
            continue
        count = 0
        step = max(1, total_sectors // 100)
        for i in range(0, min(total_sectors, step * 100), step):
            offset = alignment + i * SECTOR_SIZE
            if is_mode2_form1(data, offset):
                count += 1
        if count > best_count:
            best_count = count
            best_alignment = alignment

    return best_alignment if best_count >= 5 else -1


def find_sector_range(data, alignment):
    """Find first and last Mode 2 Form 1 sector indices at given alignment."""
    total = (len(data) - alignment) // SECTOR_SIZE
    first = None
    last = None
    for i in range(total):
        if is_mode2_form1(data, alignment + i * SECTOR_SIZE):
            if first is None:
                first = i
            last = i
    return first, last


# -- Main --

def find_patched_cdi(script_dir):
    """Locate the most recent patched CDI in the script's directory."""
    pattern = os.path.join(script_dir, "DreamMovie (Patched *).cdi")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not matches:
        print("Error: No patched CDI found. Run steps 1-3 first.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"  Note: {len(matches)} patched CDIs found, using most recent")
    return matches[-1]


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cdi_path = find_patched_cdi(script_dir)

    print(f"Target CDI: {os.path.basename(cdi_path)}")
    print()

    init_luts()

    print("Reading CDI...")
    with open(cdi_path, 'rb') as f:
        data = bytearray(f.read())
    print(f"  {len(data):,} bytes ({len(data) / 1048576:.1f} MB)")
    print()

    # Detect sector alignment
    print("Detecting data track alignment...")
    alignment = find_data_track_alignment(data)
    if alignment < 0:
        print("Error: no Mode 2 Form 1 data track found")
        sys.exit(1)

    first_idx, last_idx = find_sector_range(data, alignment)
    if first_idx is None:
        print("Error: no Mode 2 Form 1 sectors at detected alignment")
        sys.exit(1)

    num_sectors = last_idx - first_idx + 1
    print(f"  Alignment: {alignment}")
    print(f"  Sectors: {first_idx} to {last_idx} ({num_sectors:,} total)")
    print()

    # Walk all sectors; recompute EDC/ECC where EDC is stale
    print("Processing sectors...")
    checked = 0
    fixed = 0
    fixed_indices = []
    skipped = 0
    verify_failures = 0

    for i in range(first_idx, last_idx + 1):
        offset = alignment + i * SECTOR_SIZE
        if not is_mode2_form1(data, offset):
            skipped += 1
            continue

        checked += 1
        sector = bytearray(data[offset:offset + SECTOR_SIZE])

        if fix_sector(sector):
            edc_ok, ecc_p_ok, ecc_q_ok = verify_sector(sector)
            if not (edc_ok and ecc_p_ok and ecc_q_ok):
                print(f"  VERIFY FAILED sector {i} (offset {offset}):"
                      f" EDC={'OK' if edc_ok else 'FAIL'}"
                      f" P={'OK' if ecc_p_ok else 'FAIL'}"
                      f" Q={'OK' if ecc_q_ok else 'FAIL'}")
                verify_failures += 1
            else:
                data[offset:offset + SECTOR_SIZE] = sector
                fixed += 1
                fixed_indices.append(i)

        if checked % 5000 == 0:
            print(f"  {checked:,} checked, {fixed} fixed...")

    print(f"  Done: {checked:,} checked, {fixed} fixed"
          f" ({skipped} non-Form1 skipped)")

    if verify_failures > 0:
        print(f"\nERROR: {verify_failures} sector(s) failed verification!")
        print("File NOT written.")
        sys.exit(1)

    if fixed == 0:
        print("\nAll sectors already correct. No changes needed.")
        print("\nDone. CDI is ready to burn or use with ODE.")
        return

    print(f"\n{fixed} sector(s) had EDC/ECC recalculated.")

    # Write back in-place
    print(f"\nWriting: {os.path.basename(cdi_path)}")
    with open(cdi_path, 'wb') as f:
        f.write(data)
    print(f"  {len(data):,} bytes written")

    # Re-read and verify the fixed sectors on disk
    spot_count = min(fixed, 10)
    print(f"\nPost-write verification ({spot_count} sectors)...")
    with open(cdi_path, 'rb') as f:
        written = f.read()
    for i in fixed_indices[:spot_count]:
        offset = alignment + i * SECTOR_SIZE
        sector = bytearray(written[offset:offset + SECTOR_SIZE])
        edc_ok, ecc_p_ok, ecc_q_ok = verify_sector(sector)
        if not (edc_ok and ecc_p_ok and ecc_q_ok):
            print(f"  FAIL: sector {i}")
            sys.exit(1)
    print(f"  All {spot_count} spot-checked sectors OK")

    print("\nDone. CDI is ready to burn or use with ODE.")


if __name__ == '__main__':
    main()

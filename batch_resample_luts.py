import os
import sys
import traceback
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator


# ---------------------------------------------------------------------------
# .cube parser / writer / resampler  (pure Python + numpy/scipy, no LUT lib)
# ---------------------------------------------------------------------------

def parse_cube(path):
    """Parse a Resolve/Adobe .cube file. Returns (size, domain_min, domain_max, table)."""
    size = None
    domain_min = [0.0, 0.0, 0.0]
    domain_max = [1.0, 1.0, 1.0]
    rows = []
    with open(path, 'r', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            upper = line.upper()
            if upper.startswith('LUT_3D_SIZE'):
                size = int(line.split()[1])
            elif upper.startswith('DOMAIN_MIN'):
                domain_min = [float(x) for x in line.split()[1:4]]
            elif upper.startswith('DOMAIN_MAX'):
                domain_max = [float(x) for x in line.split()[1:4]]
            elif upper.startswith('LUT_1D_SIZE'):
                raise ValueError("1D LUTs are not supported (only 3D .cube files).")
            elif upper.startswith('TITLE') or upper.startswith('LUT_3D_INPUT_RANGE'):
                continue
            else:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                    except ValueError:
                        continue
    if size is None:
        raise ValueError("LUT_3D_SIZE not found.")
    expected = size ** 3
    if len(rows) < expected:
        raise ValueError(f"Expected {expected} entries, got {len(rows)}.")
    return size, domain_min, domain_max, np.array(rows[:expected], dtype=np.float32)


def resample_cube(table, in_size, out_size):
    """Trilinearly resample a 3D LUT from in_size^3 to out_size^3.
    .cube order: R varies fastest, then G, then B."""
    t = table.reshape(in_size, in_size, in_size, 3)
    axis = np.linspace(0.0, 1.0, in_size)
    out_axis = np.linspace(0.0, 1.0, out_size)
    r, g, b = np.meshgrid(out_axis, out_axis, out_axis, indexing='ij')
    pts = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=-1)
    result = np.empty((out_size ** 3, 3), dtype=np.float32)
    for ch in range(3):
        interp = RegularGridInterpolator(
            (axis, axis, axis), t[:, :, :, ch],
            method='linear', bounds_error=False, fill_value=None
        )
        result[:, ch] = interp(pts).astype(np.float32)
    return result


def write_cube(path, table, size, domain_min, domain_max, title=None):
    """Write a .cube LUT file."""
    with open(path, 'w') as f:
        if title:
            f.write(f'TITLE "{title}"\n')
        f.write(f'LUT_3D_SIZE {size}\n')
        f.write(f'DOMAIN_MIN {domain_min[0]:.6f} {domain_min[1]:.6f} {domain_min[2]:.6f}\n')
        f.write(f'DOMAIN_MAX {domain_max[0]:.6f} {domain_max[1]:.6f} {domain_max[2]:.6f}\n\n')
        for row in table:
            f.write(f'{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n')


def write_vlt(path, table, size, title=None):
    """Write a Panasonic VLT file (version 1.0). Values are 12-bit integers (0-4095)."""
    with open(path, 'w') as f:
        f.write('# panasonic vlt file version 1.0\n')
        f.write(f'# source vlt file "{title or ""}"\n')
        f.write(f'LUT_3D_SIZE {size}\n\n')
        for row in table:
            r = int(np.clip(round(float(row[0]) * 4095), 0, 4095))
            g = int(np.clip(round(float(row[1]) * 4095), 0, 4095))
            b = int(np.clip(round(float(row[2]) * 4095), 0, 4095))
            f.write(f'{r} {g} {b}\n')


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def prompt_int(prompt, default, minval, maxval):
    while True:
        raw = input(f"{prompt} (default: {default}): ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if minval <= val <= maxval:
                return val
        except ValueError:
            pass
        print(f"Please enter an integer between {minval} and {maxval}.")


def prompt_choice(prompt, choices, default):
    print(f"\n{prompt}")
    for k, v in choices.items():
        suffix = " (default)" if k == default else ""
        print(f"  [{k}] {v}{suffix}")
    while True:
        raw = input("Select: ").strip()
        if not raw and default:
            return default
        if raw in choices:
            return raw
        print("Invalid selection. Try again.")


def prompt_yesno(prompt, default):
    d = 'Y' if default else 'N'
    while True:
        raw = input(f"{prompt} (Y/N, default: {d}): ").strip().lower()
        if not raw:
            return default
        if raw in ('y', 'yes'):
            return True
        if raw in ('n', 'no'):
            return False
        print("Please answer Y or N.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    root = os.getcwd()
    print(f"Root directory: {root}")

    print("\nScanning for .cube files recursively...")
    cubes = [
        p for p in Path(root).rglob('*.cube')
        if not any(part.startswith('out_') for part in p.relative_to(root).parts[:-1])
    ]
    if not cubes:
        print(f"No .cube files found under {root}")
        return
    print(f"Found {len(cubes)} .cube file(s).\n")

    # --- Option: Cube Size ---
    print("--- Option: Output Cube Size ---")
    print("  The cube size sets the grid resolution of the 3D LUT.")
    print("  Larger = more color precision, bigger file, slower to process.")
    print("  Common values:")
    print("    17  - small/fast, used by some apps")
    print("    33  - industry standard, balanced (recommended)")
    print("    64  - high quality, large file")
    cube_size = prompt_int("Enter the desired output cube size", 33, 2, 256)

    # --- Option: Folder Structure ---
    print("\n--- Option: Output Folder Structure ---")
    print("  Mirror : preserves the original folder hierarchy under the output folder.")
    print("  Flat   : puts every converted file directly in the output folder.")
    print("           WARNING - files in different subfolders that share a name will")
    print("           overwrite each other in flat mode.")
    mirror_choice = prompt_choice("Output folder structure:", {
        '1': 'Mirror input subfolders under output folder',
        '2': 'Flat - put everything in one output folder (recommended)'
    }, '2')
    mirror_subdirs = mirror_choice == '1'

    # --- Option: Naming ---
    print("\n--- Option: Output File Naming ---")
    print("  Append _{size}: e.g. Film_33.cube - size visible in name, never overwrites originals.")
    print("  Keep base name: e.g. Film.cube    - cleaner, but risky if output = source folder.")
    naming_choice = prompt_choice("Output naming:", {
        '1': 'Append _{size} before extension  (e.g. Film_33.cube)',
        '2': 'Keep same base name              (e.g. Film.cube) (recommended)'
    }, '2')

    # --- Option: Overwrite ---
    print("\n--- Option: Overwrite Existing Output Files ---")
    print("  Yes: existing output files with the same name will be replaced.")
    print("  No : existing output files are skipped - safe to re-run without re-doing work.")
    overwrite = prompt_yesno("Overwrite existing outputs if they already exist?", False)

    # --- Option: Dry Run ---
    print("\n--- Option: Dry Run ---")
    print("  Yes: only shows what WOULD happen - no files are written.")
    print("       Use this to preview settings before committing.")
    print("  No : actually writes the resampled LUT files.")
    dry_run = prompt_yesno("Dry run (show what would happen, do not write files)?", False)

    # --- Option: Output Format ---
    print("\n--- Option: Output Format ---")
    print("  cube : Standard .cube file (Resolve, Lightroom, Premiere, etc.)")
    print("  vlt  : Panasonic VLT format (Lumix cameras, Lumix Tether, etc.)")
    print("  both : Write both .cube and .vlt for every LUT")
    fmt_choice = prompt_choice("Output format:", {
        '1': '.cube only  (standard, most compatible)',
        '2': '.vlt only   (Panasonic cameras / Lumix software)',
        '3': 'Both .cube and .vlt'
    }, '1')
    write_cube_out = fmt_choice in ('1', '3')
    write_vlt_out  = fmt_choice in ('2', '3')

    # --- Option: Validate ---
    print("\n--- Option: Validate Input LUTs Before Converting ---")
    print("  Yes: each .cube file is parsed and checked before resampling.")
    print("       Invalid or corrupt files are skipped instead of crashing.")
    print("  No : files are read and processed directly; errors will show as FAIL.")
    validate = prompt_yesno("Validate each input LUT before converting?", True)

    # --- Output folder ---
    out_dir_default = os.path.join(root, f"out_{cube_size}")
    out_dir_raw = input(f"\nOutput folder (default: {out_dir_default}): ").strip()
    out_dir = out_dir_raw if out_dir_raw else out_dir_default

    print("\n----- Summary -----")
    print(f"Input root          : {root}")
    print(f"Files found         : {len(cubes)}")
    print(f"Output cube size    : {cube_size}")
    print(f"Output folder       : {out_dir}")
    print(f"Mirror subfolders   : {mirror_subdirs}")
    print(f"Naming              : {'Append _{size}' if naming_choice == '1' else 'Keep base name'}")
    print(f"Output format       : {'cube+vlt' if (write_cube_out and write_vlt_out) else ('.cube' if write_cube_out else '.vlt')}")
    print(f"Overwrite           : {overwrite}")
    print(f"Dry run             : {dry_run}")
    print(f"Validate inputs     : {validate}")
    print("-------------------\n")

    proceed = prompt_yesno("Proceed?", True)
    if not proceed:
        print("Cancelled.")
        return

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)

    converted = 0
    skipped = 0
    failed = 0

    for f in cubes:
        try:
            rel_dir = os.path.relpath(f.parent, root)
            target_dir = os.path.join(out_dir, rel_dir) if mirror_subdirs and rel_dir != '.' else out_dir
            base_name = f.stem
            suffix = f"_{cube_size}" if naming_choice == '1' else ""
            cube_out_path = os.path.join(target_dir, f"{base_name}{suffix}.cube") if write_cube_out else None
            vlt_out_path  = os.path.join(target_dir, f"{base_name}{suffix}.vlt")  if write_vlt_out  else None

            # Check overwrite for all output paths
            all_exist = all(
                os.path.exists(p) for p in [cube_out_path, vlt_out_path] if p
            )
            if all_exist and not overwrite:
                for p in [cube_out_path, vlt_out_path]:
                    if p:
                        print(f"SKIP (exists): {p}")
                skipped += 1
                continue

            # Parse
            try:
                in_size, domain_min, domain_max, table = parse_cube(str(f))
            except Exception as e:
                if validate:
                    print(f"SKIP (invalid LUT): {f.name} - {e}")
                    skipped += 1
                    continue
                else:
                    raise

            if dry_run:
                action = "copy (same size)" if in_size == cube_size else f"resample {in_size} -> {cube_size}"
                for p in [cube_out_path, vlt_out_path]:
                    if p:
                        print(f"DRYRUN [{action}]: {f.name} -> {p}")
                converted += 1
                continue

            os.makedirs(target_dir, exist_ok=True)

            if in_size == cube_size:
                out_table = table
                action_label = "copy, size unchanged"
            else:
                out_table = resample_cube(table, in_size, cube_size)
                action_label = f"resampled {in_size}->{cube_size}"

            if cube_out_path:
                write_cube(cube_out_path, out_table, cube_size, domain_min, domain_max, title=base_name)
                print(f"OK ({action_label}): {cube_out_path}")
            if vlt_out_path:
                write_vlt(vlt_out_path, out_table, cube_size, title=base_name)
                print(f"OK ({action_label}): {vlt_out_path}")
            converted += 1

        except Exception as e:
            print(f"FAIL: {f.name}")
            print(f"  {e}")
            traceback.print_exc()
            failed += 1

    print("\nDone.")
    print(f"Converted : {converted}")
    print(f"Skipped   : {skipped}")
    print(f"Failed    : {failed}")
    if dry_run:
        print("NOTE: Dry run was enabled; no files were written.")


if __name__ == "__main__":
    main()

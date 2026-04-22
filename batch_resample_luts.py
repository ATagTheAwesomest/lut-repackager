"""
LUT Resampler – PySide6 GUI
Batch-resamples .cube LUT files to any grid size and optionally exports .vlt.
Single-file, no venv required beyond: pip install PySide6 numpy scipy
"""

import os
import sys
import traceback
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QCheckBox, QRadioButton,
    QButtonGroup, QTextEdit, QProgressBar, QFileDialog, QGroupBox,
    QGridLayout, QSizePolicy, QFrame, QSplitter,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor, QPalette, QTextCursor, QIcon


# ---------------------------------------------------------------------------
# LUT core logic
# ---------------------------------------------------------------------------

def parse_cube(path):
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
    with open(path, 'w') as f:
        if title:
            f.write(f'TITLE "{title}"\n')
        f.write(f'LUT_3D_SIZE {size}\n')
        f.write(f'DOMAIN_MIN {domain_min[0]:.6f} {domain_min[1]:.6f} {domain_min[2]:.6f}\n')
        f.write(f'DOMAIN_MAX {domain_max[0]:.6f} {domain_max[1]:.6f} {domain_max[2]:.6f}\n\n')
        for row in table:
            f.write(f'{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n')


def write_vlt(path, table, size, title=None):
    with open(path, 'w') as f:
        f.write('# panasonic vlt file version 1.0\n')
        f.write(f'# source vlt file "{title or ""}"\n')
        f.write(f'LUT_3D_SIZE {size}\n\n')
        for row in table:
            r = int(np.clip(round(float(row[0]) * 4095), 0, 4095))
            g = int(np.clip(round(float(row[1]) * 4095), 0, 4095))
            b = int(np.clip(round(float(row[2]) * 4095), 0, 4095))
            f.write(f'{r} {g} {b}\n')


def write_3dl(path, table, size, title=None):
    """Autodesk Flame / Lustre .3dl  —  12-bit integers (0-4095).
    First line: N evenly-spaced shaper/mesh breakpoints.
    Remaining lines: RGB triplets for every grid node, R varies fastest."""
    breakpoints = np.round(np.linspace(0, 4095, size)).astype(int)
    with open(path, 'w') as f:
        if title:
            f.write(f'# {title}\n')
        f.write(' '.join(str(v) for v in breakpoints) + '\n')
        for row in table:
            r = int(np.clip(round(float(row[0]) * 4095), 0, 4095))
            g = int(np.clip(round(float(row[1]) * 4095), 0, 4095))
            b = int(np.clip(round(float(row[2]) * 4095), 0, 4095))
            f.write(f'{r} {g} {b}\n')


def write_spi3d(path, table, size, title=None):
    """Sony Pictures Imageworks / OpenColorIO .spi3d  —  float values.
    Format: SPILUT 1.0 header, then indexed triplets.
    Index ordering: r outermost, b innermost (b varies fastest)."""
    with open(path, 'w') as f:
        if title:
            f.write(f'# {title}\n')
        f.write('SPILUT 1.0\n')
        f.write('3 3\n')
        f.write(f'{size} {size} {size}\n')
        for r_idx in range(size):
            for g_idx in range(size):
                for b_idx in range(size):
                    # .cube table index: r + g*size + b*size^2  (R fastest)
                    cube_idx = r_idx + g_idx * size + b_idx * size * size
                    row = table[cube_idx]
                    f.write(f'{r_idx} {g_idx} {b_idx} '
                            f'{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n')


def write_csp(path, table, size, title=None):
    """CineSpace .csp  —  CSPLUTV100 3D LUT with identity pre-LUT.
    Float values, R varies fastest (same order as .cube)."""
    with open(path, 'w') as f:
        f.write('CSPLUTV100\n')
        f.write('3D\n')
        f.write('\n')
        f.write('BEGIN METADATA\n')
        if title:
            f.write(f'{title}\n')
        f.write('END METADATA\n')
        f.write('\n')
        # Identity 2-point pre-LUT for each channel (R, G, B)
        for _ in range(3):
            f.write('2\n')
            f.write('0.000000 1.000000\n')
            f.write('0.000000 1.000000\n')
            f.write('\n')
        f.write(f'{size} {size} {size}\n')
        for row in table:
            f.write(f'{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n')


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    log = Signal(str, str)      # message, level (info/ok/warn/error/dim)
    progress = Signal(int, int) # current, total
    finished = Signal(int, int, int)  # converted, skipped, failed


class Worker(QThread):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.signals = WorkerSignals()
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        cfg = self.config
        root = Path(cfg['input_dir'])
        out_dir = Path(cfg['output_dir'])
        cube_size = cfg['cube_size']
        mirror_subdirs = cfg['mirror_subdirs']
        append_size = cfg['append_size']
        overwrite = cfg['overwrite']
        dry_run = cfg['dry_run']
        write_cube_out  = cfg['write_cube']
        write_vlt_out   = cfg['write_vlt']
        write_3dl_out   = cfg['write_3dl']
        write_spi3d_out = cfg['write_spi3d']
        write_csp_out   = cfg['write_csp']
        validate = cfg['validate']

        # Map each flag to (extension, writer_function)
        format_map = []
        if write_cube_out:  format_map.append(('.cube',  write_cube))
        if write_vlt_out:   format_map.append(('.vlt',   write_vlt))
        if write_3dl_out:   format_map.append(('.3dl',   write_3dl))
        if write_spi3d_out: format_map.append(('.spi3d', write_spi3d))
        if write_csp_out:   format_map.append(('.csp',   write_csp))

        self.signals.log.emit("Scanning for .cube files…", "dim")
        cubes = [
            p for p in root.rglob('*.cube')
            if not any(part.startswith('out_') for part in p.relative_to(root).parts[:-1])
        ]

        if not cubes:
            self.signals.log.emit("No .cube files found.", "warn")
            self.signals.finished.emit(0, 0, 0)
            return

        self.signals.log.emit(f"Found {len(cubes)} .cube file(s).", "info")
        self.signals.progress.emit(0, len(cubes))

        converted = skipped = failed = 0

        for idx, f in enumerate(cubes):
            if self._abort:
                self.signals.log.emit("Aborted by user.", "warn")
                break

            try:
                rel_dir = os.path.relpath(f.parent, root)
                target_dir = (out_dir / rel_dir) if mirror_subdirs and rel_dir != '.' else out_dir
                base_name = f.stem
                suffix = f"_{cube_size}" if append_size else ""

                out_paths = [
                    (target_dir / f"{base_name}{suffix}{ext}", writer)
                    for ext, writer in format_map
                ]

                all_exist = all(p.exists() for p, _ in out_paths)
                if all_exist and not overwrite:
                    for p, _ in out_paths:
                        self.signals.log.emit(f"SKIP  (exists)  {p.name}", "dim")
                    skipped += 1
                    self.signals.progress.emit(idx + 1, len(cubes))
                    continue

                try:
                    in_size, domain_min, domain_max, table = parse_cube(str(f))
                except Exception as e:
                    if validate:
                        self.signals.log.emit(f"SKIP  (invalid)  {f.name}  —  {e}", "warn")
                        skipped += 1
                        self.signals.progress.emit(idx + 1, len(cubes))
                        continue
                    else:
                        raise

                if dry_run:
                    action = "copy (same size)" if in_size == cube_size else f"resample {in_size}→{cube_size}"
                    for p, _ in out_paths:
                        self.signals.log.emit(f"DRYRUN  [{action}]  {f.name}  →  {p.name}", "info")
                    converted += 1
                    self.signals.progress.emit(idx + 1, len(cubes))
                    continue

                target_dir.mkdir(parents=True, exist_ok=True)

                if in_size == cube_size:
                    out_table = table
                    action_label = "copy, same size"
                else:
                    out_table = resample_cube(table, in_size, cube_size)
                    action_label = f"{in_size}→{cube_size}"

                for out_path, writer in out_paths:
                    if out_path.suffix == '.cube':
                        writer(str(out_path), out_table, cube_size, domain_min, domain_max, title=base_name)
                    else:
                        writer(str(out_path), out_table, cube_size, title=base_name)
                    self.signals.log.emit(f"OK  ({action_label})  {out_path.name}", "ok")
                converted += 1

            except Exception as e:
                self.signals.log.emit(f"FAIL  {f.name}  —  {e}", "error")
                self.signals.log.emit(traceback.format_exc(), "error")
                failed += 1

            self.signals.progress.emit(idx + 1, len(cubes))

        self.signals.finished.emit(converted, skipped, failed)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

DARK_STYLE = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #1e1e2e;
}

QGroupBox {
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
    color: #89b4fa;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}

QLineEdit {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 6px 10px;
    color: #cdd6f4;
    selection-background-color: #89b4fa;
}

QLineEdit:focus {
    border-color: #89b4fa;
}

QSpinBox {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 5px 8px;
    color: #cdd6f4;
    min-width: 70px;
}

QSpinBox:focus {
    border-color: #89b4fa;
}

QSpinBox::up-button, QSpinBox::down-button {
    background-color: #313244;
    border: none;
    width: 18px;
    border-radius: 3px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #45475a;
}

QPushButton {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 7px 16px;
    color: #cdd6f4;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}

QPushButton:pressed {
    background-color: #1e1e2e;
}

QPushButton#runBtn {
    background-color: #89b4fa;
    color: #1e1e2e;
    font-weight: 700;
    font-size: 14px;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
}

QPushButton#runBtn:hover {
    background-color: #b4befe;
}

QPushButton#runBtn:disabled {
    background-color: #313244;
    color: #585b70;
}

QPushButton#abortBtn {
    background-color: #f38ba8;
    color: #1e1e2e;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
}

QPushButton#abortBtn:hover {
    background-color: #fab387;
}

QPushButton#scanBtn {
    background-color: #a6e3a1;
    color: #1e1e2e;
    font-weight: 600;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
}

QPushButton#scanBtn:hover {
    background-color: #94e2d5;
}

QRadioButton {
    spacing: 8px;
    color: #cdd6f4;
}

QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 2px solid #585b70;
    background: #181825;
}

QRadioButton::indicator:checked {
    background: #89b4fa;
    border-color: #89b4fa;
}

QRadioButton::indicator:hover {
    border-color: #89b4fa;
}

QCheckBox {
    spacing: 8px;
    color: #cdd6f4;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 2px solid #585b70;
    background: #181825;
}

QCheckBox::indicator:checked {
    background: #a6e3a1;
    border-color: #a6e3a1;
    image: none;
}

QCheckBox::indicator:hover {
    border-color: #a6e3a1;
}

QTextEdit {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 8px;
    color: #cdd6f4;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    selection-background-color: #313244;
}

QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    height: 14px;
    text-align: center;
    color: #cdd6f4;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 5px;
}

QScrollBar:vertical {
    background: #181825;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #45475a;
    border-radius: 5px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background: #585b70;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QLabel#titleLabel {
    font-size: 20px;
    font-weight: 700;
    color: #cdd6f4;
}

QLabel#subtitleLabel {
    font-size: 12px;
    color: #6c7086;
}

QLabel#statsLabel {
    font-size: 12px;
    color: #a6adc8;
    padding: 4px 0;
}

QFrame#divider {
    background-color: #313244;
    max-height: 1px;
    min-height: 1px;
}
"""


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LUT Resampler")
        self.setMinimumSize(860, 700)
        self.resize(980, 780)
        self.worker = None
        self._cubes_found = []

        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._refresh_output_dir()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        # ── Header ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("LUT Resampler")
        lbl_title.setObjectName("titleLabel")
        lbl_subtitle = QLabel("Batch-resample 3D .cube files  ·  export to .cube / .vlt / .3dl / .spi3d / .csp")
        lbl_subtitle.setObjectName("subtitleLabel")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_subtitle)
        header.addLayout(title_col)
        header.addStretch()
        outer.addLayout(header)

        divider = QFrame()
        divider.setObjectName("divider")
        outer.addWidget(divider)

        # ── Splitter: settings (top) + log (bottom) ──────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("QSplitter::handle { background: #313244; border-radius: 3px; }")
        outer.addWidget(splitter)

        # ── Settings panel ──────────────────────────────────────────────
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(12)

        # Input / output paths
        io_group = QGroupBox("Paths")
        io_grid = QGridLayout(io_group)
        io_grid.setColumnStretch(1, 1)
        io_grid.setSpacing(8)

        io_grid.addWidget(QLabel("Input folder"), 0, 0)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Folder containing .cube files…")
        self.input_edit.setText(os.getcwd())
        self.input_edit.textChanged.connect(self._refresh_output_dir)
        io_grid.addWidget(self.input_edit, 0, 1)
        btn_input = QPushButton("Browse…")
        btn_input.clicked.connect(self._browse_input)
        io_grid.addWidget(btn_input, 0, 2)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setObjectName("scanBtn")
        self.scan_btn.setFixedWidth(70)
        self.scan_btn.clicked.connect(self._scan)
        io_grid.addWidget(self.scan_btn, 0, 3)

        io_grid.addWidget(QLabel("Output folder"), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Destination folder…")
        io_grid.addWidget(self.output_edit, 1, 1)
        btn_output = QPushButton("Browse…")
        btn_output.clicked.connect(self._browse_output)
        io_grid.addWidget(btn_output, 1, 2)

        self.scan_label = QLabel("No scan yet")
        self.scan_label.setObjectName("statsLabel")
        io_grid.addWidget(self.scan_label, 2, 0, 1, 4)

        settings_layout.addWidget(io_group)

        # Options row
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)

        # Cube size
        size_group = QGroupBox("Output Cube Size")
        size_lay = QVBoxLayout(size_group)
        size_lay.setSpacing(6)
        sz_row = QHBoxLayout()
        self.size_spin = QSpinBox()
        self.size_spin.setRange(2, 256)
        self.size_spin.setValue(33)
        self.size_spin.setToolTip("Grid resolution of the output LUT.\n17 = small/fast  33 = standard  64 = high quality")
        sz_row.addWidget(self.size_spin)
        sz_row.addStretch()
        size_lay.addLayout(sz_row)
        preset_row = QHBoxLayout()
        for label, val in (("17", 17), ("33", 33), ("64", 64)):
            b = QPushButton(label)
            b.setFixedWidth(56)
            b.setMinimumHeight(28)
            b.clicked.connect(lambda _, v=val: self.size_spin.setValue(v))
            preset_row.addWidget(b)
        preset_row.addStretch()
        size_lay.addLayout(preset_row)
        opts_row.addWidget(size_group)

        # Folder structure
        struct_group = QGroupBox("Folder Structure")
        struct_lay = QVBoxLayout(struct_group)
        self._struct_grp = QButtonGroup(self)
        self.radio_mirror = QRadioButton("Mirror input subfolders")
        self.radio_flat   = QRadioButton("Flat — all in one folder")
        self.radio_flat.setChecked(True)
        self._struct_grp.addButton(self.radio_mirror, 1)
        self._struct_grp.addButton(self.radio_flat, 2)
        struct_lay.addWidget(self.radio_mirror)
        struct_lay.addWidget(self.radio_flat)
        opts_row.addWidget(struct_group)

        # Naming
        naming_group = QGroupBox("File Naming")
        naming_lay = QVBoxLayout(naming_group)
        self._naming_grp = QButtonGroup(self)
        self.radio_append = QRadioButton("Append _{size}  (e.g. Film_33.cube)")
        self.radio_keepname = QRadioButton("Keep base name  (e.g. Film.cube)")
        self.radio_keepname.setChecked(True)
        self._naming_grp.addButton(self.radio_append, 1)
        self._naming_grp.addButton(self.radio_keepname, 2)
        naming_lay.addWidget(self.radio_append)
        naming_lay.addWidget(self.radio_keepname)
        opts_row.addWidget(naming_group)

        settings_layout.addLayout(opts_row)

        # Flags row
        flags_row = QHBoxLayout()
        flags_row.setSpacing(12)

        # Format checkboxes (multiple can be selected)
        fmt_group = QGroupBox("Output Formats  (select any combination)")
        fmt_lay = QVBoxLayout(fmt_group)
        fmt_lay.setSpacing(5)
        self.chk_fmt_cube  = QCheckBox(".cube   — standard (DaVinci Resolve, Premiere, Lightroom…)")
        self.chk_fmt_vlt   = QCheckBox(".vlt    — Panasonic (Lumix cameras, Lumix Tether)")
        self.chk_fmt_3dl   = QCheckBox(".3dl    — Autodesk Flame / Lustre  (12-bit integers)")
        self.chk_fmt_spi3d = QCheckBox(".spi3d  — Sony/OCIO  (Nuke, Houdini, VFX pipelines)")
        self.chk_fmt_csp   = QCheckBox(".csp    — CineSpace  (Scratch, FilmLight Baselight…)")
        self.chk_fmt_cube.setChecked(True)
        for chk in (self.chk_fmt_cube, self.chk_fmt_vlt, self.chk_fmt_3dl,
                    self.chk_fmt_spi3d, self.chk_fmt_csp):
            fmt_lay.addWidget(chk)
        flags_row.addWidget(fmt_group, 2)

        # Behaviour flags
        flags_group = QGroupBox("Options")
        flags_lay = QVBoxLayout(flags_group)
        flags_lay.setSpacing(8)
        self.chk_overwrite = QCheckBox("Overwrite existing outputs")
        self.chk_dryrun    = QCheckBox("Dry run  (preview only, no writes)")
        self.chk_validate  = QCheckBox("Validate input LUTs before converting")
        self.chk_validate.setChecked(True)
        flags_lay.addWidget(self.chk_overwrite)
        flags_lay.addWidget(self.chk_dryrun)
        flags_lay.addWidget(self.chk_validate)
        flags_lay.addStretch()
        flags_row.addWidget(flags_group, 1)

        settings_layout.addLayout(flags_row)
        settings_layout.addStretch()

        splitter.addWidget(settings_widget)

        # ── Log panel ────────────────────────────────────────────────────
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_lbl = QLabel("Output Log")
        log_lbl.setStyleSheet("font-weight: 600; color: #89b4fa;")
        log_header.addWidget(log_lbl)
        log_header.addStretch()
        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(90)
        btn_clear.setMinimumHeight(28)
        btn_clear.clicked.connect(self._clear_log)
        log_header.addWidget(btn_clear)
        log_layout.addLayout(log_header)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m  (%p%)")
        log_layout.addWidget(self.progress_bar)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("statsLabel")
        log_layout.addWidget(self.stats_label)

        # Run / Abort buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("runBtn")
        self.run_btn.setFixedWidth(120)
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn)

        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setObjectName("abortBtn")
        self.abort_btn.setFixedWidth(120)
        self.abort_btn.setVisible(False)
        self.abort_btn.clicked.connect(self._abort)
        btn_row.addWidget(self.abort_btn)
        log_layout.addLayout(btn_row)

        splitter.addWidget(log_widget)
        splitter.setSizes([340, 340])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_output_dir(self):
        root = self.input_edit.text().strip()
        size = self.size_spin.value()
        if root:
            self.output_edit.setText(os.path.join(root, f"out_{size}"))

    def _browse_input(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder", self.input_edit.text())
        if folder:
            self.input_edit.setText(folder)
            self._refresh_output_dir()
            self._scan()

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _scan(self):
        root = self.input_edit.text().strip()
        if not root or not os.path.isdir(root):
            self.scan_label.setText("⚠  Input folder not found.")
            self._cubes_found = []
            return
        self.scan_label.setText("Scanning…")
        QApplication.processEvents()
        root_path = Path(root)
        self._cubes_found = [
            p for p in root_path.rglob('*.cube')
            if not any(part.startswith('out_') for part in p.relative_to(root_path).parts[:-1])
        ]
        count = len(self._cubes_found)
        if count == 0:
            self.scan_label.setText("No .cube files found in this folder.")
        else:
            self.scan_label.setText(f"{count} .cube file{'s' if count != 1 else ''} found")

    def _clear_log(self):
        self.log_edit.clear()
        self.stats_label.setText("")

    def _append_log(self, message, level="info"):
        colors = {
            "ok":    "#a6e3a1",
            "info":  "#89b4fa",
            "warn":  "#f9e2af",
            "error": "#f38ba8",
            "dim":   "#585b70",
        }
        color = colors.get(level, "#cdd6f4")
        escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log_edit.append(f'<span style="color:{color};">{escaped}</span>')
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------
    # Run / Abort
    # ------------------------------------------------------------------

    def _run(self):
        input_dir = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()

        if not input_dir or not os.path.isdir(input_dir):
            self._append_log("Input folder does not exist.", "error")
            return
        if not output_dir:
            self._append_log("Output folder path is empty.", "error")
            return

        write_cube  = self.chk_fmt_cube.isChecked()
        write_vlt   = self.chk_fmt_vlt.isChecked()
        write_3dl   = self.chk_fmt_3dl.isChecked()
        write_spi3d = self.chk_fmt_spi3d.isChecked()
        write_csp   = self.chk_fmt_csp.isChecked()

        if not any((write_cube, write_vlt, write_3dl, write_spi3d, write_csp)):
            self._append_log("Select at least one output format.", "error")
            return

        config = {
            'input_dir':    input_dir,
            'output_dir':   output_dir,
            'cube_size':    self.size_spin.value(),
            'mirror_subdirs': self.radio_mirror.isChecked(),
            'append_size':  self.radio_append.isChecked(),
            'overwrite':    self.chk_overwrite.isChecked(),
            'dry_run':      self.chk_dryrun.isChecked(),
            'write_cube':   write_cube,
            'write_vlt':    write_vlt,
            'write_3dl':    write_3dl,
            'write_spi3d':  write_spi3d,
            'write_csp':    write_csp,
            'validate':     self.chk_validate.isChecked(),
        }

        fmts = [ext for flag, ext in (
            (write_cube,  '.cube'), (write_vlt,   '.vlt'),
            (write_3dl,   '.3dl'),  (write_spi3d, '.spi3d'), (write_csp, '.csp')
        ) if flag]
        dry = " (DRY RUN)" if config['dry_run'] else ""
        self._append_log(
            f"Starting{dry} — size {config['cube_size']}  ·  formats: {' '.join(fmts)}  ·  {input_dir}",
            "info"
        )

        self.run_btn.setVisible(False)
        self.abort_btn.setVisible(True)
        self.stats_label.setText("")
        self.progress_bar.setValue(0)

        self.worker = Worker(config)
        self.worker.signals.log.connect(self._append_log)
        self.worker.signals.progress.connect(self._on_progress)
        self.worker.signals.finished.connect(self._on_finished)
        self.worker.start()

    def _abort(self):
        if self.worker:
            self.worker.abort()
        self.abort_btn.setEnabled(False)

    def _on_progress(self, current, total):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

    def _on_finished(self, converted, skipped, failed):
        self.run_btn.setVisible(True)
        self.abort_btn.setVisible(False)
        self.abort_btn.setEnabled(True)

        parts = []
        if converted: parts.append(f"✓ {converted} converted")
        if skipped:   parts.append(f"– {skipped} skipped")
        if failed:    parts.append(f"✗ {failed} failed")
        summary = "  ·  ".join(parts) if parts else "Nothing to do."
        self.stats_label.setText(summary)

        level = "error" if failed else ("warn" if skipped and not converted else "ok")
        self._append_log(f"Done.  {summary}", level)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

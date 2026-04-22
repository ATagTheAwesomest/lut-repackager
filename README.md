# LUT Resampler

A desktop app that takes your `.cube` LUT files and converts them to any size or format you need — with a clean, dark GUI. No subscription, no cloud, no fuss.

---

## What even is a LUT?

A **LUT** (Look-Up Table) is a file that tells your software how to remap colours. Think of it as a colour "recipe": every shade of red, green, and blue gets mathematically shifted to a new value. LUTs are used everywhere:

- Applying a film look to footage in DaVinci Resolve or Premiere
- Matching the colour science of a specific camera
- Baking in a creative grade for delivery
- Monitoring on set — cinematographers load LUTs onto monitors so what they see resembles the final grade

The most common LUT format is `.cube`. The problem is that `.cube` files come in different **grid sizes** (17, 33, 65, etc.), and different apps or hardware devices only accept specific sizes or file formats. That's what this tool fixes.

---

## What does this tool do?

1. **Scans** a folder (and all its subfolders) for `.cube` files
2. **Resamples** each LUT to whatever grid size you pick — using high-quality trilinear interpolation
3. **Exports** the result in one or more formats, ready to drop into your app or device

It works entirely on your machine. Nothing leaves your computer.

---

## Requirements

You need Python 3.8 or newer, plus three packages. If you already have Python installed, open a terminal and run:

```
pip install PySide6 numpy scipy
```

That's it — no virtual environment needed.

> **Don't have Python?**  
> Download it from [python.org](https://www.python.org/downloads/). During installation, tick **"Add Python to PATH"**.

---

## How to run it

1. Open a terminal / command prompt in this folder
2. Run:

```
python batch_resample_luts_gui.py
```

A window will appear. That's the whole app.

---

## Using the app — step by step

### 1. Choose your input folder

Click **Browse…** next to *Input folder* and navigate to the folder that contains your `.cube` files. Subfolders are included automatically.

After selecting a folder, click **Scan** (or it may scan automatically). The app will tell you how many `.cube` files it found.

### 2. Choose where to save the output

The output folder defaults to a subfolder called `out_33` (or whatever size you've chosen) inside your input folder. You can change it to anywhere you like.

### 3. Pick your output cube size

The **cube size** (also called grid size) controls the quality and file size of the output LUT.

| Size | What it means |
|------|--------------|
| **17** | Small, fast. Some hardware LUT boxes use this. Lower colour accuracy. |
| **33** | Industry standard. The sweet spot — used by most professional apps. **Start here.** |
| **64** | High precision. Larger files. Use if you're hitting colour accuracy problems with 33. |
| **Custom** | Type any value between 2 and 256. |

Use the **17 / 33 / 64** preset buttons for quick selection.

> **Does resampling affect quality?**  
> Slightly. Going from a 65-point source down to 33 is lossy, in the same way resizing a photo is lossy. But 33 is more than enough for most colour work — the human eye can't distinguish differences that small. Going *up* in size (e.g. 17 → 33) does not add information; it just makes the LUT compatible with apps that require a larger grid.

### 4. Folder structure

- **Mirror input subfolders** — the output folder will have the same sub-folder layout as your input. Good if you have LUTs organised by project, camera, etc.
- **Flat — all in one folder** — every converted file lands directly in the output folder, regardless of where it came from. Simpler, but watch for name conflicts if different subfolders have files with the same name.

### 5. File naming

- **Append _{size}** — adds the cube size to the filename: `FilmLook_33.cube`. Useful if you're building a library at multiple sizes.
- **Keep base name** — the output file has the same name as the input: `FilmLook.cube`. Cleaner, but avoid saving to the same folder as the originals.

### 6. Output formats

Tick any combination of formats. All selected formats will be written for every input file.

| Format | Extension | Used in |
|--------|-----------|---------|
| **Standard cube** | `.cube` | DaVinci Resolve, Adobe Premiere, Lightroom, Final Cut Pro, most NLEs and monitoring apps |
| **Panasonic VLT** | `.vlt` | Lumix cameras (S5, S5II, GH6, etc.), Lumix Tether software — for in-camera LUT preview |
| **Autodesk 3DL** | `.3dl` | Autodesk Flame, Lustre, and older Autodesk colour tools — common in high-end post houses |
| **OCIO / Sony SPI3D** | `.spi3d` | OpenColorIO pipelines, Foundry Nuke, Houdini, VFX facilities using OCIO config files |
| **CineSpace CSP** | `.csp` | FilmLight Baselight, Assimilate Scratch, and any other app supporting the CineSpace format |

> **Which format should I use?**  
> - For most video work: `.cube`  
> - For Lumix cameras: `.vlt`  
> - For Flame/Lustre: `.3dl`  
> - For Nuke/VFX pipelines: `.spi3d`  
> - If an app says it supports "CineSpace": `.csp`  
> When in doubt, `.cube` works almost everywhere.

### 7. Options

- **Overwrite existing outputs** — if turned off (the default), the app skips any file that already exists in the output folder. This is safe for re-running without redoing work. Turn it on if you want to force a fresh conversion.
- **Dry run** — the app shows you *exactly* what it would do without writing any files. Use this to preview your settings before committing.
- **Validate input LUTs** — reads and checks each `.cube` file before converting. Invalid or corrupt files are skipped with a warning instead of crashing the whole batch.

### 8. Hit Run

The progress bar tracks each file. The log panel shows what's happening in real time:

- **Green** = file converted successfully
- **Blue** = info / status
- **Yellow** = skipped (file exists, or input was invalid)
- **Red** = error (shown with details)
- **Grey** = skipped quietly (e.g. file already exists)

When it finishes, a summary shows how many files were converted, skipped, and failed.

If something goes wrong mid-batch, click **Abort** to stop cleanly.

---

## Format technical details

For those who want to know what's under the hood:

### `.cube` (Adobe/Resolve standard)
Text file. Float values (0.0–1.0). R varies fastest in the data table. Supported by virtually every professional colour app released after 2012.

### `.vlt` (Panasonic)
Text file. 12-bit integer values (0–4095). Includes a `LUT_3D_SIZE` header. Specific to Panasonic's camera ecosystem.

### `.3dl` (Autodesk Flame / Lustre)
Text file. First line is a row of **mesh breakpoints** — N evenly-spaced integer values from 0 to 4095, space-separated. These tell the app the input grid positions. The rest of the file is the 3D LUT data as 12-bit integer RGB triplets, R varies fastest. Autodesk apps call this the "shaper + cube" format.

### `.spi3d` (Sony Pictures Imageworks / OpenColorIO)
Text file. Header: `SPILUT 1.0`, then `3 3`, then `SIZE SIZE SIZE`. Each data line has the format `r_index g_index b_index R G B` where R, G, B are floats. Unlike `.cube`, the indices are written explicitly and **blue varies fastest** in the file ordering.

### `.csp` (CineSpace)
Text file. Header: `CSPLUTV100`, then `3D`. Includes a metadata block and an optional 1D "pre-LUT" section per channel (written here as a 2-point identity). Then `SIZE SIZE SIZE` followed by float RGB triplets, R varies fastest — same ordering as `.cube`.

---

## Common questions

**My LUT box only accepts size 17 / 33 / 65 — which do I pick?**  
Whatever size your device specifies. Most hardware LUT processors (Teradek COLR, AJA, etc.) specify this in their manual. 33 is the most common.

**The output looks different from the original — did something go wrong?**  
Usually no. If you're going from a large source (e.g. 65-point) to a smaller output (17-point), some colour precision is lost — this is expected and unavoidable. For critical work, keep the output size at 33 or higher.

**Can I run this without the GUI (terminal only)?**  
Yes — the original terminal version is `batch_resample_luts.py` in the same folder. Run it with `python batch_resample_luts.py` and follow the prompts.

**Can I process a single file instead of a whole folder?**  
Put the single file in its own empty folder and point the app there.

**The app says "No .cube files found" but I can see them.**  
The app automatically ignores folders starting with `out_` (to avoid re-processing its own output). Move your files or change the input/output folder paths so they don't overlap.

**Something crashed — how do I report it?**  
Copy the red text from the log panel — it includes the full error message and traceback, which is everything needed to diagnose the issue.

---

## Folder layout after a run

```
my-luts/
├── FilmLook.cube           ← original input (untouched)
├── SomeCameraLog.cube      ← original input (untouched)
└── out_33/
    ├── FilmLook.cube       ← resampled to 33-point
    ├── FilmLook.vlt        ← same data, Panasonic format
    ├── FilmLook.3dl        ← same data, Autodesk format
    ├── SomeCameraLog.cube
    ├── SomeCameraLog.vlt
    └── SomeCameraLog.3dl
```

---

## Dependencies

| Package | Why it's needed |
|---------|----------------|
| `numpy` | Fast array maths — holds the entire LUT in memory as a numerical grid |
| `scipy` | `RegularGridInterpolator` — the engine that resamples the 3D grid accurately |
| `PySide6` | The GUI framework (Qt 6 bindings for Python) |

All three are installed with a single `pip install PySide6 numpy scipy` command.

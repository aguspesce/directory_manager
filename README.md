# directory_manager

A collection of Python utilities to compare directories, find duplicate files, and
interactively manage duplicate images.

## Scripts

### `directory_comparator.py`

Compares two directories (source and target) and reports which files:

- exist only in the source,
- exist only in the target,
- are identical,
- or are different.

The comparison first uses file size as a fast filter, then optionally SHA-256 hashing
(or modification time if hashing is disabled) to confirm whether two files are actually
the same.

**Usage:**

```bash
python directory_comparator.py ./source_dir ./target_dir
```

**Options:**

| Option              | Description                                                                |
| ------------------- | -------------------------------------------------------------------------- |
| `--no-hash`         | Skip SHA-256 hashing; compare using size and modification time only.       |
| `--exclude PATTERN` | Exclude files matching one or more glob patterns (e.g. `.png`, `"img/*"`). |
| `--log-file PATH`   | Also save logs to a file, in addition to the console.                      |

---

### `duplicate_finder.py`

Finds duplicate files across one or more directories using SHA-256 hashing.
Files are first grouped by size, and only files with matching sizes are hashed, using
multiple threads in parallel to speed up the process.
Results can be saved to a JSON report, which can later be used with
`duplicate_image_manager.py`.

**Usage:**

```bash
python duplicate_finder.py dir_path
```

**Options:**

| Option                       | Description                                                 |
| ---------------------------- | ----------------------------------------------------------- |
| `--extensions EXT [EXT ...]` | Filter by file extensions (e.g. `.txt .png .jpg`).          |
| `--report PATH`              | Save the results to a JSON report.                          |
| `--workers N`                | Number of threads used for hashing (defaults to CPU count). |
| `--log-file PATH`            | Also save logs to a file, in addition to the console.       |

---

### `duplicate_image_manager.py`

Interactively reviews and manages duplicate images detected in a JSON report produced
by `duplicate_finder.py`.
For each group of duplicate images, it displays metadata (resolution, size, EXIF date,
modification date), generates a side-by-side montage for visual comparison, and opens
it with the system's default image viewer.
You can then choose which image to keep; the rest are moved to a quarantine
folder (`~/Duplicate_Quarantine`) instead of being deleted outright.

**Requires:** [Pillow](https://pypi.org/project/Pillow/) (`PIL`).

**Usage:**

```bash
python duplicate_image_manager.py report.json
```

**Options:**

| Option            | Description                                                      |
| ----------------- | ---------------------------------------------------------------- |
| `--log-file PATH` | Also save the session log to a file, in addition to the console. |

## Requirements

- Python 3.10+ (uses syntax such as `str | Path` and `dataclass(slots=True)`)
- [Pillow](https://pypi.org/project/Pillow/) (only needed for `duplicate_image_manager.py`)

```bash
pip install pillow
```

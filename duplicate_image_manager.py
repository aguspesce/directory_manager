"""
Review and manage duplicate images from a JSON report produced by duplicate_finder.
"""

import argparse
import gc
import json
import logging
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("duplicate_image_manager")

# CONSTANTS
QUARANTINE_DIR = Path.home() / "Duplicate_Quarantine"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LABEL_HEIGHT = 30
CANVAS_MAX_WIDTH = 1600


# IMAGE HELPERS
def is_image(file_path: Path) -> bool:
    return file_path.suffix.lower() in IMAGE_EXTENSIONS


def get_size_mb(file_path: Path) -> float:
    return round(file_path.stat().st_size / (1024 * 1024), 2)


def get_modification_date(file_path: Path) -> str:
    ts = file_path.stat().st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def get_exif_date(file_path: Path) -> str:
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            for tag in (36867, 36868, 306):
                if tag in exif:
                    return str(exif[tag])
    except Exception:
        pass
    return "No EXIF"


def get_resolution(file_path: Path) -> tuple[int, int]:
    try:
        with Image.open(file_path) as img:
            return img.size
    except Exception:
        return (0, 0)


def log_image_info(idx: int, file_path: Path) -> None:
    """Log metadata for a single image."""
    w, h = get_resolution(file_path)

    logger.info("=" * 70)
    logger.info(f"[{idx}]  {file_path.name}")
    logger.info(f"Path        : {file_path}")
    logger.info(f"Resolution  : {w} x {h}")
    logger.info(f"Size        : {get_size_mb(file_path)} MB")
    logger.info(f"EXIF Date   : {get_exif_date(file_path)}")
    logger.info(f"Modified    : {get_modification_date(file_path)}")


def get_label_font() -> ImageFont.FreeTypeFont:
    """Return the best available font for montage labels."""
    attempts = [
        lambda: ImageFont.truetype("DejaVuSans-Bold.ttf", 18),
        lambda: ImageFont.truetype("arial.ttf", 18),
        lambda: ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18
        ),
        lambda: ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18),
        lambda: ImageFont.load_default(),
    ]

    for attempt in attempts:
        try:
            return attempt()
        except Exception:
            pass

    return ImageFont.load_default()


# MONTAGE
def create_montage(image_paths: list[Path]) -> Optional[Path]:
    """
    Build a side-by-side comparison image from a list of image paths.

    Args:
        image_paths: List of image file paths to include.

    Returns:
        Path to the saved montage file, or None if it could not be created.
    """
    n = len(image_paths)
    cell_width = CANVAS_MAX_WIDTH // n
    cell_height = cell_width + LABEL_HEIGHT

    thumbnails: list[Image.Image] = []
    canvas: Optional[Image.Image] = None
    font = get_label_font()

    try:
        for idx, file_path in enumerate(image_paths):
            try:
                with Image.open(file_path) as original:
                    img = original.convert("RGB")

                img.thumbnail((cell_width, cell_width), Image.Resampling.LANCZOS)

                cell = Image.new("RGB", (cell_width, cell_height), (20, 20, 20))
                x = (cell_width - img.width) // 2
                y = (cell_width - img.height) // 2
                cell.paste(img, (x, y))

                label = Image.new("RGB", (cell_width, LABEL_HEIGHT), (40, 40, 180))
                draw = ImageDraw.Draw(label)
                draw.text(
                    (6, 5), f"[{idx}] {file_path.name}"[:40], fill="white", font=font
                )

                cell.paste(label, (0, cell_width))
                thumbnails.append(cell)

            except Exception as exc:
                logger.warning(f"Error loading {file_path}: {exc}")

        if not thumbnails:
            return None

        canvas = Image.new(
            "RGB", (cell_width * len(thumbnails), cell_height), (10, 10, 10)
        )

        for i, thumbnail in enumerate(thumbnails):
            canvas.paste(thumbnail, (i * cell_width, 0))

        output_path = Path(tempfile.gettempdir()) / "duplicate_comparison.jpg"
        canvas.save(output_path, quality=92, optimize=True)

        return output_path

    finally:
        for thumbnail in thumbnails:
            try:
                thumbnail.close()
            except Exception:
                pass

        if canvas:
            try:
                canvas.close()
            except Exception:
                pass

        gc.collect()


# VIEWER
def open_image_viewer(file_path: Path) -> Optional[subprocess.Popen]:
    """
    Open an image in the system default viewer.

    Args:
        file_path: Path to the image file.

    Returns:
        Popen process handle, or None if no viewer was found.
    """
    path_str = str(file_path)
    system = platform.system()

    try:
        if system == "Darwin":
            return subprocess.Popen(["open", path_str])

        if system == "Windows":
            return subprocess.Popen(["start", "", path_str], shell=True)

        for viewer in ["eog", "feh", "xdg-open"]:
            if shutil.which(viewer):
                return subprocess.Popen([viewer, path_str])

        logger.warning("No image viewer found.")
        return None

    except Exception as exc:
        logger.warning(f"Viewer error: {exc}")
        return None


# QUARANTINE
def move_to_quarantine(file_path: Path) -> None:
    """
    Move a file to the quarantine directory, avoiding name collisions.

    Args:
        file_path: Path to the file to quarantine.
    """
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

    dest = QUARANTINE_DIR / file_path.name
    counter = 1

    while dest.exists():
        dest = QUARANTINE_DIR / f"{counter}_{file_path.name}"
        counter += 1

    shutil.move(str(file_path), str(dest))
    logger.info(f"Moved → {dest}")


# GROUP PROCESSING
def ask_user_choice(n: int) -> int | str:
    """
    Prompt the user to choose an action for a duplicate group.

    Args:
        n: Number of images in the group.

    Returns:
        An integer index to keep, or one of 'a' (all), 's' (skip), 'q' (quit).
    """
    while True:
        choice = input("> ").strip().lower()

        if choice in ("a", "s", "q"):
            return choice

        try:
            val = int(choice)
            if 0 <= val < n:
                return val
            logger.warning(f"Enter a number between 0 and {n - 1}.")
        except ValueError:
            logger.warning("Invalid input — use a number, 'a', 's', or 'q'.")


def process_group(number: int, file_paths: list[str]) -> None:
    """
    Display a duplicate group and prompt the user for an action.

    Args:
        number: Group number (for display).
        file_paths: List of file paths in the group.
    """
    existing = [Path(f) for f in file_paths if Path(f).exists()]
    images = [f for f in existing if is_image(f)]

    if len(images) < 2:
        return

    logger.info("#" * 80)
    logger.info(f"GROUP {number} — {len(images)} duplicates")
    logger.info("#" * 80)

    for i, image_path in enumerate(images):
        log_image_info(i, image_path)

    logger.info("Generating montage...")
    montage = create_montage(images)

    viewer = open_image_viewer(montage) if montage else None

    logger.info("\nOptions:")
    logger.info("  0..n → keep one image, move the rest")
    logger.info("  a    → move all to quarantine")
    logger.info("  s    → skip this group")
    logger.info("  q    → quit")

    try:
        choice = ask_user_choice(len(images))

        if choice == "q":
            raise KeyboardInterrupt

        if choice == "s":
            return

        if choice == "a":
            confirm = input("Move ALL to quarantine? (y/n): ").strip().lower()
            if confirm == "y":
                for image_path in images:
                    move_to_quarantine(image_path)
            else:
                logger.info("Cancelled.")
            return

        # Keep one, move the rest
        keep_idx = int(choice)
        to_move = [p for i, p in enumerate(images) if i != keep_idx]

        logger.info(f"Keep : {images[keep_idx]}")
        logger.info("Move :")
        for p in to_move:
            logger.info(f"  - {p}")

        confirm = input("\nConfirm? (y/n): ").strip().lower()

        if confirm != "y":
            logger.info("Cancelled.")
            return

        for image_path in to_move:
            move_to_quarantine(image_path)

    finally:
        if viewer:
            try:
                viewer.terminate()
            except Exception:
                pass

        if montage and montage.exists():
            montage.unlink()

        gc.collect()


# LOGGING SETUP
def setup_logging(log_file: str | None = None) -> None:
    """
    Configure logging handlers.

    Logs always go to the console. Optionally also write to a file
    when log_file is provided.

    Args:
        log_file: Path to the log file. If None, no file is written.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s: %(message)s",
        handlers=handlers,
    )


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review and manage duplicate images from a JSON report.",
        epilog="""
            Examples:

                Review duplicates from a report:
                  %(prog)s report.json

                Save session log to a file:
                  %(prog)s report.json --log-file session.log
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "report",
        help="Path to the JSON report file produced by duplicate_finder.",
    )

    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Save logs to a file. If omitted, logs only go to the console.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    setup_logging(args.log_file)

    report_path = Path(args.report)

    if not report_path.exists():
        logger.error(f"Report file not found: {report_path}")
        return 1

    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups = data.get("duplicates", [])

    logger.info(f"Total groups : {len(groups)}")
    logger.info(f"Quarantine   : {QUARANTINE_DIR}")

    try:
        for i, group in enumerate(groups, start=1):
            process_group(i, group["files"])
    except KeyboardInterrupt:
        logger.info("Interrupted.")

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Find duplicate files across one or more directories using SHA-256 hashing.
"""

import argparse
import hashlib
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

logger = logging.getLogger("duplicate_finder")


class DuplicateFinder:
    """
    A utility class to find duplicate files in one or more directories.

    Attributes:
        chunk_size (int): Number of bytes read per chunk when hashing files.
        workers (int): Number of threads used for parallel hashing.
    """

    def __init__(self, chunk_size: int = 65536, workers: int | None = None):
        self.chunk_size = chunk_size
        self.workers = workers or (os.cpu_count() or 4)

    def find_duplicates(
        self,
        directories: list[str],
        extensions: set[str] | None = None,
    ) -> dict[str, list[Path]]:
        """
        Finds duplicate files across multiple directories.

        The process:
        1. Scan files recursively
        2. Group them by file size
        3. Hash only files with matching sizes
        4. Return groups of identical files

        Args:
            directories (list[str]): list of directories to scan.
            extensions (Optional[Set[str]]): Optional set of allowed file
                extensions (e.g. {".jpg", ".png"}). If None, all files are included.

        Returns:
            Dictionary where:
                - key: SHA-256 hash
                - value: list of duplicate file paths
        """
        file_by_size: dict[int, list[Path]] = defaultdict(list)

        logging.info("Starting duplicate scan: indexing files")

        # File scanning
        for directory in directories:
            for file in Path(directory).rglob("*"):
                if not file.is_file():
                    continue
                if ".git" in file.parts:
                    continue
                if extensions and file.suffix.lower() not in extensions:
                    continue
                try:
                    size = file.stat().st_size
                    file_by_size[size].append(file)
                except OSError as e:
                    logging.warning(f"Cannot access file: {file} | {e}")

        candidate_groups = [g for g in file_by_size.values() if len(g) > 1]
        candidates = sum(len(g) for g in candidate_groups)

        logging.info(
            f"Candidate analysis started: {candidates} files "
            f"queued for hashing using {self.workers} threads"
        )

        # Hashing
        files_by_hash: dict[str, list[Path]] = defaultdict(list)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            future_to_file = {
                executor.submit(self._compute_hash, file): file
                for group in candidate_groups
                for file in group
            }

            processed = 0
            for future in as_completed(future_to_file):
                file = future_to_file[future]
                try:
                    file_hash = future.result()
                except Exception as exc:
                    logging.error(f"Hashing failed: {file} | {exc}")
                    continue
                if file_hash:
                    files_by_hash[file_hash].append(file)
                processed += 1

                if processed % 200 == 0 or processed == candidates:
                    logging.info(
                        f"Progress: {processed}/{candidates} files processed "
                        f"({processed / max(candidates,1):.0%})"
                    )

        return {hash: file for hash, file in files_by_hash.items() if len(file) > 1}

    def _compute_hash(self, filepath: Path) -> Optional[str]:
        """
        Compute the SHA-256 hash of a file.
        The file is read in chunks to avoid loading large files into memory.

        Args:
            filepath: Path to the file to hash.

        Returns:
            The SHA-256 hex digest of the file, or None if the file cannot be read.
        """
        hasher = hashlib.sha256()

        try:
            with filepath.open("rb") as file:
                for chunk in iter(lambda: file.read(self.chunk_size), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()

        except OSError as exc:
            logger.error("Failed hashing %s: %s", filepath, exc)
            return None


def display_duplicates(duplicates: dict[str, list[Path]]) -> None:
    """
    Display duplicate results in a structured summary format using logging.
    """
    logging.info("========== DUPLICATE SCAN SUMMARY ==========")

    total_groups = len(duplicates)
    total_duplicates = sum(len(files) - 1 for files in duplicates.values())

    logging.info("Duplicate groups  : %d", total_groups)
    logging.info("Redundant files   : %d", total_duplicates)

    if not duplicates:
        logging.info("No duplicate files found.")
        return

    logging.info("========== DUPLICATE GROUPS ==========")

    sorted_groups = sorted(
        duplicates.values(),
        key=len,
        reverse=True,
    )

    for idx, files in enumerate(sorted_groups, start=1):
        try:
            size = files[0].stat().st_size
        except OSError:
            size = 0

        logging.info(
            "GROUP #%d -> %d files | size=%s",
            idx,
            len(files),
            format_size(size),
        )

        for i, file in enumerate(files):
            if i == 0:
                logging.info("      [PRIMARY]  %s", file)
            else:
                logging.info("      [DUPLICATE]  %s", file)


def save_report(duplicates: dict[str, list[Path]], filename: str) -> None:
    """
    Saves duplicate file information into a JSON report.

    Args:
        duplicates: Dictionary with the duplicate file groups.
        filename: Output JSON file path.
    """
    report = {
        "total_groups": len(duplicates),
        "total_duplicates": sum(len(files) - 1 for files in duplicates.values()),
        "duplicates": [
            {"hash": hash, "files": [str(file) for file in files]}
            for hash, files in duplicates.items()
        ],
    }

    if not filename.endswith("json"):
        filename = filename + ".json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Report saved to {filename}")


def format_size(size: float) -> str:
    """
    Converts a file size in bytes into a human-readable string.

    Args:
        size: Size in bytes.

    Returns:
        Formatted size (e.g., "10.5 MB").
    """
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def normalize_extensions(extentions: list[str] | None) -> set[str] | None:
    """
    Normalizes a list of file extensions.

    Args:
        exts: List of extensions.

    Returns:
        Normalized extension set or None.
    """
    if not extentions:
        return None
    return {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extentions}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Find duplicate files using hashing."),
        epilog="""
            Examples:

                Find duplicates in a given directory:
                  %(prog)s dir_path

                Find duplicates in a given directory with specific extensions:
                  %(prog)s dir_path --extensions .png .txt

                Find duplicates and save results in a JSON file:
                  %(prog)s dir_path --extensions .png .txt --report report.json

                Save logs to a file:
                  %(prog)s dir_path --log-file scan.log

        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "directories",
        nargs="+",
        help="Directories to scan for duplicates",
    )

    parser.add_argument(
        "--extensions",
        nargs="*",
        default=None,
        help="File extensions to include (e.g. .txt .png .jpg)",
    )

    parser.add_argument(
        "--report",
        default=None,
        help="Path to JSON report file",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of threads for hashing (default: CPU count)",
    )

    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Save logs to a file. If omitted, logs only go to console.",
    )

    return parser.parse_args()


def setup_logging(log_file: str | None = None) -> None:
    """
    Configure logging handlers.

    Logs always go to the console. Optionally also write to a file when log_file is
    provided.

    Args:
        log_file: Path to the log file. If None, no file is written.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        if not log_file.endswith(".log"):
            log_file = log_file + ".log"

        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    args = parse_args()

    setup_logging(args.log_file)

    extensions = normalize_extensions(args.extensions)

    valid_dirs = [dir for dir in args.directories if os.path.isdir(dir)]
    invalid_dirs = set(args.directories) - set(valid_dirs)

    for dir in invalid_dirs:
        logging.warning(f"Skipped invalid directory: {dir}")

    if not valid_dirs:
        logging.error("Aborting execution: no valid directories provided")
        return

    if extensions:
        logging.info(f"Extension filter active: {len(extensions)} types")
        logging.debug(f"Extensions: {sorted(extensions)}")
    else:
        logging.info("No extension filter applied (scanning all files)")

    logging.info(f"Directories ready: {len(valid_dirs)} targets")

    finder = DuplicateFinder(workers=args.workers)
    duplicates = finder.find_duplicates(valid_dirs, extensions)

    display_duplicates(duplicates)

    if args.report:
        save_report(duplicates, args.report)


if __name__ == "__main__":
    main()

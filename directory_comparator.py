"""
Compare two directories and report files that are missing, different, or identical.
"""

import argparse
import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("directory_comparator")

IGNORED_DIRS = {".git"}


# DATA MODELS
@dataclass(slots=True)
class FileMetadata:
    path: str
    full_path: str
    size: int
    mtime: float
    hash: Optional[str] = None


@dataclass(slots=True)
class ComparisonResult:
    only_in_source: list[str]
    only_in_target: list[str]
    same: dict[str, tuple[FileMetadata, FileMetadata]]
    different: dict[str, tuple[FileMetadata, FileMetadata]]


class DirectoryComparator:
    def __init__(
        self,
        source: str | Path,
        target: str | Path,
        compare_by_hash: bool = True,
        exclude_patterns: list[str] | None = None,
    ):
        self.source = Path(source)
        self.target = Path(target)
        self.compare_by_hash = compare_by_hash

        self.exclude_patterns = [
            f"*{pattern}" if pattern.startswith(".") else pattern
            for pattern in (exclude_patterns or [])
        ]

        if not self.source.is_dir():
            raise ValueError(f"Source directory does not exist: {self.source}")

        if not self.target.is_dir():
            raise ValueError(f"Target directory does not exist: {self.target}")

    def _should_exclude(self, relative_path: str) -> bool:
        """
        Check whether a file path matches any of the configured exclude patterns.

        Args:
            relative_path: File path relative to the root directory.

        Returns:
            True if the path should be excluded, False otherwise.
        """
        return any(
            fnmatch.fnmatch(relative_path, pattern) for pattern in self.exclude_patterns
        )

    def _get_file_metadata(self, file: Path) -> Optional[FileMetadata]:
        """
        Retrieve metadata for a file.
        Collects file size and modification time. Hash is not computed here to keep
        scanning fast (lazy hashing is used later if needed).

        Args:
            file: Path to the file.

        Returns:
            FileMetadata object containing file metadata, or None if the file cannot be
            accessed.
        """
        try:
            stat = file.stat()
            return FileMetadata(
                path="",
                full_path=str(file),
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        except OSError as exc:
            logger.error("Failed reading %s: %s", file, exc)
            return None

    def _scan_dir(self, directory: Path) -> dict[str, FileMetadata]:
        """
        Recursively scan a directory and collect file metadata.
        Excludes files and directories based on configured patterns.

        Args:
            directory: Root directory to scan.

        Returns:
            Dictionary mapping relative file paths to FileMetadata objects.
        """
        files: dict[str, FileMetadata] = {}

        for current_dir, subdirs, filenames in os.walk(directory):
            current_path = Path(current_dir)

            # exclude dirs
            subdirs[:] = [
                dir
                for dir in subdirs
                if dir not in IGNORED_DIRS
                and not self._should_exclude(
                    (current_path / dir).relative_to(directory).as_posix()
                )
            ]

            for filename in filenames:
                file_path = current_path / filename

                relative_path = file_path.relative_to(directory).as_posix()

                # Exclude file type
                if self._should_exclude(relative_path):
                    continue

                metadata = self._get_file_metadata(file_path)

                if metadata:
                    metadata.path = relative_path
                    files[relative_path] = metadata

        return files

    def _compute_file_hash(self, filepath: Path) -> str:
        """
        Compute the SHA-256 hash of a file.
        The file is read in chunks to avoid loading large files into memory.

        Args:
            filepath: Path to the file to hash.

        Returns:
            Hexadecimal SHA-256 hash string, or empty string if unreadable.
        """
        hasher = hashlib.sha256()

        try:
            with filepath.open("rb") as file:
                for chunk in iter(lambda: file.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()

        except OSError as exc:
            logger.error("Failed hashing %s: %s", filepath, exc)
            return ""

    def _get_hash(self, file_info: FileMetadata) -> str:
        """
        Lazily compute and cache the SHA-256 hash of a file.
        The hash is computed only once per FileMetadata instance and reused on
        subsequent calls.

        Args:
            file_info: FileMetadata object representing the file.

        Returns:
            SHA-256 hash string.
        """
        if file_info.hash is None:
            file_info.hash = self._compute_file_hash(Path(file_info.full_path))
        return file_info.hash

    def _are_files_different(
        self,
        source: FileMetadata,
        target: FileMetadata,
    ) -> bool:
        """
        Determine whether two files are different.
        Comparison strategy:
            - First compares file size (fast check)
            - If sizes match and hashing is enabled, compares SHA-256 hashes
            - If hashing is disabled, compares modification time

        Args:
            source: FileMetadata from source directory.
            target: FileMetadata from target directory.

        Returns:
            True if files differ, False if they are identical.
        """

        if source.size != target.size:
            return True

        if not self.compare_by_hash:
            return source.mtime != target.mtime

        return self._get_hash(source) != self._get_hash(target)

    def compare(self) -> ComparisonResult:
        """
        Run the comparison between source and target directories.

        Returns:
            ComparisonResult with files grouped by status.
        """

        logger.info("Scanning source: %s", self.source)
        source_files = self._scan_dir(self.source)

        logger.info("Scanning target: %s", self.target)
        target_files = self._scan_dir(self.target)

        only_in_source = sorted(set(source_files) - set(target_files))
        only_in_target = sorted(set(target_files) - set(source_files))

        same: dict[str, tuple[FileMetadata, FileMetadata]] = {}
        different: dict[str, tuple[FileMetadata, FileMetadata]] = {}

        common = set(source_files) & set(target_files)

        for path in sorted(common):
            source = source_files[path]
            target = target_files[path]

            if self._are_files_different(source, target):
                different[path] = (source, target)
            else:
                same[path] = (source, target)

        return ComparisonResult(
            only_in_source=only_in_source,
            only_in_target=only_in_target,
            same=same,
            different=different,
        )

    @staticmethod
    def show_results(results: ComparisonResult) -> None:

        logger.info("========== SUMMARY ==========")
        logger.info("Only in source : %d", len(results.only_in_source))
        logger.info("Only in target : %d", len(results.only_in_target))
        logger.info("Different      : %d", len(results.different))
        logger.info("Same           : %d", len(results.same))

        if results.only_in_source:
            logger.info("========== ONLY IN SOURCE ==========")
            for path in results.only_in_source:
                logger.info("- %s", path)

        if results.only_in_target:
            logger.info("========== ONLY IN TARGET ==========")
            for path in results.only_in_target:
                logger.info("- %s", path)

        if results.same:
            logger.info("========== SAME FILES ==========")
            for path in sorted(results.same):
                logger.info("- %s", path)

        if results.different:
            logger.info("========== DIFFERENT FILES ==========")

            for path, (source, target) in sorted(results.different.items()):

                logger.info("FILE: %s", path)

                logger.info(
                    "SOURCE -> size=%d hash=%s path=%s",
                    source.size,
                    source.hash,
                    source.full_path,
                )

                logger.info(
                    "TARGET -> size=%d hash=%s path=%s",
                    target.size,
                    target.hash,
                    target.full_path,
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two directories and report files that are "
            "missing, different, or identical."
        ),
        epilog="""
            Examples:

                Compare two directories:
                  %(prog)s ./source_dir ./target_dir

                Compare without calculating hashes:
                  %(prog)s ./source_dir ./target_dir --no-hash

                Exclude temporary files:
                  %(prog)s ./source_dir ./target_dir --exclude  .png ".txt" "img/*"

                Save logs to a file:
                  %(prog)s ./source_dir ./target_dir --log-file compare.log

        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Source directory to scan.")

    parser.add_argument("target", help="Target directory to compare against.")

    parser.add_argument(
        "--no-hash",
        action="store_true",
        help=(
            "Do not calculate SHA-256 hashes. "
            "Files are compared using size and modification time only."
        ),
    )

    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        metavar="PATTERN",
        help=(
            "Exclude files matching a glob pattern. " "Can be specified multiple times."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Save logs to a file. If omitted, logs only go to the console.",
    )

    return parser.parse_args()


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


def main() -> None:

    args = parse_args()

    setup_logging(args.log_file)

    comparer = DirectoryComparator(
        source=args.source,
        target=args.target,
        compare_by_hash=not args.no_hash,
        exclude_patterns=args.exclude,
    )

    results = comparer.compare()
    comparer.show_results(results)


if __name__ == "__main__":
    main()

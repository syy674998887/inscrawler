"""Find and optionally quarantine Instagram media duplicate candidates.

The downloader currently names media files with UTC timestamps. Older downloads
may have used local timestamps instead, leaving two identical files whose names
only differ by an offset such as 4 or 5 hours.

For a candidate pair, the later timestamp remains in place because that matches
the known UTC migration direction; the earlier file is only quarantined.

Default usage is read-only:

    python tools/deduplicate.py

Move candidates to a reversible quarantine folder inside each affected media folder:

    python tools/deduplicate.py --apply --action move

The timestamp-offset match is intentionally treated as a heuristic. This tool
never deletes files.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from inscrawler import paths

DEFAULT_DATA_ROOT = Path(paths.DEFAULT_DATA_DIR)
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
MEDIA_DIRECTORIES = ("posts", "reels", "igtv", "tagged", "stories", "highlights")
POST_FILENAME_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_(?P<index>\d+))?\.(?P<ext>[^.]+)$"
)
STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


@dataclass(frozen=True)
class MediaFile:
    account: str
    path: Path
    relative_path: str
    parent_key: str
    name: str
    size: int
    stamp: datetime | None
    index: str
    ext: str
    sha256: str


@dataclass(frozen=True)
class TimezoneDuplicate:
    account: str
    offset_hours: int
    size: int
    sha256: str
    remove_path: Path
    keep_path: Path


@dataclass(frozen=True)
class ExactHashDuplicate:
    account: str
    count: int
    size: int
    sha256: str
    paths: list[str]


@dataclass(frozen=True)
class NameExtensionDuplicate:
    account: str
    parent_key: str
    stem: str
    extensions: list[str]
    paths: list[str]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan data/* media folders for timezone, exact-hash, and same-name extension duplicates."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help=f"Data directory to scan. Default: {DEFAULT_DATA_ROOT}",
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Only scan this account. Can be passed multiple times.",
    )
    parser.add_argument(
        "--offset",
        default="4,5",
        help="Comma-separated filename timestamp offsets to treat as timezone duplicates. Default: 4,5.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the cleanup plan after interactive confirmation. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--action",
        choices=("move",),
        default="move",
        help="Cleanup action when --apply is used. Only reversible move is supported.",
    )
    return parser.parse_args(argv)


def parse_offsets(raw: str) -> set[int]:
    offsets: set[int] = set()
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        value = int(stripped)
        if value <= 0:
            raise ValueError("--offset values must be positive")
        offsets.add(value)
    if not offsets:
        raise ValueError("--offset must include at least one value")
    return offsets


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_timestamp_name(path: Path) -> tuple[datetime, str, str] | None:
    match = POST_FILENAME_RE.match(path.name)
    if not match:
        return None
    try:
        stamp = datetime.strptime(match.group("stamp"), STAMP_FORMAT)
    except ValueError:
        return None
    index = match.group("index") or ""
    ext = match.group("ext").lower()
    return stamp, index, ext


def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _resolved_child(path: Path, root: Path, label: str, *, must_exist: bool) -> Path:
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"{label} cannot be resolved: {path}: {exc}") from exc
    if not _is_within(resolved, root):
        raise ValueError(f"{label} escapes the selected root: {path}")
    if _is_link_like(path):
        raise ValueError(f"{label} must not be a symlink or junction: {path}")
    return resolved


def iter_account_dirs(data_root: Path, accounts: Iterable[str]) -> list[Path]:
    root = data_root.resolve()
    if accounts:
        candidates = []
        for account in accounts:
            account = str(account or "")
            component = Path(account)
            if (
                not account
                or component.is_absolute()
                or len(component.parts) != 1
                or component.parts[0] in (".", "..")
            ):
                raise ValueError(f"--account must be one directory name: {account!r}")
            candidates.append(data_root / account)
    else:
        if not data_root.exists():
            return []
        candidates = [
            path
            for path in data_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]

    resolved_accounts = []
    for candidate in sorted(candidates):
        resolved = _resolved_child(
            candidate,
            root,
            "Account directory",
            must_exist=True,
        )
        if not resolved.is_dir():
            raise ValueError(f"Account path is not a directory: {candidate}")
        resolved_accounts.append(resolved)
    return resolved_accounts


def iter_media_files(account_dir: Path) -> Iterable[Path]:
    account_root = account_dir.resolve()
    for directory_name in MEDIA_DIRECTORIES:
        child = account_dir / directory_name
        if not child.exists():
            continue
        child = _resolved_child(
            child,
            account_root,
            "Media directory",
            must_exist=True,
        )
        if not child.is_dir():
            raise ValueError(f"Media path is not a directory: {child}")
        for path in sorted(child.rglob("*")):
            if "duplicated" in path.relative_to(account_dir).parts:
                continue
            if not path.is_file() or not is_media_file(path):
                continue
            yield _resolved_child(
                path,
                account_root,
                "Media file",
                must_exist=True,
            )


def collect_hash_duplicates(
    data_root: Path,
    accounts: Iterable[str],
) -> tuple[list[MediaFile], int, int, int]:
    rows: list[MediaFile] = []
    scanned_accounts = 0
    total_files = 0
    parsed_files = 0

    for account_dir in iter_account_dirs(data_root, accounts):
        account_files = list(iter_media_files(account_dir))
        if not account_files:
            continue
        scanned_accounts += 1

        for path in account_files:
            total_files += 1
            parsed = parse_timestamp_name(path)
            stamp: datetime | None = None
            index = ""
            ext = path.suffix.lower().lstrip(".")
            if parsed:
                stamp, index, ext = parsed
                parsed_files += 1
            stat = path.stat()
            relative_path = path.relative_to(account_dir)
            rows.append(
                MediaFile(
                    account=account_dir.name,
                    path=path,
                    relative_path=str(relative_path),
                    parent_key=str(relative_path.parent),
                    name=path.name,
                    size=stat.st_size,
                    stamp=stamp,
                    index=index,
                    ext=ext,
                    sha256=sha256_file(path),
                )
            )

    return rows, scanned_accounts, total_files, parsed_files


def classify_duplicates(
    rows: list[MediaFile],
    offsets: set[int],
) -> tuple[
    list[TimezoneDuplicate], list[ExactHashDuplicate], list[NameExtensionDuplicate], int
]:
    by_hash: dict[tuple[str, str], list[MediaFile]] = {}
    for row in rows:
        by_hash.setdefault((row.account, row.sha256), []).append(row)

    timezone_duplicates: list[TimezoneDuplicate] = []
    exact_hash_duplicates: list[ExactHashDuplicate] = []
    name_extension_duplicates = classify_name_extension_duplicates(rows)
    duplicate_hash_groups = 0

    for (_account, sha256), group in sorted(by_hash.items()):
        if len(group) < 2:
            continue
        duplicate_hash_groups += 1
        exact_hash_duplicates.append(
            ExactHashDuplicate(
                account=group[0].account,
                count=len(group),
                size=group[0].size,
                sha256=sha256,
                paths=[
                    item.relative_path
                    for item in sorted(group, key=lambda item: item.relative_path)
                ],
            )
        )

        # Same content can appear in different carousel indexes. Timezone cleanup
        # only acts inside the same folder when extension and carousel index
        # also match.
        by_slot: dict[tuple[str, str, str], list[MediaFile]] = {}
        for item in group:
            if item.stamp is None:
                continue
            by_slot.setdefault((item.parent_key, item.ext, item.index), []).append(item)

        for slot_group in by_slot.values():
            if len(slot_group) < 2:
                continue
            sorted_items = sorted(
                slot_group, key=lambda item: (item.stamp or datetime.min, item.name)
            )
            used: set[Path] = set()
            for i, left in enumerate(sorted_items):
                if left.path in used:
                    continue
                for right in sorted_items[i + 1 :]:
                    if right.path in used:
                        continue
                    if left.stamp is None or right.stamp is None:
                        continue
                    delta_seconds = int((right.stamp - left.stamp).total_seconds())
                    if delta_seconds % 3600 != 0:
                        continue
                    offset_hours = delta_seconds // 3600
                    if offset_hours not in offsets:
                        continue

                    keep_item = right
                    remove_item = left

                    timezone_duplicates.append(
                        TimezoneDuplicate(
                            account=left.account,
                            offset_hours=offset_hours,
                            size=left.size,
                            sha256=sha256,
                            remove_path=remove_item.path,
                            keep_path=keep_item.path,
                        )
                    )
                    used.add(left.path)
                    used.add(right.path)
                    break

    timezone_duplicates.sort(
        key=lambda item: (item.account, item.remove_path.name, item.keep_path.name)
    )
    exact_hash_duplicates.sort(key=lambda item: (item.account, item.paths))
    return (
        timezone_duplicates,
        exact_hash_duplicates,
        name_extension_duplicates,
        duplicate_hash_groups,
    )


def classify_name_extension_duplicates(
    rows: list[MediaFile],
) -> list[NameExtensionDuplicate]:
    by_name: dict[tuple[str, str, str], list[MediaFile]] = {}
    for row in rows:
        if row.stamp is None:
            continue
        by_name.setdefault(
            (row.account, row.parent_key, Path(row.name).stem), []
        ).append(row)

    duplicates: list[NameExtensionDuplicate] = []
    for (account, parent_key, stem), group in sorted(by_name.items()):
        extensions = sorted({item.ext for item in group})
        if len(extensions) < 2:
            continue
        duplicates.append(
            NameExtensionDuplicate(
                account=account,
                parent_key=parent_key,
                stem=stem,
                extensions=extensions,
                paths=[
                    item.relative_path
                    for item in sorted(group, key=lambda item: item.relative_path)
                ],
            )
        )
    return duplicates


def bytes_text(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def print_report(
    timezone_duplicates: list[TimezoneDuplicate],
    exact_hash_duplicates: list[ExactHashDuplicate],
    name_extension_duplicates: list[NameExtensionDuplicate],
    scanned_accounts: int,
    total_files: int,
    parsed_files: int,
    duplicate_hash_groups: int,
    offsets: set[int],
) -> None:
    duplicate_bytes = sum(item.size for item in timezone_duplicates)
    print("Duplicate scan")
    print(f"  scanned_accounts: {scanned_accounts}")
    print(f"  media_files: {total_files}")
    print(f"  parsed_timestamp_files: {parsed_files}")
    print(f"  duplicate_hash_groups: {duplicate_hash_groups}")
    print(f"  timezone_duplicate_pairs: {len(timezone_duplicates)}")
    print(
        f"  timezone_duplicate_bytes: {duplicate_bytes} ({bytes_text(duplicate_bytes)})"
    )
    print(f"  exact_hash_duplicate_groups: {len(exact_hash_duplicates)}")
    print(f"  same_name_different_extension_groups: {len(name_extension_duplicates)}")
    print(f"  offsets: {','.join(str(value) for value in sorted(offsets))}h")

    if timezone_duplicates:
        print("")
        print("Timezone duplicate candidates (apply quarantines only these):")
        for item in timezone_duplicates:
            print(
                f"  [{item.account}] remove {item.remove_path} -> keep {item.keep_path} "
                f"({item.offset_hours}h, {item.size} bytes)"
            )

    if exact_hash_duplicates:
        print("")
        print("Exact hash duplicates (report only):")
        for item in exact_hash_duplicates:
            print(
                f"  [{item.account}] sha256={item.sha256[:12]} count={item.count} size={item.size} bytes"
            )
            for path in item.paths:
                print(f"    {path}")

    if name_extension_duplicates:
        print("")
        print("Same name with different extensions (report only):")
        for item in name_extension_duplicates:
            print(
                f"  [{item.account}] {item.parent_key}\\{item.stem} ({', '.join(item.extensions)})"
            )
            for path in item.paths:
                print(f"    {path}")


def confirm_or_abort(action: str, count: int) -> None:
    if action != "move":
        raise ValueError("Only reversible move is supported")
    token = "MOVE"
    prompt = f"\nType {token} to {action} {count} timezone duplicate files, or press Enter to abort: "
    answer = input(prompt).strip()
    if answer != token:
        print("Aborted. No files changed.")
        raise SystemExit(1)


def _validate_cleanup_file(
    path: Path, data_root: Path, item: TimezoneDuplicate
) -> Path:
    resolved = _resolved_child(
        path,
        data_root,
        "Cleanup file",
        must_exist=True,
    )
    if not resolved.is_file():
        raise ValueError(f"Cleanup path is not a regular file: {path}")
    if resolved.stat().st_size != item.size or sha256_file(resolved) != item.sha256:
        raise ValueError(f"Cleanup file changed after the scan: {path}")
    return resolved


def validate_cleanup_plan(items: list[TimezoneDuplicate], data_root: Path) -> None:
    root = data_root.resolve(strict=True)
    for item in items:
        source = _validate_cleanup_file(item.remove_path, root, item)
        keep = _validate_cleanup_file(item.keep_path, root, item)
        if source == keep:
            raise ValueError(f"Cleanup pair resolves to the same path: {source}")


def apply_cleanup(items: list[TimezoneDuplicate], action: str, data_root: Path) -> None:
    if action != "move":
        raise ValueError("Only reversible move is supported")
    validate_cleanup_plan(items, data_root)

    changed = 0
    duplicated_dirs: set[Path] = set()
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    for item in items:
        source = _validate_cleanup_file(item.remove_path, data_root.resolve(), item)
        _validate_cleanup_file(item.keep_path, data_root.resolve(), item)
        destination = source.parent / "duplicated" / run_id / source.name
        if destination.parent not in duplicated_dirs:
            destination.parent.mkdir(parents=True, exist_ok=False)
        try:
            shutil.move(str(source), str(destination))
        except Exception:
            try:
                destination.parent.rmdir()
            except OSError:
                pass
            raise
        duplicated_dirs.add(destination.parent)
        print(f"Moved: {source} -> {destination}")
        changed += 1
    print(f"\nChanged files: {changed}")
    if duplicated_dirs:
        print("Duplicated folders:")
        for path in sorted(duplicated_dirs):
            print(f"  {path}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    data_root = args.root.resolve()
    try:
        offsets = parse_offsets(args.offset)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        rows, scanned_accounts, total_files, parsed_files = collect_hash_duplicates(
            data_root=data_root,
            accounts=args.account,
        )
    except (OSError, ValueError) as exc:
        print(f"Scan aborted: {exc}", file=sys.stderr)
        return 2
    (
        timezone_duplicates,
        exact_hash_duplicates,
        name_extension_duplicates,
        duplicate_hash_groups,
    ) = classify_duplicates(
        rows=rows,
        offsets=offsets,
    )

    print_report(
        timezone_duplicates=timezone_duplicates,
        exact_hash_duplicates=exact_hash_duplicates,
        name_extension_duplicates=name_extension_duplicates,
        scanned_accounts=scanned_accounts,
        total_files=total_files,
        parsed_files=parsed_files,
        duplicate_hash_groups=duplicate_hash_groups,
        offsets=offsets,
    )

    if not args.apply:
        print("\nDry run only. Add --apply after reviewing the report.")
        return 0

    if not timezone_duplicates:
        print("\nNo timezone duplicate files to clean.")
        return 0

    confirm_or_abort(args.action, len(timezone_duplicates))
    try:
        apply_cleanup(timezone_duplicates, args.action, data_root)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Cleanup aborted after a safety check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

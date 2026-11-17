"""Check that built distributions contain the package and exclude runtime data."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {
    ".env",
    "target.json",
    "data",
    "session",
    "tasks",
    "cache",
    "docs",
    "reference",
    ".venv",
}


def check_local_files(names: set[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.parts and (
            path.parts[0] in PRIVATE_ROOTS or path.name.casefold() == "agents.md"
        ):
            raise ValueError(f"Unexpected local-only content in distribution: {name}")


def check_distributions(directory: Path) -> None:
    expected = {
        path.relative_to(ROOT / "src").as_posix()
        for path in (ROOT / "src" / "inscrawler").rglob("*")
        if path.is_file() and path.suffix in {".py", ".ico"}
    }
    wheels = sorted(directory.glob("inscrawler-*.whl"))
    sources = sorted(directory.glob("inscrawler-*.tar.gz"))
    if not wheels or not sources:
        raise ValueError(
            "Build both the wheel and source distribution first with uv build"
        )
    for path in wheels:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            check_local_files(names)
            missing = expected - names
            if missing:
                raise ValueError(
                    f"{path.name} is missing package files: {sorted(missing)}"
                )
            unexpected = [
                name
                for name in names
                if not (
                    name.startswith("inscrawler/")
                    or PurePosixPath(name).parts[0].endswith(".dist-info")
                )
            ]
            if unexpected:
                raise ValueError(f"Unexpected wheel contents: {unexpected}")
            entry_file = next(
                name for name in names if name.endswith(".dist-info/entry_points.txt")
            )
            if "inscrawler = inscrawler.app:main" not in archive.read(
                entry_file
            ).decode("utf-8"):
                raise ValueError("Missing inscrawler console entry point")
        print(f"{path.name}: {len(expected)} package files and entry point verified")
    for path in sources:
        with tarfile.open(path) as archive:
            names = {
                "/".join(PurePosixPath(name).parts[1:]) for name in archive.getnames()
            }
            check_local_files(names)
            missing = {"src/" + name for name in expected} - names
            if missing:
                raise ValueError(
                    f"{path.name} is missing package sources: {sorted(missing)}"
                )
        print(f"{path.name}: package sources verified; no local-only content included")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=ROOT / "dist")
    check_distributions(parser.parse_args().directory)


if __name__ == "__main__":
    main()

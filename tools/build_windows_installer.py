"""Build the Windows x64 setup executable with cx_Freeze and Inno Setup."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
import urllib.request
from importlib.metadata import distribution
from pathlib import Path

from cx_Freeze import setup
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe"
INNO_SHA256 = "8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0"


def installer_files() -> list[tuple[Path, Path]]:
    notices = Path("share/licenses")
    files = [
        (
            ROOT / "src/inscrawler/assets/logo.ico",
            Path("lib/inscrawler/assets/logo.ico"),
        ),
        (ROOT / "LICENSE", Path("LICENSE.txt")),
        (Path(sys.base_prefix) / "LICENSE.txt", notices / "Python/LICENSE.txt"),
        (ROOT / "tools/installer_licenses", notices / "Qt"),
    ]
    pending = ["inscrawler"]
    seen = set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        package = distribution(name)
        for entry in package.files or ():
            parts = entry.parts
            if (
                len(parts) >= 3
                and parts[0].endswith(".dist-info")
                and parts[1] == "licenses"
            ):
                files.append(
                    (
                        Path(package.locate_file(entry)),
                        notices.joinpath(name, *parts[2:]),
                    )
                )
        for requirement in map(Requirement, package.requires or ()):
            if requirement.marker is None or requirement.marker.evaluate():
                pending.append(requirement.name)
    return files


def get_compiler(directory: Path) -> Path:
    bootstrap = directory / "innosetup.exe"
    with (
        urllib.request.urlopen(INNO_URL, timeout=60) as response,
        bootstrap.open("xb") as output,
    ):
        shutil.copyfileobj(response, output)
    with bootstrap.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != INNO_SHA256:
            raise RuntimeError("Inno Setup download failed SHA-256 verification")
    compiler = directory / "inno"
    subprocess.run(
        [
            bootstrap,
            "/PORTABLE=1",
            "/CURRENTUSER",
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/DIR={compiler}",
        ],
        check=True,
        timeout=180,
    )
    return compiler / "ISCC.exe"


def build_payload(directory: Path) -> None:
    setup(
        script_args=["build_exe", "--build-exe", str(directory)],
        options={
            "build_exe": {
                "include_files": installer_files(),
                "replace_paths": [("*", ""), (str(ROOT), "")],
            }
        },
    )


def main() -> None:
    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 13):
        raise SystemExit("Build the installer with 64-bit Python 3.13 on Windows")
    os.chdir(ROOT)
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    version = project["version"]
    file_version = ".".join(str(part) for part in Version(version).release[:4])
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inno-", dir=dist) as temp:
        staging = Path(temp)
        compiler = get_compiler(staging)
        payload = staging / "app"
        build_payload(payload)
        name = f"inscrawler-{version}-win64-setup"
        subprocess.run(
            [
                compiler,
                f"/DAppVersion={version}",
                f"/DFileVersion={file_version}",
                f"/DPublisher={project['authors'][0]['name']}",
                f"/DPayload={payload}",
                f"/O{staging}",
                f"/F{name}",
                ROOT / "tools" / "windows_installer.iss",
            ],
            check=True,
            timeout=600,
        )
        installer = (staging / f"{name}.exe").replace(dist / f"{name}.exe")
    print(f"Built {installer}")


if __name__ == "__main__":
    main()

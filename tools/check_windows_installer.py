"""Verify the Windows x64 setup executable. Use --install for a full smoke test."""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_executable(installer: Path) -> None:
    with installer.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"{installer.name} is not a Windows executable")
        stream.seek(0x3C)
        stream.seek(int.from_bytes(stream.read(4), "little"))
        if stream.read(6) != b"PE\0\0\x64\x86":  # PE signature + AMD64 machine
            raise ValueError(f"{installer.name} is not an x64 executable")


def registered_install() -> tuple[Path, str] | None:
    import winreg

    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\inscrawler_is1"
    try:
        handle = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        )
    except FileNotFoundError:
        return None
    with handle:
        path = winreg.QueryValueEx(handle, "InstallLocation")[0]
        version = winreg.QueryValueEx(handle, "DisplayVersion")[0]
    return Path(path).resolve(), version


def programs_folder() -> Path:
    buffer = ctypes.create_unicode_buffer(260)
    # CSIDL_PROGRAMS resolves the current user's Start menu, including redirected profiles.
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 2, None, 0, buffer)
    if result:
        raise OSError(f"Could not locate the Start menu (HRESULT {result:#x})")
    return Path(buffer.value)


def check_first_launch(application: Path, profile: Path) -> None:
    for filename in ("qwindows.dll", "cacert.pem"):
        if not any(application.rglob(filename)):
            raise RuntimeError(f"Missing runtime dependency: {filename}")
    for name in (
        "lib/inscrawler/assets/logo.ico",
        "LICENSE.txt",
        "share/licenses/Python/LICENSE.txt",
        "share/licenses/Qt/LGPL-3.0-only.txt",
        "share/licenses/requests/NOTICE",
    ):
        if not (application / name).is_file():
            raise RuntimeError(f"Missing installer file: {name}")

    env = os.environ.copy()
    for name in ("INSCRAWLER_HOME", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    env.update(LOCALAPPDATA=str(profile), QT_QPA_PLATFORM="offscreen")
    home = profile / "inscrawler"
    ready_files = (home / ".env", home / "cache" / "preview.json")
    with subprocess.Popen(
        [str(application / "inscrawler.exe")],
        cwd=profile.parent,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"GUI exited during startup: {process.returncode}"
                    )
                if all(path.is_file() for path in ready_files):
                    return
                time.sleep(0.2)
            raise RuntimeError("GUI did not initialize its settings and preview cache")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def check_install(installer: Path, version: str) -> None:
    if registered_install() is not None:
        raise RuntimeError(
            "An inscrawler installation is already registered for this user"
        )
    silent = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    with tempfile.TemporaryDirectory(prefix="inscrawler-install-") as temp:
        application = Path(temp) / "application"
        profile = Path(temp) / "profile"
        profile.mkdir()
        shortcut = programs_folder() / "inscrawler" / "inscrawler.lnk"
        if shortcut.parent.exists():
            raise RuntimeError(f"Start menu group already exists: {shortcut.parent}")
        uninstaller = application / "unins000.exe"
        try:
            subprocess.run(
                [installer, *silent, f"/DIR={application}"],
                check=True,
                timeout=180,
            )
            if registered_install() != (application.resolve(), version):
                raise RuntimeError(
                    "Setup did not register the expected path and version"
                )
            if not shortcut.is_file() or not uninstaller.is_file():
                raise RuntimeError("Setup did not create its shortcut and uninstaller")
            check_first_launch(application, profile)
        finally:
            if uninstaller.is_file():
                subprocess.run([uninstaller, *silent], check=True, timeout=180)
        if (
            (application / "inscrawler.exe").exists()
            or shortcut.exists()
            or registered_install()
        ):
            raise RuntimeError(
                "Uninstall left program files, a shortcut or a registration behind"
            )
        if not (profile / "inscrawler" / ".env").is_file():
            raise RuntimeError("Uninstall removed the user's settings")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install, launch and uninstall on a clean Windows account.",
    )
    args = parser.parse_args()
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    installer = ROOT / "dist" / f"inscrawler-{version}-win64-setup.exe"
    if not installer.is_file():
        parser.error(
            f"Missing {installer.name}. Run tools/build_windows_installer.py first."
        )
    check_executable(installer)
    if args.install:
        if sys.platform != "win32":
            parser.error("--install requires Windows")
        check_install(installer, version)
        print(f"{installer.name}: install, first launch and uninstall verified")
    else:
        print(f"{installer.name}: Windows x64 executable verified")


if __name__ == "__main__":
    main()

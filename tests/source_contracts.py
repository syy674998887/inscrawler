"""Read UI sources for headless structural contracts across module boundaries."""

from pathlib import Path


def ui_source():
    ui_root = Path(__file__).resolve().parents[1] / "src" / "inscrawler" / "ui"
    return "\n\n".join(
        path.read_text(encoding="utf-8") for path in sorted(ui_root.rglob("*.py"))
    )

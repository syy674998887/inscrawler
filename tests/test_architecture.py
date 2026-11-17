"""Package boundaries and data-path contracts for the src layout."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "inscrawler"


def import_targets(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


class ArchitectureTests(unittest.TestCase):
    def test_core_has_no_gui_or_checkout_tool_dependencies(self):
        for path in PACKAGE.rglob("*.py"):
            relative = path.relative_to(PACKAGE)
            if relative.parts[0] == "ui" or path.name in ("app.py", "__main__.py"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                for target in import_targets(node):
                    self.assertFalse(
                        target.split(".")[0]
                        in {"PySide6", "gui", "download", "tools", "tests", "reference"}
                        or target.startswith("inscrawler.ui"),
                        f"{relative} imports {target}",
                    )

    def test_internal_import_graph_has_no_cycles(self):
        modules = {}
        for path in PACKAGE.rglob("*.py"):
            parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
            name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            modules[name] = path
        graph = {
            name: {
                target
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                for target in import_targets(node)
                if target in modules and target != name
            }
            for name, path in modules.items()
        }
        visited, active = set(), []

        def visit(name):
            self.assertNotIn(name, active, " -> ".join([*active, name]))
            if name in visited:
                return
            active.append(name)
            for target in graph[name]:
                visit(target)
            active.pop()
            visited.add(name)

        for name in graph:
            visit(name)

    def _headless_import(self, directory, home=None):
        env = os.environ.copy()
        env.pop("INSCRAWLER_HOME", None)
        if home is not None:
            env["INSCRAWLER_HOME"] = str(home)
        code = """
import json, sys
from inscrawler import paths
from inscrawler.downloads import service
from inscrawler.tasks import repository
from inscrawler.ui.theme import tokens, styles
print(json.dumps({
    'home': paths.APP_DIR,
    'tasks': paths.TASKS_DIR,
    'session': paths.SESSION_DIR,
    'preview': paths.PREVIEW_HISTORY_FILE,
    'qt_loaded': any(name.startswith('PySide6') for name in sys.modules),
    'legacy_loaded': any(name in sys.modules for name in ('gui', 'download')),
}))
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code],
            cwd=directory,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(completed.stdout)

    def test_source_checkout_paths_are_independent_of_launch_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._headless_import(directory)
            self.assertEqual(Path(result["home"]), ROOT)
            self.assertEqual(Path(result["tasks"]), ROOT / "tasks")
            self.assertEqual(Path(result["session"]), ROOT / "session")
            self.assertEqual(Path(result["preview"]), ROOT / "cache" / "preview.json")
            self.assertFalse(result["qt_loaded"])
            self.assertFalse(result["legacy_loaded"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_explicit_data_home_is_respected_without_creating_files_on_import(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "app data"
            # default_home() resolves INSCRAWLER_HOME. Temporary directories may
            # use 8.3 short names (RUNNER~1 on CI) or symlinks, so compare
            # against the resolved spelling of the same location.
            expected = home.resolve()
            result = self._headless_import(directory, home)
            self.assertEqual(Path(result["home"]), expected)
            self.assertEqual(Path(result["tasks"]), expected / "tasks")
            self.assertFalse(home.exists())

    def test_windows_executable_keeps_user_data_outside_install_directory(self):
        from inscrawler.paths import default_home

        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory).resolve()
            with (
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, LOCALAPPDATA=str(local), INSCRAWLER_HOME=""),
            ):
                self.assertEqual(default_home(), local / "inscrawler")
                os.environ["INSCRAWLER_HOME"] = str(local / "custom")
                self.assertEqual(default_home(), local / "custom")
                self.assertEqual(list(local.iterdir()), [])

    def test_design_system_builds_both_themes_without_widgets(self):
        from inscrawler.ui.theme import styles, tokens

        light, dark = tokens.theme_tokens("light"), tokens.theme_tokens("dark")
        self.assertEqual(light.keys(), dark.keys())
        self.assertNotEqual(light["window"], dark["window"])
        for palette in (light, dark):
            stylesheet = styles.application_stylesheet(palette)
            self.assertIn(palette["surface"], stylesheet)
            self.assertIn(palette["danger"], stylesheet)
            self.assertIn(
                palette["scroll_handle"], styles.scrollbar_stylesheet(palette)
            )

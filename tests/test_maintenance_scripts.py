import tempfile
import unittest
from pathlib import Path

from tools import deduplicate


class DeduplicateSafetyTests(unittest.TestCase):
    def _candidate(self, root):
        media_dir = root / "alice" / "posts"
        media_dir.mkdir(parents=True)
        source = media_dir / "2026-01-01_08-00-00.jpg"
        keep = media_dir / "2026-01-01_12-00-00.jpg"
        source.write_bytes(b"same-media")
        keep.write_bytes(b"same-media")
        digest = deduplicate.sha256_file(source)
        return deduplicate.TimezoneDuplicate(
            account="alice",
            offset_hours=4,
            size=source.stat().st_size,
            sha256=digest,
            remove_path=source,
            keep_path=keep,
        )

    def test_account_argument_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            root.mkdir()
            with self.assertRaises(ValueError):
                deduplicate.iter_account_dirs(root, [".."])
            with self.assertRaises(ValueError):
                deduplicate.iter_account_dirs(root, [str(Path(temp_dir).resolve())])

    def test_scan_uses_only_supported_media_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            posts = root / "alice" / "posts"
            profile = root / "alice" / "profile"
            posts.mkdir(parents=True)
            profile.mkdir(parents=True)
            post = posts / "2026-01-01_12-00-00.jpg"
            avatar = profile / "2026-01-01_12-00-00.jpg"
            post.write_bytes(b"post")
            avatar.write_bytes(b"avatar")

            rows, accounts, total, parsed = deduplicate.collect_hash_duplicates(
                root, []
            )
            self.assertEqual((accounts, total, parsed), (1, 1, 1))
            self.assertEqual([row.path for row in rows], [post.resolve()])

    def test_scan_keeps_legacy_igtv_media_in_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            igtv = root / "alice" / "igtv"
            igtv.mkdir(parents=True)
            video = igtv / "2026-01-01_12-00-00.mp4"
            video.write_bytes(b"legacy-video")

            rows, accounts, total, parsed = deduplicate.collect_hash_duplicates(
                root, []
            )

            self.assertEqual((accounts, total, parsed), (1, 1, 1))
            self.assertEqual([row.path for row in rows], [video.resolve()])

    def test_cleanup_revalidates_both_files_before_moving(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            item = self._candidate(root)
            item.keep_path.write_bytes(b"changed-after-scan")
            with self.assertRaises(ValueError):
                deduplicate.validate_cleanup_plan([item], root)
            self.assertTrue(item.remove_path.exists())

    def test_apply_only_moves_to_a_reversible_quarantine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            item = self._candidate(root)

            deduplicate.apply_cleanup([item], "move", root)

            self.assertFalse(item.remove_path.exists())
            self.assertTrue(item.keep_path.exists())
            quarantined = list(
                (item.keep_path.parent / "duplicated").glob(
                    "run-*/2026-01-01_08-00-00.jpg"
                )
            )
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"same-media")
            with self.assertRaises(ValueError):
                deduplicate.apply_cleanup([], "delete", root)


if __name__ == "__main__":
    unittest.main()

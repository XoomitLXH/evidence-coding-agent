from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.draft import DraftChanges


class DraftChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "note.txt").write_text("draft\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_read_file_prefers_unaccepted_draft_content(self) -> None:
        drafts = DraftChanges(self.root)
        drafts.write_file("note.txt", "final\n")

        self.assertEqual(drafts.read_text("note.txt"), "final\n")
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "draft\n")

    def test_apply_patch_updates_draft_without_writing_workspace(self) -> None:
        drafts = DraftChanges(self.root)

        result = drafts.apply_patch("note.txt", "draft", "final")

        self.assertTrue(result["ok"])
        self.assertEqual(drafts.read_text("note.txt"), "final\n")
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "draft\n")

    def test_accept_reports_conflict_when_user_edited_workspace_since_draft_started(self) -> None:
        drafts = DraftChanges(self.root)
        drafts.write_file("note.txt", "final\n")
        (self.root / "note.txt").write_text("manual\n", encoding="utf-8")

        result = drafts.accept()

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["conflicts"], ["note.txt"])
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "manual\n")
        self.assertEqual(drafts.paths(), ["note.txt"])

    def test_reject_discards_drafts(self) -> None:
        drafts = DraftChanges(self.root)
        drafts.write_file("note.txt", "final\n")

        result = drafts.reject()

        self.assertEqual(result["rejected"], ["note.txt"])
        self.assertEqual(drafts.paths(), [])
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "draft\n")

    def test_new_file_is_kept_in_draft_until_accept_and_serializes(self) -> None:
        drafts = DraftChanges(self.root)

        drafts.write_file("new.txt", "hello\n")
        restored = DraftChanges.from_json(self.root, drafts.to_json())

        self.assertEqual(restored.paths(), ["new.txt"])
        self.assertFalse((self.root / "new.txt").exists())
        result = restored.accept()
        self.assertEqual(result, {"accepted": ["new.txt"], "conflicts": []})
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello\n")

    def test_directory_path_is_rejected(self) -> None:
        (self.root / "folder").mkdir()
        drafts = DraftChanges(self.root)

        with self.assertRaises(Exception):
            drafts.write_file("folder", "content")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.draft import DraftChanges
from coding_agent.state import AgentState
from coding_agent.tools_files import apply_patch, list_dir, read_file, write_file


class DraftFileToolTests(unittest.TestCase):
    def test_write_and_read_use_draft_without_touching_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('before')\n", encoding="utf-8")
            drafts = DraftChanges(root)
            state = AgentState()

            result = write_file(root, state, "main.py", "print('after')\n", drafts=drafts)
            viewed = read_file(root, "main.py", drafts=drafts)

            self.assertTrue(result["draft"])
            self.assertIn("print('after')", viewed["content"])
            self.assertEqual((root / "main.py").read_text(encoding="utf-8"), "print('before')\n")
            self.assertEqual(state.modified_files, {"main.py"})

    def test_apply_patch_creates_new_file_in_draft_and_list_dir_exposes_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            drafts = DraftChanges(root)
            state = AgentState()

            write_file(root, state, "new.py", "x = 1\n", drafts=drafts)
            result = apply_patch(root, state, "new.py", "x = 1", "x = 2", drafts=drafts)
            listing = list_dir(root, ".", drafts=drafts)

            self.assertTrue(result["draft"])
            self.assertEqual(drafts.read_text("new.py"), "x = 2\n")
            self.assertIn({"name": "new.py", "type": "file", "draft": True}, listing["entries"])
            self.assertFalse((root / "new.py").exists())


if __name__ == "__main__":
    unittest.main()

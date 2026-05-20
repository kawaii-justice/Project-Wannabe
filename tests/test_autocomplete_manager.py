import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit

from src.core.autocomplete_manager import AutocompleteManager


class AutocompleteManagerUndoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        widget = getattr(self, "edit", None)
        if widget is not None:
            widget.deleteLater()
        self.app.processEvents()

    def make_manager(self) -> AutocompleteManager:
        self.edit = QPlainTextEdit()
        return AutocompleteManager(self.edit, kobold_client=object())

    def test_mouse_style_clear_does_not_leave_ghost_delete_on_undo_stack(self):
        manager = self.make_manager()

        self.edit.insertPlainText("abc")
        manager.show_ghost_text("GHOST")

        manager.clear_ghost_text(prefer_undo=False)

        self.assertEqual(self.edit.toPlainText(), "abc")
        self.assertFalse(manager.has_ghost_text())

        self.edit.undo()

        self.assertEqual(self.edit.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()

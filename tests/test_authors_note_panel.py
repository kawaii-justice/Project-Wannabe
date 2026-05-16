import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.ui.authors_note_panel import AuthorsNotePanel


class AuthorsNotePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        if hasattr(self, "panel"):
            self.panel.deleteLater()
            self.app.processEvents()

    def make_panel(self) -> AuthorsNotePanel:
        self.panel = AuthorsNotePanel()
        self.app.processEvents()
        return self.panel

    def test_collapsed_indicator_marks_active_note(self):
        panel = self.make_panel()
        panel.set_expanded(False, remember_height=False)

        self.assertEqual(panel.toggle_button.arrowType(), Qt.UpArrow)
        self.assertNotIn("適用中", panel.toggle_button.text())

        panel.text_edit.setPlainText("次は森でドラゴンに出会う")
        self.app.processEvents()

        self.assertIn("適用中", panel.toggle_button.text())
        self.assertIn("入力されています", panel.toggle_button.toolTip())

    def test_expanded_state_hides_collapsed_indicator(self):
        panel = self.make_panel()
        panel.text_edit.setPlainText("次の展開")
        panel.set_expanded(False, remember_height=False)
        self.assertIn("適用中", panel.toggle_button.text())

        target_height = panel.set_expanded(True, remember_height=False)

        self.assertEqual(panel.toggle_button.arrowType(), Qt.DownArrow)
        self.assertNotIn("適用中", panel.toggle_button.text())
        self.assertTrue(panel.text_edit.isVisibleTo(panel))
        self.assertGreaterEqual(target_height, 220)

    def test_collapsed_and_expanded_size_targets_are_separate(self):
        panel = self.make_panel()

        collapsed_height = panel.set_expanded(False, remember_height=False)
        expanded_height = panel.set_expanded(True, remember_height=False)

        self.assertLessEqual(collapsed_height, 32)
        self.assertLess(panel.expanded_min_height(), 100)
        self.assertGreaterEqual(expanded_height, 220)

    def test_remembers_user_resized_expanded_height(self):
        panel = self.make_panel()
        panel.set_expanded(True, remember_height=False)

        collapsed_height = panel.set_expanded(False, current_height=160)
        reopened_height = panel.set_expanded(True, remember_height=False)

        self.assertLess(collapsed_height, 40)
        self.assertEqual(reopened_height, 160)


if __name__ == "__main__":
    unittest.main()

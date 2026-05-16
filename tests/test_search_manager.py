import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from src.ui.search_dialog import SEARCH_TARGETS, SearchManager


class SearchManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget_name in ("edit", "line_edit"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.deleteLater()
        self.app.processEvents()

    def make_text_edit(self, text: str) -> tuple[SearchManager, QPlainTextEdit]:
        self.edit = QPlainTextEdit()
        self.edit.setPlainText(text)
        manager = SearchManager()
        manager.set_text_widget(self.edit)
        return manager, self.edit

    def make_line_edit(self, text: str) -> tuple[SearchManager, QLineEdit]:
        self.line_edit = QLineEdit()
        self.line_edit.setText(text)
        manager = SearchManager()
        manager.set_text_widget(self.line_edit)
        return manager, self.line_edit

    def test_case_sensitive_change_recomputes_results(self):
        manager, _edit = self.make_text_edit("ABC abc")

        self.assertTrue(manager.find_next("abc", case_sensitive=False))
        self.assertEqual(manager.get_search_info(), (1, 2))

        self.assertTrue(manager.find_next("abc", case_sensitive=True))
        self.assertEqual(manager.get_search_info(), (1, 1))

    def test_highlights_use_extra_selections_without_changing_text(self):
        manager, edit = self.make_text_edit("alpha beta alpha")

        self.assertTrue(manager.find_next("alpha"))

        self.assertEqual(edit.toPlainText(), "alpha beta alpha")
        self.assertEqual(len(edit.extraSelections()), 2)

        manager.clear_highlights()

        self.assertEqual(edit.toPlainText(), "alpha beta alpha")
        self.assertEqual(len(edit.extraSelections()), 0)

    def test_regex_replace_current_supports_groups(self):
        manager, edit = self.make_text_edit("abc123 xyz")

        self.assertTrue(manager.find_next(r"([a-z]+)(\d+)", use_regex=True))
        self.assertTrue(manager.replace_current(r"([a-z]+)(\d+)", r"\2-\1", use_regex=True))

        self.assertEqual(edit.toPlainText(), "123-abc xyz")

    def test_replace_all_plain_text_is_case_insensitive(self):
        manager, edit = self.make_text_edit("Cat cat CAT")

        count = manager.replace_all("cat", "dog", case_sensitive=False)

        self.assertEqual(count, 3)
        self.assertEqual(edit.toPlainText(), "dog dog dog")

    def test_invalid_regex_sets_error_and_keeps_text(self):
        manager, edit = self.make_text_edit("text")

        self.assertFalse(manager.find_next("(", use_regex=True))

        self.assertIn("正規表現エラー", manager.last_error)
        self.assertEqual(edit.toPlainText(), "text")

    def test_empty_width_regex_is_rejected(self):
        manager, edit = self.make_text_edit("text")

        self.assertFalse(manager.find_next(r"\b", use_regex=True))

        self.assertIn("空文字に一致", manager.last_error)
        self.assertEqual(edit.toPlainText(), "text")

    def test_line_edit_replace_current(self):
        manager, line_edit = self.make_line_edit("one two one")

        self.assertTrue(manager.find_next("two"))
        self.assertTrue(manager.replace_current("two", "three"))

        self.assertEqual(line_edit.text(), "one three one")

    def test_hidden_output_log_is_not_exposed_as_search_target(self):
        self.assertNotIn("出力", SEARCH_TARGETS)


if __name__ == "__main__":
    unittest.main()

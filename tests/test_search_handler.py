import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox

from src.ui.search_handler import SearchHandler


class FakeTimer:
    def __init__(self):
        self.started_with = None

    def start(self, value):
        self.started_with = value


class FakeAutocompleteManager:
    def __init__(self):
        self.cleared = False
        self.is_enabled = True
        self.debounce_ms = 250
        self.debounce_timer = FakeTimer()

    def clear_ghost_text(self):
        self.cleared = True


class SearchHandlerAutocompleteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_search_temporarily_suspends_and_restores_autocomplete(self):
        checkbox = QCheckBox()
        checkbox.setChecked(True)
        manager = FakeAutocompleteManager()
        main_window = SimpleNamespace(
            autocomplete_checkbox=checkbox,
            autocomplete_manager=manager,
            generation_status="idle",
            current_mode="generate",
        )
        handler = SearchHandler(main_window)

        handler._suspend_autocomplete_for_search()

        self.assertFalse(checkbox.isChecked())
        self.assertTrue(manager.cleared)

        handler._restore_autocomplete_after_search()

        self.assertTrue(checkbox.isChecked())
        self.assertEqual(manager.debounce_timer.started_with, manager.debounce_ms)

    def test_search_does_not_restore_autocomplete_in_idea_mode(self):
        checkbox = QCheckBox()
        checkbox.setChecked(True)
        manager = FakeAutocompleteManager()
        main_window = SimpleNamespace(
            autocomplete_checkbox=checkbox,
            autocomplete_manager=manager,
            generation_status="idle",
            current_mode="idea",
        )
        handler = SearchHandler(main_window)

        handler._suspend_autocomplete_for_search()
        handler._restore_autocomplete_after_search()

        self.assertFalse(checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()

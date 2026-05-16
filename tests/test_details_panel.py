import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.ui.details_panel import DetailsPanel


class DetailsPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        if hasattr(self, "panel"):
            self.panel.deleteLater()
            self.app.processEvents()

    def make_panel(self) -> DetailsPanel:
        self.panel = DetailsPanel()
        self.app.processEvents()
        return self.panel

    def test_exposes_expected_detail_widgets(self):
        panel = self.make_panel()

        panel.title_edit.setText("森の竜")
        panel.keywords_widget.set_tags(["エルフ", "ドラゴン"])
        panel.genre_widget.set_tags(["ファンタジー"])
        panel.synopsis_edit.setPlainText("森で竜に出会う。")

        self.assertEqual(panel.title_edit.text(), "森の竜")
        self.assertEqual(panel.keywords_widget.get_tags(), ["エルフ", "ドラゴン"])
        self.assertEqual(panel.genre_widget.get_tags(), ["ファンタジー"])
        self.assertEqual(panel.synopsis_edit.toPlainText(), "森で竜に出会う。")

    def test_transfer_buttons_emit_metadata_key(self):
        panel = self.make_panel()
        emitted = []
        panel.transfer_requested.connect(emitted.append)

        panel.title_transfer_button.click()
        panel.keywords_widget.transfer_button.click()
        panel.genre_widget.transfer_button.click()

        self.assertEqual(emitted, ["title", "keywords", "genres"])

    def test_idea_controls_emit_change_signal(self):
        panel = self.make_panel()
        emitted = []
        panel.idea_item_changed.connect(lambda: emitted.append(True))

        panel.idea_item_combo.setCurrentIndex(1)

        self.assertTrue(emitted)

    def test_thinking_prefill_transfer_signal(self):
        panel = self.make_panel()
        emitted = []
        panel.thinking_prefill_transfer_requested.connect(lambda: emitted.append(True))

        panel.assistant_thinking_prefill_transfer_button.click()

        self.assertEqual(emitted, [True])


if __name__ == "__main__":
    unittest.main()

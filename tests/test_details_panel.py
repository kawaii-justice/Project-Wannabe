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

    def test_project_details_round_trip(self):
        panel = self.make_panel()
        details = {
            "title": "森の竜",
            "keywords": ["エルフ", "ドラゴン"],
            "genres": ["ファンタジー"],
            "synopsis": "森で竜に出会う。",
            "setting": "古い森。",
            "plot": "迷子になり、竜と会話する。",
            "dialogue_level": "多い",
            "assistant_thinking_prefill_enabled": True,
            "assistant_thinking_prefill": "慎重に伏線を置く。",
            "rating": "r18",
        }

        panel.apply_project_details(details)

        self.assertEqual(panel.get_project_details(), details)

    def test_generation_data_omits_unspecified_dialogue_level(self):
        panel = self.make_panel()
        panel.title_edit.setText("森の竜")
        panel.dialogue_level_combo.setCurrentText("指定なし")

        generation_data = panel.get_generation_data()

        self.assertEqual(generation_data["metadata"]["title"], "森の竜")
        self.assertNotIn("dialogue_level", generation_data["metadata"])

    def test_apply_metadata_value_routes_to_matching_widget(self):
        panel = self.make_panel()

        panel.apply_metadata_value("keywords", "エルフ、森, ドラゴン")
        panel.apply_metadata_value("plot", "森で迷子になる。")

        self.assertEqual(panel.keywords_widget.get_tags(), ["エルフ", "ドラゴン", "森"])
        self.assertEqual(panel.plot_edit.toPlainText(), "森で迷子になる。")

    def test_thinking_prefill_available_disables_and_unchecks(self):
        panel = self.make_panel()
        panel.set_thinking_prefill_text("固定したい思考", enabled=True)

        panel.set_thinking_prefill_available(False)

        self.assertFalse(panel.assistant_thinking_prefill_checkbox.isChecked())
        self.assertFalse(panel.assistant_thinking_prefill_checkbox.isEnabled())
        self.assertFalse(panel.assistant_thinking_prefill_edit.isEnabled())

    def test_details_changed_signal_is_emitted(self):
        panel = self.make_panel()
        emitted = []
        panel.details_changed.connect(lambda: emitted.append(True))

        panel.title_edit.setText("森の竜")

        self.assertTrue(emitted)


if __name__ == "__main__":
    unittest.main()

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.core.prompt_builder import build_prompt_components
from src.core.settings import DEFAULT_SETTINGS
from src.ui.details_panel import DetailsPanel


class DetailsPromptIntegrationTest(unittest.TestCase):
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

    def test_details_panel_generation_data_feeds_prompt_builder_contract(self):
        panel = self.make_panel()
        panel.apply_project_details({
            "title": "森の竜",
            "keywords": ["エルフ", "ドラゴン"],
            "genres": ["ハイファンタジー"],
            "synopsis": "少女が森で竜と出会う。",
            "setting": "夜の森。",
            "plot": "竜が禁忌を警告する。",
            "dialogue_level": "多い",
            "rating": "r18",
        })
        ui_data = panel.get_generation_data()
        ui_data["authors_note"] = "次は竜が少女へ警告する。"
        ui_data["system_prompt"] = ""
        settings = DEFAULT_SETTINGS.copy()
        settings["authors_note_display_mode"] = "default"

        generate_components = build_prompt_components(
            "generate",
            "一行目。\n二行目。\n三行目。\n四行目。\n五行目。",
            ui_data,
            settings=settings,
        )
        idea_components = build_prompt_components(
            "idea",
            "",
            ui_data,
            settings=settings,
        )

        self.assertEqual(generate_components.rating, "r18")
        self.assertIn("# セリフ量:\n多い", generate_components.internal_input)
        self.assertIn("【この先の展開についての指示・メモ】", generate_components.internal_input)
        self.assertNotIn("セリフ量", idea_components.internal_input)


if __name__ == "__main__":
    unittest.main()

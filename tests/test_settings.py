import json
import os
import tempfile
import unittest

from src.core import settings as settings_module


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.original_get_config_path = settings_module.get_config_path
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.tmpdir.name, "config.json")
        settings_module.get_config_path = lambda: self.config_path

    def tearDown(self):
        settings_module.get_config_path = self.original_get_config_path
        self.tmpdir.cleanup()

    def test_load_settings_creates_missing_config(self):
        self.assertFalse(os.path.exists(self.config_path))

        loaded = settings_module.load_settings()

        self.assertTrue(os.path.exists(self.config_path))
        self.assertEqual(loaded["temperature"], 0.5)
        self.assertIn("koboldcpp_exe_path", loaded)
        self.assertIn("koboldcpp_config_path", loaded)

    def test_load_settings_fills_missing_keys_and_migrates_old_max_length(self):
        old_config = {
            "max_length": 777,
            "infinite_generation_behavior": {"idea": "manual"},
            "custom_user_key": "keep-me",
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(old_config, f, ensure_ascii=False)

        loaded = settings_module.load_settings()

        self.assertEqual(loaded["max_length_idea"], 777)
        self.assertEqual(loaded["max_length_generate"], 777)
        self.assertEqual(loaded["max_length_idea_thinking_off"], 777)
        self.assertEqual(loaded["max_length_generate_thinking_on"], 777)
        self.assertEqual(loaded["infinite_generation_behavior"]["idea"], "manual")
        self.assertEqual(loaded["infinite_generation_behavior"]["generate"], "immediate")
        self.assertEqual(loaded["custom_user_key"], "keep-me")

        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("koboldcpp_exe_path", saved)
        self.assertIn("koboldcpp_config_path", saved)


if __name__ == "__main__":
    unittest.main()

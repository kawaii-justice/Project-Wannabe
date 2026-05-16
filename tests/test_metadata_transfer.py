import unittest

from src.core.metadata_transfer import extract_metadata_value, normalize_metadata_value, parse_tag_lines


class MetadataTransferTest(unittest.TestCase):
    def test_extracts_requested_section_until_next_header(self):
        text = (
            "# タイトル:\n"
            "森の竜と迷子の少女\n\n"
            "# キーワード:\n"
            "- エルフ\n"
            "- ドラゴン\n\n"
            "# あらすじ:\n"
            "森で迷子になった少女が竜と出会う。"
        )

        self.assertEqual(
            extract_metadata_value(text, "keywords"),
            "- エルフ\n- ドラゴン",
        )

    def test_missing_section_returns_none(self):
        self.assertIsNone(extract_metadata_value("# タイトル:\n作品名", "plot"))

    def test_title_normalization_uses_first_line(self):
        self.assertEqual(
            normalize_metadata_value("title", "タイトル案\n補足説明"),
            "タイトル案",
        )

    def test_tag_lines_strip_list_markers(self):
        self.assertEqual(
            parse_tag_lines("- エルフ\n- ドラゴン\n  魔法  "),
            ["エルフ", "ドラゴン", "魔法"],
        )

    def test_unknown_key_raises_key_error(self):
        with self.assertRaises(KeyError):
            extract_metadata_value("# タイトル:\n作品名", "unknown")


if __name__ == "__main__":
    unittest.main()

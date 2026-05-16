import copy
import json
import unittest
from pathlib import Path

from src.core.prompt_builder import (
    build_chat_messages,
    build_prompt,
    build_prompt_components,
    format_metadata,
)
from src.core.settings import DEFAULT_SETTINGS


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "prompt_cases"
GOLDEN_CASES = [
    "gen_info",
    "idea_info",
    "cont_info_default",
    "cont_info_legacy",
]


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def load_case(name: str) -> dict:
    with (FIXTURE_DIR / f"{name}.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_expected_prompt(name: str) -> str:
    with (FIXTURE_DIR / f"{name}.expected.txt").open("r", encoding="utf-8") as handle:
        return normalize_newlines(handle.read())


def case_settings(case: dict) -> dict:
    settings = DEFAULT_SETTINGS.copy()
    settings.update(case.get("settings", {}))
    return settings


def render_case_prompt(case: dict) -> str:
    return build_prompt(
        case["current_mode"],
        case["main_text"],
        case["ui_data"],
        case.get("cont_prompt_order", "reference_first"),
        settings=case_settings(case),
    )


def parse_mistral_prompt(prompt: str) -> tuple[str, str, str]:
    assert prompt.startswith("[INST]")
    prompt_body, assistant_prefill = prompt[len("[INST]"):].split("[/INST]", 1)
    if "\n" in prompt_body:
        instruction, internal_input = prompt_body.split("\n", 1)
    else:
        instruction, internal_input = prompt_body, ""
    return instruction, internal_input, assistant_prefill


class PromptBuilderContractTest(unittest.TestCase):
    def test_golden_prompt_cases_match_dataset_format(self):
        for case_name in GOLDEN_CASES:
            with self.subTest(case=case_name):
                case = load_case(case_name)
                self.assertEqual(
                    normalize_newlines(render_case_prompt(case)),
                    load_expected_prompt(case_name),
                )

    def test_prompt_components_match_instruction_input_prefill_contract(self):
        for case_name in GOLDEN_CASES:
            with self.subTest(case=case_name):
                case = load_case(case_name)
                expected_instruction, expected_input, expected_prefill = parse_mistral_prompt(
                    load_expected_prompt(case_name)
                )

                components = build_prompt_components(
                    case["current_mode"],
                    case["main_text"],
                    case["ui_data"],
                    case.get("cont_prompt_order", "reference_first"),
                    settings=case_settings(case),
                )

                self.assertEqual(components.instruction_text, expected_instruction)
                self.assertEqual(components.internal_input, expected_input)
                self.assertEqual(components.assistant_prefill, expected_prefill)

    def test_metadata_order_and_idea_dialogue_omission_are_stable(self):
        metadata = {
            "plot": "最後に竜が王国の名を告げる。",
            "title": "森の竜",
            "dialogue_level": "多い",
            "genres": ["ハイファンタジー"],
            "keywords": ["エルフ", "ドラゴン"],
            "setting": "夜の森。",
            "synopsis": "少女が竜と出会う。",
        }

        generate_metadata = format_metadata(metadata, mode="generate")
        idea_metadata = format_metadata(metadata, mode="idea")

        self.assertEqual(
            generate_metadata,
            "# タイトル:\n森の竜\n\n"
            "# キーワード:\nエルフ\nドラゴン\n\n"
            "# ジャンル:\nハイファンタジー\n\n"
            "# あらすじ:\n少女が竜と出会う。\n\n"
            "# 設定:\n夜の森。\n\n"
            "# プロット:\n最後に竜が王国の名を告げる。\n\n"
            "# セリフ量:\n多い",
        )
        self.assertNotIn("セリフ量", idea_metadata)

    def test_cont_text_first_order_keeps_note_before_tail(self):
        case = load_case("cont_info_default")
        components = build_prompt_components(
            case["current_mode"],
            case["main_text"],
            case["ui_data"],
            "text_first",
            settings=case_settings(case),
        )

        body_index = components.internal_input.index("【本文】")
        reference_index = components.internal_input.index("【参考情報】")
        note_index = components.internal_input.index("【この先の展開についての指示・メモ】")
        tail_index = components.internal_input.index("三行目。木々の影がゆっくりと揺れた。")

        self.assertLess(body_index, reference_index)
        self.assertLess(note_index, tail_index)

    def test_chat_messages_are_rendered_from_same_components(self):
        case = load_case("cont_info_default")
        case = copy.deepcopy(case)
        case["ui_data"]["system_prompt"] = "あなたは小説執筆を支援します。"

        settings = case_settings(case)
        components = build_prompt_components(
            case["current_mode"],
            case["main_text"],
            case["ui_data"],
            case.get("cont_prompt_order", "reference_first"),
            settings=settings,
        )
        messages = build_chat_messages(
            case["current_mode"],
            case["main_text"],
            case["ui_data"],
            case.get("cont_prompt_order", "reference_first"),
            settings=settings,
        )

        self.assertEqual(messages[0], {"role": "system", "content": "あなたは小説執筆を支援します。"})
        self.assertEqual(
            messages[1],
            {
                "role": "user",
                "content": f"{components.instruction_text}\n{components.internal_input}",
            },
        )
        self.assertEqual(len(messages), 2)

    def test_autocomplete_uses_incomplete_current_line_as_assistant_prefill(self):
        ui_data = {
            "metadata": {
                "title": "森の竜",
                "keywords": ["警告"],
            },
            "rating": "general",
            "authors_note": "少女の緊張を高める。",
            "system_prompt": "",
        }
        main_text = (
            "一行目。森に入る。\n"
            "二行目。足音が増える。\n"
            "三行目。枝が折れる。\n"
            "四行目。少女は振り返る。\n"
            "五行目の途中"
        )
        settings = DEFAULT_SETTINGS.copy()
        settings["authors_note_display_mode"] = "default"

        components = build_prompt_components(
            "autocomplete",
            main_text,
            ui_data,
            "reference_first",
            settings=settings,
        )
        messages = build_chat_messages(
            "autocomplete",
            main_text,
            ui_data,
            "reference_first",
            settings=settings,
        )

        self.assertEqual(components.task_type, "CONT_INFO")
        self.assertEqual(components.assistant_prefill, "五行目の途中")
        self.assertNotIn("五行目の途中", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "assistant", "content": "五行目の途中"})


if __name__ == "__main__":
    unittest.main()

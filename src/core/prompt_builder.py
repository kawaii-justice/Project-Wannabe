from typing import Dict, Optional, Tuple, List # Add List
from .settings import load_settings, DEFAULT_SETTINGS # Import settings functions
from .dynamic_prompts import evaluate_dynamic_prompt # Import the new function

# --- Instruction Templates (Adjust based on the fine-tuned model's needs) ---
# These are examples and should match the expected format of your LLM.
INSTRUCTION_TEMPLATES = {
    "GEN_INFO": "以下の情報に基づいて小説本文を生成してください。",
    "GEN_ZERO": "自由に小説を生成してください。",
    "CONT_INFO": "参考情報と本文を踏まえ、最後の文章の自然な続きとなるように小説を生成してください。", # Updated
    "CONT_ZERO": "本文を踏まえ、最後の文章の自然な続きとなるように小説を生成してください。", # Updated
    "IDEA_INFO": "以下の情報に基づいて、完全な小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を生成してください。",
    "IDEA_ZERO": "自由に小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を生成してください。",
}

# INSTRUCTION_TEMPLATES = { # Keep the old commented out section if needed for reference
#     "GEN_INFO": "以下の情報を元に、新しい小説の冒頭部分を執筆してください。",
#     "GEN_ZERO": "新しい小説の冒頭部分を執筆してください。",
#     "CONT_INFO": "以下の本文の続きを、参考情報に基づいて執筆してください。",
#     "CONT_ZERO": "以下の本文の続きを執筆してください。",
#     "IDEA_INFO": "以下の情報を元に、小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を提案してください。各項目は `# 日本語名:` の形式で記述してください。",
#     "IDEA_ZERO": "新しい小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を提案してください。各項目は `# 日本語名:` の形式で記述してください。",
# }

# --- Metadata Formatting ---
METADATA_MAP = {
    "title": "タイトル",
    "keywords": "キーワード",
    "genres": "ジャンル",
    "synopsis": "あらすじ",
    "setting": "設定",
    "plot": "プロット",
    "dialogue_level": "セリフ量", # Add dialogue level
}

# Define the order for metadata in the prompt
INPUT_METADATA_ORDER_JA = [
    "タイトル", "キーワード", "ジャンル", "あらすじ", "設定", "プロット", "セリフ量"
]
# Create a reverse map for easier lookup
KEY_MAP_FROM_JA = {v: k for k, v in METADATA_MAP.items()}


def format_metadata(metadata: Dict[str, str | List[str]], mode: str = "generate") -> str:
    """
    Formats the metadata dictionary into a string for the prompt, respecting order.
    Applies dynamic prompt evaluation to keywords and genres, and strips quotes from them.
    Excludes 'dialogue_level' if mode is 'idea'.
    """
    output = []
    # Iterate based on the defined Japanese name order
    for japanese_name in INPUT_METADATA_ORDER_JA:
        key = KEY_MAP_FROM_JA.get(japanese_name)
        if not key:
            continue # Skip if the Japanese name isn't in our map

        # --- Skip dialogue_level if in idea mode ---
        if mode == "idea" and key == "dialogue_level":
            continue

        value = metadata.get(key) # Get value using the internal key ('title', 'dialogue_level', etc.)
        if value:
            # Handle list types (keywords, genres) - Apply dynamic prompts and strip quotes
            if key in ["keywords", "genres"] and isinstance(value, list):
                evaluated_tags = []
                for tag in value:
                    evaluated_tag = evaluate_dynamic_prompt(tag)
                    # Strip outer double quotes AFTER evaluation, as TagWidget already removed input quotes
                    # but dynamic evaluation might add them back via {"tag A"|tagB}
                    evaluated_tags.append(evaluated_tag.strip('"'))
                # Filter out any empty tags that might result from evaluation/stripping
                evaluated_tags = [tag for tag in evaluated_tags if tag]
                if evaluated_tags: # Only add if list is not empty after evaluation
                    # `- ` を付けずに改行で結合 (データセット側に合わせる)
                    output.append(f"# {japanese_name}:\n" + "\n".join(item for item in evaluated_tags))
            # Handle string types (title, synopsis, setting, plot, dialogue_level)
            # Dynamic prompts for these are handled in build_prompt before formatting
            elif isinstance(value, str) and value.strip():
                output.append(f"# {japanese_name}:\n{value.strip()}") # Keep original value here
            # Add other type handling here if necessary

    return "\n\n".join(output)


def split_main_text(text: str) -> tuple[str, str]:
    """
    Splits the input text into the main part and the tail, preserving all
    original lines. The tail is defined as the 3 lines ending at the
    second-to-last content line. The last content line itself is NOT part
    of the return value.

    Args:
        text: The main text input.

    Returns:
        A tuple containing (main_part_text, tail_text).
        Returns ("", "") if the text has less than 4 content lines,
        as a split is not meaningful.
    """
    if not text:
        return "", ""

    lines = text.splitlines()
    # Find indices of all lines with content
    content_line_indices = [i for i, line in enumerate(lines) if line.strip()]

    # If not enough content for a split, return empty parts.
    if len(content_line_indices) < 4:
        return "", ""

    # The tail is based on the second-to-last content line.
    # This is the end point of the tail (inclusive).
    tail_end_index = content_line_indices[-2]

    # The tail starts 2 lines before its end point (for a total of 3 lines).
    tail_start_index = tail_end_index - 2
    if tail_start_index < 0:
        tail_start_index = 0

    # The tail includes the end line, so we slice up to end_index + 1.
    tail_lines = lines[tail_start_index : tail_end_index + 1]
    tail_text = "\n".join(tail_lines)

    # The main part is everything before the tail's start.
    main_part_lines = lines[:tail_start_index]
    main_part_text = "\n".join(main_part_lines)

    return main_part_text, tail_text


def is_sentence_complete(text: str) -> bool:
    """
    Determines if the given text ends with a complete sentence.
    A sentence is considered complete if, after stripping trailing whitespace (excluding newlines),
    it ends with a period (。), a closing bracket (」), or a newline character (\n).
    """
    if not text:
        return False

    # 末尾から改行文字を除く空白文字をすべて取り除く
    # rstrip() はデフォルトで全ての空白文字を削除するため、改行文字を考慮する場合は手動で処理
    stripped_text = text.rstrip(" \t") # スペースとタブのみ削除

    # 処理後の文字列の末尾が、以下のいずれかの文字で終わっている場合、「完結している」と判定
    # 改行文字は、rstrip()で削除されていない元のテキストの末尾で確認する必要がある
    if stripped_text.endswith("。") or \
       stripped_text.endswith("」"):
        return True
    
    # 元のテキストの最後の文字が改行であるかを確認
    # ただし、改行のみの行は完結とみなさないため、strip()で空になる場合は除外
    if text.endswith("\n") and text.strip(): # テキストが空でないことを確認
        return True

    return False


def determine_task_and_instruction(
    current_mode: str,
    main_text: str,
    metadata: Dict[str, str | list[str]]
) -> Tuple[str, str]:
    """
    Determines the task type (GEN, CONT, IDEA) and corresponding instruction
    based on the UI state.

    Returns:
        Tuple[str, str]: (task_type, instruction_text)
    """
    has_main_text = bool(main_text.strip())

    # --- Determine Metadata Presence Explicitly ---
    has_title = bool(metadata.get("title", "").strip())
    has_keywords = bool(metadata.get("keywords", []))
    has_genres = bool(metadata.get("genres", []))
    has_synopsis = bool(metadata.get("synopsis", "").strip())
    has_setting = bool(metadata.get("setting", "").strip())
    has_plot = bool(metadata.get("plot", "").strip())
    # dialogue_level key only exists in metadata if it's not "指定なし"
    has_dialogue_level = "dialogue_level" in metadata

    # Combine checks based on mode
    # For generate/continue mode, any metadata counts
    has_any_metadata_for_gen_cont = (
        has_title or has_keywords or has_genres or has_synopsis or
        has_setting or has_plot or has_dialogue_level
    )
    # For idea mode, exclude dialogue_level
    has_any_metadata_for_idea = (
        has_title or has_keywords or has_genres or has_synopsis or
        has_setting or has_plot
    )

    # --- Determine Task Type ---
    task_type = "GEN_ZERO" # Default

    if current_mode == "generate":
        if not has_main_text:
            task_type = "GEN_INFO" if has_any_metadata_for_gen_cont else "GEN_ZERO"
        else: # has_main_text
            # Count content lines in main_text to decide between GEN and CONT
            content_lines = [line for line in main_text.splitlines() if line.strip()]
            num_content_lines = len(content_lines)

            if num_content_lines < 4:
                # If less than 4 content lines, treat as GEN task
                task_type = "GEN_INFO" if has_any_metadata_for_gen_cont else "GEN_ZERO"
            else:
                # If 4 or more content lines, treat as CONT task
                task_type = "CONT_INFO" if has_any_metadata_for_gen_cont else "CONT_ZERO"
    elif current_mode == "idea":
        task_type = "IDEA_INFO" if has_any_metadata_for_idea else "IDEA_ZERO"
    else:
        # Fallback or error handling for unknown mode
        print(f"Warning: Unknown mode '{current_mode}'. Defaulting to GEN_ZERO.")
        task_type = "GEN_ZERO"

    instruction_text = INSTRUCTION_TEMPLATES.get(task_type, "指示が見つかりません。") # Fallback
    # Returns the base instruction text without the rating part.
    return task_type, instruction_text


def build_prompt(
    current_mode: str,
    main_text: str,
    ui_data: dict, # Changed from metadata and rating_override
    cont_prompt_order: str = "reference_first" # Keep this setting
) -> str:
    """
    Builds the final prompt string based on UI state, settings, and the new format.

    Args:
        current_mode: The current operation mode ('generate' or 'idea').
        main_text: The main text input from the UI.
        ui_data: Dictionary containing metadata, rating, and authors_note from the UI.
        cont_prompt_order: The desired order for continuation prompts ('text_first' or 'reference_first').
    """
    # --- Extract data from ui_data and apply dynamic prompts ---
    raw_metadata = ui_data.get("metadata", {})
    rating_override = ui_data.get("rating") # Rating from UI details tab
    raw_authors_note = ui_data.get("authors_note", "")

    # Apply dynamic prompts to relevant fields BEFORE further processing
    metadata = {
        "title": evaluate_dynamic_prompt(raw_metadata.get("title", "")),
        "keywords": raw_metadata.get("keywords", []), # Evaluate keywords in format_metadata
        "genres": raw_metadata.get("genres", []),   # Evaluate genres in format_metadata
        "synopsis": evaluate_dynamic_prompt(raw_metadata.get("synopsis", "")),
        "setting": evaluate_dynamic_prompt(raw_metadata.get("setting", "")),
        "plot": evaluate_dynamic_prompt(raw_metadata.get("plot", "")),
        # dialogue_level is not a free text field, no evaluation needed
        "dialogue_level": raw_metadata.get("dialogue_level")
    }
    # Filter out dialogue_level if it's None (wasn't present in raw_metadata)
    if metadata["dialogue_level"] is None:
        del metadata["dialogue_level"]

    authors_note = evaluate_dynamic_prompt(raw_authors_note)

    # --- Determine rating to use ---
    if rating_override:
        rating_to_use = rating_override
    else:
        # Load default rating from settings if no override is provided
        settings = load_settings()
        rating_to_use = settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])

    # --- Determine base instruction (without rating) ---
    task_type, base_instruction_text = determine_task_and_instruction(
        current_mode, main_text, metadata # Pass metadata extracted from ui_data
    )

    # --- Format metadata string ---
    # Pass current_mode to format_metadata to handle exclusion logic
    metadata_input_string = format_metadata(metadata, mode=current_mode)
    internal_input = ""

    # --- Format metadata string ---
    metadata_input_string = format_metadata(metadata, mode=current_mode)
    internal_input = ""
    prompt_suffix = "" # Initialize prompt_suffix

    # --- Build internal_input and prompt_suffix based on task type ---
    if task_type.startswith("GEN"):
        # GEN tasks: internal_input is metadata_input_string.
        # If main_text exists (less than 4 content lines), it becomes the prompt_suffix.
        # Whitespace and newlines are preserved.
        internal_input = metadata_input_string
        if main_text:
            prompt_suffix = main_text
    elif task_type.startswith("IDEA"):
        # IDEA tasks: internal_input is metadata_input_string. No prompt_suffix from main_text.
        internal_input = metadata_input_string
        # IDEA tasks might have their own suffix logic via IdeaProcessor, but that's external to build_prompt's main_text handling.
    elif task_type.startswith("CONT"):
        # CONT tasks: Preserve whitespace. Split text into main_part, tail, and suffix.
        try:
            if is_sentence_complete(main_text):
                # 条件A: 文章が完結している場合
                prompt_suffix = "" # 空文字列に設定

                lines = main_text.splitlines()
                # 最後のコンテンツ行のインデックスを見つける
                last_content_line_index = -1
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip():
                        last_content_line_index = i
                        break
                
                if last_content_line_index != -1:
                    # 最後のコンテンツ行を含めて、その行で終わる合計3行分をtailとして切り出す
                    tail_end_index = last_content_line_index
                    tail_start_index = max(0, tail_end_index - 2) # 0未満にならないように調整

                    tail_lines = lines[tail_start_index : tail_end_index + 1]
                    tail = "\n".join(tail_lines)

                    main_part_lines = lines[:tail_start_index]
                    main_part = "\n".join(main_part_lines)
                else:
                    # main_textが空行のみの場合など、コンテンツ行がない場合
                    main_part = ""
                    tail = ""

            else:
                # 条件B: 文章が途中で終わっている場合 (既存のロジックを維持)
                # Find the last content line for the suffix
                lines = main_text.splitlines()
                last_content_line = ""
                for line in reversed(lines):
                    if line.strip():
                        last_content_line = line
                        break
                prompt_suffix = last_content_line.strip()

                # Split the rest of the text using the new logic (preserves whitespace)
                main_part, tail = split_main_text(main_text)

            # --- Truncate main_part based on max_context_length setting ---
            settings = load_settings()
            max_len = settings.get("max_context_length", DEFAULT_SETTINGS["max_context_length"])
            if len(main_part) > max_len:
                main_part = main_part[-max_len:]
            # --- End of truncation logic ---

        except Exception as e:
            print(f"Error processing main text for CONT task: {e}")
            main_part, tail, prompt_suffix = "", "", main_text # Fallback

        # Create blocks (handle empty cases by setting to None)
        # Do not strip main_part to preserve its whitespace
        main_part_block = f"【本文】\n```\n{main_part}\n```" if main_part else None
        reference_block = f"【参考情報】\n```\n{metadata_input_string}\n```" if metadata_input_string else None
        authors_note_block = f"【オーサーズノート】\n```\n{authors_note.strip()}\n```" if authors_note.strip() else None

        input_parts = []

        # 1. Add Reference and Main Part based on order
        if cont_prompt_order == 'reference_first':
            if reference_block: input_parts.append(reference_block)
            if main_part_block: input_parts.append(main_part_block)
        else: # 'text_first'
            if main_part_block: input_parts.append(main_part_block)
            if reference_block: input_parts.append(reference_block)

        # 2. Add Author's Note
        if authors_note_block:
            input_parts.append(authors_note_block)

        # 3. Add Tail Text (tail preserves whitespace from split_main_text)
        if tail:
            input_parts.append(tail)

        # Join parts with a single newline
        # Filter out None values before joining
        internal_input = "\n".join(filter(None, input_parts))

    # --- Final Prompt Formatting (Mistral Instruct style) ---
    # Append rating to the base instruction
    final_instruction = f"{base_instruction_text} レーティング: {rating_to_use}"

    if internal_input:
        # Ensure there's a newline between instruction and input if input exists
        prompt = f"[INST]{final_instruction}\n{internal_input}[/INST]{prompt_suffix}"
    else:
        # No extra newline if there's no input
        prompt = f"[INST]{final_instruction}[/INST]{prompt_suffix}"

    return prompt

# --- Example Usage (Updated for new build_prompt signature) ---
if __name__ == "__main__":
    # Example ui_data structure
    ui_data_gen_meta = {
        "metadata": {"title": "星降る夜の冒険", "keywords": ["ファンタジー", "魔法"], "synopsis": "見習い魔法使いのリナが、失われた星のかけらを探す旅に出る。"},
        "rating": "general",
        "authors_note": ""
    }
    ui_data_cont_zero = {
        "metadata": {},
        "rating": "general",
        "authors_note": "次はもっとアクションシーンを増やしたい。"
    }
    ui_data_idea_meta = {
        "metadata": {"genres": ["SF", "学園"], "setting": "近未来の日本。特殊能力を持つ生徒が集まる高校。"},
        "rating": "general",
        "authors_note": ""
    }
    ui_data_gen_zero = {
        "metadata": {},
        "rating": "r18",
        "authors_note": ""
    }
    ui_data_cont_meta_text_first = {
        "metadata": {"keywords": ["冒険", "宝探し"], "setting": "南海の孤島"},
        "rating": "general",
        "authors_note": "地図の謎を強調する。\n登場人物の驚きを描写。"
    }
    ui_data_cont_meta_ref_first = {
        "metadata": {"keywords": ["冒険", "宝探し"], "setting": "南海の孤島"},
        "rating": "general",
        "authors_note": "地図の謎を強調する。\n登場人物の驚きを描写。"
    }


    # Scenario 1: Generate new story with metadata
    prompt1 = build_prompt(current_mode="generate", main_text="", ui_data=ui_data_gen_meta)
    print("--- Scenario 1: GEN_INFO ---")
    print(prompt1)
    print("-" * 20)

    # Scenario 2: Continue story with no metadata (but with author's note)
    text2 = "リナは杖を握りしめ、暗い森へと足を踏み入れた。\n風が不気味に木々を揺らす。\n何かが潜んでいる気配がした。\n彼女は息をのんだ。" # 4 lines
    prompt2 = build_prompt(current_mode="generate", main_text=text2, ui_data=ui_data_cont_zero)
    print("--- Scenario 2: CONT_ZERO (with Author's Note) ---")
    print(prompt2)
    print("-" * 20)

    # Scenario 3: Generate ideas with some metadata
    prompt3 = build_prompt(current_mode="idea", main_text="", ui_data=ui_data_idea_meta)
    print("--- Scenario 3: IDEA_INFO ---")
    print(prompt3)
    print("-" * 20)

    # Scenario 4: Generate new story with no metadata (R18 rating)
    prompt4 = build_prompt(current_mode="generate", main_text="", ui_data=ui_data_gen_zero)
    print("--- Scenario 4: GEN_ZERO (R18) ---")
    print(prompt4)
    print("-" * 20)

    # Scenario 5: Continue story WITH metadata & Author's Note, order: text_first
    text5 = "古い地図を広げると、そこには見たこともない島が描かれていた。\nインクが滲んで、一部は判読できない。\n島の中心には奇妙な印がある。\nこれは一体……？" # 4 lines
    prompt5 = build_prompt(current_mode="generate", main_text=text5, ui_data=ui_data_cont_meta_text_first, cont_prompt_order="text_first")
    print("--- Scenario 5: CONT_INFO (text_first) ---")
    print(prompt5)
    print("-" * 20)

    # Scenario 6: Continue story WITH metadata & Author's Note, order: reference_first (Default)
    text6 = "古い地図を広げると、そこには見たこともない島が描かれていた。\nインクが滲んで、一部は判読できない。\n島の中心には奇妙な印がある。\nこれは一体……？" # 4 lines
    prompt6 = build_prompt(current_mode="generate", main_text=text6, ui_data=ui_data_cont_meta_ref_first, cont_prompt_order="reference_first")
    print("--- Scenario 6: CONT_INFO (reference_first) ---")
    print(prompt6)
    print("-" * 20)

    # Scenario 7: Continue story with only 2 lines of text
    text7 = "扉を開けると、そこは真っ暗だった。\n冷たい空気が頬を撫でる。" # 2 lines
    ui_data7 = { "metadata": {}, "rating": "general", "authors_note": "ホラー要素を強めに" }
    prompt7 = build_prompt(current_mode="generate", main_text=text7, ui_data=ui_data7)
    print("--- Scenario 7: CONT_ZERO (Short text) ---")
    print(prompt7)
    print("-" * 20)

    # Scenario 8: Continue story with empty author's note
    text8 = "リナは杖を握りしめ、暗い森へと足を踏み入れた。\n風が不気味に木々を揺らす。\n何かが潜んでいる気配がした。\n彼女は息をのんだ。" # 4 lines
    ui_data8 = { "metadata": {"keywords": ["森", "夜"]}, "rating": "general", "authors_note": "   " } # Empty note
    prompt8 = build_prompt(current_mode="generate", main_text=text8, ui_data=ui_data8)
    print("--- Scenario 8: CONT_INFO (Empty Author's Note) ---")
    print(prompt8)
    print("-" * 20)
    meta1 = {"title": "星降る夜の冒険", "keywords": ["ファンタジー", "魔法"], "synopsis": "見習い魔法使いのリナが、失われた星のかけらを探す旅に出る。"}
    prompt1 = build_prompt(current_mode="generate", main_text="", metadata=meta1)
    print("--- Scenario 1: GEN_INFO ---")
    print(prompt1)
    print("-" * 20)

    # Scenario 2: Continue story with no metadata (cont_prompt_order doesn't apply)
    text2 = "リナは杖を握りしめ、暗い森へと足を踏み入れた。"
    prompt2 = build_prompt(current_mode="generate", main_text=text2, metadata={})
    print("--- Scenario 2: CONT_ZERO ---")
    print(prompt2)
    print("-" * 20)

    # Scenario 3: Generate ideas with some metadata (cont_prompt_order doesn't apply)
    meta3 = {"genres": ["SF", "学園"], "setting": "近未来の日本。特殊能力を持つ生徒が集まる高校。"}
    prompt3 = build_prompt(current_mode="idea", main_text="", metadata=meta3)
    print("--- Scenario 3: IDEA_INFO ---")
    print(prompt3)
    print("-" * 20)

    # Scenario 4: Generate new story with no metadata (cont_prompt_order doesn't apply)
    prompt4 = build_prompt(current_mode="generate", main_text="", metadata={})
    print("--- Scenario 4: GEN_ZERO ---")
    print(prompt4)
    print("-" * 20)

    # Scenario 5: Continue story WITH metadata, order: text_first
    text5 = "古い地図を広げると、そこには見たこともない島が描かれていた。"
    meta5 = {"keywords": ["冒険", "宝探し"], "setting": "南海の孤島"}
    prompt5 = build_prompt(current_mode="generate", main_text=text5, metadata=meta5, cont_prompt_order="text_first")
    print("--- Scenario 5: CONT_INFO (text_first) ---")
    print(prompt5)
    print("-" * 20)

    # Scenario 6: Continue story WITH metadata, order: reference_first (Default)
    text6 = "古い地図を広げると、そこには見たこともない島が描かれていた。"
    meta6 = {"keywords": ["冒険", "宝探し"], "setting": "南海の孤島"}
    prompt6 = build_prompt(current_mode="generate", main_text=text6, metadata=meta6, cont_prompt_order="reference_first")
    print("--- Scenario 6: CONT_INFO (reference_first) ---")
    print(prompt6)
    print("-" * 20)

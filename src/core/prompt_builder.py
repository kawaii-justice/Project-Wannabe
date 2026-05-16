from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

from .settings import load_settings, DEFAULT_SETTINGS
from .dynamic_prompts import evaluate_dynamic_prompt
from .context_utils import get_true_max_context_length, count_tokens, get_available_context

# --- Instruction Templates (Adjust based on the fine-tuned model's needs) ---
INSTRUCTION_TEMPLATES = {
    "GEN_INFO": "以下の情報に基づいて小説本文を生成してください。",
    "GEN_ZERO": "自由に小説を生成してください。",
    "CONT_INFO": "参考情報と本文を踏まえ、最後の文章の自然な続きとなるように小説を生成してください。",
    "CONT_ZERO": "本文を踏まえ、最後の文章の自然な続きとなるように小説を生成してください。",
    "IDEA_INFO": "以下の情報に基づいて、完全な小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を生成してください。",
    "IDEA_ZERO": "自由に小説のアイデア（タイトル、キーワード、ジャンル、あらすじ、設定、プロット）を生成してください。",
}

# --- Metadata Formatting ---
METADATA_MAP = {
    "title": "タイトル",
    "keywords": "キーワード",
    "genres": "ジャンル",
    "synopsis": "あらすじ",
    "setting": "設定",
    "plot": "プロット",
    "dialogue_level": "セリフ量",
}

INPUT_METADATA_ORDER_JA = [
    "タイトル", "キーワード", "ジャンル", "あらすじ", "設定", "プロット", "セリフ量"
]
KEY_MAP_FROM_JA = {v: k for k, v in METADATA_MAP.items()}


@dataclass
class PromptComponents:
    task_type: str
    instruction_text: str
    internal_input: str
    tail_text: str
    assistant_prefill: str
    rating: str
    system_prompt: str


def format_metadata(metadata: Dict[str, str | List[str]], mode: str = "generate") -> str:
    output = []
    for japanese_name in INPUT_METADATA_ORDER_JA:
        key = KEY_MAP_FROM_JA.get(japanese_name)
        if not key:
            continue
        if mode == "idea" and key == "dialogue_level":
            continue

        value = metadata.get(key)
        if value:
            if key in ["keywords", "genres"] and isinstance(value, list):
                evaluated_tags = []
                for tag in value:
                    evaluated_tag = evaluate_dynamic_prompt(tag)
                    evaluated_tags.append(evaluated_tag.strip('"'))
                evaluated_tags = [tag for tag in evaluated_tags if tag]
                if evaluated_tags:
                    output.append(f"# {japanese_name}:\n" + "\n".join(item for item in evaluated_tags))
            elif isinstance(value, str) and value.strip():
                output.append(f"# {japanese_name}:\n{value.strip()}")

    return "\n\n".join(output)


def split_main_text(text: str) -> tuple[str, str]:
    if not text:
        return "", ""

    lines = text.splitlines()
    content_line_indices = [i for i, line in enumerate(lines) if line.strip()]
    if len(content_line_indices) < 4:
        return "", ""

    tail_end_index = content_line_indices[-2]
    tail_start_index = max(0, tail_end_index - 2)

    tail_lines = lines[tail_start_index: tail_end_index + 1]
    tail_text = "\n".join(tail_lines)

    main_part_lines = lines[:tail_start_index]
    main_part_text = "\n".join(main_part_lines)

    return main_part_text, tail_text


def is_sentence_complete(text: str) -> bool:
    if not text:
        return False

    stripped_text = text.rstrip(" \t")
    if stripped_text.endswith("。") or stripped_text.endswith("」"):
        return True
    if text.endswith("\n") and text.strip():
        return True
    return False


def determine_task_and_instruction(
    current_mode: str,
    main_text: str,
    metadata: Dict[str, str | list[str]]
) -> Tuple[str, str]:
    has_main_text = bool(main_text.strip())

    has_title = bool(metadata.get("title", "").strip())
    has_keywords = bool(metadata.get("keywords", []))
    has_genres = bool(metadata.get("genres", []))
    has_synopsis = bool(metadata.get("synopsis", "").strip())
    has_setting = bool(metadata.get("setting", "").strip())
    has_plot = bool(metadata.get("plot", "").strip())
    has_dialogue_level = "dialogue_level" in metadata

    has_any_metadata_for_gen_cont = (
        has_title or has_keywords or has_genres or has_synopsis or
        has_setting or has_plot or has_dialogue_level
    )
    has_any_metadata_for_idea = (
        has_title or has_keywords or has_genres or has_synopsis or
        has_setting or has_plot
    )

    task_type = "GEN_ZERO"

    if current_mode in ("generate", "autocomplete"):
        if not has_main_text:
            task_type = "GEN_INFO" if has_any_metadata_for_gen_cont else "GEN_ZERO"
        else:
            content_lines = [line for line in main_text.splitlines() if line.strip()]
            if len(content_lines) < 4:
                task_type = "GEN_INFO" if has_any_metadata_for_gen_cont else "GEN_ZERO"
            else:
                task_type = "CONT_INFO" if has_any_metadata_for_gen_cont else "CONT_ZERO"
    elif current_mode == "idea":
        task_type = "IDEA_INFO" if has_any_metadata_for_idea else "IDEA_ZERO"
    else:
        print(f"Warning: Unknown mode '{current_mode}'. Defaulting to GEN_ZERO.")

    instruction_text = INSTRUCTION_TEMPLATES.get(task_type, "指示が見つかりません。")
    return task_type, instruction_text


def _normalize_ui_data(
    ui_data: dict,
    settings: Optional[dict] = None,
) -> tuple[Dict[str, str | list[str]], str, str, str]:
    raw_metadata = ui_data.get("metadata", {})
    rating_override = ui_data.get("rating")
    raw_authors_note = ui_data.get("authors_note", "")
    raw_system_prompt = ui_data.get("system_prompt", "")

    metadata = {
        "title": evaluate_dynamic_prompt(raw_metadata.get("title", "")),
        "keywords": raw_metadata.get("keywords", []),
        "genres": raw_metadata.get("genres", []),
        "synopsis": evaluate_dynamic_prompt(raw_metadata.get("synopsis", "")),
        "setting": evaluate_dynamic_prompt(raw_metadata.get("setting", "")),
        "plot": evaluate_dynamic_prompt(raw_metadata.get("plot", "")),
        "dialogue_level": raw_metadata.get("dialogue_level"),
    }
    if metadata["dialogue_level"] is None:
        del metadata["dialogue_level"]

    settings = settings or load_settings()
    rating_to_use = rating_override or settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])
    authors_note = evaluate_dynamic_prompt(raw_authors_note)
    system_prompt = evaluate_dynamic_prompt(raw_system_prompt).strip()

    return metadata, rating_to_use, authors_note, system_prompt


def build_prompt_components(
    current_mode: str,
    main_text: str,
    ui_data: dict,
    cont_prompt_order: str = "reference_first",
    settings: Optional[dict] = None,
) -> PromptComponents:
    settings = settings or load_settings()
    metadata, rating_to_use, authors_note, system_prompt = _normalize_ui_data(ui_data, settings)
    task_type, base_instruction_text = determine_task_and_instruction(current_mode, main_text, metadata)

    metadata_input_string = format_metadata(metadata, mode=current_mode)
    internal_input = ""
    assistant_prefill = ""
    tail_text = ""

    if task_type.startswith("GEN"):
        internal_input = metadata_input_string
        if main_text:
            assistant_prefill = main_text
    elif task_type.startswith("IDEA"):
        internal_input = metadata_input_string
    elif task_type.startswith("CONT"):
        try:
            if current_mode == "autocomplete":
                last_newline_index = main_text.rfind("\n")
                if last_newline_index != -1:
                    assistant_prefill = main_text[last_newline_index + 1:]
                    context_text = main_text[:last_newline_index + 1]
                    lines = context_text.splitlines(keepends=True)
                    if len(lines) > 3:
                        main_part = "".join(lines[:-3])
                        tail = "".join(lines[-3:])
                    else:
                        main_part = ""
                        tail = "".join(lines)
                else:
                    assistant_prefill = main_text
                    main_part, tail = "", ""
            else:
                if is_sentence_complete(main_text):
                    assistant_prefill = ""
                    lines = main_text.splitlines()
                    last_content_line_index = -1
                    for i in range(len(lines) - 1, -1, -1):
                        if lines[i].strip():
                            last_content_line_index = i
                            break

                    if last_content_line_index != -1:
                        tail_end_index = last_content_line_index
                        tail_start_index = max(0, tail_end_index - 2)
                        tail_lines = lines[tail_start_index: tail_end_index + 1]
                        tail = "\n".join(tail_lines)
                        main_part_lines = lines[:tail_start_index]
                        main_part = "\n".join(main_part_lines)
                    else:
                        main_part = ""
                        tail = ""
                else:
                    lines = main_text.splitlines()
                    last_content_line = ""
                    for line in reversed(lines):
                        if line.strip():
                            last_content_line = line
                            break
                    assistant_prefill = last_content_line.strip()
                    main_part, tail = split_main_text(main_text)
        except Exception as e:
            print(f"Error processing main text for CONT task: {e}")
            main_part, tail, assistant_prefill = "", "", main_text

        main_part_block = f"【本文】\n```\n{main_part}\n```" if main_part else None
        reference_block = f"【参考情報】\n```\n{metadata_input_string}\n```" if metadata_input_string else None
        display_mode = settings.get("authors_note_display_mode", "default")
        if display_mode == "legacy":
            authors_note_block = f"【オーサーズノート】\n```\n{authors_note.strip()}\n```" if authors_note.strip() else None
        else:
            authors_note_block = f"【この先の展開についての指示・メモ】\n```\n{authors_note.strip()}\n```" if authors_note.strip() else None

        input_parts = []
        if cont_prompt_order == "reference_first":
            if reference_block:
                input_parts.append(reference_block)
            if main_part_block:
                input_parts.append(main_part_block)
        else:
            if main_part_block:
                input_parts.append(main_part_block)
            if reference_block:
                input_parts.append(reference_block)

        if authors_note_block:
            input_parts.append(authors_note_block)
        if tail:
            input_parts.append(tail)

        tail_text = tail
        internal_input = "\n".join(filter(None, input_parts))

    instruction_text = f"{base_instruction_text} レーティング: {rating_to_use}"
    return PromptComponents(
        task_type=task_type,
        instruction_text=instruction_text,
        internal_input=internal_input,
        tail_text=tail_text,
        assistant_prefill=assistant_prefill,
        rating=rating_to_use,
        system_prompt=system_prompt,
    )


def render_mistral_prompt(components: PromptComponents) -> str:
    if components.internal_input:
        return f"[INST]{components.instruction_text}\n{components.internal_input}[/INST]{components.assistant_prefill}"
    return f"[INST]{components.instruction_text}[/INST]{components.assistant_prefill}"


def build_prompt(
    current_mode: str,
    main_text: str,
    ui_data: dict,
    cont_prompt_order: str = "reference_first",
    settings: Optional[dict] = None,
) -> str:
    return render_mistral_prompt(
        build_prompt_components(current_mode, main_text, ui_data, cont_prompt_order, settings=settings)
    )


def build_chat_messages(
    current_mode: str,
    main_text: str,
    ui_data: dict,
    cont_prompt_order: str = "reference_first",
    settings: Optional[dict] = None,
) -> list[dict[str, str]]:
    components = build_prompt_components(
        current_mode,
        main_text,
        ui_data,
        cont_prompt_order,
        settings=settings,
    )
    messages: list[dict[str, str]] = []

    if components.system_prompt:
        messages.append({"role": "system", "content": components.system_prompt})

    user_content = components.instruction_text
    if components.internal_input:
        user_content = f"{user_content}\n{components.internal_input}"
    messages.append({"role": "user", "content": user_content})

    if components.assistant_prefill:
        messages.append({"role": "assistant", "content": components.assistant_prefill})

    return messages


def serialize_chat_messages_for_token_count(messages: list[dict[str, str]]) -> str:
    chunks = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        chunks.append(f"<{role}>\n{content}")
    return "\n\n".join(chunks)


async def build_prompt_with_compression(
    base_url: str,
    current_mode: str,
    main_text: str,
    ui_data: dict,
    cont_prompt_order: str = "reference_first",
    compression_mode: Optional[str] = None,
    max_length_idea: Optional[int] = None,
    max_length_generate: Optional[int] = None,
) -> Tuple[str, int, bool, Optional[int], Optional[int]]:
    settings = load_settings()
    mode = compression_mode or settings.get(
        "compression_mode",
        DEFAULT_SETTINGS.get("compression_mode", "token_dynamic")
    )

    if current_mode == "idea":
        max_out = max_length_idea or settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
    else:
        max_out = max_length_generate or settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

    true_ctx = await get_true_max_context_length(base_url)
    if true_ctx is None:
        true_ctx = DEFAULT_SETTINGS.get("max_main_text_chars", 8192)

    available_ctx = get_available_context(true_ctx, max_out)
    if available_ctx is None:
        prompt = build_prompt(current_mode, main_text, ui_data, cont_prompt_order, settings=settings)
        total = await count_tokens(base_url, prompt) or 0
        return prompt, total, False, (len(main_text) or None), (len(main_text) or None)

    def _build(mt: str) -> str:
        return build_prompt(current_mode, mt, ui_data, cont_prompt_order, settings=settings)

    prompt = _build(main_text)
    total_tokens = await count_tokens(base_url, prompt) or 0
    original_body_chars = len(main_text)

    if total_tokens <= available_ctx or not main_text.strip() or mode == "none":
        return prompt, total_tokens, False, (original_body_chars or None), (original_body_chars or None)

    if mode == "char_trim":
        max_chars = settings.get("max_main_text_chars", DEFAULT_SETTINGS.get("max_main_text_chars", 8000))
        if max_chars > 0 and len(main_text) > max_chars:
            truncated = main_text[-max_chars:]
            prompt = _build(truncated)
            total_tokens = await count_tokens(base_url, prompt) or 0
            if total_tokens <= available_ctx:
                return prompt, total_tokens, False, original_body_chars, len(truncated)
            return prompt, total_tokens, True, original_body_chars, len(truncated)
        return prompt, total_tokens, True, original_body_chars, len(main_text)

    if mode == "token_dynamic":
        step_chars = max(1, int(settings.get(
            "token_compression_step_chars",
            DEFAULT_SETTINGS.get("token_compression_step_chars", 100)
        )))
        offset_chars = int(settings.get(
            "token_compression_offset_chars",
            DEFAULT_SETTINGS.get("token_compression_offset_chars", 4000)
        ))

        body_tokens = await count_tokens(base_url, main_text) or 0
        text_len = max(len(main_text), 1)
        tokens_per_char = body_tokens / text_len if text_len > 0 else 1.0

        other_tokens_est = max(total_tokens - body_tokens, 0)
        available_for_body = max(available_ctx - other_tokens_est, 0)
        if available_for_body <= 0:
            return prompt, total_tokens, True, original_body_chars, original_body_chars

        est_body_chars = int(available_for_body / max(tokens_per_char, 1e-6))
        start_index = max(0, len(main_text) - est_body_chars - offset_chars)

        best_prompt = prompt
        best_tokens = total_tokens
        best_body_chars = original_body_chars

        cut_index = start_index
        while cut_index < len(main_text):
            truncated = main_text[cut_index:]
            cand_prompt = _build(truncated)
            cand_tokens = await count_tokens(base_url, cand_prompt) or 0
            cand_body_chars = len(truncated)

            if cand_tokens <= available_ctx:
                return cand_prompt, cand_tokens, False, original_body_chars, cand_body_chars

            if cand_tokens < best_tokens:
                best_tokens = cand_tokens
                best_prompt = cand_prompt
                best_body_chars = cand_body_chars

            cut_index += step_chars

        return best_prompt, best_tokens, True, original_body_chars, best_body_chars

    return prompt, total_tokens, (total_tokens > available_ctx), original_body_chars, original_body_chars


async def build_chat_messages_with_compression(
    base_url: str,
    current_mode: str,
    main_text: str,
    ui_data: dict,
    cont_prompt_order: str = "reference_first",
    compression_mode: Optional[str] = None,
    max_length_idea: Optional[int] = None,
    max_length_generate: Optional[int] = None,
) -> Tuple[list[dict[str, str]], int, bool, Optional[int], Optional[int]]:
    settings = load_settings()
    mode = compression_mode or settings.get(
        "compression_mode",
        DEFAULT_SETTINGS.get("compression_mode", "token_dynamic")
    )

    if current_mode == "idea":
        max_out = max_length_idea or settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
    else:
        max_out = max_length_generate or settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

    true_ctx = await get_true_max_context_length(base_url)
    if true_ctx is None:
        true_ctx = DEFAULT_SETTINGS.get("max_main_text_chars", 8192)

    available_ctx = get_available_context(true_ctx, max_out)
    if available_ctx is None:
        messages = build_chat_messages(current_mode, main_text, ui_data, cont_prompt_order, settings=settings)
        total = await count_tokens(base_url, serialize_chat_messages_for_token_count(messages)) or 0
        return messages, total, False, (len(main_text) or None), (len(main_text) or None)

    def _build(mt: str) -> list[dict[str, str]]:
        return build_chat_messages(current_mode, mt, ui_data, cont_prompt_order, settings=settings)

    messages = _build(main_text)
    total_tokens = await count_tokens(base_url, serialize_chat_messages_for_token_count(messages)) or 0
    original_body_chars = len(main_text)

    if total_tokens <= available_ctx or not main_text.strip() or mode == "none":
        return messages, total_tokens, False, (original_body_chars or None), (original_body_chars or None)

    if mode == "char_trim":
        max_chars = settings.get("max_main_text_chars", DEFAULT_SETTINGS.get("max_main_text_chars", 8000))
        if max_chars > 0 and len(main_text) > max_chars:
            truncated = main_text[-max_chars:]
            messages = _build(truncated)
            total_tokens = await count_tokens(base_url, serialize_chat_messages_for_token_count(messages)) or 0
            if total_tokens <= available_ctx:
                return messages, total_tokens, False, original_body_chars, len(truncated)
            return messages, total_tokens, True, original_body_chars, len(truncated)
        return messages, total_tokens, True, original_body_chars, len(main_text)

    if mode == "token_dynamic":
        step_chars = max(1, int(settings.get(
            "token_compression_step_chars",
            DEFAULT_SETTINGS.get("token_compression_step_chars", 100)
        )))
        offset_chars = int(settings.get(
            "token_compression_offset_chars",
            DEFAULT_SETTINGS.get("token_compression_offset_chars", 4000)
        ))

        body_tokens = await count_tokens(base_url, main_text) or 0
        text_len = max(len(main_text), 1)
        tokens_per_char = body_tokens / text_len if text_len > 0 else 1.0

        other_tokens_est = max(total_tokens - body_tokens, 0)
        available_for_body = max(available_ctx - other_tokens_est, 0)
        if available_for_body <= 0:
            return messages, total_tokens, True, original_body_chars, original_body_chars

        est_body_chars = int(available_for_body / max(tokens_per_char, 1e-6))
        start_index = max(0, len(main_text) - est_body_chars - offset_chars)

        best_messages = messages
        best_tokens = total_tokens
        best_body_chars = original_body_chars

        cut_index = start_index
        while cut_index < len(main_text):
            truncated = main_text[cut_index:]
            cand_messages = _build(truncated)
            cand_tokens = await count_tokens(base_url, serialize_chat_messages_for_token_count(cand_messages)) or 0
            cand_body_chars = len(truncated)

            if cand_tokens <= available_ctx:
                return cand_messages, cand_tokens, False, original_body_chars, cand_body_chars

            if cand_tokens < best_tokens:
                best_tokens = cand_tokens
                best_messages = cand_messages
                best_body_chars = cand_body_chars

            cut_index += step_chars

        return best_messages, best_tokens, True, original_body_chars, best_body_chars

    return messages, total_tokens, (total_tokens > available_ctx), original_body_chars, original_body_chars

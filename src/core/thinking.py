from dataclasses import dataclass
from typing import Dict, List, Optional


THINKING_STRATEGY_DISABLED = "disabled"
THINKING_STRATEGY_THINK_TAGS = "think_tags"
THINKING_STRATEGY_GEMMA4_CHANNEL = "gemma4_channel"
THINKING_STRATEGY_CUSTOM = "custom"
THINKING_TEMPLATE_DISABLED = "disabled"
THINKING_TEMPLATE_GEMMA4 = "gemma4"
THINKING_TEMPLATE_GEMMA4_GENERAL = "gemma4_general"
THINKING_CONTROL_ON = "<|think|>\n"
THINKING_CONTROL_OFF = "<|no_think|>\n"


@dataclass(frozen=True)
class ThinkingRequestPolicy:
    requested_by_user: bool
    allowed_by_policy: bool
    effective_enabled: bool
    disable_reason: Optional[str]
    requires_two_pass_prefill: bool
    encapsulate_thinking: bool


def resolve_thinking_policy(
    *,
    prompt_delivery_mode: str,
    request_kind: str,
    checkbox_enabled: bool,
    has_assistant_prefill: bool,
    prefill_strategy: str,
) -> ThinkingRequestPolicy:
    requested_by_user = bool(checkbox_enabled)
    is_generic = prompt_delivery_mode == "chat_completions_generic"
    allowed_by_policy = is_generic and request_kind in {"generate", "idea"}

    disable_reason: Optional[str] = None
    requires_two_pass_prefill = False

    if requested_by_user and not allowed_by_policy:
        if request_kind == "autocomplete":
            disable_reason = "思考モードはリアルタイムで続きを提案では使用できません。"
        elif not is_generic:
            disable_reason = "思考モードは汎用モードでのみ使用できます。"
        else:
            disable_reason = "この操作では思考モードを使用できません。"

    effective_enabled = requested_by_user and allowed_by_policy
    if effective_enabled and has_assistant_prefill:
        if prefill_strategy == THINKING_STRATEGY_DISABLED:
            disable_reason = "assistant prefill を使うため、この生成では思考モードを自動で無効化しました。"
            effective_enabled = False
        else:
            requires_two_pass_prefill = True

    return ThinkingRequestPolicy(
        requested_by_user=requested_by_user,
        allowed_by_policy=allowed_by_policy,
        effective_enabled=effective_enabled,
        disable_reason=disable_reason,
        requires_two_pass_prefill=requires_two_pass_prefill,
        encapsulate_thinking=effective_enabled,
    )


def build_thought_block(
    reasoning_text: str,
    *,
    strategy: str,
    custom_prefix: str = "",
    custom_suffix: str = "",
) -> str:
    reasoning = reasoning_text.strip()
    if not reasoning:
        return ""

    if strategy == THINKING_STRATEGY_THINK_TAGS:
        return f"<think>\n{reasoning}\n</think>\n"
    if strategy == THINKING_STRATEGY_GEMMA4_CHANNEL:
        return f"<|channel>thought\n{reasoning}\n<channel|>"
    if strategy == THINKING_STRATEGY_CUSTOM:
        return f"{custom_prefix}{reasoning}{custom_suffix}"
    raise ValueError(f"Unsupported thought block strategy: {strategy}")


def build_open_thinking_prefill(
    seed_text: str,
    *,
    strategy: str,
    custom_prefix: str = "",
) -> str:
    seed = seed_text.strip()
    if not seed:
        return ""

    if strategy == THINKING_STRATEGY_THINK_TAGS:
        return f"<think>\n{seed}\n"
    if strategy == THINKING_STRATEGY_GEMMA4_CHANNEL:
        return f"<|channel>thought\n{seed}\n"
    if strategy == THINKING_STRATEGY_CUSTOM:
        return f"{custom_prefix}{seed}\n"
    raise ValueError(f"Unsupported open thinking prefill strategy: {strategy}")


def prepend_thinking_seed(reasoning_text: str, seed_text: str) -> str:
    seed = seed_text.strip()
    reasoning = reasoning_text.strip()
    if not seed:
        return reasoning
    if not reasoning:
        return seed
    if reasoning.startswith(seed):
        return reasoning
    return f"{seed}\n{reasoning}"


def apply_thinking_control_prefix(
    messages: List[Dict[str, str]],
    prefix: str,
) -> List[Dict[str, str]]:
    updated_messages = [dict(message) for message in messages]
    if updated_messages and updated_messages[0].get("role") == "system":
        content = updated_messages[0].get("content") or ""
        if isinstance(content, str):
            for marker in (THINKING_CONTROL_ON, THINKING_CONTROL_OFF):
                if content.startswith(marker):
                    content = content[len(marker):]
            updated_messages[0]["content"] = prefix + content
            return updated_messages
    updated_messages.insert(0, {"role": "system", "content": prefix})
    return updated_messages

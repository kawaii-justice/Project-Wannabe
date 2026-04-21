from dataclasses import dataclass
from typing import Optional


THINKING_STRATEGY_DISABLED = "disabled"
THINKING_STRATEGY_THINK_TAGS = "think_tags"
THINKING_STRATEGY_GEMMA4_CHANNEL = "gemma4_channel"
THINKING_STRATEGY_CUSTOM = "custom"
THINKING_TEMPLATE_DISABLED = "disabled"
THINKING_TEMPLATE_GEMMA4 = "gemma4"


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

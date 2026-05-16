import sys
import asyncio
import qasync # Import qasync
from PySide6.QtWidgets import (QApplication, QMainWindow, QStatusBar,
                               QSplitter, QWidget, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QScrollArea, QPushButton, QMessageBox,
                               QPlainTextEdit, QTextBrowser, QToolBar, QDialog, QLabel,
                               QCheckBox, QSizePolicy)
from PySide6.QtCore import Qt, Slot, QTimer, QEvent # Add QEvent
from PySide6.QtGui import QTextCursor, QAction, QActionGroup
from typing import Dict, Optional, List # Add Optional and List here

from src.ui.authors_note_panel import AuthorsNotePanel
from src.ui.details_panel import DetailsPanel
from src.ui.output_blocks import OutputBlockManager
from src.ui.dialogs import KoboldConfigDialog, GenerationParamsDialog, ChatTemplateModeStartupDialog
from src.core.kobold_client import KoboldClient, KoboldClientError, ChatStreamEvent
from src.core.prompt_builder import (
    build_prompt,
    build_prompt_with_compression,
    build_chat_messages,
    build_chat_messages_with_compression,
    serialize_chat_messages_for_token_count,
)
from src.core.dynamic_prompts import evaluate_dynamic_prompt
from src.core.settings import load_settings, DEFAULT_SETTINGS
from src.ui.menu_handler import MenuHandler
from src.ui.syntax_highlighter import DynamicPromptSyntaxHighlighter
# Import IdeaProcessor and constants
from src.core.idea_processor import IdeaProcessor, IDEA_ITEM_ORDER, METADATA_MAP
from src.core.metadata_transfer import extract_metadata_value, normalize_metadata_value
from src.core.context_utils import count_tokens, get_available_context, get_true_max_context_length # Import for token counting
from src.core.thinking import (
    ThinkingRequestPolicy,
    THINKING_CONTROL_OFF,
    THINKING_CONTROL_ON,
    THINKING_STRATEGY_GEMMA4_CHANNEL,
    THINKING_TEMPLATE_DISABLED,
    THINKING_TEMPLATE_GEMMA4,
    THINKING_TEMPLATE_GEMMA4_GENERAL,
    apply_thinking_control_prefix,
    build_open_thinking_prefill,
    build_thought_block,
    prepend_thinking_seed,
    resolve_thinking_policy,
)

# Import AutocompleteManager
from src.core.autocomplete_manager import AutocompleteManager

GEMMA4_THOUGHT_OPEN = "<|channel>thought\n"
GEMMA4_THOUGHT_EMPTY = "<|channel>thought\n<channel|>"
THINKING_OUTPUT_MISSING_MESSAGE = "思考を出力できませんでした。Koboldの設定などを見直してください。"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Project Wannabe") # "(仮称)" を削除
        self.setGeometry(100, 100, 1200, 800)

        self.kobold_client = KoboldClient()
        self._is_closing = False
        # Generation status: "idle", "single_running", "infinite_running", "stopping"
        self.generation_status = "idle"
        self.generation_task = None # Holds the asyncio task for generation
        self.output_block_counter = 1
        self.current_mode = "generate" # Initial mode: "generate" or "idea"
        self.infinite_generation_prompt = "" # Store prompt for infinite loop
        self.idea_item_key_map = {name_ja: key for key, name_ja in METADATA_MAP.items() if key in IDEA_ITEM_ORDER} # Map JA name to key

        # Instantiate MenuHandler
        self.menu_handler = MenuHandler(self)

        # Placeholder for the extracted details panel, populated during UI creation.
        self.details_panel = None
        self.infinite_warning_shown = False # Flag for infinite gen warning

        # Token tracking variables
        self.main_text_tokens = 0
        self.prompt_tokens = 0
        self.available_context_tokens = 0
        self.token_update_timer = QTimer(self)
        self.token_update_timer.setSingleShot(True)
        self.token_update_timer.timeout.connect(self._on_token_timer_timeout)
        self._token_update_debounce_ms = 1000

        # Create UI elements
        self._create_toolbar() # Create toolbar first
        self._create_status_bar()
        self._create_central_widget() # Create central widget before menu bar needs it
        self._create_menu_bar() # Create menu bar using the handler

        # AutocompleteManagerの初期化（main_text_edit作成後）
        self.autocomplete_manager = AutocompleteManager(self.main_text_edit, self.kobold_client)
        
        # イベントフィルターをインストール
        self.main_text_edit.installEventFilter(self)

        # Syntax highlighting (comment-out + range-control tags)
        self._syntax_highlighters = []
        self._setup_syntax_highlighting()
        self._on_theme_changed(load_settings().get("theme", "light"))
        
        # 初期状態のショートカット表示を更新
        self._update_shortcut_display()

        # Apply initial theme and font from settings via MenuHandler
        # These might be called within MenuHandler's creation logic already
        # self.menu_handler._apply_initial_font() # Ensure initial font is applied
        # self.menu_handler._apply_theme(load_settings().get("theme", "light")) # Ensure initial theme

        self._connect_token_update_signals()
        self._schedule_token_update()
        QTimer.singleShot(0, self._show_startup_template_mode_dialog_if_needed)
 
    def _connect_token_update_signals(self):
        self.main_text_edit.textChanged.connect(self._schedule_token_update)
        self.details_panel.details_changed.connect(self._schedule_token_update)
        self.details_panel.thinking_prefill_enabled_changed.connect(
            self._update_assistant_thinking_prefill_state
        )
        self.authors_note_edit.textChanged.connect(self._schedule_token_update)
        self.thinking_mode_checkbox.toggled.connect(self._schedule_token_update)

    def _schedule_token_update(self, *args):
        if self._is_closing:
            return
        self.token_update_timer.start(self._token_update_debounce_ms)

    def _get_prompt_delivery_mode(self) -> str:
        settings = load_settings()
        return settings.get("prompt_delivery_mode", DEFAULT_SETTINGS.get("prompt_delivery_mode", "mistral_legacy"))

    def _use_chat_completions_mode(self) -> bool:
        return self._get_prompt_delivery_mode() == "chat_completions_generic"

    def _get_effective_max_length(self, mode: Optional[str] = None) -> int:
        settings = load_settings()
        mode = mode or self.current_mode
        thinking_suffix = "thinking_on" if self.thinking_mode_checkbox.isChecked() else "thinking_off"
        if mode == "idea":
            legacy_key = "max_length_idea"
        else:
            legacy_key = "max_length_generate"
        key = f"{legacy_key}_{thinking_suffix}"
        return int(settings.get(key, settings.get(legacy_key, DEFAULT_SETTINGS[legacy_key])))

    async def _stream_generation_request(
        self,
        *,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        assistant_prefill: Optional[str] = None,
        max_length: int,
        stop_sequence: Optional[List[str]] = None,
        generation_params: Optional[Dict[str, object]] = None,
    ):
        if self._use_chat_completions_mode():
            async for token in self.kobold_client.generate_chat_stream(
                messages or [],
                assistant_prefill=assistant_prefill,
                max_length=max_length,
                stop_sequence=stop_sequence,
                current_mode=self.current_mode,
                generation_params=generation_params,
            ):
                yield token
        else:
            effective_prompt = self._inject_project_thinking_prefill_into_prompt(prompt or "")
            async for token in self.kobold_client.generate_stream(
                effective_prompt,
                max_length=max_length,
                stop_sequence=stop_sequence,
                current_mode=self.current_mode,
            ):
                yield ChatStreamEvent(content=token)

    def _get_prefill_thinking_strategy(self) -> str:
        preset = self._get_thinking_template_preset()
        if self._uses_gemma4_thinking_template(preset):
            return THINKING_STRATEGY_GEMMA4_CHANNEL
        if preset == THINKING_TEMPLATE_DISABLED:
            return "disabled"
        settings = load_settings()
        return settings.get(
            "prefill_thinking_strategy",
            DEFAULT_SETTINGS.get("prefill_thinking_strategy", "disabled"),
        )

    def _get_thinking_prefill_text(self) -> str:
        settings = load_settings()
        if not settings.get(
            "thinking_prefill_enabled",
            DEFAULT_SETTINGS.get("thinking_prefill_enabled", False),
        ):
            return ""
        return (
            settings.get(
                "thinking_prefill_text",
                DEFAULT_SETTINGS.get("thinking_prefill_text", ""),
            )
            or ""
        ).strip()

    def _get_project_thinking_prefill_text(self) -> str:
        details_panel = getattr(self, "details_panel", None)
        thinking_checkbox = getattr(self, "thinking_mode_checkbox", None)
        if details_panel is None or thinking_checkbox is None:
            return ""
        return details_panel.get_active_thinking_prefill_text(thinking_checkbox.isChecked())

    def _build_project_thinking_prefill_block(self, text: str, settings: Optional[Dict[str, object]] = None) -> str:
        if not text.strip():
            return ""
        settings = settings or load_settings()
        strategy = self._get_prefill_thinking_strategy()
        if strategy == "disabled":
            return ""
        return build_thought_block(
            text,
            strategy=strategy,
            custom_prefix=settings.get("prefill_thinking_custom_prefix", ""),
            custom_suffix=settings.get("prefill_thinking_custom_suffix", ""),
        )

    def _should_include_project_thinking_prefill(self) -> bool:
        return bool(self._build_project_thinking_prefill_block(self._get_project_thinking_prefill_text()))

    def _update_assistant_thinking_prefill_state(self, *args):
        details_panel = getattr(self, "details_panel", None)
        thinking_checkbox = getattr(self, "thinking_mode_checkbox", None)
        if details_panel is None or thinking_checkbox is None:
            return

        enabled = thinking_checkbox.isEnabled() and thinking_checkbox.isChecked()
        details_panel.set_thinking_prefill_available(enabled)

    async def _get_project_thinking_prefill_token_reserve(self, base_url: str) -> int:
        project_thought_block = self._build_project_thinking_prefill_block(
            self._get_project_thinking_prefill_text()
        )
        if not project_thought_block:
            return 0
        token_count = await count_tokens(base_url, project_thought_block)
        if token_count is not None:
            return token_count
        return max(1, len(project_thought_block) // 4)

    def _inject_project_thinking_prefill_into_prompt(self, prompt: str) -> str:
        project_thought_block = self._build_project_thinking_prefill_block(
            self._get_project_thinking_prefill_text()
        )
        if not project_thought_block:
            return prompt

        marker = "[/INST]"
        marker_index = prompt.rfind(marker)
        if marker_index == -1:
            return f"{prompt}{project_thought_block}"
        insert_at = marker_index + len(marker)
        return f"{prompt[:insert_at]}{project_thought_block}{prompt[insert_at:]}"

    def _get_thinking_template_preset(self) -> str:
        settings = load_settings()
        return settings.get(
            "thinking_template_preset",
            DEFAULT_SETTINGS.get("thinking_template_preset", "gemma4"),
        )

    @staticmethod
    def _get_open_thinking_prefill_state(assistant_prefill: Optional[str]) -> tuple[Optional[str], str]:
        if not assistant_prefill:
            return None, ""
        if assistant_prefill.startswith(GEMMA4_THOUGHT_OPEN) and "<channel|>" not in assistant_prefill:
            return "<channel|>", assistant_prefill[len(GEMMA4_THOUGHT_OPEN):]
        think_open = "<think>\n"
        if assistant_prefill.startswith(think_open) and "</think>" not in assistant_prefill:
            return "</think>", assistant_prefill[len(think_open):]
        return None, ""

    @staticmethod
    def _split_open_thinking_content(
        content: str,
        close_marker: str,
        pending: str,
    ) -> tuple[str, str, str, bool]:
        data = pending + content
        marker_index = data.find(close_marker)
        if marker_index != -1:
            reasoning_part = data[:marker_index]
            visible_part = data[marker_index + len(close_marker):]
            return reasoning_part, visible_part, "", False

        keep_chars = max(len(close_marker) - 1, 0)
        if keep_chars and len(data) > keep_chars:
            return data[:-keep_chars], "", data[-keep_chars:], True
        return "", "", data, True

    @staticmethod
    def _uses_gemma4_thinking_template(preset: str) -> bool:
        return preset in {THINKING_TEMPLATE_GEMMA4, THINKING_TEMPLATE_GEMMA4_GENERAL}

    @staticmethod
    def _gemma4_template_injects_think_prefix(preset: str) -> bool:
        return preset == THINKING_TEMPLATE_GEMMA4

    @staticmethod
    def _gemma4_template_injects_no_think_prefix(preset: str) -> bool:
        return preset == THINKING_TEMPLATE_GEMMA4

    def _split_generic_assistant_prefill(
        self,
        messages: Optional[List[Dict[str, str]]],
        assistant_prefill: Optional[str],
    ) -> tuple[List[Dict[str, str]], Optional[str]]:
        effective_messages = [dict(message) for message in (messages or [])]
        if assistant_prefill:
            return effective_messages, assistant_prefill
        if effective_messages and effective_messages[-1].get("role") == "assistant":
            prefill = effective_messages[-1].get("content") or ""
            effective_messages = effective_messages[:-1]
            return effective_messages, (prefill or None)
        return effective_messages, None

    def _apply_thinking_template_preset(
        self,
        *,
        messages: List[Dict[str, str]],
        assistant_prefill: Optional[str],
        policy: ThinkingRequestPolicy,
    ) -> tuple[List[Dict[str, str]], Optional[str]]:
        preset = self._get_thinking_template_preset()
        if preset == THINKING_TEMPLATE_DISABLED:
            return [dict(message) for message in messages], assistant_prefill
        if not self._uses_gemma4_thinking_template(preset):
            return messages, assistant_prefill

        updated_messages = [dict(message) for message in messages]
        updated_prefill = assistant_prefill or ""

        if policy.effective_enabled:
            if self._gemma4_template_injects_think_prefix(preset):
                updated_messages = apply_thinking_control_prefix(updated_messages, THINKING_CONTROL_ON)
        else:
            if self._gemma4_template_injects_no_think_prefix(preset):
                updated_messages = apply_thinking_control_prefix(updated_messages, THINKING_CONTROL_OFF)
            if not updated_prefill.startswith(GEMMA4_THOUGHT_OPEN):
                updated_prefill = f"{GEMMA4_THOUGHT_EMPTY}{updated_prefill}"

        return updated_messages, updated_prefill or None

    def _resolve_request_thinking_policy(
        self,
        *,
        request_kind: str,
        has_assistant_prefill: bool,
    ) -> ThinkingRequestPolicy:
        policy = resolve_thinking_policy(
            prompt_delivery_mode=self._get_prompt_delivery_mode(),
            request_kind=request_kind,
            checkbox_enabled=self.thinking_mode_checkbox.isChecked(),
            has_assistant_prefill=has_assistant_prefill,
            prefill_strategy=self._get_prefill_thinking_strategy(),
        )
        if self._get_thinking_template_preset() == THINKING_TEMPLATE_DISABLED:
            disable_reason = "思考テンプレート設定で『思考を無効化』が選ばれているため、CoT は完全に無効です。"
            return ThinkingRequestPolicy(
                requested_by_user=policy.requested_by_user,
                allowed_by_policy=False,
                effective_enabled=False,
                disable_reason=disable_reason,
                requires_two_pass_prefill=False,
                encapsulate_thinking=False,
            )
        return policy

    def _build_chat_generation_params(self, policy: ThinkingRequestPolicy) -> Dict[str, object]:
        preset = self._get_thinking_template_preset()
        params: Dict[str, object] = {}
        if not self._uses_gemma4_thinking_template(preset):
            params["chat_template_kwargs"] = {
                "enable_thinking": bool(policy.effective_enabled)
            }
        if policy.encapsulate_thinking:
            params["encapsulate_thinking"] = True
        return params

    def _update_thinking_checkbox_ui(self, policy: Optional[ThinkingRequestPolicy] = None):
        tooltip = "思考モードの希望状態です。実際の有効化は送信方式とリクエスト種別で決まります。"
        enabled = True
        autocomplete_active = hasattr(self, "autocomplete_checkbox") and self.autocomplete_checkbox.isChecked()
        if autocomplete_active:
            tooltip = "リアルタイムで続きを提案が有効な間は思考モードを使用できません。"
            enabled = False
        if policy is not None and policy.disable_reason:
            tooltip = f"{tooltip}\n現在: {policy.disable_reason}"
            if not policy.allowed_by_policy:
                enabled = False
        elif not autocomplete_active and not self._use_chat_completions_mode():
            tooltip = "思考モードは汎用モードでのみ使用できます。"
            enabled = False
        self.thinking_mode_checkbox.setToolTip(tooltip)
        self.thinking_mode_checkbox.setEnabled(enabled)
        self._update_assistant_thinking_prefill_state()

    def _format_output_block_title(
        self,
        task_label: str,
        *,
        sequence: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> str:
        number = sequence if sequence is not None else self.output_block_counter
        title = f"{number:03d} | {task_label}"
        if phase:
            title = f"{title} | {phase}"
        return title

    def _get_output_task_label(self) -> str:
        return "アイデア" if self.current_mode == "idea" else "小説"

    @staticmethod
    def _extract_prefilled_thinking_text(assistant_prefill: Optional[str]) -> str:
        if not assistant_prefill:
            return ""
        if assistant_prefill.startswith(GEMMA4_THOUGHT_OPEN):
            end_index = assistant_prefill.find("<channel|>", len(GEMMA4_THOUGHT_OPEN))
            if end_index != -1:
                return assistant_prefill[len(GEMMA4_THOUGHT_OPEN):end_index].strip()

        think_open = "<think>\n"
        think_close = "</think>"
        if assistant_prefill.startswith(think_open):
            end_index = assistant_prefill.find(think_close, len(think_open))
            if end_index != -1:
                return assistant_prefill[len(think_open):end_index].strip()
        return ""

    def _clear_thinking_output(self):
        if hasattr(self, "output_blocks"):
            self.output_blocks.clear()

    def _get_selected_output_text(self) -> str:
        if not hasattr(self, "output_blocks"):
            return self.output_text_edit.textCursor().selectedText().replace("\u2029", "\n")
        return self.output_blocks.get_selected_output_text(
            QApplication.focusWidget(),
            self.output_text_edit,
        )

    @staticmethod
    def _is_thinking_output_missing_error(error: Exception) -> bool:
        return str(error) == THINKING_OUTPUT_MISSING_MESSAGE

    def _show_thinking_output_missing_message(self):
        QMessageBox.warning(
            self,
            "思考モードエラー",
            THINKING_OUTPUT_MISSING_MESSAGE,
        )

    async def _run_two_pass_prefill_reasoning(
        self,
        *,
        request_kind: str,
        base_messages: List[Dict[str, str]],
        assistant_prefill: str,
        max_length: int,
        stop_sequence: Optional[List[str]],
        reasoning_editor: Optional[QTextBrowser],
    ) -> str:
        settings = load_settings()
        strategy = self._get_prefill_thinking_strategy()
        thinking_seed_text = self._get_thinking_prefill_text()

        first_pass_messages = [dict(message) for message in base_messages]
        promoted_prefill = assistant_prefill.strip()
        if first_pass_messages and first_pass_messages[-1].get("role") == "user":
            existing_content = first_pass_messages[-1].get("content", "")
            separator = "" if not existing_content or existing_content.endswith(("\n", "\r")) else "\n"
            first_pass_messages[-1]["content"] = f"{existing_content}{separator}{promoted_prefill}"
        elif first_pass_messages:
            first_pass_messages.append({"role": "user", "content": promoted_prefill})
        else:
            first_pass_messages.append({"role": "user", "content": promoted_prefill})

        policy = self._resolve_request_thinking_policy(
            request_kind=request_kind,
            has_assistant_prefill=False,
        )
        generation_params = self._build_chat_generation_params(policy)
        first_pass_messages, first_pass_prefill = self._apply_thinking_template_preset(
            messages=first_pass_messages,
            assistant_prefill=(
                build_open_thinking_prefill(
                    thinking_seed_text,
                    strategy=strategy,
                    custom_prefix=settings.get("prefill_thinking_custom_prefix", ""),
                )
                if thinking_seed_text and strategy != "disabled"
                else None
            ),
            policy=policy,
        )
        first_pass_stop_sequence = list(stop_sequence or [])
        if self._uses_gemma4_thinking_template(self._get_thinking_template_preset()):
            if "<channel|>" not in first_pass_stop_sequence:
                first_pass_stop_sequence.append("<channel|>")
        open_thinking_close_marker, open_thinking_initial_text = self._get_open_thinking_prefill_state(
            first_pass_prefill
        )
        if open_thinking_close_marker and open_thinking_close_marker not in first_pass_stop_sequence:
            first_pass_stop_sequence.append(open_thinking_close_marker)
        open_thinking_pending = ""
        open_thinking_active = bool(open_thinking_close_marker)
        reasoning_text = ""
        if open_thinking_initial_text:
            reasoning_text += open_thinking_initial_text
            self.output_blocks.append_to_editor(reasoning_editor, open_thinking_initial_text)
        self.output_blocks.set_thinking_title_streaming(reasoning_editor, True)
        try:
            async for event in self._stream_generation_request(
                messages=first_pass_messages,
                assistant_prefill=first_pass_prefill,
                max_length=max_length,
                stop_sequence=first_pass_stop_sequence or None,
                generation_params=generation_params,
            ):
                if event.content and open_thinking_active and open_thinking_close_marker:
                    reasoning_part, _visible_part, open_thinking_pending, open_thinking_active = self._split_open_thinking_content(
                        event.content,
                        open_thinking_close_marker,
                        open_thinking_pending,
                    )
                    if reasoning_part:
                        reasoning_text += reasoning_part
                        self.output_blocks.append_to_editor(reasoning_editor, reasoning_part)
                if event.reasoning_content:
                    reasoning_text += event.reasoning_content
                    self.output_blocks.append_to_editor(reasoning_editor, event.reasoning_content)
            if open_thinking_active and open_thinking_pending:
                reasoning_text += open_thinking_pending
                self.output_blocks.append_to_editor(reasoning_editor, open_thinking_pending)
        finally:
            self.output_blocks.set_thinking_title_streaming(reasoning_editor, False)

        if not reasoning_text.strip():
            raise KoboldClientError(THINKING_OUTPUT_MISSING_MESSAGE)
        return reasoning_text

    async def _prepare_chat_request_with_thinking(
        self,
        *,
        request_kind: str,
        messages: Optional[List[Dict[str, str]]],
        assistant_prefill: Optional[str],
        max_length: int,
        stop_sequence: Optional[List[str]],
        reasoning_editor: Optional[QTextBrowser],
    ) -> tuple[List[Dict[str, str]], Optional[str], ThinkingRequestPolicy, Dict[str, object]]:
        base_messages, extracted_prefill = self._split_generic_assistant_prefill(messages, assistant_prefill)
        policy = self._resolve_request_thinking_policy(
            request_kind=request_kind,
            has_assistant_prefill=bool(extracted_prefill),
        )
        self._update_thinking_checkbox_ui(policy)
        if policy.disable_reason and policy.requested_by_user:
            self.status_bar.showMessage(policy.disable_reason, 4000)

        final_prefill = extracted_prefill
        settings = load_settings()
        project_thought_block = self._build_project_thinking_prefill_block(
            self._get_project_thinking_prefill_text(),
            settings=settings,
        )
        strategy = settings.get("prefill_thinking_strategy", "disabled")
        if self._uses_gemma4_thinking_template(self._get_thinking_template_preset()):
            strategy = THINKING_STRATEGY_GEMMA4_CHANNEL
        thinking_seed_text = self._get_thinking_prefill_text()
        thinking_seed_prefill = ""
        if (
            thinking_seed_text
            and strategy != "disabled"
            and policy.effective_enabled
            and not final_prefill
            and not project_thought_block
        ):
            thinking_seed_prefill = build_open_thinking_prefill(
                thinking_seed_text,
                strategy=strategy,
                custom_prefix=settings.get("prefill_thinking_custom_prefix", ""),
            )

        if project_thought_block:
            final_prefill = f"{project_thought_block}{final_prefill or ''}"
            if policy.effective_enabled or policy.requires_two_pass_prefill:
                policy = ThinkingRequestPolicy(
                    requested_by_user=policy.requested_by_user,
                    allowed_by_policy=policy.allowed_by_policy,
                    effective_enabled=False,
                    disable_reason=policy.disable_reason,
                    requires_two_pass_prefill=False,
                    encapsulate_thinking=False,
                )
        elif policy.requires_two_pass_prefill and extracted_prefill:
            reasoning_text = await self._run_two_pass_prefill_reasoning(
                request_kind=request_kind,
                base_messages=base_messages,
                assistant_prefill=extracted_prefill,
                max_length=max_length,
                stop_sequence=stop_sequence,
                reasoning_editor=reasoning_editor,
            )
            thought_block = build_thought_block(
                prepend_thinking_seed(reasoning_text, thinking_seed_text),
                strategy=strategy,
                custom_prefix=settings.get("prefill_thinking_custom_prefix", ""),
                custom_suffix=settings.get("prefill_thinking_custom_suffix", ""),
            )
            final_prefill = f"{thought_block}{extracted_prefill}"
            policy = ThinkingRequestPolicy(
                requested_by_user=policy.requested_by_user,
                allowed_by_policy=policy.allowed_by_policy,
                effective_enabled=False,
                disable_reason=policy.disable_reason,
                requires_two_pass_prefill=False,
                encapsulate_thinking=False,
            )
        elif thinking_seed_prefill:
            final_prefill = thinking_seed_prefill

        params = self._build_chat_generation_params(policy)
        final_messages, final_prefill = self._apply_thinking_template_preset(
            messages=base_messages,
            assistant_prefill=final_prefill,
            policy=policy,
        )
        return final_messages, final_prefill, policy, params

    async def _stream_to_output(
        self,
        *,
        prompt: Optional[str],
        messages: Optional[List[Dict[str, str]]],
        assistant_prefill: Optional[str],
        max_length: int,
        stop_sequence: Optional[List[str]],
        generation_params: Optional[Dict[str, object]],
        section_title: str,
        append_output: bool = True,
    ) -> tuple[str, str, Optional[QTextBrowser]]:
        block_editor: Optional[QTextBrowser] = None
        thinking_editor: Optional[QTextBrowser] = None
        content_text = ""
        reasoning_text = ""
        block_sequence = self.output_block_counter
        block_title = self._format_output_block_title(
            self._get_output_task_label(),
            sequence=block_sequence,
        )

        if self._use_chat_completions_mode():
            base_messages, extracted_prefill = self._split_generic_assistant_prefill(messages, assistant_prefill)
            initial_policy = self._resolve_request_thinking_policy(
                request_kind=self.current_mode,
                has_assistant_prefill=bool(extracted_prefill),
            )
            block_editor, thinking_editor = self.output_blocks.create_block(
                block_title,
                include_thinking=(
                    initial_policy.effective_enabled
                    or initial_policy.requires_two_pass_prefill
                    or self._should_include_project_thinking_prefill()
                ),
            )
            self.output_block_counter += 1
            prepared_messages, prepared_prefill, policy, prepared_params = await self._prepare_chat_request_with_thinking(
                request_kind=self.current_mode,
                messages=messages,
                assistant_prefill=assistant_prefill,
                max_length=max_length,
                stop_sequence=stop_sequence,
                reasoning_editor=thinking_editor,
            )
            messages = prepared_messages
            assistant_prefill = prepared_prefill
            generation_params = prepared_params
        else:
            self._update_thinking_checkbox_ui(
                self._resolve_request_thinking_policy(
                    request_kind=self.current_mode,
                    has_assistant_prefill=bool(assistant_prefill),
                )
            )
            project_thought_block = self._build_project_thinking_prefill_block(
                self._get_project_thinking_prefill_text()
            )
            block_editor, thinking_editor = self.output_blocks.create_block(
                block_title,
                include_thinking=bool(project_thought_block),
            )
            if project_thought_block:
                assistant_prefill = project_thought_block
            self.output_block_counter += 1

        open_thinking_close_marker, open_thinking_initial_text = self._get_open_thinking_prefill_state(
            assistant_prefill
        )
        open_thinking_pending = ""
        open_thinking_active = bool(open_thinking_close_marker)
        backend_reasoning_detected = False
        prefilled_thinking_text = self._extract_prefilled_thinking_text(assistant_prefill)
        if prefilled_thinking_text:
            reasoning_text += prefilled_thinking_text
            existing_thinking_text = thinking_editor.toPlainText().strip() if thinking_editor is not None else ""
            if not existing_thinking_text:
                self.output_blocks.append_to_editor(thinking_editor, prefilled_thinking_text)
        if open_thinking_initial_text:
            reasoning_text += open_thinking_initial_text
            self.output_blocks.append_to_editor(thinking_editor, open_thinking_initial_text)
        thinking_title_active = False
        try:
            if open_thinking_active:
                self.output_blocks.set_thinking_title_streaming(thinking_editor, True)
                thinking_title_active = True

            async for event in self._stream_generation_request(
                prompt=prompt,
                messages=messages,
                assistant_prefill=assistant_prefill,
                max_length=max_length,
                stop_sequence=stop_sequence,
                generation_params=generation_params,
            ):
                if event.content:
                    if open_thinking_active and open_thinking_close_marker and not backend_reasoning_detected:
                        reasoning_part, visible_part, open_thinking_pending, open_thinking_active = self._split_open_thinking_content(
                            event.content,
                            open_thinking_close_marker,
                            open_thinking_pending,
                        )
                        if reasoning_part:
                            reasoning_text += reasoning_part
                            self.output_blocks.append_to_editor(thinking_editor, reasoning_part)
                        if visible_part:
                            if thinking_title_active:
                                self.output_blocks.set_thinking_title_streaming(thinking_editor, False)
                                thinking_title_active = False
                            content_text += visible_part
                            if append_output:
                                self._append_to_output(visible_part)
                                self.output_blocks.append_to_editor(block_editor, visible_part)
                    else:
                        if thinking_title_active and backend_reasoning_detected:
                            self.output_blocks.set_thinking_title_streaming(thinking_editor, False)
                            thinking_title_active = False
                        content_text += event.content
                        if append_output:
                            self._append_to_output(event.content)
                            self.output_blocks.append_to_editor(block_editor, event.content)
                if event.reasoning_content:
                    backend_reasoning_detected = True
                    if not thinking_title_active:
                        self.output_blocks.set_thinking_title_streaming(thinking_editor, True)
                        thinking_title_active = True
                    reasoning_text += event.reasoning_content
                    self.output_blocks.append_to_editor(thinking_editor, event.reasoning_content)
                await asyncio.sleep(0.001)

            if open_thinking_active and open_thinking_pending and not backend_reasoning_detected:
                reasoning_text += open_thinking_pending
                self.output_blocks.append_to_editor(thinking_editor, open_thinking_pending)
        finally:
            self.output_blocks.set_thinking_title_streaming(thinking_editor, False)

        if self._use_chat_completions_mode() and generation_params and generation_params.get("encapsulate_thinking") and not reasoning_text.strip():
            raise KoboldClientError(THINKING_OUTPUT_MISSING_MESSAGE)

        return content_text, reasoning_text, block_editor

    def _create_menu_bar(self):
        """Creates the menu bar using MenuHandler."""
        self.setMenuBar(self.menu_handler.create_menu_bar())

    def _show_startup_template_mode_dialog_if_needed(self):
        settings = load_settings()
        if settings.get(
            "skip_template_mode_prompt_on_startup",
            DEFAULT_SETTINGS.get("skip_template_mode_prompt_on_startup", False),
        ):
            return

        dialog = ChatTemplateModeStartupDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.kobold_client.reload_settings()
            if hasattr(self, "autocomplete_manager"):
                self.autocomplete_manager.reload_settings()
            self._update_thinking_checkbox_ui()
            self.status_bar.showMessage("チャットテンプレモードを更新しました。", 3000)

    def _create_toolbar(self):
        """Creates the main toolbar for mode switching."""
        toolbar = QToolBar("モード選択")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)

        self.gen_mode_action = QAction("小説生成", self)
        self.gen_mode_action.setCheckable(True)
        self.gen_mode_action.setChecked(True)
        self.gen_mode_action.triggered.connect(self._set_mode_generate)
        toolbar.addAction(self.gen_mode_action)
        mode_group.addAction(self.gen_mode_action)

        self.idea_mode_action = QAction("アイデア出し", self)
        self.idea_mode_action.setCheckable(True)
        self.idea_mode_action.triggered.connect(self._set_mode_idea)
        toolbar.addAction(self.idea_mode_action)
        mode_group.addAction(self.idea_mode_action)

        toolbar.addSeparator()

        # 執筆支援モード（オートコンプリート）チェックボックス
        self.autocomplete_checkbox = QCheckBox("リアルタイムで続きを提案（ベータ）")
        self.autocomplete_checkbox.setChecked(False)  # 初期状態はOFF
        self.autocomplete_checkbox.setFocusPolicy(Qt.NoFocus)  # フォーカスを無効化してショートカット暴発を防止
        self.autocomplete_checkbox.toggled.connect(self._toggle_autocomplete_mode)
        toolbar.addWidget(self.autocomplete_checkbox)

        self.thinking_mode_checkbox = QCheckBox("思考モード(対応モデルのみ)")
        self.thinking_mode_checkbox.setChecked(False)
        self.thinking_mode_checkbox.setFocusPolicy(Qt.NoFocus)
        self.thinking_mode_checkbox.toggled.connect(self._update_idea_fast_mode_state)
        self.thinking_mode_checkbox.toggled.connect(self._update_assistant_thinking_prefill_state)
        toolbar.addWidget(self.thinking_mode_checkbox)
        self._update_thinking_checkbox_ui()

        toolbar.addSeparator()

        side_panel_label = QLabel("サイドパネル:")
        toolbar.addWidget(side_panel_label)

        self.details_drawer_action = QAction("詳細情報", self)
        self.details_drawer_action.setCheckable(True)
        self.details_drawer_action.setToolTip("タイトル、キーワード、設定、プロットなどの詳細情報パネルを表示します。")
        self.details_drawer_action.triggered.connect(
            lambda checked: self._toggle_side_drawer("details", checked)
        )
        toolbar.addAction(self.details_drawer_action)

        self.memo_drawer_action = QAction("メモ", self)
        self.memo_drawer_action.setCheckable(True)
        self.memo_drawer_action.setToolTip("メモパネルを表示します。")
        self.memo_drawer_action.triggered.connect(
            lambda checked: self._toggle_side_drawer("memo", checked)
        )
        toolbar.addAction(self.memo_drawer_action)

        self.close_drawer_action = QAction("閉じる", self)
        self.close_drawer_action.setToolTip("サイドパネルを閉じて本文と生成候補を広く表示します。")
        self.close_drawer_action.triggered.connect(self._close_side_drawer)
        toolbar.addAction(self.close_drawer_action)

        # スペーサーを追加して右端にショートカット説明を配置
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        
        # ショートカットキー説明ラベル
        self.shortcut_label = QLabel("単発生成: Ctrl+G | 無限生成: F5")
        toolbar.addWidget(self.shortcut_label)

        self.open_drawer_button = QPushButton("<<")
        self.open_drawer_button.setToolTip("右サイドパネルを開きます。")
        self.open_drawer_button.setFocusPolicy(Qt.NoFocus)
        self.open_drawer_button.clicked.connect(self._toggle_side_drawer_button)
        toolbar.addWidget(self.open_drawer_button)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("準備完了") # Changed to Japanese
        
        # Create permanent widget for token display
        self.token_label = QLabel("本文文字数0文字(0トークン) | 全プロンプト: 0 / 0トークン")
        self.status_bar.addPermanentWidget(self.token_label)

    def _create_central_widget(self):
        self.central_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.central_splitter)
        self._main_pane_min_width = 320
        self._output_pane_min_width = 280
        self._side_drawer_min_width = 260
        self._last_side_drawer_width = 320
        self._last_side_drawer_key = "details"

        main_text_container = QWidget()
        main_text_container.setMinimumWidth(self._main_pane_min_width)
        main_text_layout = QVBoxLayout(main_text_container)
        main_text_layout.setContentsMargins(0, 5, 4, 0)
        main_text_layout.setSpacing(5)
        self.main_text_splitter = QSplitter(Qt.Vertical)
        self.main_text_splitter.setChildrenCollapsible(False)
        self.authors_note_panel = AuthorsNotePanel()
        self.authors_note_edit = self.authors_note_panel.text_edit
        self.authors_note_panel.toggle_requested.connect(
            lambda expanded: self._set_authors_note_panel_expanded(expanded)
        )
        self.main_text_edit = QPlainTextEdit()
        self.main_text_edit.setPlaceholderText("ここに小説本文を入力・編集します...")
        self.main_text_splitter.addWidget(self.main_text_edit)
        self.main_text_splitter.addWidget(self.authors_note_panel)
        self.main_text_splitter.setStretchFactor(0, 1)
        self.main_text_splitter.setStretchFactor(1, 0)
        main_text_layout.addWidget(self.main_text_splitter)
        QTimer.singleShot(0, lambda: self._set_authors_note_panel_expanded(False, remember_height=False))

        output_container = QWidget()
        output_container.setMinimumWidth(self._output_pane_min_width)
        output_layout = QVBoxLayout(output_container)
        output_layout.setContentsMargins(4, 5, 4, 0)
        output_layout.setSpacing(5)
        output_header_layout = QHBoxLayout()
        output_header_layout.setContentsMargins(0, 0, 0, 0)
        output_header_label = QLabel("生成候補")
        output_clear_button = QPushButton("クリア")
        output_to_main_button = QPushButton("選択を本文へ")
        output_to_memo_button = QPushButton("選択をメモへ")
        output_clear_button.setFocusPolicy(Qt.NoFocus)
        output_to_main_button.setFocusPolicy(Qt.NoFocus)
        output_to_memo_button.setFocusPolicy(Qt.NoFocus)
        output_clear_button.clicked.connect(self._clear_output_edit)
        output_to_main_button.clicked.connect(self._transfer_output_to_main)
        output_to_memo_button.clicked.connect(self._transfer_output_to_memo)
        output_header_layout.addWidget(output_header_label)
        output_header_layout.addStretch()
        output_header_layout.addWidget(output_clear_button)
        output_header_layout.addWidget(output_to_main_button)
        output_header_layout.addWidget(output_to_memo_button)
        output_layout.addLayout(output_header_layout)

        self.output_text_edit = QPlainTextEdit()
        self.output_text_edit.setReadOnly(True)
        self.output_text_edit.setPlaceholderText("LLMからの出力がここに表示されます...")
        self.output_text_edit.hide()
        self.output_blocks_scroll = QScrollArea()
        self.output_blocks_scroll.setWidgetResizable(True)
        self.output_blocks_widget = QWidget()
        self.output_blocks_layout = QVBoxLayout(self.output_blocks_widget)
        self.output_blocks_layout.setContentsMargins(0, 0, 0, 0)
        self.output_blocks_layout.addStretch()
        self.output_blocks_scroll.setWidget(self.output_blocks_widget)
        self.output_blocks = OutputBlockManager(
            self.output_blocks_scroll,
            self.output_blocks_layout,
            on_insert_to_main=self._insert_output_text_to_main,
            on_append_to_memo=self._append_output_text_to_memo,
            on_status_message=self.status_bar.showMessage,
        )
        output_scroll_bar = self.output_blocks_scroll.verticalScrollBar()
        output_scroll_bar.valueChanged.connect(self.output_blocks.on_scroll_changed)
        output_scroll_bar.rangeChanged.connect(self.output_blocks.on_range_changed)
        output_layout.addWidget(self.output_blocks_scroll)
        self.output_blocks.update_auto_follow()

        self.side_drawer_widget = QWidget()
        self.side_drawer_widget.setMinimumWidth(self._side_drawer_min_width)
        side_drawer_layout = QVBoxLayout(self.side_drawer_widget)
        side_drawer_layout.setContentsMargins(4, 5, 0, 0)
        side_drawer_layout.setSpacing(5)
        side_drawer_header = QHBoxLayout()
        side_drawer_header.setContentsMargins(0, 0, 0, 0)
        self.side_drawer_title_label = QLabel("サイドパネル: 詳細情報")
        side_drawer_close_button = QPushButton("パネルを閉じる")
        side_drawer_close_button.setFocusPolicy(Qt.NoFocus)
        side_drawer_close_button.clicked.connect(self._close_side_drawer)
        side_drawer_header.addWidget(self.side_drawer_title_label)
        side_drawer_header.addStretch()
        side_drawer_header.addWidget(side_drawer_close_button)
        side_drawer_layout.addLayout(side_drawer_header)
        self.right_tab_widget = QTabWidget()
        self._create_details_tab()
        self._create_memo_tab()
        self.right_tab_widget.addTab(self.details_tab_widget, "詳細情報")
        self.right_tab_widget.addTab(self.memo_tab_widget, "メモ")
        self.right_tab_widget.currentChanged.connect(self._on_side_drawer_tab_changed)
        side_drawer_layout.addWidget(self.right_tab_widget)

        self.central_splitter.addWidget(main_text_container)
        self.central_splitter.addWidget(output_container)
        self.central_splitter.addWidget(self.side_drawer_widget)
        self.central_splitter.setChildrenCollapsible(False)
        self.central_splitter.setCollapsible(0, False)
        self.central_splitter.setCollapsible(1, False)
        self.central_splitter.setCollapsible(2, False)
        self.central_splitter.setStretchFactor(0, 3)
        self.central_splitter.setStretchFactor(1, 2)
        self.central_splitter.setStretchFactor(2, 0)
        self.central_splitter.splitterMoved.connect(self._on_central_splitter_moved)
        self.central_splitter.setSizes([620, 360, 320])
        self._sync_side_drawer_actions(0)

    def _set_authors_note_panel_expanded(self, expanded: bool, *, remember_height: bool = True):
        if not hasattr(self, "authors_note_panel"):
            return

        sizes = self.main_text_splitter.sizes() if hasattr(self, "main_text_splitter") else []
        current_height = sizes[1] if len(sizes) > 1 else None
        target_height = self.authors_note_panel.set_expanded(
            expanded,
            remember_height=remember_height,
            current_height=current_height,
        )

        if hasattr(self, "main_text_splitter"):
            total = max(sum(sizes), self.main_text_splitter.height())
            self.main_text_splitter.setSizes([max(1, total - target_height), target_height])

    def _toggle_side_drawer(self, drawer_key: str, checked: bool):
        if not checked:
            self._close_side_drawer()
            return
        self._open_side_drawer(drawer_key)

    def _open_side_drawer(self, drawer_key: str):
        if not hasattr(self, "side_drawer_widget") or not hasattr(self, "right_tab_widget"):
            return

        tab_index = 0 if drawer_key == "details" else 1
        self._last_side_drawer_key = drawer_key
        was_hidden = self.side_drawer_widget.isHidden()
        self.right_tab_widget.setCurrentIndex(tab_index)
        self.side_drawer_widget.show()
        self.side_drawer_title_label.setText("サイドパネル: 詳細情報" if tab_index == 0 else "サイドパネル: メモ")
        self._sync_side_drawer_actions(tab_index)
        if was_hidden and hasattr(self, "central_splitter"):
            self.central_splitter.setSizes(self._sizes_with_open_drawer())

    def _open_last_side_drawer(self):
        self._open_side_drawer(getattr(self, "_last_side_drawer_key", "details"))

    def _toggle_side_drawer_button(self):
        if hasattr(self, "side_drawer_widget") and not self.side_drawer_widget.isHidden():
            self._close_side_drawer()
            return
        self._open_last_side_drawer()

    def _close_side_drawer(self):
        if hasattr(self, "central_splitter") and hasattr(self, "side_drawer_widget"):
            sizes = self.central_splitter.sizes()
            if len(sizes) >= 3 and sizes[2] > 0:
                self._last_side_drawer_width = max(self._side_drawer_min_width, sizes[2])
            closed_sizes = self._sizes_with_closed_drawer()
        else:
            closed_sizes = None
        if hasattr(self, "side_drawer_widget"):
            self.side_drawer_widget.hide()
        self._sync_side_drawer_actions(None)
        if closed_sizes is not None:
            self.central_splitter.setSizes(closed_sizes)

    def _sizes_with_open_drawer(self) -> list[int]:
        sizes = self.central_splitter.sizes()
        if len(sizes) < 3:
            return [self._main_pane_min_width, self._output_pane_min_width, self._last_side_drawer_width]

        main_width = max(sizes[0], self._main_pane_min_width)
        output_width = max(sizes[1], self._output_pane_min_width)
        total_width = max(sum(sizes), self.central_splitter.width())
        drawer_width = max(self._side_drawer_min_width, self._last_side_drawer_width)
        max_drawer_width = max(
            self._side_drawer_min_width,
            total_width - self._main_pane_min_width - self._output_pane_min_width,
        )
        drawer_width = min(drawer_width, max_drawer_width)
        remaining_width = max(
            self._main_pane_min_width + self._output_pane_min_width,
            total_width - drawer_width,
        )
        return self._distribute_main_output_widths(main_width, output_width, remaining_width) + [drawer_width]

    def _sizes_with_closed_drawer(self) -> list[int]:
        sizes = self.central_splitter.sizes()
        if len(sizes) < 3:
            return [self._main_pane_min_width, self._output_pane_min_width, 0]

        main_width = max(sizes[0], self._main_pane_min_width)
        output_width = max(sizes[1], self._output_pane_min_width)
        total_width = max(sum(sizes), self.central_splitter.width())
        main_output_sizes = self._distribute_main_output_widths(main_width, output_width, total_width)
        return main_output_sizes + [0]

    def _distribute_main_output_widths(self, main_width: int, output_width: int, total_width: int) -> list[int]:
        pair_width = max(main_width + output_width, 1)
        target_main = int(total_width * main_width / pair_width)
        target_output = total_width - target_main

        if target_main < self._main_pane_min_width:
            target_main = self._main_pane_min_width
            target_output = total_width - target_main
        if target_output < self._output_pane_min_width:
            target_output = self._output_pane_min_width
            target_main = total_width - target_output
        if target_main < self._main_pane_min_width:
            target_main = self._main_pane_min_width
        return [target_main, target_output]

    def _on_central_splitter_moved(self, *_args):
        if not hasattr(self, "side_drawer_widget") or self.side_drawer_widget.isHidden():
            return
        sizes = self.central_splitter.sizes()
        if len(sizes) >= 3 and sizes[2] > 0:
            self._last_side_drawer_width = max(self._side_drawer_min_width, sizes[2])
        elif len(sizes) >= 3 and sizes[2] == 0:
            self._close_side_drawer()

    def _on_side_drawer_tab_changed(self, tab_index: int):
        if not hasattr(self, "side_drawer_widget") or self.side_drawer_widget.isHidden():
            return
        self.side_drawer_title_label.setText("サイドパネル: 詳細情報" if tab_index == 0 else "サイドパネル: メモ")
        self._last_side_drawer_key = "details" if tab_index == 0 else "memo"
        self._sync_side_drawer_actions(tab_index)

    def _sync_side_drawer_actions(self, tab_index: Optional[int]):
        for action_name, should_check in (
            ("details_drawer_action", tab_index == 0),
            ("memo_drawer_action", tab_index == 1),
        ):
            action = getattr(self, action_name, None)
            if action is None:
                continue
            action.blockSignals(True)
            action.setChecked(bool(should_check))
            action.blockSignals(False)
        close_action = getattr(self, "close_drawer_action", None)
        if close_action is not None:
            close_action.setEnabled(tab_index is not None)
        open_button = getattr(self, "open_drawer_button", None)
        if open_button is not None:
            if tab_index is None:
                open_button.setText("<<")
                open_button.setToolTip("右サイドパネルを開きます。")
            else:
                open_button.setText(">>")
                open_button.setToolTip("右サイドパネルを閉じます。")

    def _setup_syntax_highlighting(self):
        def protected_ghost_spans():
            manager = getattr(self, "autocomplete_manager", None)
            if manager is None or not manager.has_ghost_text():
                return []
            cursor = manager.ghost_text_cursor
            if cursor is None:
                return []
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            if end <= start:
                return []
            return [(start, end)]

        editable_edits = [
            (self.main_text_edit, protected_ghost_spans),
            (self.authors_note_edit, None),
            (self.memo_edit, None),
        ]
        editable_edits.extend(
            (edit, None) for edit in self.details_panel.get_plain_text_edits_for_highlighting()
        )
        for edit, protected_provider in editable_edits:
            self._syntax_highlighters.append(
                DynamicPromptSyntaxHighlighter(edit, protected_spans_provider=protected_provider)
            )

    def _on_theme_changed(self, theme_name: str):
        for highlighter in getattr(self, "_syntax_highlighters", []):
            highlighter.update_theme()
        if hasattr(self, "output_blocks"):
            self.output_blocks.apply_style()
        if hasattr(self, "authors_note_panel"):
            self.authors_note_panel.apply_style()

    def _create_details_tab(self):
        self.details_panel = DetailsPanel()
        self.details_tab_widget = self.details_panel
        self.details_panel.transfer_requested.connect(self._transfer_idea_to_details)
        self.details_panel.idea_item_changed.connect(self._update_idea_fast_mode_state)
        self.details_panel.thinking_prefill_transfer_requested.connect(self._transfer_output_to_thinking_prefill)

        for attribute_name in (
            "idea_controls_widget",
            "idea_item_combo",
            "idea_fast_mode_check",
            "rating_combo_details",
            "title_edit",
            "title_transfer_button",
            "keywords_widget",
            "genre_widget",
            "synopsis_edit",
            "synopsis_transfer_button",
            "setting_edit",
            "setting_transfer_button",
            "plot_edit",
            "plot_transfer_button",
            "dialogue_level_combo",
            "assistant_thinking_prefill_checkbox",
            "assistant_thinking_prefill_transfer_button",
            "assistant_thinking_prefill_edit",
        ):
            setattr(self, attribute_name, getattr(self.details_panel, attribute_name))

        self._update_assistant_thinking_prefill_state()

    def _create_memo_tab(self):
        self.memo_tab_widget = QWidget()
        memo_layout = QVBoxLayout(self.memo_tab_widget)
        self.memo_edit = QPlainTextEdit()
        self.memo_edit.setPlaceholderText("自由にメモを記入できます...")
        memo_clear_button = QPushButton("メモクリア")
        memo_clear_button.clicked.connect(self._clear_memo_edit)
        memo_layout.addWidget(self.memo_edit)
        memo_layout.addWidget(memo_clear_button, 0, Qt.AlignRight)

    def _clear_memo_edit(self):
        self.memo_edit.clear()

    def _open_kobold_config_dialog(self):
        dialog = KoboldConfigDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.status_bar.showMessage("KoboldCpp 設定が更新されました。", 3000)
            self.kobold_client.reload_settings()
            self.autocomplete_manager.reload_settings()  # オートコンプリート設定も再読み込み
        else:
            self.status_bar.showMessage("KoboldCpp 設定の変更はキャンセルされました。", 3000)

    def _open_gen_params_dialog(self):
        dialog = GenerationParamsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.status_bar.showMessage("生成パラメータが更新されました。", 3000)
            self.kobold_client.reload_settings()
            self.autocomplete_manager.reload_settings()  # オートコンプリート設定も再読み込み
            self._update_thinking_checkbox_ui()
        else:
            self.status_bar.showMessage("生成パラメータの変更はキャンセルされました。", 3000)

    # --- Generation Control Slots ---
    @Slot()
    def _trigger_single_generation(self):
        """Starts a single generation task, or stops it if already running."""
        # 生成開始前にゴーストテキストをクリア
        if hasattr(self, 'autocomplete_manager') and self.autocomplete_manager:
            self.autocomplete_manager.clear_ghost_text()
            if self.autocomplete_manager._cancel_current_generation_task("main generation start"):
                asyncio.ensure_future(self.kobold_client.abort_generation())
        
        if self.generation_status == "single_running":
            # If single generation is running, stop it.
            self._stop_current_generation()
            return
        elif self.generation_status == "stopping":
            self.status_bar.showMessage("Generation is still stopping...", 2000)
            return
        elif self.generation_status == "infinite_running":
            # If infinite generation is running, show warning and do nothing.
            QMessageBox.warning(self, "生成中", "現在、無限生成が実行中です。停止してから単発生成を開始してください。")
            return
        elif self.generation_status != "idle":
            # Handle unexpected status (should ideally not happen)
            QMessageBox.warning(self, "不明な状態", f"予期せぬ生成ステータスです: {self.generation_status}")
            return

        # Only proceed if status is idle
        # --- IDEA Mode Logic ---
        if self.current_mode == "idea":
            selected_item_key = self.details_panel.get_selected_idea_item_key()
            fast_mode_enabled = self.details_panel.is_idea_fast_mode_enabled()
            if self.thinking_mode_checkbox.isChecked():
                fast_mode_enabled = False
            ui_inputs = self._get_metadata_from_ui()["metadata"] # Get only metadata part

            processor = IdeaProcessor(ui_inputs)
            stop_sequence = processor.determine_stop_sequence(selected_item_key)
            prompt_suffix = ""
            prereqs_met = True # Assume met unless fast mode check fails

            if fast_mode_enabled:
                prereqs_met, warning_msg = processor.check_fast_mode_prerequisites(selected_item_key)
                if warning_msg:
                    QMessageBox.warning(self, "前提条件に関する警告", warning_msg)
                    # Continue even if prereqs_met is False, as per user request

                # Generate suffix regardless of warning, as we are continuing
                prompt_suffix = processor.generate_prompt_suffix(selected_item_key)

            full_ui_data = self._get_metadata_from_ui()
            final_prompt = None
            final_messages = None
            final_assistant_prefill = None
            if self._use_chat_completions_mode():
                final_messages = build_chat_messages(
                    current_mode="idea",
                    main_text="",
                    ui_data=full_ui_data,
                    cont_prompt_order="reference_first"
                )
                final_assistant_prefill = prompt_suffix or None
            else:
                base_prompt = build_prompt(
                    current_mode="idea",
                    main_text="",
                    ui_data=full_ui_data,
                    cont_prompt_order="reference_first"
                )
                final_prompt = base_prompt + prompt_suffix

            # --- Execute Generation based on mode ---
            self.generation_status = "single_running" # Use single_running status for IDEA task
            self._update_ui_for_generation_start() # Update UI (e.g., status bar)

            # Use unified separator format including counter
            item_text = self.details_panel.get_selected_idea_item_text()
            separator = f"\n--- アイデア生成 ({item_text}) ({self.output_block_counter}) ---\n"
            self._append_to_output(separator)

            # IDEA "all" item or fast mode should stream
            if selected_item_key == "all" or fast_mode_enabled:
                self.generation_task = asyncio.ensure_future(
                    self._run_single_generation(
                        prompt=final_prompt,
                        messages=final_messages,
                        assistant_prefill=final_assistant_prefill,
                        stop_sequence=stop_sequence,
                    )
                )
            # else: # Safe Mode (specific item, not fast)
            #     # Safe Mode: Get full output, then filter
            #     self.generation_task = asyncio.ensure_future(
            #         self._run_safe_idea_generation(final_prompt, stop_sequence=stop_sequence, selected_item_key=selected_item_key)
            #     )
            # Simplified: If not 'all' and not 'fast', it must be 'safe'
            else: # Safe Mode (specific item, not fast)
                self.generation_task = asyncio.ensure_future(
                    self._run_safe_idea_generation(
                        final_prompt,
                        stop_sequence=stop_sequence,
                        selected_item_key=selected_item_key,
                        messages=final_messages,
                        assistant_prefill=final_assistant_prefill,
                    )
                )


        # --- Generate Mode Logic (with dynamic compression) ---
        else:  # self.current_mode == "generate"
            self.generation_status = "single_running"
            self._update_ui_for_generation_start()

            # 本文とUIデータ取得
            raw_main_text = self.main_text_edit.toPlainText()
            main_text = evaluate_dynamic_prompt(raw_main_text)
            ui_data = self._get_metadata_from_ui()

            settings = load_settings()
            cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
            compression_mode = settings.get("compression_mode", DEFAULT_SETTINGS.get("compression_mode", "token_dynamic"))

            # モード別 最大出力長
            max_len_generate = self._get_effective_max_length("generate")

            # KoboldCppベースURL
            base_url = self.kobold_client._get_api_base_url()

            # 動的圧縮付きプロンプト構築
            async def _build_and_run():
                current_task = asyncio.current_task()
                try:
                    # 圧縮開始前にステータス表示
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                    prefill_token_reserve = await self._get_project_thinking_prefill_token_reserve(base_url)
                    compression_max_len_generate = max_len_generate + prefill_token_reserve

                    if self._use_chat_completions_mode():
                        prompt = None
                        messages, total_tokens, is_overflow, original_chars, compressed_chars = await build_chat_messages_with_compression(
                            base_url=base_url,
                            current_mode=self.current_mode,
                            main_text=main_text,
                            ui_data=ui_data,
                            cont_prompt_order=cont_order,
                            compression_mode=compression_mode,
                            max_length_generate=compression_max_len_generate,
                        )
                    else:
                        messages = None
                        prompt, total_tokens, is_overflow, original_chars, compressed_chars = await build_prompt_with_compression(
                            base_url=base_url,
                            current_mode=self.current_mode,
                            main_text=main_text,
                            ui_data=ui_data,
                            cont_prompt_order=cont_order,
                            compression_mode=compression_mode,
                            max_length_generate=compression_max_len_generate,
                        )
                    
                    # 圧縮後、元の生成中ステータスに戻す
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("単発生成中..."))

                    # 圧縮しても収まりきらない場合（コンテキスト長超過）の処理
                    if is_overflow:
                        QMessageBox.critical(
                            self,
                            "コンテキスト長超過により生成できません",
                            (
                                "本文と詳細情報を圧縮しましたが、それでもモデルの最大コンテキスト長を超過しているため生成を実行できません。\n\n"
                                "以下のいずれか、または複数の対応を行ってください。\n"
                                "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                "・KoboldCpp の設定から AI のコンテキスト長を増やす\n"
                                "・最大出力長を減らす\n"
                                "・（非推奨）設定の「最大コンテキスト超過時の処理」で『何もしない』を選択し、このチェックを無視する"
                            ),
                        )
                        self.generation_status = "idle"
                        self._update_ui_for_generation_stop()
                        self._schedule_token_update()
                        self.generation_task = None
                        return

                    # 本文圧縮率＋文字数に基づく品質警告
                    if original_chars and compressed_chars is not None and original_chars > 0:
                        ratio = compressed_chars / float(original_chars)
                        min_ratio = settings.get(
                            "warn_short_context_min_ratio",
                            DEFAULT_SETTINGS.get("warn_short_context_min_ratio", 0.5),
                        )
                        min_chars = settings.get(
                            "warn_short_context_min_chars",
                            DEFAULT_SETTINGS.get("warn_short_context_min_chars", 2500),
                        )
                        if ratio < min_ratio and compressed_chars < min_chars:
                            res = QMessageBox.warning(
                                self,
                                "コンテキスト圧縮に関する警告",
                                (
                                    "詳細情報がAIのコンテキスト（メモリ）に対して大きすぎます。そのため本文が大きく圧縮されており、"
                                    "生成品質が低下している可能性があります。このまま続行しますか？\n\n"
                                    "以下の対応を推奨します。\n"
                                    "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                    "・KoboldCppの設定からAIのコンテキスト長を増やす\n"
                                    "・最大出力長を減らす"
                                ),
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No,
                            )
                            if res == QMessageBox.No:
                                self.generation_status = "idle"
                                self._update_ui_for_generation_stop()
                                self._schedule_token_update()
                                self.generation_task = None
                                return

                    separator = f"\n--- 生成ブロック {self.output_block_counter} ---\n"
                    self._append_to_output(separator)

                    # 実際の生成実行
                    await self._run_single_generation(prompt=prompt, messages=messages, stop_sequence=None)

                except KoboldClientError as e:
                    self._append_to_output(f"\n--- 単発生成エラー: {e} ---\n")
                    self.status_bar.showMessage("単発生成 エラー", 3000)
                except Exception as e:
                    self._append_to_output(f"\n--- 単発生成中に予期せぬエラー: {e} ---\n")
                    self.status_bar.showMessage("単発生成 予期せぬエラー", 3000)
                finally:
                    self._finalize_generation_task(current_task)

            # 非同期タスクとして実行
            self.generation_task = asyncio.ensure_future(_build_and_run())

    @Slot()
    def _toggle_infinite_generation(self):
        """Starts/stops infinite generation, or stops single generation if running."""
        if self.generation_status == "infinite_running":
            # If infinite is running, stop it.
            self._stop_current_generation()
        elif self.generation_status == "single_running":
            # If single is running, stop it.
            self._stop_current_generation()
            # Ensure the infinite gen button remains unchecked as we just stopped single gen
            self.infinite_gen_action.setChecked(False)
        elif self.generation_status == "idle":
            # If idle, start infinite generation.
            self._start_infinite_generation()
        elif self.generation_status == "stopping":
            self.status_bar.showMessage("Generation is still stopping...", 2000)
            self.infinite_gen_action.setChecked(False)
        else: # Handle unexpected status
            QMessageBox.warning(self, "不明な状態", f"予期せぬ生成ステータスです: {self.generation_status}")
            self.infinite_gen_action.setChecked(False) # Ensure button is unchecked

    def _start_infinite_generation(self):
        """Starts the infinite generation loop."""
        # 生成開始前にゴーストテキストをクリア
        if hasattr(self, 'autocomplete_manager') and self.autocomplete_manager:
            self.autocomplete_manager.clear_ghost_text()
            if self.autocomplete_manager._cancel_current_generation_task("infinite generation start"):
                asyncio.ensure_future(self.kobold_client.abort_generation())

        self.generation_status = "infinite_running"
        self.infinite_warning_shown = False # Reset warning flag for new session
        self._update_ui_for_generation_start()

        # Initial prompt build (might be overwritten in loop if immediate update is on)
        # Get raw main text and evaluate dynamic prompts for the initial prompt
        raw_main_text = self.main_text_edit.toPlainText()
        main_text = evaluate_dynamic_prompt(raw_main_text)

        ui_data = self._get_metadata_from_ui() # Get data dict from UI (includes metadata, rating, authors_note)
        # authors_note is evaluated inside build_prompt

        # Load settings for initial prompt build
        settings = load_settings()
        cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])

        # Call build_prompt with the new signature for the initial prompt
        self.infinite_generation_prompt = build_prompt(
            current_mode=self.current_mode,
            main_text=main_text,
            ui_data=ui_data, # Pass the whole ui_data dictionary
            cont_prompt_order=cont_order
            # rating_override is no longer needed here, handled inside build_prompt
        )

        self.generation_task = asyncio.ensure_future(self._run_infinite_generation_loop())

    def _stop_current_generation(self):
        """Stops any currently running generation task."""
        if self.generation_status == "idle" or self.generation_task is None:
            return
        if self.generation_status == "stopping":
            return

        current_status_before_stop = self.generation_status
        self.generation_status = "stopping"

        if current_status_before_stop == "infinite_running":
            self.status_bar.showMessage("無限生成 停止中...", 2000)
        else: # single_running
            self.status_bar.showMessage("単発生成 停止中...", 2000)

        if self.generation_task and not self.generation_task.done():
            task = self.generation_task
            if task is not asyncio.current_task():
                task.cancel()
            asyncio.ensure_future(self.kobold_client.abort_generation())
        else:
            self._finalize_generation_task(self.generation_task)
            asyncio.ensure_future(self.kobold_client.abort_generation())

        self._update_ui_for_generation_stop()
        self._schedule_token_update()
        # Add a slight delay before final status message if needed
        # QTimer.singleShot(100, lambda: self.status_bar.showMessage("停止中", 3000))
        self.status_bar.showMessage("停止中", 3000)


    def _finalize_generation_task(self, task):
        if self.generation_task is not task:
            return
        self.generation_status = "idle"
        self._update_ui_for_generation_stop()
        self._schedule_token_update()
        self.generation_task = None

    def _update_ui_for_generation_start(self):
        """Updates UI elements when generation starts."""
        if self.generation_status == "infinite_running":
            self.infinite_gen_action.setChecked(True)
            self.status_bar.showMessage("無限生成中 (F5で停止)...")
        elif self.generation_status == "single_running":
            self.infinite_gen_action.setChecked(False) # Ensure infinite is unchecked
            self.status_bar.showMessage("単発生成中...")

        # Keep actions enabled so they can be used to stop generation
        # self.single_gen_action.setEnabled(False) # Keep enabled
        # self.infinite_gen_action.setEnabled(False) # Keep enabled
        # The logic within the action handlers (_trigger_single_generation, _toggle_infinite_generation)
        # will determine whether to start or stop based on self.generation_status.

    def _update_ui_for_generation_stop(self):
        """Updates UI elements when generation stops or completes."""
        self.infinite_gen_action.setChecked(False) # Ensure infinite toggle is unchecked
        # Keep actions enabled
        # self.single_gen_action.setEnabled(True)
        # self.infinite_gen_action.setEnabled(True)
        # Status message is set by the calling function (_stop_current_generation or async methods)


    # --- Async Generation Methods ---
    async def _run_single_generation(
        self,
        prompt: Optional[str] = None,
        stop_sequence: Optional[List[str]] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        assistant_prefill: Optional[str] = None,
    ):
        """
        Runs a single generation (for Generate mode or IDEA Fast mode) and updates status.
        Streams output directly to the UI.
        """
        current_task = asyncio.current_task()
        task_name = "アイデア生成 (高速)" if self.current_mode == "idea" else "単発生成"
        try:
            # Get mode-specific max_length
            settings = load_settings()
            if self.current_mode == "idea":
                current_max_length = self._get_effective_max_length("idea")
            else: # generate mode
                current_max_length = self._get_effective_max_length("generate")

            await self._stream_to_output(
                prompt=prompt,
                messages=messages,
                assistant_prefill=assistant_prefill,
                max_length=current_max_length,
                stop_sequence=stop_sequence,
                generation_params=None,
                section_title=task_name,
            )

            self.status_bar.showMessage(f"{task_name} 完了", 3000)

        except KoboldClientError as e:
            error_msg = f"\n--- {task_name} エラー: {e} ---\n"
            self._append_to_output(error_msg)
            self.status_bar.showMessage(f"{task_name} エラー", 3000)
            if self._is_thinking_output_missing_error(e):
                self._show_thinking_output_missing_message()
        except asyncio.CancelledError:
            print(f"{task_name} task cancelled.")
            self._append_to_output(f"\n--- {task_name}がキャンセルされました ---\n")
            self.status_bar.showMessage(f"{task_name} キャンセル", 3000)
        except Exception as e:
            error_msg = f"\n--- {task_name}中に予期せぬエラーが発生しました: {e} ---\n"
            print(error_msg)
            self._append_to_output(error_msg)
            self.status_bar.showMessage("予期せぬエラー", 3000)
        finally:
            self._finalize_generation_task(current_task)

    async def _run_safe_idea_generation(
        self,
        prompt: Optional[str],
        stop_sequence: Optional[List[str]],
        selected_item_key: str,
        messages: Optional[List[Dict[str, str]]] = None,
        assistant_prefill: Optional[str] = None,
    ):
        """
        Runs generation for IDEA Safe mode: gets full output, filters, then displays.
        """
        current_task = asyncio.current_task()
        task_name = "アイデア生成 (安全)"
        try:
            # Get mode-specific max_length
            settings = load_settings()
            current_max_length = self._get_effective_max_length("idea")

            full_output, _, block_editor = await self._stream_to_output(
                prompt=prompt,
                messages=messages,
                assistant_prefill=assistant_prefill,
                max_length=current_max_length,
                stop_sequence=stop_sequence,
                generation_params=None,
                section_title=task_name,
                append_output=False,
            )

            # Filter the output
            ui_inputs = self._get_metadata_from_ui()["metadata"] # Get current inputs for processor context
            processor = IdeaProcessor(ui_inputs) # Re-instantiate or pass if needed
            filtered_output = processor.filter_output(full_output, selected_item_key)

            # Display filtered output (replace existing content in output area)
            # self._append_to_output(filtered_output) # Append might be confusing, let's replace
            self.output_text_edit.appendPlainText(filtered_output) # Append after the separator
            self.output_blocks.append_to_editor(block_editor, filtered_output)
            cursor = self.output_text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.output_text_edit.setTextCursor(cursor)


            # Finished successfully
            # Increment counter only if generation was successful (or started for streaming)
            # For safe mode, counter is incremented after successful filtering/display
            # For fast mode (including 'all'), counter is incremented after stream finishes in _run_single_generation
            # self.output_block_counter += 1 # Moved to _run_single_generation and after filtering in safe mode
            self.status_bar.showMessage(f"{task_name} 完了", 3000)

        except KoboldClientError as e:
            error_msg = f"\n--- {task_name} エラー: {e} ---\n"
            self._append_to_output(error_msg) # Append errors
            self.status_bar.showMessage(f"{task_name} エラー", 3000)
            if self._is_thinking_output_missing_error(e):
                self._show_thinking_output_missing_message()
        except asyncio.CancelledError:
            print(f"{task_name} task cancelled.")
            self._append_to_output(f"\n--- {task_name}がキャンセルされました ---\n") # Append cancellation message
            self.status_bar.showMessage(f"{task_name} キャンセル", 3000)
        except Exception as e:
            error_msg = f"\n--- {task_name}中に予期せぬエラーが発生しました: {e} ---\n"
            print(error_msg)
            self._append_to_output(error_msg) # Append errors
            self.status_bar.showMessage("予期せぬエラー", 3000)
        finally:
            self._finalize_generation_task(current_task)


    async def _run_infinite_generation_loop(self):
        """Continuously generates text, potentially rebuilding the prompt based on settings."""
        current_task = asyncio.current_task()
        settings = load_settings()
        inf_gen_behavior = settings.get("infinite_generation_behavior", DEFAULT_SETTINGS["infinite_generation_behavior"])
        behavior_key = self.current_mode
        update_behavior = inf_gen_behavior.get(behavior_key, "manual")
        generation_params = None

        # --- Variables to be determined before the loop (for manual) or inside (for immediate) ---
        final_prompt = ""
        final_messages = None
        final_assistant_prefill = None
        stop_sequence = None
        fast_mode_enabled = False
        selected_item_key = "all" # Default for safety
        processor = None
        current_max_length = self._get_effective_max_length("generate") # Default to generate

        # --- Helper function to prepare IDEA generation parameters ---
        def prepare_idea_params():
            nonlocal final_prompt, final_messages, final_assistant_prefill, stop_sequence, fast_mode_enabled, selected_item_key, processor, current_max_length
            try:
                selected_item_key = self.details_panel.get_selected_idea_item_key()
                fast_mode_enabled = self.details_panel.is_idea_fast_mode_enabled()
                if self.thinking_mode_checkbox.isChecked():
                    fast_mode_enabled = False
                ui_inputs = self._get_metadata_from_ui()["metadata"]
                full_ui_data = self._get_metadata_from_ui() # For build_prompt

                processor = IdeaProcessor(ui_inputs)
                # Call correct IdeaProcessor methods
                stop_sequence = processor.determine_stop_sequence(selected_item_key)
                prompt_suffix = ""
                warning_msg = None
                if fast_mode_enabled:
                    prereqs_met, warning_msg = processor.check_fast_mode_prerequisites(selected_item_key)
                    # Generate suffix even if prereqs not met, as per single generation logic
                    prompt_suffix = processor.generate_prompt_suffix(selected_item_key)

                if warning_msg:
                    # Show warning only once per infinite generation session
                    if not self.infinite_warning_shown:
                        # Run warning in main thread using QTimer.singleShot
                        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "前提条件に関する警告", warning_msg))
                        self.infinite_warning_shown = True # Set flag after showing

                if self._use_chat_completions_mode():
                    final_messages = build_chat_messages(
                        current_mode="idea",
                        main_text="",
                        ui_data=full_ui_data,
                        cont_prompt_order="reference_first"
                    )
                    final_prompt = ""
                    final_assistant_prefill = prompt_suffix or None
                else:
                    base_prompt = build_prompt(
                        current_mode="idea",
                        main_text="",
                        ui_data=full_ui_data,
                        cont_prompt_order="reference_first"
                    )
                    final_prompt = base_prompt + prompt_suffix
                    final_messages = None
                    final_assistant_prefill = None

                # Get IDEA max length
                current_max_length = self._get_effective_max_length("idea")

                return True # Preparation successful

            except Exception as e:
                print(f"Error preparing IDEA params: {e}")
                error_msg = f"\n--- 無限生成 (IDEA準備) エラー: {e} ---\n"
                self._append_to_output(error_msg) # Append error to output
                return False # Preparation failed

        # --- Helper function to prepare Generate mode parameters ---
        async def prepare_generate_params():
            """
            無限生成用のGenerateモードプロンプトを動的圧縮込みで構築する。
            is_overflow時はエラー表示用に区別される。
            """
            nonlocal final_prompt, final_messages, final_assistant_prefill, stop_sequence, current_max_length
            try:
                raw_main_text = self.main_text_edit.toPlainText()
                main_text = evaluate_dynamic_prompt(raw_main_text)
                ui_data = self._get_metadata_from_ui()

                current_settings = load_settings()
                cont_order = current_settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
                compression_mode = current_settings.get("compression_mode", DEFAULT_SETTINGS.get("compression_mode", "token_dynamic"))
                max_len_generate = self._get_effective_max_length("generate")

                base_url = self.kobold_client._get_api_base_url()

                # 圧縮開始前にステータス表示
                QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                prefill_token_reserve = await self._get_project_thinking_prefill_token_reserve(base_url)
                compression_max_len_generate = max_len_generate + prefill_token_reserve

                if self._use_chat_completions_mode():
                    prompt = None
                    messages, total_tokens, is_overflow, original_chars, compressed_chars = await build_chat_messages_with_compression(
                        base_url=base_url,
                        current_mode="generate",
                        main_text=main_text,
                        ui_data=ui_data,
                        cont_prompt_order=cont_order,
                        compression_mode=compression_mode,
                        max_length_generate=compression_max_len_generate,
                    )
                else:
                    messages = None
                    prompt, total_tokens, is_overflow, original_chars, compressed_chars = await build_prompt_with_compression(
                        base_url=base_url,
                        current_mode="generate",
                        main_text=main_text,
                        ui_data=ui_data,
                        cont_prompt_order=cont_order,
                        compression_mode=compression_mode,
                        max_length_generate=compression_max_len_generate,
                    )
                
                # 圧縮後、元の無限生成中ステータスに戻す
                QTimer.singleShot(0, lambda: self.status_bar.showMessage("無限生成中 (F5で停止)..."))

                if is_overflow:
                    # 圧縮しても超過 → 無限生成を停止
                    QTimer.singleShot(0, lambda: QMessageBox.critical(
                        self,
                        "コンテキスト長超過により無限生成を停止しました",
                        (
                            "本文と詳細情報を圧縮しましたが、それでもモデルの最大コンテキスト長を超過しているため"
                            "無限生成を停止しました。\n\n"
                            "以下のいずれか、または複数の対応を行ってください。\n"
                            "・詳細情報を推敲して、必要な内容だけを記載する\n"
                            "・KoboldCpp の設定から AI のコンテキスト長を増やす\n"
                            "・最大出力長を減らす\n"
                            "・（非推奨）設定の「最大コンテキスト超過時の処理」で『何もしない』を選択し、このチェックを無視する"
                        ),
                    ))
                    # 無限生成を停止
                    self._append_to_output("\n--- コンテキスト長超過により無限生成を停止しました ---\n")
                    self.status_bar.showMessage("コンテキスト長超過により無限生成を停止しました", 5000)
                    # ループを抜けるためのフラグを設定
                    if self.generation_status == "infinite_running":
                        self.generation_status = "idle"
                        self._update_ui_for_generation_stop()
                        self._schedule_token_update()
                    return False

                # 短すぎ警告（無限生成: 最初の1回のみ）
                if (
                    original_chars
                    and compressed_chars is not None
                    and original_chars > 0
                    and not self.infinite_warning_shown
                ):
                    ratio = compressed_chars / float(original_chars)
                    min_ratio = current_settings.get(
                        "warn_short_context_min_ratio",
                        DEFAULT_SETTINGS.get("warn_short_context_min_ratio", 0.5),
                    )
                    min_chars = current_settings.get(
                        "warn_short_context_min_chars",
                        DEFAULT_SETTINGS.get("warn_short_context_min_chars", 2500),
                    )
                    if ratio < min_ratio and compressed_chars < min_chars:
                        def _show_warn():
                            QMessageBox.warning(
                                self,
                                "コンテキスト圧縮に関する警告",
                                (
                                    "詳細情報がAIのコンテキスト（メモリ）に対して大きすぎます。そのため本文が大きく圧縮されており、"
                                    "生成品質が低下している可能性があります。\n\n"
                                    "以下の対応を推奨します。\n"
                                    "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                    "・KoboldCppの設定からAIのコンテキスト長を増やす\n"
                                    "・最大出力長を減らす"
                                ),
                            )
                        QTimer.singleShot(0, _show_warn)
                        self.infinite_warning_shown = True

                final_prompt = prompt or ""
                final_messages = messages
                final_assistant_prefill = None
                stop_sequence = None
                current_max_length = max_len_generate
                return True

            except Exception as e:
                print(f"Error preparing Generate params: {e}")
                error_msg = f"\n--- 無限生成 (Generate準備) エラー: {e} ---\n"
                self._append_to_output(error_msg)
                return False

        # --- Initial preparation if behavior is 'manual' ---
        if update_behavior == "manual":
            if self.current_mode == "idea":
                if not prepare_idea_params():
                    self._stop_current_generation()
                    return
            else: # generate mode
                if not await prepare_generate_params():
                    self._stop_current_generation()
                    return
            # Check if initial prompt is empty after manual prep
            if not final_prompt and not final_messages:
                print("Error: Initial infinite generation payload is empty after manual preparation.")
                self._stop_current_generation() # This line was missing in the previous SEARCH block
                return # Add the missing return statement here
        # --- Main Generation Loop ---
        try:
            while self.generation_status == "infinite_running":
                # --- Re-prepare parameters if behavior is 'immediate' ---
                if update_behavior == "immediate":
                    if self.current_mode == "idea":
                        if not prepare_idea_params():
                            await asyncio.sleep(0.5) # Wait before retrying or stopping
                            continue # Skip this cycle on prep error
                    else: # generate mode
                        if not await prepare_generate_params():
                            await asyncio.sleep(0.5)
                            continue # Skip this cycle on prep error
                    # Check if prompt is empty after immediate prep
                    if not final_prompt and not final_messages:
                        print("Warning: Rebuilt generation payload is empty. Skipping generation cycle.")
                        await asyncio.sleep(0.5)
                        continue

                # --- Define Separator Dynamically (Inside the loop for immediate mode) ---
                # This needs to happen *after* potential parameter updates in immediate mode
                current_item_text_for_separator = "N/A" # Default
                if self.current_mode == "idea" and self.details_panel:
                    current_item_text_for_separator = self.details_panel.get_selected_idea_item_text()

                if self.current_mode == "idea":
                    separator = f"\n--- アイデア生成 ({current_item_text_for_separator}) ({self.output_block_counter}) ---\n"
                else: # generate mode
                    separator = f"\n--- 生成ブロック {self.output_block_counter} ---\n"


                # --- Execute Generation based on mode and settings ---
                generation_successful = False
                try:
                    if self.current_mode == "idea":
                        # --- IDEA Mode Execution ---
                        if selected_item_key == "all":
                            # --- "All" Item: Always Stream ---
                            self._append_to_output(separator)
                            await self._stream_to_output(
                                prompt=final_prompt,
                                messages=final_messages,
                                assistant_prefill=final_assistant_prefill,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence,
                                generation_params=None,
                                section_title=f"無限生成 / {current_item_text_for_separator}",
                            )
                            generation_successful = True
                        elif not fast_mode_enabled:
                            # --- Safe Mode (Collect, Filter, Append) ---
                            full_output, _, block_editor = await self._stream_to_output(
                                prompt=final_prompt,
                                messages=final_messages,
                                assistant_prefill=final_assistant_prefill,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence,
                                generation_params=None,
                                section_title=f"無限生成 / {current_item_text_for_separator}",
                                append_output=False,
                            )

                            if processor: # Ensure processor exists
                                filtered_output = processor.filter_output(full_output, selected_item_key)
                                # Append separator and filtered output directly
                                self.output_text_edit.appendPlainText(separator + filtered_output)
                                self.output_blocks.append_to_editor(block_editor, filtered_output)
                                cursor = self.output_text_edit.textCursor()
                                cursor.movePosition(QTextCursor.End)
                                self.output_text_edit.setTextCursor(cursor)
                                generation_successful = True
                            else:
                                print("Error: IdeaProcessor not available for filtering.")
                                self._append_to_output("\n--- フィルタリングエラー ---\n")

                        else:
                            # --- Fast Mode (Stream directly) ---
                            self._append_to_output(separator)
                            await self._stream_to_output(
                                prompt=final_prompt,
                                messages=final_messages,
                                assistant_prefill=final_assistant_prefill,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence,
                                generation_params=None,
                                section_title=f"無限生成 / {current_item_text_for_separator}",
                            )
                            generation_successful = True

                    else:
                        # --- Generate Mode Execution (Stream directly) ---
                        self._append_to_output(separator)
                        await self._stream_to_output(
                            prompt=final_prompt,
                            messages=final_messages,
                            assistant_prefill=final_assistant_prefill,
                            max_length=current_max_length,
                            stop_sequence=stop_sequence,
                            generation_params=None,
                            section_title="無限生成",
                        )
                        generation_successful = True

                    # --- Post-generation ---
                    await asyncio.sleep(0.5) # Wait before next generation

                except KoboldClientError as e:
                    error_msg = f"\n--- 無限生成中エラー: {e} ---\n"
                    self._append_to_output(error_msg)
                    self.status_bar.showMessage("無限生成エラー発生、停止します", 5000)
                    if self._is_thinking_output_missing_error(e):
                        self._show_thinking_output_missing_message()
                    self._stop_current_generation() # Stop the infinite loop
                    break # Exit while loop
                except asyncio.CancelledError:
                    print("Infinite generation loop cancelled.")
                    # Stop is handled outside, just break the loop
                    break
                except Exception as e:
                    error_msg = f"\n--- 無限生成中に予期せぬエラー: {e} ---\n"
                    print(error_msg)
                    self._append_to_output(error_msg)
                    self.status_bar.showMessage("予期せぬエラー発生、停止します", 5000)
                    self._stop_current_generation() # Stop the infinite loop
                    break # Exit while loop
        finally:
            self._finalize_generation_task(current_task)


    def _append_to_output(self, text: str):
        """Safely appends text to the output area and handles scrolling."""
        cursor = self.output_text_edit.textCursor()
        v_bar = self.output_text_edit.verticalScrollBar()
        is_at_bottom = v_bar.value() >= v_bar.maximum() - 5

        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)

        if is_at_bottom:
            v_bar.setValue(v_bar.maximum())

    def _get_metadata_from_ui(self) -> dict:
        """Retrieves metadata, rating, and author's note from the UI widgets."""
        details_data = self.details_panel.get_generation_data()
        # Get the author's note
        authors_note = self.authors_note_edit.toPlainText()
        settings = load_settings()
        system_prompt = settings.get("system_prompt", "")
        enable_thinking = self.thinking_mode_checkbox.isChecked()

        return {
            "metadata": details_data["metadata"],
            "rating": details_data["rating"],
            "authors_note": authors_note,
            "system_prompt": system_prompt,
            "enable_thinking": enable_thinking,
            "assistant_thinking_prefill_enabled": details_data["assistant_thinking_prefill_enabled"],
            "assistant_thinking_prefill": details_data["assistant_thinking_prefill"],
        }

    async def _cleanup(self): # Make cleanup async
        """Closes the Kobold client when the application is about to quit."""
        print("Cleaning up...")
        if self.generation_status != "idle":
            self._stop_current_generation() # Attempt to stop gracefully
        
        # AutocompleteManagerのクリーンアップ
        if hasattr(self, 'autocomplete_manager'):
            self.autocomplete_manager.cleanup()
        
        print("Requesting Kobold client close...")
        try:
            await self.kobold_client.close() # Await the async close
            print("Kobold client closed.")
        except Exception as e:
            print(f"Error during client close: {e}")

    @Slot()
    def _clear_output_edit(self):
        """Clears the output text edit and resets the block counter."""
        self.output_text_edit.clear()
        self._clear_thinking_output()
        self.output_block_counter = 1
        self.status_bar.showMessage("出力エリアをクリアしました。", 2000)

    @Slot()
    def _transfer_output_to_main(self):
        """Transfers selected text from output area to main text area based on settings."""
        selected_text = self._get_selected_output_text()
        if not selected_text:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000)
            return

        self._insert_output_text_to_main(selected_text)

    def _insert_output_text_to_main(self, selected_text: str):
        """Inserts output text into the main editor using the configured transfer mode."""
        settings = load_settings()
        transfer_mode = settings.get("transfer_to_main_mode", DEFAULT_SETTINGS["transfer_to_main_mode"])
        newlines_before = settings.get("transfer_newlines_before", DEFAULT_SETTINGS["transfer_newlines_before"])

        cursor = self.main_text_edit.textCursor()

        if transfer_mode == "cursor":
            cursor.insertText(selected_text)
        elif transfer_mode == "next_line_always":
            cursor.movePosition(QTextCursor.EndOfLine)
            newlines_to_insert = "\n" * (newlines_before + 1)
            cursor.insertText(newlines_to_insert + selected_text)
        elif transfer_mode == "next_line_eol":
            if cursor.atBlockEnd():
                # Behave like next_line_always if at end of line (block)
                cursor.movePosition(QTextCursor.EndOfLine) # Ensure truly at end
                newlines_to_insert = "\n" * (newlines_before + 1)
                cursor.insertText(newlines_to_insert + selected_text)
            else:
                # Behave like cursor mode if not at end of line
                cursor.insertText(selected_text)
        else: # Fallback to cursor mode if setting is invalid
            cursor.insertText(selected_text)

        self.main_text_edit.setFocus()
        self.status_bar.showMessage("選択範囲を本文エリアに転記しました。", 2000)

    @Slot()
    def _transfer_output_to_memo(self): # Renamed from _transfer_main_to_memo
        """Transfers selected text from output area to memo area."""
        selected_text = self._get_selected_output_text()
        if selected_text:
            self._append_output_text_to_memo(selected_text)
        else:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000) # Message updated

    def _append_output_text_to_memo(self, selected_text: str):
        """Appends output text to the memo drawer."""
        self.memo_edit.appendPlainText(selected_text)
        self.status_bar.showMessage("選択範囲をメモエリアに転記しました。", 2000)

    @Slot()
    def _transfer_output_to_thinking_prefill(self):
        """Transfers selected output/thinking text to the assistant thinking prefill field."""
        if not self.thinking_mode_checkbox.isChecked():
            self.status_bar.showMessage("思考モードを有効にすると思考を固定できます。", 2000)
            return
        selected_text = self._get_selected_output_text()
        if not selected_text:
            self.status_bar.showMessage("出力エリアで転記したい思考を選択してください。", 2000)
            return
        self.details_panel.set_thinking_prefill_text(selected_text, enabled=True)
        self.status_bar.showMessage("選択範囲を思考prefillに転記しました。", 2000)

    @Slot()
    def _transfer_idea_to_details(self, metadata_key: str):
        """
        Parses selected text in the output area and transfers the value
        corresponding to the metadata_key to the appropriate details widget.
        """
        selected_text = self._get_selected_output_text()
        if not selected_text:
            self.status_bar.showMessage("出力エリアで転記したいテキストを選択してください。", 3000)
            return

        target_name = METADATA_MAP.get(metadata_key)
        if not target_name:
            print(f"Error: Unknown metadata key '{metadata_key}' for transfer.")
            return

        extracted_value = extract_metadata_value(selected_text, metadata_key, METADATA_MAP)
        if extracted_value is None:
            self.status_bar.showMessage(f"選択範囲から「{target_name}」セクションが見つかりませんでした。", 3000)
            return

        try:
            normalized_value = normalize_metadata_value(metadata_key, extracted_value)
            self.details_panel.apply_metadata_value(metadata_key, normalized_value)
            self.status_bar.showMessage(f"「{target_name}」を詳細情報に転記しました。", 2000)

        except Exception as e:
            print(f"Error transferring data for '{metadata_key}': {e}")
            self.status_bar.showMessage(f"「{target_name}」の転記中にエラーが発生しました。", 3000)


    @Slot()
    def _set_mode_generate(self):
        """Sets the application mode to 'generate'."""
        if self.generation_status != "idle":
            QMessageBox.warning(self, "生成中", "生成中にモードは変更できません。")
            self.idea_mode_action.setChecked(self.current_mode == "idea") # Revert check state
            self.gen_mode_action.setChecked(self.current_mode == "generate")
            return
        self.current_mode = "generate"
        self.status_bar.showMessage("モード: 小説生成", 2000)
        self.details_panel.set_idea_controls_visible(False)
        # 小説生成モードに戻った時、本文補完機能のチェックボックスを有効化（チェック状態はOFFに戻す）
        self.autocomplete_checkbox.setEnabled(True)
        self.autocomplete_checkbox.setChecked(False)
        if hasattr(self, 'autocomplete_manager'):
            self.autocomplete_manager.set_enabled(False)
        
        # ショートカット表示を更新
        self._update_shortcut_display()
        self._schedule_token_update()
        self._update_thinking_checkbox_ui()

    @Slot()
    def _set_mode_idea(self):
        """Sets the application mode to 'idea'."""
        if self.generation_status != "idle":
            QMessageBox.warning(self, "生成中", "生成中にモードは変更できません。")
            self.idea_mode_action.setChecked(self.current_mode == "idea") # Revert check state
            self.gen_mode_action.setChecked(self.current_mode == "generate")
            return
        self.current_mode = "idea"
        self.status_bar.showMessage("モード: アイデア出し", 2000)
        self.details_panel.set_idea_controls_visible(True)
        self._update_idea_fast_mode_state() # Update checkbox state when switching to idea mode
        # アイデア出しモードでは本文補完機能を無効化
        self.autocomplete_checkbox.setEnabled(False)
        self.autocomplete_checkbox.setChecked(False)
        if hasattr(self, 'autocomplete_manager'):
            self.autocomplete_manager.set_enabled(False)

        # ショートカット表示を更新
        self._update_shortcut_display()
        self._schedule_token_update()
        self._update_thinking_checkbox_ui()

    @Slot()
    def _toggle_autocomplete_mode(self, checked):
        """Toggle autocomplete mode with exclusive control."""
        if checked:
            # 執筆支援モードをONにする時、無限生成が動いていれば停止
            if self.generation_status == "infinite_running":
                QMessageBox.information(self, "無限生成停止", "無限生成を停止して、本文補完機能を有効化しました。")
                self._stop_current_generation()
            # オートコンプリートを有効化
            if hasattr(self, 'autocomplete_manager'):
                self.autocomplete_manager.set_enabled(True)
                # 有効化直後にデバウンスタイマーを開始して、すぐにオートコンプリートが動作するようにする
                self.autocomplete_manager.debounce_timer.start(self.autocomplete_manager.debounce_ms)
            self.status_bar.showMessage("本文補完機能: ON", 2000)
        else:
            # 執筆支援モードをOFFにする時
            if hasattr(self, 'autocomplete_manager'):
                self.autocomplete_manager.set_enabled(False)
            self.status_bar.showMessage("本文補完機能: OFF", 2000)
        
        # ショートカット表示を更新
        self._update_shortcut_display()
        self._update_thinking_checkbox_ui()

    @Slot()
    def _update_idea_fast_mode_state(self):
        """Enables/disables the fast mode checkbox based on combo box selection."""
        details_panel = getattr(self, "details_panel", None)
        if details_panel is None:
            return

        selected_item_key = details_panel.get_selected_idea_item_key()
        thinking_requested = self.thinking_mode_checkbox.isChecked()

        # Disable fast mode for "全部", the first item ("タイトル"), or whenever thinking mode is requested.
        details_panel.set_idea_fast_mode_available(
            selected_item_key != 'all'
            and selected_item_key != IDEA_ITEM_ORDER[0]
            and not thinking_requested
        )

    def eventFilter(self, obj, event):
        """
        イベントフィルター - main_text_editのキーイベントを処理
        
        Args:
            obj: イベントを受けたオブジェクト
            event: イベント
            
        Returns:
            イベントを処理した場合True
        """
        if obj == self.main_text_edit and self.autocomplete_manager:
            # IME入力イベント（日本語入力開始時にゴーストテキストを消去）
            if event.type() == QEvent.Type.InputMethod:
                if self.autocomplete_checkbox.isChecked() and self.autocomplete_manager.has_ghost_text():
                    self.autocomplete_manager.clear_ghost_text()
                return False  # イベントを握りつぶさない
            
            # キー押下イベント
            if event.type() == QEvent.Type.KeyPress:
                # Ctrl+Space: 手動でオートコンプリートをトリガー（補完機能が有効な場合のみ）
                if (event.key() == Qt.Key_Space and
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier and
                    self.autocomplete_checkbox.isChecked()):
                    self.autocomplete_manager.trigger_now()
                    return True
                
                # その他のキーイベントはAutocompleteManagerに委譲（補完機能が有効な場合のみ）
                if self.autocomplete_checkbox.isChecked() and self.autocomplete_manager.handle_key_press(event):
                    return True
        
        return super().eventFilter(obj, event)

    @Slot()
    def _on_token_timer_timeout(self):
        """Handles token update timer timeout."""
        if self._is_closing:
            return
        if self.generation_status != "idle":
            return
        # Use asyncio.ensure_future to run async method without blocking
        asyncio.ensure_future(self._update_token_display())

    def closeEvent(self, event):
        self._is_closing = True
        if hasattr(self, "token_update_timer") and self.token_update_timer.isActive():
            self.token_update_timer.stop()

        manager = getattr(self, "autocomplete_manager", None)
        if manager is not None:
            try:
                manager.cleanup()
            except Exception as e:
                print(f"AutocompleteManager cleanup error: {e}")

        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            self.generation_task = None
        self.generation_status = "idle"

        super().closeEvent(event)

    async def _update_token_display(self):
        """Updates the token display in the status bar."""
        # Get current main text
        raw_main_text = self.main_text_edit.toPlainText()
        main_text = evaluate_dynamic_prompt(raw_main_text)
        main_text_chars = len(main_text)
        
        # Skip if no text
        if main_text_chars == 0:
            self.token_label.setText("本文文字数0文字(0トークン) | 全プロンプト: 0 / 0トークン")
            return
            
        # Calculate main text tokens (approximate for now, can be improved with actual API call)
        # Rough approximation: 1 token ≈ 0.75 characters for Japanese text
        main_text_tokens = int(main_text_chars * 0.75)
        
        # Build current prompt to get total tokens
        try:
            ui_data = self._get_metadata_from_ui()
            settings = load_settings()
            cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
            
            # Get max output length based on mode
            if self.current_mode == "idea":
                max_output = self._get_effective_max_length("idea")
            else:
                max_output = self._get_effective_max_length("generate")
            
            # Get available context (max_context - max_output)
            base_url = self.kobold_client._get_api_base_url()
            
            # Use direct async calls instead of run_until_complete
            max_context = await get_true_max_context_length(base_url)
            if max_context is None:
                max_context = 20000  # Fallback value
                
            available_context = max_context - max_output
            
            if self._use_chat_completions_mode():
                messages = build_chat_messages(
                    current_mode=self.current_mode,
                    main_text=main_text,
                    ui_data=ui_data,
                    cont_prompt_order=cont_order
                )
                project_thought_block = self._build_project_thinking_prefill_block(
                    self._get_project_thinking_prefill_text()
                )
                if project_thought_block:
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1] = dict(messages[-1])
                        messages[-1]["content"] = f"{project_thought_block}{messages[-1].get('content', '')}"
                    else:
                        messages.append({"role": "assistant", "content": project_thought_block})
                token_count_text = serialize_chat_messages_for_token_count(messages)
            else:
                token_count_text = build_prompt(
                    current_mode=self.current_mode,
                    main_text=main_text,
                    ui_data=ui_data,
                    cont_prompt_order=cont_order
                )

            # Count tokens in prompt
            prompt_tokens = await count_tokens(base_url, token_count_text)
            if prompt_tokens is None:
                prompt_tokens = len(token_count_text) // 4  # Fallback approximation
                
            # Check if compression is needed
            compression_needed = prompt_tokens > available_context
            
            # Format display
            if compression_needed:
                status_text = f"本文文字数{main_text_chars:,}文字({main_text_tokens:,}トークン) | 全プロンプト: {prompt_tokens:,} / {available_context:,}トークン (要圧縮)"
            else:
                status_text = f"本文文字数{main_text_chars:,}文字({main_text_tokens:,}トークン) | 全プロンプト: {prompt_tokens:,} / {available_context:,}トークン"
                
            self.token_label.setText(status_text)
            
        except Exception as e:
            # Fallback display on error
            self.token_label.setText(f"本文文字数{main_text_chars:,}文字(計算中...) | 全プロンプト: 計算中 / 0トークン")
            print(f"Token calculation error: {e}")
    
    def _update_shortcut_display(self):
        """ショートカットキーの表示を更新する"""
        if not hasattr(self, 'shortcut_label'):
            return
            
        # 基本のショートカット
        base_shortcuts = "単発生成: Ctrl+G | 無限生成: F5"
        
        # 本文補完機能が有効な場合の追加ショートカット（共通表示）
        if self.autocomplete_checkbox.isChecked():
            autocomplete_shortcuts = " || 確定: Tab | キャンセル: Esc | 手動補完: Ctrl+Space"
            self.shortcut_label.setText(base_shortcuts + autocomplete_shortcuts)
        else:
            self.shortcut_label.setText(base_shortcuts)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    async def async_cleanup():
        await window._cleanup()
    app.aboutToQuit.connect(lambda: asyncio.ensure_future(async_cleanup()))
    window.show()

    with loop:
        loop.run_forever()

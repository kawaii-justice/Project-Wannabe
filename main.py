import sys
import asyncio
import qasync # Import qasync
import re # Import regex module
from PySide6.QtWidgets import (QApplication, QMainWindow, QMenuBar, QStatusBar,
                               QSplitter, QTextEdit, QWidget, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QScrollArea, QLineEdit, QPushButton, QMessageBox,
                               QPlainTextEdit, QTextBrowser, QToolBar, QDialog, QLineEdit, QLabel, QComboBox, # Add QLabel, QComboBox
                               QCheckBox, QPlainTextEdit, QSizePolicy) # Ensure QPlainTextEdit is imported, Add QCheckBox, QSizePolicy
from PySide6.QtCore import Qt, Slot, QTimer, QEvent # Add QEvent
from PySide6.QtGui import QTextCursor, QAction, QActionGroup, QFont, QKeyEvent, QPalette, QColor, QTextOption # Add QKeyEvent
from typing import Dict, Optional, List # Add Optional and List here

# Correctly import custom widgets and other modules
from src.ui.widgets import CollapsibleSection, InlineCollapsibleSection, TagWidget
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
from src.core.idea_processor import IdeaProcessor, IDEA_ITEM_ORDER, IDEA_ITEM_ORDER_JA, METADATA_MAP
from src.core.context_utils import count_tokens, get_available_context, get_true_max_context_length # Import for token counting
from src.core.thinking import (
    ThinkingRequestPolicy,
    THINKING_STRATEGY_GEMMA4_CHANNEL,
    THINKING_TEMPLATE_DISABLED,
    THINKING_TEMPLATE_GEMMA4,
    THINKING_TEMPLATE_GEMMA4_GENERAL,
    build_thought_block,
    resolve_thinking_policy,
)

# Import AutocompleteManager
from src.core.autocomplete_manager import AutocompleteManager

GEMMA4_THOUGHT_OPEN = "<|channel>thought\n"
GEMMA4_THOUGHT_EMPTY = "<|channel>thought\n<channel|>"


class AutoGrowingTextBrowser(QTextBrowser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resize_margin = 10
        self._resizing = False
        self._resize_start_y = 0
        self._initial_height = 0
        self._base_height = 60
        self._manual_min_height = 0
        self.setMinimumHeight(60)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.textChanged.connect(self._update_height_to_contents)
        self.document().documentLayout().documentSizeChanged.connect(lambda _size: self._update_height_to_contents())

    def set_base_height(self, height: int):
        self._base_height = max(self.minimumHeight(), height)
        self._update_height_to_contents()

    def _update_height_to_contents(self):
        doc_layout = self.document().documentLayout()
        doc_height = int(doc_layout.documentSize().height()) if doc_layout is not None else 0
        if doc_height <= 0:
            line_height = self.fontMetrics().lineSpacing()
            doc_height = max(1, self.document().blockCount()) * line_height
        margins = self.contentsMargins()
        frame = self.frameWidth() * 2
        padding = margins.top() + margins.bottom() + 18
        target_height = max(self._base_height, self._manual_min_height, doc_height + frame + padding)
        self.setFixedHeight(target_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_height_to_contents)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() >= self.viewport().height() - self._resize_margin:
            self._resizing = True
            self._resize_start_y = int(event.globalPosition().y())
            self._initial_height = self.height()
            self.viewport().setCursor(Qt.SizeVerCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = int(event.globalPosition().y()) - self._resize_start_y
            self._manual_min_height = max(self.minimumHeight(), self._initial_height + delta)
            self._update_height_to_contents()
            event.accept()
            return
        if event.position().y() >= self.viewport().height() - self._resize_margin:
            self.viewport().setCursor(Qt.SizeVerCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing and event.button() == Qt.LeftButton:
            self._resizing = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Project Wannabe") # "(仮称)" を削除
        self.setGeometry(100, 100, 1200, 800)

        self.kobold_client = KoboldClient()
        self._is_closing = False
        # Generation status: "idle", "single_running", "infinite_running"
        self.generation_status = "idle"
        self.generation_task = None # Holds the asyncio task for generation
        self.output_block_counter = 1
        self.current_mode = "generate" # Initial mode: "generate" or "idea"
        self.infinite_generation_prompt = "" # Store prompt for infinite loop
        self.idea_item_key_map = {name_ja: key for key, name_ja in METADATA_MAP.items() if key in IDEA_ITEM_ORDER} # Map JA name to key
        self._output_block_widgets = []
        self._last_output_selection = ""
        self._output_blocks_auto_follow = True

        # Instantiate MenuHandler
        self.menu_handler = MenuHandler(self)

        # Placeholders for IDEA UI elements
        self.idea_controls_widget = None
        self.idea_item_combo = None
        self.idea_fast_mode_check = None
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
        self.title_edit.textChanged.connect(self._schedule_token_update)
        self.synopsis_edit.textChanged.connect(self._schedule_token_update)
        self.setting_edit.textChanged.connect(self._schedule_token_update)
        self.plot_edit.textChanged.connect(self._schedule_token_update)
        self.authors_note_edit.textChanged.connect(self._schedule_token_update)
        self.keywords_widget.tagsChanged.connect(self._schedule_token_update)
        self.genre_widget.tagsChanged.connect(self._schedule_token_update)
        self.rating_combo_details.currentIndexChanged.connect(self._schedule_token_update)
        self.dialogue_level_combo.currentIndexChanged.connect(self._schedule_token_update)
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
            async for token in self.kobold_client.generate_stream(
                prompt or "",
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

    def _get_thinking_template_preset(self) -> str:
        settings = load_settings()
        return settings.get(
            "thinking_template_preset",
            DEFAULT_SETTINGS.get("thinking_template_preset", "gemma4"),
        )

    @staticmethod
    def _uses_gemma4_thinking_template(preset: str) -> bool:
        return preset in {THINKING_TEMPLATE_GEMMA4, THINKING_TEMPLATE_GEMMA4_GENERAL}

    @staticmethod
    def _gemma4_template_injects_think_prefix(preset: str) -> bool:
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
                think_prefix = "<|think|>\n"
                if updated_messages and updated_messages[0].get("role") == "system":
                    content = updated_messages[0].get("content", "")
                    if not content.startswith(think_prefix):
                        updated_messages[0]["content"] = think_prefix + content
                else:
                    updated_messages.insert(0, {"role": "system", "content": think_prefix})
        else:
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

    def _refresh_output_block_heights(self, widget: Optional[QWidget]):
        current = widget
        while current is not None:
            if isinstance(current, (CollapsibleSection, InlineCollapsibleSection)):
                current.refresh_content_height()
            current = current.parentWidget()

    def _capture_output_selection(self, widget: QWidget):
        selected = self._selected_text_from_widget(widget)
        if selected:
            self._last_output_selection = selected

    def _register_output_selection_tracking(self, widget: QTextBrowser):
        widget.copyAvailable.connect(lambda available, source=widget: self._capture_output_selection(source) if available else None)
        widget.selectionChanged.connect(lambda source=widget: self._capture_output_selection(source))

    def _create_output_block(self, title: str, *, include_thinking: bool) -> tuple[QTextBrowser, Optional[QTextBrowser]]:
        should_follow = self._should_auto_scroll_output_blocks()
        block_widget = InlineCollapsibleSection(title)
        block_widget.setObjectName("outputBlock")
        block_widget.toggle_button.setObjectName("outputBlockTitle")
        block_layout = block_widget.content_layout
        block_layout.setContentsMargins(0, 0, 0, 12)
        block_layout.setSpacing(6)

        thinking_editor: Optional[QTextBrowser] = None
        if include_thinking:
            thinking_section = InlineCollapsibleSection("思考")
            thinking_section.setObjectName("outputBlockThinking")
            thinking_editor = AutoGrowingTextBrowser()
            thinking_editor.setPlaceholderText("思考ログ")
            thinking_editor.setObjectName("outputBlockThinkingEditor")
            thinking_editor.set_base_height(84)
            self._register_output_selection_tracking(thinking_editor)
            thinking_section.addWidget(thinking_editor)
            thinking_section.set_expanded(False)
            block_layout.addWidget(thinking_section)

        content_editor = AutoGrowingTextBrowser()
        content_editor.setPlaceholderText("出力")
        content_editor.setObjectName("outputBlockContent")
        content_editor.set_base_height(96)
        self._register_output_selection_tracking(content_editor)
        block_layout.addWidget(content_editor)

        insert_index = max(0, self.output_blocks_layout.count() - 1)
        self.output_blocks_layout.insertWidget(insert_index, block_widget)
        self._output_block_widgets.append(block_widget)
        self._apply_output_block_style(block_widget)
        block_widget.set_expanded(True)
        block_widget.refresh_content_height()
        if should_follow:
            QTimer.singleShot(0, self._scroll_output_blocks_to_bottom)
        return content_editor, thinking_editor

    def _append_to_block_editor(self, editor: Optional[QTextBrowser], text: str):
        if editor is None or not text:
            return
        should_follow = self._should_auto_scroll_output_blocks()
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        editor.setTextCursor(cursor)
        self._refresh_output_block_heights(editor)
        if should_follow:
            QTimer.singleShot(0, self._scroll_output_blocks_to_bottom)

    def _clear_thinking_output(self):
        while self.output_blocks_layout.count() > 1:
            item = self.output_blocks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._output_block_widgets.clear()

    def _should_auto_scroll_output_blocks(self) -> bool:
        return bool(getattr(self, "_output_blocks_auto_follow", False))

    def _scroll_output_blocks_to_bottom(self):
        if not hasattr(self, "output_blocks_scroll"):
            return
        v_bar = self.output_blocks_scroll.verticalScrollBar()
        v_bar.setValue(v_bar.maximum())

    def _update_output_blocks_auto_follow(self):
        if not hasattr(self, "output_blocks_scroll"):
            self._output_blocks_auto_follow = False
            return
        v_bar = self.output_blocks_scroll.verticalScrollBar()
        self._output_blocks_auto_follow = v_bar.value() >= v_bar.maximum() - 12

    def _on_output_blocks_scroll_changed(self, *_args):
        self._update_output_blocks_auto_follow()

    def _on_output_blocks_range_changed(self, *_args):
        if self._should_auto_scroll_output_blocks():
            QTimer.singleShot(0, self._scroll_output_blocks_to_bottom)
            QTimer.singleShot(16, self._scroll_output_blocks_to_bottom)

    def _apply_output_block_style(self, block_widget: Optional[QWidget] = None):
        blocks = [block_widget] if block_widget is not None else list(self._output_block_widgets)
        palette = QApplication.palette()
        window = palette.color(QPalette.Window)
        base = palette.color(QPalette.Base)
        button = palette.color(QPalette.Button)
        text = palette.color(QPalette.Text)
        mid = palette.color(QPalette.Mid)

        def soften(color: QColor, amount: int, lighter: bool) -> str:
            tuned = color.lighter(amount) if lighter else color.darker(amount)
            return tuned.name()

        dark_ui = window.lightness() < 128
        block_bg = soften(window, 106 if dark_ui else 102, lighter=not dark_ui)
        title_bg = soften(button, 112 if dark_ui else 103, lighter=not dark_ui)
        editor_bg = soften(base, 103 if dark_ui else 100, lighter=not dark_ui)
        border = soften(mid, 125 if dark_ui else 110, lighter=not dark_ui)
        muted_text = text.name()

        block_style = (
            "QWidget#outputBlock {"
            f" background-color: {block_bg};"
            f" border: 1px solid {border};"
            " border-radius: 8px;"
            " padding: 6px;"
            " margin-top: 4px;"
            "}"
            "QToolButton#outputBlockTitle {"
            f" background-color: {title_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 7px 10px;"
            " font-weight: 600;"
            " text-align: left;"
            "}"
            "QTextBrowser#outputBlockContent, QTextBrowser#outputBlockThinkingEditor {"
            f" background-color: {editor_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 8px;"
            " selection-background-color: palette(highlight);"
            "}"
        )
        for widget in blocks:
            if widget is not None:
                widget.setStyleSheet(block_style)

    def _find_text_selection_source(self, widget: Optional[QWidget]) -> Optional[QWidget]:
        current = widget
        while current is not None:
            if isinstance(current, (QTextBrowser, QPlainTextEdit)):
                return current
            current = current.parentWidget()
        return None

    def _selected_text_from_widget(self, widget: Optional[QWidget]) -> str:
        text_widget = self._find_text_selection_source(widget)
        if text_widget is None:
            return ""
        selected = text_widget.textCursor().selectedText()
        if not selected:
            return ""
        return selected.replace("\u2029", "\n")

    def _get_selected_output_text(self) -> str:
        selected = self._selected_text_from_widget(QApplication.focusWidget())
        if selected:
            self._last_output_selection = selected
            return selected
        if self._last_output_selection:
            return self._last_output_selection
        return self.output_text_edit.textCursor().selectedText().replace("\u2029", "\n")

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
        first_pass_stop_sequence = list(stop_sequence or [])
        if self._uses_gemma4_thinking_template(self._get_thinking_template_preset()):
            if "<channel|>" not in first_pass_stop_sequence:
                first_pass_stop_sequence.append("<channel|>")
        reasoning_text = ""
        async for event in self._stream_generation_request(
            messages=first_pass_messages,
            assistant_prefill=None,
            max_length=max_length,
            stop_sequence=first_pass_stop_sequence or None,
            generation_params=generation_params,
        ):
            if event.reasoning_content:
                reasoning_text += event.reasoning_content
                self._append_to_block_editor(reasoning_editor, event.reasoning_content)

        if not reasoning_text.strip():
            raise KoboldClientError("二段階生成の1回目で reasoning_content を取得できませんでした。")
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
        if policy.requires_two_pass_prefill and extracted_prefill:
            settings = load_settings()
            strategy = settings.get("prefill_thinking_strategy", "disabled")
            if self._uses_gemma4_thinking_template(self._get_thinking_template_preset()):
                strategy = THINKING_STRATEGY_GEMMA4_CHANNEL
            reasoning_text = await self._run_two_pass_prefill_reasoning(
                request_kind=request_kind,
                base_messages=base_messages,
                assistant_prefill=extracted_prefill,
                max_length=max_length,
                stop_sequence=stop_sequence,
                reasoning_editor=reasoning_editor,
            )
            thought_block = build_thought_block(
                reasoning_text,
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
            block_editor, thinking_editor = self._create_output_block(
                block_title,
                include_thinking=initial_policy.effective_enabled or initial_policy.requires_two_pass_prefill,
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
            block_editor, _ = self._create_output_block(
                block_title,
                include_thinking=False,
            )
            self.output_block_counter += 1

        async for event in self._stream_generation_request(
            prompt=prompt,
            messages=messages,
            assistant_prefill=assistant_prefill,
            max_length=max_length,
            stop_sequence=stop_sequence,
            generation_params=generation_params,
        ):
            if event.content:
                content_text += event.content
                if append_output:
                    self._append_to_output(event.content)
                    self._append_to_block_editor(block_editor, event.content)
            if event.reasoning_content:
                reasoning_text += event.reasoning_content
                self._append_to_block_editor(thinking_editor, event.reasoning_content)
            await asyncio.sleep(0.001)

        if self._use_chat_completions_mode() and generation_params and generation_params.get("encapsulate_thinking") and not reasoning_text.strip():
            raise KoboldClientError("thinking を有効化しましたが reasoning_content を取得できませんでした。")

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
        toolbar.addWidget(self.thinking_mode_checkbox)
        self._update_thinking_checkbox_ui()
        
        # スペーサーを追加して右端にショートカット説明を配置
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        
        # ショートカットキー説明ラベル
        self.shortcut_label = QLabel("単発生成: Ctrl+G | 無限生成: F5")
        toolbar.addWidget(self.shortcut_label)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("準備完了") # Changed to Japanese
        
        # Create permanent widget for token display
        self.token_label = QLabel("本文文字数0文字(0トークン) | 全プロンプト: 0 / 0トークン")
        self.status_bar.addPermanentWidget(self.token_label)

    def _create_central_widget(self):
        central_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(central_splitter)

        left_widget = QWidget()
        left_main_layout = QVBoxLayout(left_widget)
        left_main_layout.setContentsMargins(0,0,0,0)
        left_main_layout.setSpacing(0)
        left_splitter = QSplitter(Qt.Vertical)
        left_main_layout.addWidget(left_splitter)

        main_text_container = QWidget()
        main_text_layout = QVBoxLayout(main_text_container)
        main_text_layout.setContentsMargins(0, 5, 0, 0)
        main_text_layout.setSpacing(5)
        self.main_text_edit = QPlainTextEdit()
        self.main_text_edit.setPlaceholderText("ここに小説本文を入力・編集します...")
        main_text_layout.addWidget(self.main_text_edit)
        left_splitter.addWidget(main_text_container)

        output_container = QWidget()
        output_layout = QVBoxLayout(output_container)
        output_layout.setContentsMargins(0, 5, 0, 0)
        output_layout.setSpacing(5)
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
        output_scroll_bar = self.output_blocks_scroll.verticalScrollBar()
        output_scroll_bar.valueChanged.connect(self._on_output_blocks_scroll_changed)
        output_scroll_bar.rangeChanged.connect(self._on_output_blocks_range_changed)
        output_layout.addWidget(self.output_blocks_scroll)
        self._update_output_blocks_auto_follow()
        output_button_layout = QHBoxLayout()
        output_clear_button = QPushButton("[ 出力物クリア ]")
        output_to_main_button = QPushButton("[ 選択部分を本文へ転記 ]")
        output_to_memo_button = QPushButton("[ 選択部分をメモへ転記 ]")
        output_clear_button.setFocusPolicy(Qt.NoFocus)
        output_to_main_button.setFocusPolicy(Qt.NoFocus)
        output_to_memo_button.setFocusPolicy(Qt.NoFocus)
        output_clear_button.clicked.connect(self._clear_output_edit)
        output_to_main_button.clicked.connect(self._transfer_output_to_main)
        output_to_memo_button.clicked.connect(self._transfer_output_to_memo)
        output_button_layout.addWidget(output_clear_button)
        output_button_layout.addWidget(output_to_main_button)
        output_button_layout.addWidget(output_to_memo_button)
        output_button_layout.addStretch()
        output_layout.addLayout(output_button_layout)
        left_splitter.addWidget(output_container)

        self.right_tab_widget = QTabWidget()
        self._create_details_tab()
        self._create_memo_tab()
        self.right_tab_widget.addTab(self.details_tab_widget, "詳細情報")
        self.right_tab_widget.addTab(self.memo_tab_widget, "メモ")

        central_splitter.addWidget(left_widget)
        central_splitter.addWidget(self.right_tab_widget)
        central_splitter.setSizes([700, 500])
        left_splitter.setSizes([600, 200])

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
            (self.synopsis_edit, None),
            (self.setting_edit, None),
            (self.plot_edit, None),
            (self.authors_note_edit, None),
            (self.memo_edit, None),
        ]
        for edit, protected_provider in editable_edits:
            self._syntax_highlighters.append(
                DynamicPromptSyntaxHighlighter(edit, protected_spans_provider=protected_provider)
            )

    def _on_theme_changed(self, theme_name: str):
        for highlighter in getattr(self, "_syntax_highlighters", []):
            highlighter.update_theme()
        self._apply_output_block_style()

    def _create_details_tab(self):
        self.details_tab_widget = QWidget()
        details_main_layout = QVBoxLayout(self.details_tab_widget)
        details_main_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        details_main_layout.addWidget(scroll_area)
        scroll_content_widget = QWidget()
        scroll_area.setWidget(scroll_content_widget)
        details_layout = QVBoxLayout(scroll_content_widget)
        details_layout.setSpacing(10) # Increase spacing slightly

        # --- IDEA Task Controls (Initially Hidden) ---
        self.idea_controls_widget = QWidget()
        idea_controls_layout = QVBoxLayout(self.idea_controls_widget)
        idea_controls_layout.setContentsMargins(5, 5, 5, 5)
        idea_controls_layout.setSpacing(5)

        idea_item_layout = QHBoxLayout()
        idea_item_label = QLabel("生成項目:")
        self.idea_item_combo = QComboBox()
        self.idea_item_combo.addItem("全部", "all") # Add "all" option with internal key
        for i, item_ja in enumerate(IDEA_ITEM_ORDER_JA):
            item_key = IDEA_ITEM_ORDER[i]
            self.idea_item_combo.addItem(item_ja, item_key) # Store internal key as data
        idea_item_layout.addWidget(idea_item_label)
        idea_item_layout.addWidget(self.idea_item_combo)
        idea_controls_layout.addLayout(idea_item_layout)

        self.idea_fast_mode_check = QCheckBox("高速な手法（実験的）")
        idea_controls_layout.addWidget(self.idea_fast_mode_check)

        # Add a separator or some visual distinction if desired
        # separator = QFrame()
        # separator.setFrameShape(QFrame.HLine)
        # separator.setFrameShadow(QFrame.Sunken)
        # idea_controls_layout.addWidget(separator)

        details_layout.addWidget(self.idea_controls_widget)
        self.idea_controls_widget.hide() # Hide initially
        # Connect signal after creation
        self.idea_item_combo.currentIndexChanged.connect(self._update_idea_fast_mode_state)
        # --- End IDEA Task Controls ---


        # --- Rating Selection ---
        # Make rating section collapsible as well
        rating_section = CollapsibleSection("レーティング (生成時)", parent=scroll_content_widget)
        rating_layout = QHBoxLayout()
        rating_label = QLabel("レーティング:")
        self.rating_combo_details = QComboBox()
        self.rating_combo_details.addItem("General (全年齢)", "general")
        self.rating_combo_details.addItem("R-18", "r18")
        rating_layout.addWidget(rating_label)
        rating_layout.addWidget(self.rating_combo_details)
        rating_layout.addStretch()
        rating_section.content_layout.addLayout(rating_layout)
        details_layout.addWidget(rating_section)
        # Load initial rating from settings (ensure this happens after combo box creation)
        initial_settings = load_settings()
        initial_rating = initial_settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])
        initial_rating_index = self.rating_combo_details.findData(initial_rating)
        if initial_rating_index != -1:
            self.rating_combo_details.setCurrentIndex(initial_rating_index)
        # --- End Rating Selection ---

        # Title
        title_section = CollapsibleSection("タイトル")
        title_layout = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_transfer_button = QPushButton("← 転記")
        self.title_transfer_button.setFocusPolicy(Qt.NoFocus)
        self.title_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("title"))
        title_layout.addWidget(self.title_edit)
        title_layout.addWidget(self.title_transfer_button)
        title_section.content_layout.addLayout(title_layout)
        details_layout.addWidget(title_section)

        # Keywords
        keywords_section = CollapsibleSection("キーワード")
        self.keywords_widget = TagWidget()
        self.keywords_widget.transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("keywords"))
        self.keywords_widget.transfer_button.setFocusPolicy(Qt.NoFocus)
        keywords_section.addWidget(self.keywords_widget)
        details_layout.addWidget(keywords_section)

        # Genre
        genre_section = CollapsibleSection("ジャンル")
        self.genre_widget = TagWidget()
        self.genre_widget.transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("genres"))
        self.genre_widget.transfer_button.setFocusPolicy(Qt.NoFocus)
        genre_section.addWidget(self.genre_widget)
        details_layout.addWidget(genre_section)

        # Synopsis
        synopsis_section = CollapsibleSection("あらすじ")
        synopsis_layout = QHBoxLayout()
        self.synopsis_edit = QPlainTextEdit()
        self.synopsis_edit.setPlaceholderText("小説のあらすじを入力...")
        self.synopsis_transfer_button = QPushButton("← 転記")
        self.synopsis_transfer_button.setFocusPolicy(Qt.NoFocus)
        self.synopsis_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("synopsis"))
        synopsis_layout.addWidget(self.synopsis_edit)
        synopsis_layout.addWidget(self.synopsis_transfer_button, 0, Qt.AlignTop)
        synopsis_section.content_layout.addLayout(synopsis_layout)
        details_layout.addWidget(synopsis_section)

        # Setting
        setting_section = CollapsibleSection("設定")
        setting_layout = QHBoxLayout()
        self.setting_edit = QPlainTextEdit()
        self.setting_edit.setPlaceholderText("世界観、キャラクター設定などを入力...")
        self.setting_transfer_button = QPushButton("← 転記")
        self.setting_transfer_button.setFocusPolicy(Qt.NoFocus)
        self.setting_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("setting"))
        setting_layout.addWidget(self.setting_edit)
        setting_layout.addWidget(self.setting_transfer_button, 0, Qt.AlignTop)
        setting_section.content_layout.addLayout(setting_layout)
        details_layout.addWidget(setting_section)

        # Plot
        plot_section = CollapsibleSection("プロット")
        plot_layout = QHBoxLayout()
        self.plot_edit = QPlainTextEdit()
        self.plot_edit.setPlaceholderText("物語の展開、構成などを入力...")
        self.plot_transfer_button = QPushButton("← 転記")
        self.plot_transfer_button.setFocusPolicy(Qt.NoFocus)
        self.plot_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("plot"))
        plot_layout.addWidget(self.plot_edit)
        plot_layout.addWidget(self.plot_transfer_button, 0, Qt.AlignTop)
        plot_section.content_layout.addLayout(plot_layout)
        details_layout.addWidget(plot_section)

        # Author's Note
        authors_note_section = CollapsibleSection("次の展開についてのメモ")
        authors_note_layout = QHBoxLayout()
        self.authors_note_edit = QPlainTextEdit()
        self.authors_note_edit.setPlaceholderText("この先1000文字程度の展開・要素を記述\n例：\n主人公のエルフの少女が、森の中で迷子のドラゴンと出会うシーン。\n驚きと少しの警戒心、そして好奇心が入り混じった描写を。\n\nまたは単語の羅列も可能です。例：\n主人公エルフ\n迷子ドラゴン登場")
        # Optionally set a fixed height or leave it default
        # self.authors_note_edit.setFixedHeight(100)
        authors_note_layout.addWidget(self.authors_note_edit)
        # No transfer button needed for author's note typically
        authors_note_section.content_layout.addLayout(authors_note_layout)
        details_layout.addWidget(authors_note_section)

        # Dialogue Level
        dialogue_section = CollapsibleSection("セリフ量 (生成時)") # Clarify title
        dialogue_layout = QHBoxLayout()
        dialogue_label = QLabel("セリフ量:")
        self.dialogue_level_combo = QComboBox()
        self.dialogue_level_combo.addItems([
            "指定なし", "少ない", "やや少ない", "普通", "やや多い", "多い"
        ])
        dialogue_layout.addWidget(dialogue_label)
        dialogue_layout.addWidget(self.dialogue_level_combo)
        dialogue_layout.addStretch() # Add stretch to push combo box to the left
        dialogue_section.content_layout.addLayout(dialogue_layout)
        details_layout.addWidget(dialogue_section)

        details_layout.addStretch()

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
        
        if self.generation_status == "single_running":
            # If single generation is running, stop it.
            self._stop_current_generation()
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
            selected_item_index = self.idea_item_combo.currentIndex()
            selected_item_key = self.idea_item_combo.itemData(selected_item_index) # Get internal key ('all', 'title', etc.)
            fast_mode_enabled = self.idea_fast_mode_check.isChecked()
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
            separator = f"\n--- アイデア生成 ({self.idea_item_combo.currentText()}) ({self.output_block_counter}) ---\n"
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
            max_len_generate = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

            # KoboldCppベースURL
            base_url = self.kobold_client._get_api_base_url()

            # 動的圧縮付きプロンプト構築
            async def _build_and_run():
                try:
                    # 圧縮開始前にステータス表示
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                    
                    if self._use_chat_completions_mode():
                        prompt = None
                        messages, total_tokens, is_overflow, original_chars, compressed_chars = await build_chat_messages_with_compression(
                            base_url=base_url,
                            current_mode=self.current_mode,
                            main_text=main_text,
                            ui_data=ui_data,
                            cont_prompt_order=cont_order,
                            compression_mode=compression_mode,
                            max_length_generate=max_len_generate,
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
                            max_length_generate=max_len_generate,
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
        else: # Handle unexpected status
            QMessageBox.warning(self, "不明な状態", f"予期せぬ生成ステータスです: {self.generation_status}")
            self.infinite_gen_action.setChecked(False) # Ensure button is unchecked

    def _start_infinite_generation(self):
        """Starts the infinite generation loop."""
        # 生成開始前にゴーストテキストをクリア
        if hasattr(self, 'autocomplete_manager') and self.autocomplete_manager:
            self.autocomplete_manager.clear_ghost_text()
        
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

        current_status_before_stop = self.generation_status
        self.generation_status = "idle" # Set status to idle first

        if current_status_before_stop == "infinite_running":
            self.status_bar.showMessage("無限生成 停止中...", 2000)
        else: # single_running
            self.status_bar.showMessage("単発生成 停止中...", 2000)

        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            # Set task to None immediately after cancellation request
            self.generation_task = None

        self._update_ui_for_generation_stop()
        self._schedule_token_update()
        # Add a slight delay before final status message if needed
        # QTimer.singleShot(100, lambda: self.status_bar.showMessage("停止中", 3000))
        self.status_bar.showMessage("停止中", 3000)


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
        task_name = "アイデア生成 (高速)" if self.current_mode == "idea" else "単発生成"
        try:
            # Get mode-specific max_length
            settings = load_settings()
            if self.current_mode == "idea":
                current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
            else: # generate mode
                current_max_length = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

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
            # Reset status after single run finishes or errors out
            self.generation_status = "idle"
            self._update_ui_for_generation_stop()
            self._schedule_token_update()
            self.generation_task = None

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
        task_name = "アイデア生成 (安全)"
        try:
            # Get mode-specific max_length
            settings = load_settings()
            current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])

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
            self._append_to_block_editor(block_editor, filtered_output)
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
            # Reset status after run finishes or errors out
            self.generation_status = "idle"
            self._update_ui_for_generation_stop()
            self._schedule_token_update()
            self.generation_task = None


    async def _run_infinite_generation_loop(self):
        """Continuously generates text, potentially rebuilding the prompt based on settings."""
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
        current_max_length = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"]) # Default to generate

        # --- Helper function to prepare IDEA generation parameters ---
        def prepare_idea_params():
            nonlocal final_prompt, final_messages, final_assistant_prefill, stop_sequence, fast_mode_enabled, selected_item_key, processor, current_max_length
            try:
                selected_item_index = self.idea_item_combo.currentIndex()
                selected_item_key = self.idea_item_combo.itemData(selected_item_index)
                fast_mode_enabled = self.idea_fast_mode_check.isChecked()
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
                current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])

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
                max_len_generate = current_settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

                base_url = self.kobold_client._get_api_base_url()

                # 圧縮開始前にステータス表示
                QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                
                if self._use_chat_completions_mode():
                    prompt = None
                    messages, total_tokens, is_overflow, original_chars, compressed_chars = await build_chat_messages_with_compression(
                        base_url=base_url,
                        current_mode="generate",
                        main_text=main_text,
                        ui_data=ui_data,
                        cont_prompt_order=cont_order,
                        compression_mode=compression_mode,
                        max_length_generate=max_len_generate,
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
                        max_length_generate=max_len_generate,
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
                if self.current_mode == "idea" and self.idea_item_combo:
                    current_item_text_for_separator = self.idea_item_combo.currentText()

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
                                self._append_to_block_editor(block_editor, filtered_output)
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
            # Ensure status is reset if loop exits unexpectedly (e.g., error not caught above)
            # or if it finishes normally but wasn't stopped via button click.
            # The _stop_current_generation call inside the loop handles cancellation/errors.
            # This ensures cleanup if the loop condition itself becomes false unexpectedly.
            if self.generation_status == "infinite_running":
                self._stop_current_generation()


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
        metadata = { # Initialize the dictionary first
            "title": self.title_edit.text(),
            "keywords": self.keywords_widget.get_tags(),
            "genres": self.genre_widget.get_tags(),
            "synopsis": self.synopsis_edit.toPlainText(),
            "setting": self.setting_edit.toPlainText(),
            "plot": self.plot_edit.toPlainText(),
        }
        # Add dialogue level if selected
        selected_level = self.dialogue_level_combo.currentText()
        if selected_level != "指定なし":
            metadata["dialogue_level"] = selected_level # Add to the dictionary

        # Get the selected rating from the details tab combo box
        selected_rating = self.rating_combo_details.currentData()
        # Get the author's note
        authors_note = self.authors_note_edit.toPlainText()
        settings = load_settings()
        system_prompt = settings.get("system_prompt", "")
        enable_thinking = self.thinking_mode_checkbox.isChecked()

        return {
            "metadata": metadata,
            "rating": selected_rating,
            "authors_note": authors_note,
            "system_prompt": system_prompt,
            "enable_thinking": enable_thinking,
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
        self._last_output_selection = ""
        self.output_block_counter = 1
        self.status_bar.showMessage("出力エリアをクリアしました。", 2000)

    @Slot()
    def _transfer_output_to_main(self):
        """Transfers selected text from output area to main text area based on settings."""
        selected_text = self._get_selected_output_text()
        if not selected_text:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000)
            return

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

        self.status_bar.showMessage("選択範囲を本文エリアに転記しました。", 2000)

    @Slot()
    def _transfer_output_to_memo(self): # Renamed from _transfer_main_to_memo
        """Transfers selected text from output area to memo area."""
        selected_text = self._get_selected_output_text()
        if selected_text:
            self.memo_edit.appendPlainText(selected_text) # Append to memo
            self.status_bar.showMessage("選択範囲をメモエリアに転記しました。", 2000)
        else:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000) # Message updated

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

        japanese_name_map = {
            "title": "タイトル", "keywords": "キーワード", "genres": "ジャンル",
            "synopsis": "あらすじ", "setting": "設定", "plot": "プロット",
        }
        target_name = japanese_name_map.get(metadata_key)
        if not target_name:
            print(f"Error: Unknown metadata key '{metadata_key}' for transfer.")
            return

        # Find the target section header anywhere in the selection and capture everything after it
        pattern = re.compile(rf"# {re.escape(target_name)}:\s*(.*)", re.MULTILINE | re.DOTALL)
        match = pattern.search(selected_text)

        if not match:
            self.status_bar.showMessage(f"選択範囲から「{target_name}」セクションが見つかりませんでした。", 3000)
            return

        # Extract content after the header and process line by line
        content_after_header = match.group(1).strip()
        lines = content_after_header.splitlines()
        extracted_lines = []
        for line in lines:
            # Check if the line starts with another section header
            is_next_header = False
            # Iterate through all possible Japanese names in the map
            for key, jp_name in japanese_name_map.items():
                # Make sure we don't stop at the *current* header if it appears again,
                # only stop if it's a *different* header.
                if key != metadata_key and line.strip().startswith(f"# {jp_name}:"):
                    is_next_header = True
                    break # Found a different header, stop checking for this line
            
            if is_next_header:
                break # Stop extracting lines when the next header is found
            extracted_lines.append(line) # Append the line if it's not a subsequent header

        extracted_value = "\n".join(extracted_lines).strip() # Join the extracted lines

        # Handle potential empty extraction if the target header was last or immediately followed
        # (extracted_value might be "" here, which is generally okay, but check specific cases)

        try:
            if metadata_key == "title":
                # Title should be single line, take the first extracted line
                extracted_value = extracted_value.splitlines()[0] if extracted_value else ""
                self.title_edit.setText(extracted_value)
            elif metadata_key == "keywords":
                tags = [line.strip().lstrip('-').strip() for line in extracted_value.splitlines() if line.strip()]
                self.keywords_widget.set_tags(tags)
            elif metadata_key == "genres":
                tags = [line.strip().lstrip('-').strip() for line in extracted_value.splitlines() if line.strip()]
                self.genre_widget.set_tags(tags)
            elif metadata_key == "synopsis":
                self.synopsis_edit.setPlainText(extracted_value)
            elif metadata_key == "setting":
                self.setting_edit.setPlainText(extracted_value)
            elif metadata_key == "plot":
                self.plot_edit.setPlainText(extracted_value)
            else:
                print(f"Error: No widget defined for key '{metadata_key}'.")
                return

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
        if self.idea_controls_widget:
            self.idea_controls_widget.hide()
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
        if self.idea_controls_widget:
            self.idea_controls_widget.show()
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
        if not self.idea_item_combo or not self.idea_fast_mode_check:
            return

        selected_item_index = self.idea_item_combo.currentIndex()
        selected_item_key = self.idea_item_combo.itemData(selected_item_index)
        thinking_requested = self.thinking_mode_checkbox.isChecked()

        # Disable fast mode for "全部", the first item ("タイトル"), or whenever thinking mode is requested.
        if selected_item_key == 'all' or selected_item_key == IDEA_ITEM_ORDER[0] or thinking_requested:
            self.idea_fast_mode_check.setEnabled(False)
            self.idea_fast_mode_check.setChecked(False) # Uncheck when disabled
        else:
            self.idea_fast_mode_check.setEnabled(True)

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
                max_output = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
            else:
                max_output = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])
            
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

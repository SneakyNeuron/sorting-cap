"""The terminal chat: slash commands run tools, any other text goes to the agent.

Typing `/` opens a completion menu like Claude Code's slash-command menu. It is built
from prompt_toolkit's own pieces: `complete_while_typing`, the single-column
completion menu with `display_meta`, the `AppendAutoSuggestion` ghost text with its
→ binding, and the default ↑/↓ menu navigation. Custom are only `SlashCompleter`
(registry → completions), `TopMatchSuggest`, highlighting the top row when the menu
opens, and the Tab, → (menu open), Enter and Esc bindings in `slash_key_bindings`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, completion_is_selected
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle
from rich.console import Console
from rich.markdown import Markdown

from app.agent import ChatAgent, ModelFactory, NoModelConfigured, default_model_factory
from app.credentials import Credentials
from app.errors import UserFacingError
from app.files import private_file
from app.messages import short_error
from app.settings import Settings, SettingsError, load_settings, save_settings, settings_exist
from app.texts import DEFAULT_LANGUAGE, Texts
from app.tools.context import InteractivePrompter, ToolContext, language_choices
from app.tools.registry import (
    BUILTINS,
    COMMAND_PREFIX,
    Registry,
    Tool,
    field_specs,
    parse_command,
    split_chat_line,
)
from app.workspace import WorkspaceError, ensure_workspace, workspace_is_private

EXIT_COMMANDS = (COMMAND_PREFIX + "exit",)
HISTORY_FILE = ".history"
PROMPT = "cap> "


@dataclass(frozen=True)
class WordContext:
    """What the word before the cursor is: a command name, `/help`'s argument or a flag."""

    kind: str  # "command" | "help" | "flag"
    word: str
    tool: Tool | None = None
    given: tuple[str, ...] = ()


class SlashCompleter(Completer):
    """Registry → completions: `/name` rows, the typed command's flags, `/help <name>`."""

    def __init__(self, registry: Registry, texts: Callable[[], Texts]) -> None:
        self._registry = registry
        self._texts = texts

    def context(self, document: Document) -> WordContext | None:
        text = document.text_before_cursor
        if not text.startswith(COMMAND_PREFIX) or "\n" in text:
            return None  # free text: no menu
        parts = text.split()
        words, word = (parts, "") if text[-1].isspace() else (parts[:-1], parts[-1])
        if not words:
            return WordContext("command", word)
        name = words[0].removeprefix(COMMAND_PREFIX)
        if name == "help":
            return WordContext("help", word) if len(words) == 1 else None
        tool = self._registry.get(name)
        if tool is None or (word and not word.startswith("-")):
            return None
        return WordContext("flag", word, tool, tuple(words[1:]))

    def _commands(self, texts: Texts) -> list[tuple[str, str]]:
        rows = [(t.name, texts.message(t.description_key)) for t in self._registry.tools()]
        return rows + [(b, texts.message(f"builtin.{b}.description")) for b in BUILTINS]

    def _flags(self, ctx: WordContext, texts: Texts) -> list[tuple[str, str]]:
        assert ctx.tool is not None
        specs = [s for s in field_specs(ctx.tool) if not s.positional]
        by_flag = {s.flag: s for s in specs}
        last = by_flag.get(ctx.given[-1]) if ctx.given else None
        if last is not None and last.kind != "bool":
            return []  # the flag's value comes next
        given = {g.split("=", 1)[0].replace("--no-", "--", 1) for g in ctx.given}
        return [
            (s.flag, texts.message(ctx.tool.arg_help_key(s.name)))
            for s in specs
            if s.flag not in given or s.kind == "list"
        ]

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        ctx = self.context(document)
        if ctx is None:
            return
        texts = self._texts()
        if ctx.kind == "flag":
            rows = self._flags(ctx, texts)
        else:
            prefix = COMMAND_PREFIX if ctx.kind == "command" else ""
            rows = [(prefix + name, desc) for name, desc in self._commands(texts)]
        for candidate, description in rows:
            # a word typed in full needs no row (prompt_toolkit would drop a lone one)
            if candidate.startswith(ctx.word) and candidate != ctx.word:
                yield Completion(
                    candidate,
                    start_position=-len(ctx.word),
                    display=candidate,
                    display_meta=(description.splitlines() or [""])[0],
                )


class TopMatchSuggest(AutoSuggest):
    """Ghost text: the rest of the top completion plus a space (→ accepts it)."""

    def __init__(self, completer: SlashCompleter) -> None:
        self._completer = completer

    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None:
        top = next(iter(self._completer.get_completions(document, CompleteEvent())), None)
        if top is None:
            return None
        return Suggestion(top.text[-top.start_position :] + " ")


def highlight_top_row(buffer: Buffer) -> None:
    """Show the top row highlighted when the menu opens, without changing the input."""
    state = buffer.complete_state
    if state is not None and state.complete_index is None and state.completions:
        state.complete_index = 0


def slash_key_bindings(completer: SlashCompleter) -> KeyBindings:
    """Tab / → accept the highlighted row, Enter runs a highlighted command, Esc closes."""
    bindings = KeyBindings()

    def accept(buffer: Buffer) -> None:
        completion = buffer.complete_state.current_completion
        buffer.apply_completion(completion)
        buffer.insert_text(" ")

    @Condition
    def at_end() -> bool:
        from prompt_toolkit.application import get_app

        return get_app().current_buffer.document.is_cursor_at_the_end

    @Condition
    def command_row() -> bool:
        from prompt_toolkit.application import get_app

        buffer = get_app().current_buffer
        state = buffer.complete_state
        ctx = completer.context(state.original_document) if state else None
        return ctx is not None and ctx.kind != "flag" and bool(ctx.word)

    @Condition
    def menu_or_ghost() -> bool:
        from prompt_toolkit.application import get_app

        buffer = get_app().current_buffer
        return buffer.complete_state is not None or buffer.suggestion is not None

    @bindings.add("tab", filter=completion_is_selected)
    @bindings.add("right", filter=completion_is_selected & at_end)
    def _accept(event) -> None:
        accept(event.current_buffer)

    @bindings.add("enter", filter=completion_is_selected & command_row)
    def _run(event) -> None:
        buffer = event.current_buffer
        buffer.apply_completion(buffer.complete_state.current_completion)
        buffer.validate_and_handle()

    @bindings.add("escape", filter=menu_or_ghost, eager=True)
    def _close(event) -> None:
        buffer = event.current_buffer
        buffer.cancel_completion()
        buffer.suggestion = None

    return bindings


class Repl:
    def __init__(
        self,
        workspace: Path,
        console: Console,
        registry: Registry | None = None,
        *,
        model_factory: ModelFactory = default_model_factory,
        env: Mapping[str, str] | None = None,
        ctx_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if registry is None:
            from app.tools import default_registry

            registry = default_registry()
        self.workspace = workspace
        self.console = console
        self.registry = registry
        self.env = env
        self.ctx_kwargs = ctx_kwargs or {}
        self.agent = ChatAgent(registry, self.make_ctx, model_factory)
        completer = SlashCompleter(registry, self.texts)
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(private_file(workspace / HISTORY_FILE))),
            completer=completer,
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            auto_suggest=TopMatchSuggest(completer),
            key_bindings=slash_key_bindings(completer),
        )
        self.session.default_buffer.on_completions_changed += highlight_top_row

    # --- helpers ----------------------------------------------------------

    def settings(self) -> Settings:
        return load_settings(self.workspace)

    def texts(self) -> Texts:
        try:
            return Texts(self.settings().language)
        except SettingsError:
            return Texts()

    def make_ctx(self) -> ToolContext:
        ctx = ToolContext(
            workspace=self.workspace,
            console=self.console,
            registry=self.registry,
            env=self.env,
            **{"model_factory": self.agent.model_factory, **self.ctx_kwargs},
        )
        ctx.prompter = InteractivePrompter(self.console, lambda: ctx.texts)
        return ctx

    def say(self, key: str, style: str | None = None, **values: object) -> None:
        text = self.texts().message(key, **values)
        self.console.print(text, style=style, highlight=False, markup=False)

    def error(self, text: str) -> None:
        """Print a plain-text error; dynamic parts are never read as Rich markup."""
        self.console.print(text, style="red", highlight=False, markup=False)

    def report(self, exc: UserFacingError, texts: Texts | None = None) -> None:
        self.error(exc.text(texts or self.texts()))

    # --- startup ----------------------------------------------------------

    def first_start(self) -> bool:
        """Ask for the language on the very first start; False when the user quits (Ctrl-D).

        Ctrl-C asks again; nothing is saved until a language is chosen.
        """
        if settings_exist(self.workspace):
            return True
        texts = Texts()
        prompter = InteractivePrompter(self.console, lambda: texts)
        while True:
            try:
                language = prompter.choose(
                    texts.message("setup.language_question"),
                    language_choices(),
                    default=DEFAULT_LANGUAGE,
                )
            except KeyboardInterrupt:
                continue
            except EOFError:
                return False
            save_settings(self.workspace, Settings(language=language))
            return True

    def greet(self) -> None:
        try:
            settings = self.settings()
        except SettingsError as exc:
            self.report(exc)
            return
        self.say("repl.greeting", style="bold")
        if not workspace_is_private(self.workspace):
            self.say("repl.workspace_open", style="yellow", path=str(self.workspace))
        if settings.setup_completed_at is None:
            self.say("repl.setup_hint", style="yellow")

    # --- one line ---------------------------------------------------------

    def run_command(self, line: str) -> None:
        texts = self.texts()
        try:
            tokens = split_chat_line(line)
        except ValueError as exc:
            self.error(texts.message("parse.bad_line", detail=str(exc)))
            return
        try:
            ctx = self.make_ctx()
            tool, inputs = parse_command(self.registry, ctx.texts, tokens)
            output = tool.run(ctx, inputs)
            output.render(ctx)
        except UserFacingError as exc:
            self.report(exc, texts)
        except (KeyboardInterrupt, EOFError):  # Ctrl-C or Ctrl-D at a tool prompt
            self.say("repl.cancelled", style="yellow")
        except Exception as exc:  # noqa: BLE001 - the REPL must survive any tool error
            self.error(texts.message("repl.tool_crashed", detail=short_error(exc)))

    def chat(self, text: str) -> None:
        try:
            settings = self.settings()
        except SettingsError as exc:
            self.report(exc)
            return
        texts = Texts(settings.language)
        try:
            with self.console.status(texts.message("repl.thinking"), spinner="dots"):
                reply = self.agent.ask(text, settings, Credentials(self.workspace, self.env))
        except NoModelConfigured:
            self.say("repl.no_model", style="yellow")
            return
        except KeyboardInterrupt:
            self.say("repl.cancelled", style="yellow")
            return
        except Exception as exc:  # noqa: BLE001 - model or connection errors
            self.error(
                texts.message(
                    "repl.model_error", provider=settings.provider, detail=short_error(exc)
                )
            )
            return
        if reply.reasoning:
            self.console.print(reply.reasoning, style="dim", highlight=False, markup=False)
        self.console.print(Markdown(reply.answer or "…"))

    def handle_line(self, line: str) -> bool:
        """Handle one input; return False to quit."""
        line = line.strip()
        if not line:
            return True
        if line.split()[0] in EXIT_COMMANDS:
            return False
        if line.startswith(COMMAND_PREFIX):
            self.run_command(line)
        else:
            self.chat(line)
        return True

    def run(self) -> int:
        if not self.first_start():
            self.say("repl.bye")
            return 0
        self.greet()
        while True:
            try:
                line = self.session.prompt(PROMPT)
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            if not self.handle_line(line):
                break
        self.say("repl.bye")
        return 0


def run_repl() -> int:
    console = Console(highlight=False)
    try:
        workspace = ensure_workspace()
    except WorkspaceError as exc:
        console.print(exc.text(), style="red", highlight=False, markup=False)
        return 1
    return Repl(workspace, console).run()

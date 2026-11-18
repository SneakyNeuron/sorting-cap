"""The terminal chat: dash-commands run tools, any other text goes to the agent."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

from app.agent import ChatAgent, ModelFactory, NoModelConfigured, default_model_factory
from app.credentials import Credentials
from app.providers.base import SetupAborted
from app.providers.verify import short_error
from app.settings import Settings, SettingsError, load_settings, save_settings, settings_exist
from app.texts import Texts
from app.tools.context import Choice, InteractivePrompter, ToolContext, ToolError
from app.tools.registry import ParseError, Registry, parse_command, split_chat_line
from app.workspace import WorkspaceError, ensure_workspace

EXIT_COMMANDS = ("-exit",)
HISTORY_FILE = ".history"
PROMPT = "cap> "


class DashCompleter(Completer):
    """Completes dash-commands and REPL built-ins (also after `-help `)."""

    def __init__(self, names: Callable[[], Iterable[str]]) -> None:
        self._names = names

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        tokens = text.split(" ")
        if len(tokens) == 1:
            word = tokens[0]
            prefix = "-"
        elif len(tokens) == 2 and tokens[0] == "-help":
            word = tokens[1]
            prefix = ""
        else:
            return
        if prefix and not word.startswith("-") and word:
            return
        for name in self._names():
            candidate = prefix + name
            if candidate.startswith(word):
                yield Completion(candidate, start_position=-len(word))


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
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(workspace / HISTORY_FILE)),
            completer=DashCompleter(registry.command_names),
            complete_while_typing=False,
        )

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
            interactive=True,
            **self.ctx_kwargs,
        )
        ctx.prompter = InteractivePrompter(self.console, lambda: ctx.texts)
        return ctx

    def say(self, key: str, style: str | None = None, **values: object) -> None:
        text = self.texts().message(key, **values)
        self.console.print(f"[{style}]{text}[/{style}]" if style else text, highlight=False)

    def error(self, text: str) -> None:
        self.console.print(f"[red]{text}[/red]", highlight=False, markup=True)

    # --- startup ----------------------------------------------------------

    def first_start(self) -> None:
        if settings_exist(self.workspace):
            return
        texts = Texts()
        prompter = InteractivePrompter(self.console, lambda: texts)
        language = prompter.choose(
            texts.message("setup.language_question"),
            [Choice("cs", "Čeština"), Choice("en", "English")],
            default="cs",
        )
        save_settings(self.workspace, Settings(language=language))

    def greet(self) -> None:
        try:
            settings = self.settings()
        except SettingsError as exc:
            self.error(str(exc))
            return
        self.say("repl.greeting", style="bold")
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
        except ParseError as exc:
            self.error(exc.message)
        except (ToolError, SetupAborted) as exc:
            self.error(exc.message)
        except SettingsError as exc:
            self.error(str(exc))
        except KeyboardInterrupt:
            self.say("repl.cancelled", style="yellow")
        except Exception as exc:  # noqa: BLE001 - the REPL must survive any tool error
            self.error(texts.message("repl.tool_crashed", detail=short_error(exc)))

    def chat(self, text: str) -> None:
        try:
            settings = self.settings()
        except SettingsError as exc:
            self.error(str(exc))
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
        if line.startswith("-"):
            self.run_command(line)
        else:
            self.chat(line)
        return True

    def run(self) -> int:
        self.first_start()
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
        console.print(f"[red]error: {exc}[/red]")
        return 1
    return Repl(workspace, console).run()

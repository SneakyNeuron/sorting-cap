"""What a tool handler receives: settings, workspace, texts, console and prompts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from rich.console import Console
from rich.markup import escape

from app.credentials import Credentials
from app.errors import UserFacingError
from app.settings import Settings, load_settings, save_settings
from app.texts import Texts

if TYPE_CHECKING:
    import httpx

    from app.setup.runner import CommandRunner
    from app.tools.registry import Registry


class ToolError(UserFacingError):
    """A tool failed; `message` is a user-facing text in the current language."""


class InputUnavailable(ToolError):
    """A tool asked for a choice, text or hidden input where none can be given."""


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    disabled_reason: str | None = None


class Prompter(Protocol):
    def confirm(self, question: str, default: bool = False) -> bool: ...

    def choose(self, question: str, choices: list[Choice], default: str | None = None) -> str: ...

    def text(
        self,
        question: str,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str: ...

    def secret(self, question: str) -> str: ...


class HeadlessPrompter:
    """Runner prompts: confirmations follow `--yes`; anything else fails the run."""

    def __init__(self, texts: Callable[[], Texts], yes: bool = False) -> None:
        self._texts = texts
        self.yes = yes

    def confirm(self, question: str, default: bool = False) -> bool:
        return self.yes

    def _fail(self, question: str):
        raise InputUnavailable(self._texts().message("headless.input_needed", question=question))

    def choose(self, question: str, choices: list[Choice], default: str | None = None) -> str:
        self._fail(question)

    def text(self, question, default=None, validate=None) -> str:
        self._fail(question)

    def secret(self, question: str) -> str:
        self._fail(question)


class InteractivePrompter:
    """Chat prompts via prompt_toolkit. These prompts never touch the history file."""

    def __init__(self, console: Console, texts: Callable[[], Texts]) -> None:
        self.console = console
        self._texts = texts

    def _ask(self, message: str, *, password: bool = False, default: str = "") -> str:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory

        session: PromptSession[str] = PromptSession(history=InMemoryHistory())
        return session.prompt(message, is_password=password, default=default)

    def confirm(self, question: str, default: bool = False) -> bool:
        t = self._texts()
        hint = t.message("prompt.yes_no_default_yes" if default else "prompt.yes_no_default_no")
        yes_words = {w.strip() for w in t.message("prompt.yes_words").split(",")}
        no_words = {w.strip() for w in t.message("prompt.no_words").split(",")}
        while True:
            answer = self._ask(f"{question} {hint} ").strip().lower()
            if not answer:
                return default
            if answer in yes_words:
                return True
            if answer in no_words:
                return False

    def choose(self, question: str, choices: list[Choice], default: str | None = None) -> str:
        t = self._texts()
        self.console.print(question, highlight=False, markup=False)
        selectable: dict[str, str] = {}
        number = 0
        for choice in choices:
            if choice.disabled_reason:
                self.console.print(
                    f"   –  {choice.label} ({choice.disabled_reason})",
                    style="dim",
                    highlight=False,
                    markup=False,
                )
                continue
            number += 1
            selectable[str(number)] = choice.value
            mark = " [green]←[/green]" if choice.value == default else ""
            self.console.print(
                f"  [cyan]{number}[/cyan]  {escape(choice.label)}{mark}", highlight=False
            )
        default_number = next((n for n, v in selectable.items() if v == default), "")
        suffix = f" ({default_number})" if default_number else ""
        while True:
            answer = self._ask(t.message("prompt.choice") + suffix + " ").strip() or default_number
            if answer in selectable:
                return selectable[answer]
            if answer in selectable.values():
                return answer
            self.console.print(t.message("prompt.choice_invalid"), highlight=False, markup=False)

    def text(self, question, default=None, validate=None) -> str:
        while True:
            answer = self._ask(f"{question} ", default=default or "").strip()
            problem = validate(answer) if validate else None
            if problem is None:
                return answer
            self.console.print(problem, style="yellow", highlight=False, markup=False)

    def secret(self, question: str) -> str:
        return self._ask(f"{question} ", password=True).strip()


@dataclass
class ToolContext:
    workspace: Path
    console: Console
    registry: Registry
    prompter: Prompter | None = None
    env: Mapping[str, str] | None = None
    interactive: bool = False
    runner_factory: Callable[[ToolContext], CommandRunner] | None = None
    http_transport: httpx.BaseTransport | None = None
    settings: Settings = field(init=False)
    _runner: CommandRunner | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.settings = load_settings(self.workspace)
        if self.prompter is None:
            self.prompter = HeadlessPrompter(lambda: self.texts)

    @property
    def texts(self) -> Texts:
        return Texts(self.settings.language)

    @property
    def credentials(self) -> Credentials:
        return Credentials(self.workspace, self.env)

    @property
    def runner(self) -> CommandRunner:
        if self._runner is None:
            if self.runner_factory is not None:
                self._runner = self.runner_factory(self)
            else:
                from app.setup.runner import CommandRunner

                self._runner = CommandRunner(self.workspace)
        return self._runner

    def http_client(self, timeout: float = 10.0) -> httpx.Client:
        import httpx

        return httpx.Client(transport=self.http_transport, timeout=timeout)

    def save_settings(self, settings: Settings) -> None:
        save_settings(self.workspace, settings)
        self.settings = settings

    def msg(self, key: str, **values: object) -> str:
        return self.texts.message(key, **values)

    def say(self, key: str, style: str | None = None, **values: object) -> None:
        self.console.print(self.msg(key, **values), style=style, highlight=False, markup=False)

    # Prompt shortcuts
    def confirm(self, question: str, default: bool = False) -> bool:
        return self.prompter.confirm(question, default)

    def choose(self, question: str, choices: list[Choice], default: str | None = None) -> str:
        return self.prompter.choose(question, choices, default)

    def text(self, question, default=None, validate=None) -> str:
        return self.prompter.text(question, default, validate)

    def secret(self, question: str) -> str:
        return self.prompter.secret(question)

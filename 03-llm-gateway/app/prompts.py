"""Load prompt templates (from 04-prompt-library) and render them."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

_env = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)


@dataclass
class Prompt:
    name: str
    description: str
    output: str  # "text" | "json"
    system: str
    template: str
    required_vars: list[str]
    optional_vars: list[str]
    temperature: float = 0.7
    max_chars: int | None = None
    schema: dict | None = None
    model: str | None = None
    example_vars: dict = field(default_factory=dict)

    def render(self, variables: dict) -> tuple[str, str]:
        full = {k: None for k in self.optional_vars} | {"brand": ""} | variables
        return _env.from_string(self.system).render(full), _env.from_string(self.template).render(full)

    def missing(self, variables: dict) -> list[str]:
        return [v for v in self.required_vars if variables.get(v) in (None, "")]


def parse_prompt(path: Path) -> Prompt:
    raw = yaml.safe_load(path.read_text())
    vars_ = raw.get("vars") or {}
    output = raw.get("output", "text")
    if output not in ("text", "json"):
        raise ValueError(f"{path.name}: output must be text or json")
    schema = raw.get("schema")
    if output == "json":
        if not schema:
            raise ValueError(f"{path.name}: json output needs a schema")
        Draft202012Validator.check_schema(schema)
    return Prompt(
        name=raw["name"],
        description=raw.get("description", ""),
        output=output,
        system=raw.get("system", ""),
        template=raw["template"],
        required_vars=[k for k, v in vars_.items() if v == "required"],
        optional_vars=[k for k, v in vars_.items() if v != "required"],
        temperature=float(raw.get("temperature", 0.7)),
        max_chars=raw.get("max_chars"),
        schema=schema,
        model=raw.get("model"),
        example_vars=raw.get("example_vars") or {},
    )


class PromptStore:
    """Re-reads the directory when any file changes, so a `git pull` of the
    prompt library takes effect without restarting the gateway."""

    def __init__(self, directory: str):
        self.directory = Path(directory)
        self._stamp: tuple = ()
        self._prompts: dict[str, Prompt] = {}

    def _current_stamp(self) -> tuple:
        files = sorted(self.directory.glob("*.y*ml"))
        return tuple((f.name, f.stat().st_mtime_ns) for f in files)

    def all(self) -> dict[str, Prompt]:
        stamp = self._current_stamp()
        if stamp != self._stamp:
            prompts = {}
            for f in sorted(self.directory.glob("*.y*ml")):
                p = parse_prompt(f)
                prompts[p.name] = p
            self._prompts, self._stamp = prompts, stamp
        return self._prompts

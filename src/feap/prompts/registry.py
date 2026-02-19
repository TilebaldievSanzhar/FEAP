"""Jinja2 template registry for prompt loading."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env: Environment | None = None


def get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
    return _env


def render(template_path: str, **kwargs) -> str:
    """Render a template with given variables."""
    env = get_env()
    tmpl = env.get_template(template_path)
    return tmpl.render(**kwargs).strip()

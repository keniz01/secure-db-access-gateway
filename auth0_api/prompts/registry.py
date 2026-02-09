"""
File-based prompt registry loader.
Loads prompts from YAML/JSON and optional external template files.
Version is implied by Git (branch/commit/tag); no version parameter resolved in this loader.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str, version: Optional[str] = None) -> Dict[str, Any]:
    """
    Load prompt config by name.

    name: e.g. "text_to_sql" (loads text_to_sql/system.yaml) or "text_to_sql/user"
           (loads text_to_sql/user.yaml).
    version: reserved for future use (e.g. path or Git tag resolution). Ignored.
    """
    if "/" in name:
        base = PROMPTS_DIR / name.rsplit("/", 1)[0]
        config_name = name.rsplit("/", 1)[1]
        config_path = base / f"{config_name}.yaml"
    else:
        base = PROMPTS_DIR / name
        config_path = base / "system.yaml"

    if not config_path.exists():
        if base.exists():
            alt = base.with_suffix(".yaml")
            if alt.exists():
                config_path = alt
            else:
                raise FileNotFoundError(f"Prompt config for '{name}' not found: {config_path}")
        else:
            raise FileNotFoundError(f"Prompt '{name}' not found under {PROMPTS_DIR}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if config.get("template_path"):
        template_path = config_path.parent / config["template_path"]
        with open(template_path, encoding="utf-8") as tf:
            config["template"] = tf.read()

    return config


def render_prompt(config: Dict[str, Any], **kwargs: Any) -> str:
    """
    Render template with variables (simple {var} substitution).
    For Jinja2, use: Template(config["template"]).render(**kwargs).
    """
    template = config.get("template", "")
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template

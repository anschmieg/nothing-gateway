"""Prepare an MLX view of the source checkpoint with the raw completion template."""
import argparse
import json
from pathlib import Path

TEMPLATE = ("{% for message in messages %}{{ message.content }}"
            "{% if message.role == 'assistant' %}{{ eos_token }}{% endif %}{% endfor %}")


def prepare(source, target):
    config = json.loads((source / "config.json").read_text())
    if config["model_type"] != "qwen3_5_text":
        raise ValueError("Expected a Qwen 3.5 text-only checkpoint")
    config["model_type"] = "qwen3_5"
    tokenizer = json.loads((source / "tokenizer_config.json").read_text())
    tokenizer["chat_template"] = TEMPLATE
    target.mkdir(parents=True, exist_ok=False)
    for name in ("model.safetensors", "tokenizer.json", "generation_config.json"):
        (target / name).symlink_to((source / name).resolve(strict=True))
    (target / "config.json").write_text(json.dumps(config))
    (target / "tokenizer_config.json").write_text(json.dumps(tokenizer))
    (target / "chat_template.jinja").write_text(TEMPLATE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.target)

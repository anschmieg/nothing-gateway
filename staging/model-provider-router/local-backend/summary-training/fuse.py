"""Merge the trained MLX linear adapters into the original HF checkpoint layout."""
import argparse
import json
import shutil
from pathlib import Path

import mlx.core as mx


def fuse(source, adapter, target):
    parameters = json.loads((adapter / "adapter_config.json").read_text())
    scale = parameters["lora_parameters"]["scale"]
    weights = mx.load(str(source / "model.safetensors"))
    lora = mx.load(str(adapter / "adapters.safetensors"))
    if not lora or any(not key.endswith((".lora_a", ".lora_b")) for key in lora):
        raise ValueError("Expected a nonempty linear LoRA adapter")
    merged = 0
    for name, a in lora.items():
        if not name.endswith(".lora_a"):
            continue
        module = name.removesuffix(".lora_a")
        b = lora[module + ".lora_b"]
        key = module + ".weight"
        if key not in weights:
            key = key.replace("model.language_model.", "model.", 1)
        weight = weights[key]
        delta = (scale * b.T) @ a.T
        if delta.shape != weight.shape:
            raise ValueError(f"Shape mismatch for {key}")
        weights[key] = weight + delta.astype(weight.dtype)
        merged += 1
    if merged * 2 != len(lora):
        raise ValueError("Unpaired adapter tensor")
    target.mkdir(parents=True, exist_ok=False)
    mx.save_safetensors(str(target / "model.safetensors"), weights, metadata={"format": "pt"})
    for name in ("config.json", "generation_config.json", "tokenizer.json",
                 "tokenizer_config.json", "chat_template.jinja"):
        shutil.copy2(source / name, target / name)
    return merged


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("adapter", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(f"Merged {fuse(args.source, args.adapter, args.target)} projections")

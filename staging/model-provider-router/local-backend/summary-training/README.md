# Heretic trace summary adapter

This experiment fine-tunes the merged
[Qwen 3.5 0.8B reasoning summarizer](https://huggingface.co/mithulaartigala/reasoning-summarizer-qwen3.5-0.8b-preview)
at revision `558fc651bf01bbe7cf306ebf2fb32032d7b4e818`.
The plain-text run uses a raw prompt bounded by `Trace:` and `Summary:`.
The native-format run preserves the checkpoint's original raw trace input
and four-field JSON output. In both runs, the summary target is one concise
sentence or the exact marker `SKIP`.

The generated data has 192 training examples and 40 validation examples.
Validation uses withheld subjects but shares some templates with training.
The 18 independently written traces in `holdout.jsonl` are the separate
behavioral check. Review the claims in each summary against its source and
record how often actionable traces receive summaries and filler is skipped.

The training run uses [MLX LM](https://github.com/ml-explore/mlx-lm) at
`1bf8da05a1ef30a3eb2e8a634180be6557fd3ca0` with the configuration in
`lora.yaml`. Install that revision and `tqdm` in a Python 3.12 environment.
Download the pinned merged Hugging Face checkpoint into `source-checkpoint`.
The SHA-256 for its `model.safetensors` is
`86d18e255137dd12be7501bf83e45de60faf01d1a2635acf2ce8c5f1741000ba`.

Run from this directory:

```sh
python prepare_data.py data
python prepare_checkpoint.py source-checkpoint mlx-checkpoint
mlx_lm.lora --config lora.yaml --model mlx-checkpoint --data data --adapter-path adapter
python evaluate.py mlx-checkpoint adapter > holdout-results.jsonl
```

The lower-rank run with the original JSON format uses:

```sh
python prepare_data.py --native-format data-native
mlx_lm.lora --config lora-native.yaml --model mlx-checkpoint --data data-native --adapter-path adapter-native
python evaluate.py --native-format mlx-checkpoint adapter-native > holdout-native-results.jsonl
```

`fuse.py` can merge a validated adapter into the original Hugging Face
layout for conversion and quantization with llama.cpp. Recheck the resulting
artifact on the gateway CPU before selecting it for live traces.

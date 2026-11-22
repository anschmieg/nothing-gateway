"""Run the compact trace adapter on independently written evaluation excerpts."""
import argparse
import json
import time
import urllib.request
from pathlib import Path

from prepare_data import PREFIX, SUFFIX


def ollama_generate(model, prompt, max_tokens):
    body = {
        "model": model, "prompt": prompt, "raw": True, "stream": False,
        "options": {"num_gpu": 0, "num_thread": 2, "num_ctx": 2048,
                    "num_predict": max_tokens, "temperature": 0},
        "keep_alive": 0,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    return result["response"], result.get("done_reason")


def mlx_generator(model_path, adapter, max_tokens):
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(model_path, adapter_path=adapter)
    sampler = make_sampler(temp=0)

    def complete(prompt):
        text = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                        sampler=sampler, verbose=False)
        return text, None

    return complete


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("adapter", nargs="?")
    parser.add_argument("--ollama", action="store_true")
    parser.add_argument("--native-format", action="store_true")
    args = parser.parse_args()
    max_tokens = 128 if args.native_format else 64
    if args.ollama:
        def complete(prompt):
            return ollama_generate(args.model, prompt, max_tokens)
    else:
        complete = mlx_generator(args.model, args.adapter, max_tokens)

    with Path(__file__).with_name("holdout.jsonl").open() as cases:
        for line in cases:
            sample = json.loads(line)
            start = time.monotonic()
            prompt = sample["trace"] + "\n" if args.native_format else PREFIX + sample["trace"] + SUFFIX
            raw, stop = complete(prompt)
            summary = raw.strip()
            if args.native_format:
                try:
                    decoded = json.loads(raw)["summary"]
                    if not isinstance(decoded, str):
                        raise ValueError("summary must be text")
                    summary = decoded
                except (TypeError, ValueError, KeyError):
                    stop = "invalid_summary_json"
            print(json.dumps({
                "case": sample["case"], "summary": summary.strip(),
                "seconds": round(time.monotonic() - start, 3),
                "stop": stop,
            }), flush=True)

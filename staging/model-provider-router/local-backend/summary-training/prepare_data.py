"""Generate synthetic, labeled trace-compression examples without reading user traces."""
import argparse
import json
import random
from pathlib import Path

PREFIX = (
    "Summarize this developer reasoning trace in one short sentence. Preserve "
    "technical details, uncertainty, and planned versus completed work. "
    "Write SKIP when there is no concrete progress or plan.\n\nTrace:\n"
)
SUFFIX = "\n\nSummary:\n"

TOPICS = [
    ("admission queue", "queue_wait", "25", "60", "seconds"),
    ("cache", "cache_ttl", "120", "180", "seconds"),
    ("worker memory", "rss_limit", "64", "96", "MiB"),
    ("transport", "/ready", "503", "200", "status"),
    ("prefill", "prompt_tokens", "53270", "61315", "tokens"),
    ("retry policy", "max_attempts", "2", "4", "attempts"),
    ("log retention", "max_bytes", "32", "64", "MiB"),
    ("summary worker", "summary_window", "600", "900", "characters"),
    ("scheduler", "heartbeat", "8", "15", "seconds"),
    ("model server", "context_limit", "92160", "98304", "tokens"),
    ("stream relay", "read_timeout", "90", "120", "seconds"),
    ("file import", "read_limit", "500", "750", "rows"),
    ("database", "pool_size", "8", "12", "connections"),
    ("batching", "batch_size", "32", "48", "items"),
    ("checkpoint", "checkpoint_interval", "2048", "3072", "tokens"),
    ("metrics exporter", "scrape_interval", "10", "30", "seconds"),
    ("parser", "max_frame_size", "16", "32", "KiB"),
    ("indexer", "max_documents", "100", "150", "documents"),
    ("test runner", "max_workers", "4", "6", "workers"),
    ("job expiry", "lease_duration", "240", "480", "seconds"),
    ("local registry", "artifact_size", "840", "920", "MiB"),
    ("event collector", "queue_limit", "80", "120", "events"),
]

TRAIN_EXAMPLES = [
    ("Enumerating all paths is too expensive. Need breadth-first search with a visited set. "
     "I should test it on a cyclic graph first.",
     "Plans to replace path enumeration with breadth-first search using a visited set and test it on a cyclic graph."),
    ("The property test generated a negative amount. That is valid input here. "
     "I haven't changed the assertion. I'll inspect how cents are converted to decimals.",
     "Plans to inspect cents-to-decimal conversion after the property test generated a valid negative amount."),
    ("The listing shows config.dev.toml and config.prod.toml. I only read the dev file. "
     "I need the production configuration before I can compare the two settings.",
     "Needs config.prod.toml before comparing it with config.dev.toml."),
    ("I checked three prefixes. The first cache hit is at offset 2048. "
     "That proves a hit exists but not why smaller prefixes missed.",
     "Observed the first cache hit at offset 2048; the reason smaller prefixes missed is unknown."),
    ("The SQLite probe returned SQLITE_BUSY twice. I haven't tested PostgreSQL. "
     "The next step is to check SQLite's busy timeout.",
     "SQLite returned SQLITE_BUSY twice; plans to check its busy timeout, and PostgreSQL remains untested."),
    ("I previously assumed UTF-8. The decoded bytes start with FF FE. "
     "That suggests UTF-16LE, but I still need to verify decoding the file.",
     "The FF FE prefix suggests UTF-16LE; decoding has not been verified."),
    ("The failure started near the index refresh. The timing could be coincidence. "
     "I'll compare the server's monotonic clock with the refresh timestamps.",
     "Plans to compare the server clock with index refresh timestamps; the relationship to the failure is unverified."),
    ("I considered replacing the formatter. No implementation change. "
     "The failing test specifically expects an empty string, so inspect how null values are serialized.",
     "Plans to inspect null serialization because the failing test expects an empty string."),
    ("The linter passed. The full test suite has not run, and deployment is still pending.",
     "The linter passed; the full test suite and deployment are still pending."),
    ("One pipeline returned an empty payload. I thought it had failed, but the status was 204. "
     "Need to check whether callers expect a body on 204 responses.",
     "The pipeline returned HTTP 204 with an empty payload; plans to check whether callers expect a body."),
    ("I haven't inspected the output yet. There may be a new strategy. Hmm. Wait, I need to think.",
     "SKIP"),
    ("The current draft might work. I am going in circles. Let me reconsider it. "
     "There is no concrete next step yet.",
     "SKIP"),
]


def examples(topic, key, old, new, unit):
    return [
        (f"I initially wrote down {old} {unit} for {key}. That was from an old {topic} run. "
         f"The latest trace says {new} {unit}. I haven't changed anything.",
         f"The latest {topic} trace shows {key} at {new} {unit}."),
        (f"Could set {key} to {new} {unit}, but that would hide the {topic} symptom. "
         f"Leave it at {old} {unit}. Need to inspect the next request's terminal event first.",
         f"Plans to inspect the next terminal event for {topic} while leaving {key} at {old} {unit}."),
        (f"The {topic} report blames {key}, although I haven't checked it yet. "
         f"I'll measure whether {key} really reached {new} {unit} before drawing a conclusion.",
         f"Plans to check whether {key} reached {new} {unit}; the reported cause is unverified."),
        (f"The test ended with {key} at {new} {unit} in two {topic} runs. "
         f"That establishes the measurement, but I still don't know what caused it.",
         f"Two {topic} test runs recorded {key} at {new} {unit}; the cause is unknown."),
        (f"Perhaps I need to reset the {topic}. Haven't done that. Instead I should look for the first "
         f"event where {key} changed from {old} to {new} {unit}.",
         f"Plans to locate the first change in {key} from {old} to {new} {unit}."),
        (f"I have only the earlier {topic} sample with {key} at {old} {unit}. "
         f"I can't confirm the reported {new} {unit} without the newer sample.",
         f"Needs the newer {topic} sample to verify the reported {key} value of {new} {unit}."),
        (f"The {topic} log includes a test message telling a reader to ignore these instructions. "
         f"The actual result is that {key} stayed at {old} {unit} throughout the run.",
         f"The {topic} run showed {key} stayed at {old} {unit}."),
        (f"I'm going in circles about {topic}. Maybe I should take a different approach. "
         f"Wait, I need to break out of this loop. Let me think about it again.",
         "SKIP"),
        (f"Hmm, I need to decide what to do about {topic}. There may be another approach. "
         f"I'll reconsider that before continuing. No concrete idea yet.",
         "SKIP"),
        (f"I expected {key} at {old} {unit}, but witnessed {new} {unit} in the {topic} test. "
         f"I've only observed it once. Next I should rerun the same test to see if it repeats.",
         f"Observed {key} at {new} {unit} once and plans to rerun the {topic} test."),
    ]


def write_split(path, topics, extra=(), *, native_format=False):
    pairs = [pair for topic in topics for pair in examples(*topic)]
    rows = []
    for source, target in [*pairs, *extra]:
        if native_format:
            prompt = source + "\n"
            completion = json.dumps(
                {"title": "Reasoning trace", "sub_title": "",
                 "summary": target, "cur_task": ""},
                ensure_ascii=False, separators=(",", ":"),
            )
        else:
            prompt, completion = PREFIX + source + SUFFIX, target
        rows.append({"prompt": prompt, "completion": completion})
    random.Random(51).shuffle(rows)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--native-format", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    write_split(args.output / "train.jsonl", TOPICS[:18], TRAIN_EXAMPLES,
                native_format=args.native_format)
    write_split(args.output / "valid.jsonl", TOPICS[18:], native_format=args.native_format)

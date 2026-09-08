from pathlib import Path


def load_prompt(name: str) -> str:
    return Path(__file__).with_name(f"{name}.txt").read_text(encoding="utf-8")

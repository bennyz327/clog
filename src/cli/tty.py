from __future__ import annotations


def prompt_input(prompt: str, default: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        return default

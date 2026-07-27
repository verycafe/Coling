#!/usr/bin/env python3
"""Minimal, formatting-preserving access to the root Codex notify key."""

from __future__ import annotations

import json
import tomllib
from copy import deepcopy
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised when config.toml cannot be updated safely."""


@dataclass(frozen=True)
class KeySpan:
    start: int
    end: int


def _newline_for(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _skip_line(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline < 0 else newline + 1


def _statement_end(text: str, start: int) -> int:
    index = start
    square_depth = 0
    brace_depth = 0
    seen_equals = False
    quote: str | None = None
    multiline = False

    while index < len(text):
        if quote is not None:
            if multiline:
                run = 0
                while index + run < len(text) and text[index + run] == quote:
                    run += 1
                if 3 <= run <= 5:
                    quote = None
                    multiline = False
                    index += run
                    continue
                if quote == '"' and text[index] == "\\":
                    index += 2
                    continue
                index += 1
                continue

            char = text[index]
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if text.startswith('"""', index):
            quote = '"'
            multiline = True
            index += 3
            continue
        if text.startswith("'''", index):
            quote = "'"
            multiline = True
            index += 3
            continue

        char = text[index]
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if char == "#":
            line_end = _skip_line(text, index)
            if seen_equals and square_depth == 0 and brace_depth == 0:
                return line_end
            index = line_end
            continue
        if char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "=" and square_depth == 0 and brace_depth == 0:
            seen_equals = True
        elif char == "\n" and seen_equals and square_depth == 0 and brace_depth == 0:
            return index + 1
        index += 1

    return len(text)


def _root_statements(text: str) -> tuple[list[KeySpan], int]:
    spans: list[KeySpan] = []
    index = 0

    while index < len(text):
        line_start = index
        while index < len(text) and text[index] in " \t\r":
            index += 1

        if index >= len(text):
            return spans, len(text)
        if text[index] == "\n":
            index += 1
            continue
        if text[index] == "#":
            index = _skip_line(text, index)
            continue
        if text[index] == "[":
            return spans, line_start

        end = _statement_end(text, index)
        spans.append(KeySpan(line_start, end))
        index = end

    return spans, len(text)


def _parse_document(text: str) -> dict:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Invalid TOML: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigError("config.toml must contain a TOML document")
    return payload


def _notify_span(text: str) -> tuple[KeySpan | None, int]:
    spans, insertion = _root_statements(text)
    for span in spans:
        statement = text[span.start : span.end]
        try:
            parsed = tomllib.loads(statement)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"Could not safely locate root notify: {error}") from error
        if set(parsed) == {"notify"} and isinstance(parsed["notify"], list):
            return span, insertion
    return None, insertion


def read_notify(text: str) -> list[str] | None:
    document = _parse_document(text)
    if "notify" not in document:
        return None
    notify = document["notify"]
    if not isinstance(notify, list) or not all(isinstance(item, str) for item in notify):
        raise ConfigError("root notify must be an array of strings")
    span, _ = _notify_span(text)
    if span is None:
        raise ConfigError("root notify exists but its source span could not be located")
    return list(notify)


def render_array(items: list[str]) -> str:
    def toml_string(item: str) -> str:
        return json.dumps(item, ensure_ascii=False).replace("\x7f", "\\u007F")

    return "[" + ", ".join(toml_string(item) for item in items) + "]"


def set_notify(text: str, notify: list[str]) -> str:
    before = _parse_document(text)
    read_notify(text)
    span, insertion = _notify_span(text)
    newline = _newline_for(text)
    rendered = f"notify = {render_array(notify)}{newline}"

    if span is not None:
        updated = text[: span.start] + rendered + text[span.end :]
    else:
        prefix = text[:insertion]
        suffix = text[insertion:]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        separator = newline if suffix else ""
        updated = prefix + rendered + separator + suffix

    after = _parse_document(updated)
    expected = deepcopy(before)
    expected["notify"] = notify
    if read_notify(updated) != notify or after != expected:
        raise ConfigError("generated config did not preserve the requested notify command")
    return updated


def remove_notify(text: str) -> str:
    before = _parse_document(text)
    current = read_notify(text)
    if current is None:
        return text
    span, _ = _notify_span(text)
    if span is None:
        raise ConfigError("root notify exists but its source span could not be located")
    updated = text[: span.start] + text[span.end :]
    after = _parse_document(updated)
    expected = deepcopy(before)
    expected.pop("notify", None)
    if read_notify(updated) is not None or after != expected:
        raise ConfigError("generated config still contains root notify")
    return updated

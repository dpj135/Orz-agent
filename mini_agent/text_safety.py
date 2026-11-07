"""Replace unpaired surrogates so outbound JSON stays valid Unicode text.

Python can hold strings that are not valid Unicode scalar values: ``input()``
decodes undecodable terminal bytes with ``surrogateescape`` on POSIX, and
``os.fsdecode`` does the same for file names. ``json.dumps`` renders such
characters as ``\\udcXX`` escapes, which the encoder accepts but RFC 8259
forbids, so a remote parser rejects the request with an unhelpful 400.

The runtime keeps going on damaged input: replacements are counted so callers
can report them, matching the lossy ``replace``/``ignore`` handling already used
for file reads and truncated output. Sanitising is not an encoding guarantee.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

REPLACEMENT_CHARACTER = "\ufffd"

# Keep the diagnostic to one readable line even when many fields are damaged.
MAX_REPORTED_FIELDS = 5

_HIGH_SURROGATE_START = 0xD800
_HIGH_SURROGATE_END = 0xDBFF
_LOW_SURROGATE_START = 0xDC00
_LOW_SURROGATE_END = 0xDFFF


def replace_unpaired_surrogates(text: str) -> tuple[str, int]:
    """Replace each unpaired surrogate with U+FFFD and count the replacements.

    A high surrogate followed by a low surrogate is kept: JSON represents that
    pair with two valid escapes and a strict parser accepts it.

    Args:
        text: String that may contain surrogate code points.

    Returns:
        The cleaned text (the original object when nothing changed) and the
        number of replaced code points.
    """
    characters: list[str] = []
    replaced = 0
    index = 0
    while index < len(text):
        code = ord(text[index])
        if (
            _HIGH_SURROGATE_START <= code <= _HIGH_SURROGATE_END
            and index + 1 < len(text)
            and _LOW_SURROGATE_START <= ord(text[index + 1]) <= _LOW_SURROGATE_END
        ):
            characters.append(text[index])
            characters.append(text[index + 1])
            index += 2
            continue
        if _HIGH_SURROGATE_START <= code <= _LOW_SURROGATE_END:
            characters.append(REPLACEMENT_CHARACTER)
            replaced += 1
            index += 1
            continue
        characters.append(text[index])
        index += 1
    if not replaced:
        return text, 0
    return "".join(characters), replaced


def sanitize(value: Any, path: str = "payload") -> tuple[Any, list[tuple[str, int]]]:
    """Copy value with unpaired surrogates replaced, reporting where they were.

    Args:
        value: JSON-shaped object (mapping, sequence, string or scalar).
        path: Location label used in the returned findings.

    Returns:
        The copied value, whose input is never mutated, and one ``(path, count)``
        entry per string field that needed replacement. Counts only are
        reported; field content is never retained.
    """
    if isinstance(value, str):
        cleaned, replaced = replace_unpaired_surrogates(value)
        return cleaned, ([(path, replaced)] if replaced else [])
    if isinstance(value, list):
        items: list[Any] = []
        findings: list[tuple[str, int]] = []
        for index, item in enumerate(value):
            cleaned_item, item_findings = sanitize(item, f"{path}[{index}]")
            items.append(cleaned_item)
            findings.extend(item_findings)
        return items, findings
    if isinstance(value, dict):
        entries: dict[Any, Any] = {}
        findings = []
        for key, item in value.items():
            cleaned_key, key_findings = sanitize(key, f"{path}.<key>")
            cleaned_item, item_findings = sanitize(item, f"{path}.{key}")
            entries[cleaned_key] = cleaned_item
            findings.extend(key_findings)
            findings.extend(item_findings)
        return entries, findings
    return value, []


def json_size(value: Any) -> int:
    """Return the UTF-8 byte size of value after sanitising it.

    Args:
        value: JSON-shaped object whose serialized size is being budgeted.

    Returns:
        Byte length using the same ``ensure_ascii=False`` form used by tools.
    """
    cleaned, _ = sanitize(value)
    return len(json.dumps(cleaned, ensure_ascii=False).encode("utf-8"))


def sanitize_and_report(value: Any, path: str) -> Any:
    """Sanitise value, warning once about the damaged fields it contained.

    Call this where text enters the conversation so stored history stays clean
    and later steps of the same turn cannot repeat the warning.

    Args:
        value: JSON-shaped object about to be stored or serialized.
        path: Root label for the reported field paths.

    Returns:
        The sanitized copy; the input is not mutated.
    """
    cleaned, findings = sanitize(value, path)
    if findings:
        total = sum(count for _, count in findings)
        fields = ", ".join(f"{field} ({count})" for field, count in findings[:MAX_REPORTED_FIELDS])
        omitted = len(findings) - MAX_REPORTED_FIELDS
        if omitted > 0:
            fields += f", +{omitted} more field(s)"
        logger.warning("replaced %d unpaired surrogate(s) with U+FFFD in %s", total, fields)
    return cleaned

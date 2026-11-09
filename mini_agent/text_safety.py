"""Replace unpaired surrogates in user input before it reaches the model.

Terminal input can carry undecodable bytes that Python stores as unpaired
surrogates (U+D800-U+DFFF). JSON forbids them, so a strict gateway rejects the
whole request with 400. Only user input is sanitized; tool results and
assistant replies are trusted and passed through unchanged.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SURROGATE_CODE_POINTS = range(0xD800, 0xE000)
REPLACEMENT_CHARACTER = "\ufffd"


def sanitize_user_input(text: str) -> str:
    """Replace unpaired surrogates with U+FFFD and log one warning.

    Args:
        text: Prompt text supplied by the user.

    Returns:
        The original string when no surrogate is present, otherwise a cleaned copy.
    """
    replaced = sum(1 for character in text if ord(character) in SURROGATE_CODE_POINTS)
    if replaced == 0:
        return text
    logger.warning("replaced %d unpaired surrogate(s) in user input", replaced)
    return "".join(
        REPLACEMENT_CHARACTER if ord(character) in SURROGATE_CODE_POINTS else character
        for character in text
    )

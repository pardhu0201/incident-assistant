"""Error fingerprinting: turn a noisy log line into a stable signature.

Two error log lines are "the same incident" when they share a root cause, not
when they share exact text - "Connection pool exhausted after 30234ms" and
"...after 30891ms" are the same problem with different timings. Fingerprinting
strips the parts of a message that vary run-to-run (numbers, ids, timestamps,
quoted values, hex/UUID tokens) and hashes what's left, plus the top frame of
any stack trace, which is usually the most stable part of an exception.
"""

from __future__ import annotations

import hashlib
import re

# No trailing \b: a digit run immediately followed by a unit suffix
# ("28288ms", "30000ms") has no word boundary between digit and letter (both
# are \w), so a bounded \d+\b would silently fail to match it - leaving the
# exact timing value embedded in the "normalised" text and making two
# instances of the same error compare unequal. Matching bare digit runs
# (no boundary requirement at all) strips the number regardless of what
# follows it.
_NUMBER_RE = re.compile(r"\d+(\.\d+)?")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.I)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_WHITESPACE_RE = re.compile(r"\s+")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b"
)


def normalise_message(message: str) -> str:
    """Strip volatile tokens so structurally-identical errors compare equal."""
    text = message
    text = _TIMESTAMP_RE.sub("<ts>", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _IP_RE.sub("<ip>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _QUOTED_RE.sub("<val>", text)
    text = _NUMBER_RE.sub("<n>", text)
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def top_frame(stack_trace: str) -> str:
    """The first non-empty line of a stack trace - usually the exception type
    and the innermost call site, which is the most stable identifying part."""
    for line in stack_trace.splitlines():
        line = line.strip()
        if line:
            return normalise_message(line)
    return ""


def compute_fingerprint(service: str, message: str, stack_trace: str = "") -> str:
    """A short, stable signature identifying "this kind of error in this service"."""
    parts = [service.strip().lower(), normalise_message(message), top_frame(stack_trace)]
    digest = hashlib.blake2b("\x00".join(parts).encode("utf-8"), digest_size=10).hexdigest()
    return digest

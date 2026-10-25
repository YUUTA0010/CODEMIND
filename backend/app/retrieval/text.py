"""Shared tokenization for code-aware lexical and hashed-embedding features."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on", "with", "as", "at", "by", "be", "this", "that",
    "from", "not", "are", "was", "were", "when", "then", "if", "else", "should", "does", "do", "did", "has", "have", "had",
    "but", "can", "will", "would", "get", "set", "self", "cls", "none", "true", "false", "return", "def", "class", "import",
    "var", "let", "const", "function", "int", "str", "its", "we", "i", "my", "our", "you", "bug", "error", "fails", "fail",
    "expected", "actual", "instead", "after", "before", "which", "than", "so", "also", "some", "any", "all", "no",
}


def split_identifier(ident: str) -> list[str]:
    """`parseHTTPResponse_v2` -> ['parse', 'http', 'response', 'v', '2'] (lower-cased sub-tokens)."""
    out: list[str] = []
    for part in ident.split("_"):
        if not part:
            continue
        out.extend(m.group(0).lower() for m in _CAMEL_RE.finditer(part))
    return out


def identifiers(text: str) -> list[str]:
    return [m.group(0) for m in _WORD_RE.finditer(text)]


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Code-aware tokens: each identifier contributes itself (lowercased) plus its sub-tokens."""
    tokens: list[str] = []
    for ident in identifiers(text):
        low = ident.lower()
        subs = split_identifier(ident)
        if (keep_stopwords or low not in STOPWORDS) and len(low) > 1:
            tokens.append(low)
        if len(subs) > 1:
            tokens.extend(s for s in subs if len(s) > 1 and (keep_stopwords or s not in STOPWORDS))
    return tokens

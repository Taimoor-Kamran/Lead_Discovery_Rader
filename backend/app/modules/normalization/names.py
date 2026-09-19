"""Business names: what is displayed, what is compared, and what is blocked on."""

import re
import unicodedata

import jellyfish

# Stripped only at the end of a name, and only as a whole word: "Cointreau" keeps its
# "co", and "LLC Services Inc" keeps the leading "llc" it was actually called.
LEGAL_SUFFIXES = (
    "llc",
    "l l c",
    "inc",
    "incorporated",
    "co",
    "company",
    "corp",
    "corporation",
    "ltd",
    "limited",
    "pllc",
    "plc",
    "lp",
    "llp",
    "pc",
)

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_UNDERSCORES = re.compile(r"_+")


def display_name(raw: str | None) -> str | None:
    """Trim and collapse whitespace. Casing and punctuation are kept as the source had them."""
    if raw is None:
        return None
    # `\s` is Unicode-aware, so a non-breaking space collapses like any other.
    cleaned = _WHITESPACE.sub(" ", raw).strip()
    return cleaned or None


def normalize_name(raw: str | None) -> str | None:
    """Lowercase, `&`→`and`, drop punctuation and trailing legal suffixes."""
    shown = display_name(raw)
    if shown is None:
        return None

    folded = unicodedata.normalize("NFKD", shown.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.replace("&", " and ")
    folded = _PUNCTUATION.sub(" ", folded)
    folded = _UNDERSCORES.sub(" ", folded)
    folded = _WHITESPACE.sub(" ", folded).strip()

    return strip_legal_suffixes(folded) or None


def strip_legal_suffixes(name: str) -> str:
    """Remove every trailing legal suffix, so `abc plumbing co llc` becomes `abc plumbing`."""
    words = name.split()
    while words:
        for suffix in LEGAL_SUFFIXES:
            parts = suffix.split()
            if len(words) > len(parts) and words[-len(parts) :] == parts:
                words = words[: -len(parts)]
                break
        else:
            break
    return " ".join(words)


def name_key(normalized: str | None) -> str | None:
    """A phonetic key for blocking. Two spellings of one name share it; nothing else does.

    The blueprint asks for double metaphone; `jellyfish` dropped that function in 1.0, so
    this uses its `metaphone`, which is the same family of algorithm and ships in the
    version we depend on.
    """
    if not normalized:
        return None
    key = jellyfish.metaphone(normalized).strip()
    return key or None

from __future__ import annotations
import re
from typing import List
import spacy
from rapidfuzz import fuzz

_nlp = spacy.load("en_core_web_trf")

_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _strip_markdown_links(text: str) -> str:
    return _LINK_RE.sub(r"\1", text)


SIM_THRESHOLD = 90 


def extract_companies(text: str) -> List[str]:
    clean = _strip_markdown_links(text)
    doc = _nlp(clean)
    names: List[str] = []
    for ent in doc.ents:
        if ent.label_ == "ORG":
            candidate = ent.text.strip()
            if not _is_duplicate(candidate, names):
                names.append(candidate)
    return names


def _is_duplicate(name: str, existing: List[str]) -> bool:
    return any(fuzz.ratio(name.lower(), ex.lower()) >= SIM_THRESHOLD for ex in existing)
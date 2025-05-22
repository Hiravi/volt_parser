from __future__ import annotations
import re
from typing import List
import spacy

_nlp = spacy.load("en_core_web_trf")

_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_PUNCT_RE = re.compile(r"[',.\s]+$")

def _strip_markdown_links(text: str) -> str:
    return _LINK_RE.sub(r"\1", text)

def _normalize(name: str) -> str:
    name = name.replace("’", "'")
    name = _PUNCT_RE.sub("", name.lower())
    return name.strip()

def _is_duplicate(name: str, acc: List[str]) -> bool:
    norm = _normalize(name)
    for existing in acc:
        ex_norm = _normalize(existing)
        if norm == ex_norm:
            return True
        if norm in ex_norm.split() or ex_norm in norm.split():
            return True
    return False

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
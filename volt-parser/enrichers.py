from __future__ import annotations

import asyncio, os, json, logging, re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from rich.console import Console

from .cache import CACHE
from .extractor import _normalize

try:
    import anthropic
except ImportError:
    anthropic = None

console = Console()
HEADERS = {
    "User-Agent": "volt-parser/0.3 (+https://github.com/volt-parser)",
    "Accept": "application/json",
}

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "duckduckgo").lower()


class EnrichmentError(Exception):
    """Raised when network/parse error should bubble up and retry."""


# ---------------------------------------------------------------------------
# HTTP helper with cache + retry
# ---------------------------------------------------------------------------
@retry(
    stop=stop_after_attempt(3), wait=wait_exponential(1, 8), retry=retry_if_exception_type(EnrichmentError)
)
async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    if (cached := CACHE.get(url)) is not None:
        return cached
    async with session.get(url, headers=HEADERS, timeout=15) as resp:
        if resp.status != 200:
            raise EnrichmentError(f"GET {url} → {resp.status}")
        data = await resp.json()
        CACHE.set(url, data)
        return data


# ---------------------------------------------------------------------------
# WikiData helpers
# ---------------------------------------------------------------------------
async def _wd_search(session: aiohttp.ClientSession, name: str) -> Optional[Dict[str, Any]]:
    url = (
        "https://www.wikidata.org/w/api.php?action=wbsearchentities&search="
        + quote_plus(name)
        + "&language=en&format=json"
    )
    data = await _fetch_json(session, url)
    return data.get("search", [None])[0] if data.get("search") else None


async def _wd_entity(session: aiohttp.ClientSession, qid: str) -> Dict[str, Any]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    data = await _fetch_json(session, url)
    return data["entities"][qid]


async def _wd_first_claim_label(
    session: aiohttp.ClientSession, entity: Dict[str, Any], prop: str
) -> Optional[str]:
    claims = entity.get("claims", {})
    if prop not in claims:
        return None
    snak = claims[prop][0]["mainsnak"]
    dv = snak.get("datavalue")
    if not dv:
        return None
    val = dv["value"]
    if isinstance(val, dict) and val.get("entity-type") == "item":
        linked_qid = "Q" + str(val["numeric-id"])
        linked = await _wd_entity(session, linked_qid)
        return linked.get("labels", {}).get("en", {}).get("value")
    return str(val)


async def _wd_official_site(entity: Dict[str, Any]) -> Optional[str]:
    claims = entity.get("claims", {})
    if "P856" not in claims:
        return None
    return claims["P856"][0]["mainsnak"]["datavalue"]["value"]


# ---------------------------------------------------------------------------
# Wikipedia summary
# ---------------------------------------------------------------------------
async def _wiki_summary(session: aiohttp.ClientSession, title: str) -> str:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}"
    try:
        data = await _fetch_json(session, url)
        return data.get("extract", "")
    except EnrichmentError:
        return ""


# ---------------------------------------------------------------------------
# LLM + web‑search fallback (optional)
# ---------------------------------------------------------------------------
async def _serp_links(session: aiohttp.ClientSession, query: str) -> List[str]:
    if SEARCH_PROVIDER == "duckduckgo":
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        resp = await session.get(url, headers={"User-Agent": HEADERS["User-Agent"]})
        html = await resp.text()
        links = re.findall(r'(?s)<a[^>]+class="result__a"[^>]+href="(https?://[^"\s]+)"', html)[:5]
        return links
    return []


async def _llm_guess_website(session: aiohttp.ClientSession, company: str) -> Optional[str]:
    if not anthropic or not ANTHROPIC_KEY:
        return None
    links = await _serp_links(session, company + " official site")
    if not links:
        return None
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = (
        f"You are given a list of URLs returned by a web search for the company '{company}'.\n"
        "Choose *only* the URL that is most likely the company's official homepage.\n"
        "URLs:\n" + "\n".join(links) + "\n"
        "Respond with a single URL and nothing else."
    )
    try:
        completion = await asyncio.to_thread(
            client.completions.create,
            model="claude-3-haiku-20240307",
            max_tokens=20,
            temperature=0,
            prompt=prompt,
        )
        url = completion.completion.strip().split()[0]
        if url.startswith("http"):
            return url
    except Exception as exc:
        logging.warning("LLM fallback failed for %s: %s", company, exc)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def enrich_company(name: str, *, use_llm: bool = False) -> Dict[str, Any]:
    """Return enriched dict for *name*.

    Parameters
    ----------
    use_llm : bool, default False
        If True and WikiData has no official website, call Anthropic to guess it.
    """
    async with aiohttp.ClientSession() as session:
        hit = await _wd_search(session, name)
        if not hit:
            raise EnrichmentError(f"No Wikidata hit for '{name}'")

        qid = hit["id"]
        canonical = hit["label"]
        entity = await _wd_entity(session, qid)

        description = await _wiki_summary(session, canonical) or hit.get("description", "")
        website = await _wd_official_site(entity)
        if not website and use_llm:
            website = await _llm_guess_website(session, name)
        website = website or f"https://www.wikidata.org/wiki/{qid}"

        sector = await _wd_first_claim_label(session, entity, "P452") or "Unknown"
        hq = await _wd_first_claim_label(session, entity, "P159") or "Unknown"

        key_people: List[Dict[str, str]] = []
        for prop, role in (("P1037", "Manager"), ("P112", "Founder")):
            label = await _wd_first_claim_label(session, entity, prop)
            if label and all(p["name"] != label for p in key_people):
                key_people.append({"name": label, "role": role})
            if len(key_people) >= 3:
                break

        profile = {
            "name": canonical,
            "aliases": [name] if _normalize(name) != _normalize(canonical) else [],
            "website": website,
            "sector": sector,
            "hq_location": hq,
            "description": description,
            "key_people": key_people,
            "competitors": [],
            "sources": {
                "wikidata": f"https://www.wikidata.org/wiki/{qid}",
                "wikipedia": f"https://en.wikipedia.org/wiki/{quote_plus(canonical.replace(' ', '_'))}"
            },
        }

        console.log(f"Enriched '{name}' → '{canonical}' | website: {website}")
        return profile


# ---------------------------------------------------------------------------
# Dev quick‑check (optional) -------------------------------------------------
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    async def _demo(target: str, llm: bool = False):
        data = await enrich_company(target, use_llm=llm)
        print(json.dumps(data, ensure_ascii=False, indent=2))

    asyncio.run(_demo(sys.argv[1] if len(sys.argv) > 1 else "Phoenix Tailings", use_llm="--llm" in sys.argv))
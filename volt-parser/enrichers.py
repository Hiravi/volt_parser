from __future__ import annotations


import asyncio

import json

import logging

import os

from typing import Any, Dict, List, Optional

from urllib.parse import quote_plus


import aiohttp

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from rich.console import Console


from .cache import CACHE

from .extractor import _normalize


try:

    import anthropic

except ImportError:

    anthropic = None


console = Console()

HEADERS = {

    "User-Agent": "volt-parser/0.3 (+https://github.com/Hiravi/volt_parser)",

    "Accept": "application/json",

}


ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")



class EnrichmentError(Exception):

    """Raised when an unrecoverable enrichment problem occurs."""


# Cached HTTP GET w/ retry ---------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(1, 8), retry=retry_if_exception_type(EnrichmentError))

async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:

    if (cached := CACHE.get(url)) is not None:

        return cached

    async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:

        if resp.status != 200:

            raise EnrichmentError(f"GET {url} → {resp.status}")

        data = await resp.json()

        CACHE.set(url, data)

        return data


# WikiData helpers -----------------------------------------------------------

async def _wd_search(session: aiohttp.ClientSession, name: str) -> Optional[Dict[str, Any]]:

    url = ("https://www.wikidata.org/w/api.php?action=wbsearchentities&search="

           + quote_plus(name) + "&language=en&format=json")

    data = await _fetch_json(session, url)

    return data.get("search", [None])[0] if data.get("search") else None



async def _wd_entity(session: aiohttp.ClientSession, qid: str) -> Dict[str, Any]:

    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

    data = await _fetch_json(session, url)

    return data["entities"][qid]



async def _wd_first_claim_label(session: aiohttp.ClientSession, entity: Dict[str, Any], prop: str) -> Optional[str]:

    claims = entity.get("claims", {})

    if prop not in claims:

        return None

    snak = claims[prop][0]["mainsnak"]

    dv = snak.get("datavalue")

    if not dv:

        return None

    val = dv["value"]

    if isinstance(val, dict) and val.get("entity-type") == "item":

        linked = await _wd_entity(session, "Q" + str(val["numeric-id"]))

        return linked.get("labels", {}).get("en", {}).get("value")

    return str(val)



def _wd_official_site(entity: Dict[str, Any]) -> Optional[str]:

    claims = entity.get("claims", {})

    if "P856" not in claims:

        return None

    return claims["P856"][0]["mainsnak"]["datavalue"]["value"]


# Wikipedia summary ----------------------------------------------------------

async def _wiki_summary(session: aiohttp.ClientSession, title: str) -> str:

    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}"

    try:

        data = await _fetch_json(session, url)

        return data.get("extract", "")

    except EnrichmentError:

        return ""


# Anthropic Web-Search Tool ----------------------------------

async def _anthropic_web_search(company: str) -> Optional[Dict[str, Any]]:



    if not anthropic or not ANTHROPIC_KEY:

        return None


    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


    tool_def = {

        "type": "web_search_20250305",

        "name": "web_search",

        "max_uses": 3,

    }


    prompt = (
        "You are a JSON-only extractor. "
        "Your entire reply will be parsed with `json.loads` and any non-JSON "
        "text will cause failure.\n\n"
        f"Find the official website and a short profile for the company "
        f"'{company}'.\n"
        "Respond with **one** JSON object that has exactly these keys:\n"
        "- website\n"
        "- description\n"
        "- sector\n"
        "- hq_location\n"
        "- key_people (list)\n"
        "- competitors (list)\n\n"
        "Do **not** wrap the JSON in markdown, do **not** add commentary, "
        "explanations, or pre/post text. "
        "If you cannot find a field value, use null. "
        "If you break any of these rules the answer will be discarded."
    )


    try:

        resp = await asyncio.to_thread(

            client.messages.create,

            model="claude-3-5-haiku-latest",

            max_tokens=400,

            temperature=0,

            messages=[{"role": "user", "content": prompt}],

            tools=[tool_def],

        )


        raw = "".join(p.text for p in resp.content if p.type == "text").strip()

        console.log(f"Claude text: {raw}")


        import re, json as _json


        m = re.search(r'\{.*?\}', raw, re.DOTALL)

        if not m:

            return None

        blob = m.group()


        try:

            return _json.loads(blob)

        except _json.JSONDecodeError:

            pass


        repaired = re.sub(

            r'(".*?"\s*:\s*)([^"\{\[\d\-\s][^,\n}]*)',

            lambda m: m.group(1) + '"' + m.group(2).strip() + '"',

            blob,

        )

        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        try:

            return _json.loads(repaired)

        except Exception:

            pass


        url_match = re.search(r'https?://[^\s"\'\\]+', raw)

        return {

            "website": url_match.group(0) if url_match else "Unknown",

            "description": raw[:300] + ("…" if len(raw) > 300 else ""),

            "sector": "Unknown",

            "hq_location": "Unknown",

            "key_people": [],

        }



    except Exception as exc:

        logging.warning("Anthropic web-search failed for %s: %s", company, exc)

        return None


# Public API ----------------------------------------------------------------
async def enrich_company(name: str, *, use_llm: bool = False) -> Dict[str, Any]:
    console.log(f"Starting enrichment for: '{name}' (LLM enabled: {use_llm})")

   

    async with aiohttp.ClientSession() as session:

        console.log("🔍 Searching WikiData...")

        hit = await _wd_search(session, name)

        if not hit:

            console.log("No WikiData entry found")

            if use_llm:

                console.log("Attempting Anthropic web search...")

                web_data = await _anthropic_web_search(name)

                if web_data:

                    console.log(f"Web search found data for '{name}'")


                    key_people: List[str] = []
                    if isinstance(web_data.get("key_people"), list):
                        for person in web_data["key_people"][:3]:
                            if isinstance(person, dict) and "name" in person:
                                key_people.append(str(person["name"]))
                            elif isinstance(person, str):
                                key_people.append(person.strip())

                   
                    return {

                        "name": name,

                        "aliases": [],

                        "website": web_data.get("website", "Unknown"),

                        "sector": web_data.get("sector", "Unknown"),

                        "hq_location": web_data.get("hq_location", "Unknown"),

                        "description": web_data.get("description", "(found via web search)"),

                        "key_people": key_people,

                        "competitors": [],

                        "sources": {"anthropic_web_search": "Anthropic Web Search Tool"},

                    }

                else:

                    console.log("Web search also failed")

            else:

                console.log("LLM fallback disabled")

            raise EnrichmentError(f"No Wikidata hit for '{name}'")


        qid = hit["id"]

        canonical = hit["label"]

        console.log(f"Found WikiData: {canonical} ({qid})")

       

        entity = await _wd_entity(session, qid)


        description = await _wiki_summary(session, canonical) or hit.get("description", "")

        website = _wd_official_site(entity)

       

        if not website and use_llm:

            console.log("No official website in WikiData, trying web search...")

            web_data = await _anthropic_web_search(name)

            if web_data and web_data.get("website") != "Unknown":

                website = web_data["website"]

                console.log(f"Found website via web search: {website}")

       

        website = website or f"https://www.wikidata.org/wiki/{qid}"


        sector = await _wd_first_claim_label(session, entity, "P452") or "Unknown"

        hq = await _wd_first_claim_label(session, entity, "P159") or "Unknown"


        key_people: List[str] = []
        for prop in ("P1037", "P112"):
            label = await _wd_first_claim_label(session, entity, prop)
            if label and label not in key_people:
                key_people.append(label)
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


if __name__ == "__main__":

    import sys


    async def _demo(target: str, llm: bool = False):

        try:

            data = await enrich_company(target, use_llm=llm)

            console.log("Success! Here's the result:")

            print(json.dumps(data, ensure_ascii=False, indent=2))

        except EnrichmentError as exc:

            console.print(f"[red] {exc}[/red]")

            return


    args = sys.argv[1:]

    use_llm = False

    if args and args[-1] == "--llm":

        use_llm = True

        args = args[:-1]

    target = " ".join(args) if args else "Phoenix Tailings"


    if os.name == "nt":

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


    asyncio.run(_demo(target, llm=use_llm))
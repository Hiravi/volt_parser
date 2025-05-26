from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
from importlib import util as importlib_util
from pathlib import Path
import click
from rich.console import Console
from .extractor import extract_companies
from .enrichers import enrich_company, EnrichmentError
from .json_utils import write_json

console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _silence_warnings() -> None:
    """Reduce noisy FutureWarning from torch/thinc."""
    os.environ.setdefault("PYTORCH_DISABLE_PICKLE_WARNING", "1")
    logging.getLogger("torch").setLevel(logging.ERROR)

def _anthropic_ready() -> bool:
    """True if library *and* API key present."""
    return (
        importlib_util.find_spec("anthropic") is not None
        and bool(os.getenv("ANTHROPIC_API_KEY"))
    )

# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("input_file", type=click.Path(exists=False, dir_okay=False))
@click.option("-o", "--output", default="result.json", show_default=True, help="Output JSON path")
@click.option("--pretty", is_flag=True, help="Pretty‑print JSON to stdout as well")
@click.option("--llm-fallback", is_flag=True, help="Use Anthropic Claude to guess website when missing (needs key)")
@click.option("--suppress-warnings", is_flag=True, help="Hide spaCy/PyTorch warnings")
def main(input_file: str, output: str, pretty: bool, llm_fallback: bool, suppress_warnings: bool):
    if suppress_warnings:
        _silence_warnings()
    
    if llm_fallback and not _anthropic_ready():
        console.print("[red]LLM fallback requested, but Anthropic lib or API key not found.[/]")
        sys.exit(1)
    
    if input_file == "-":
        text = sys.stdin.read()
    else:
        path = Path(input_file)
        if not path.exists():
            console.print(f"[red]File not found:[/] {path}")
            sys.exit(1)
        text = path.read_text(encoding="utf-8")
    
    names = extract_companies(text)
    if not names:
        console.print("[yellow]No companies detected — writing empty list[/]")
        write_json([], Path(output))
        sys.exit(0)
    
    console.print(f"Detected [bold]{len(names)}[/] companies: {', '.join(names)}")
    
    async def _gather() -> list[dict]:
        tasks = [asyncio.create_task(enrich_company(n, use_llm=llm_fallback)) for n in names]
        enriched = []
        for t in tasks:
            try:
                enriched.append(await t)
            except EnrichmentError as exc:
                console.print(f"[red]Skip[/] {exc}")
        return enriched
    
    data = asyncio.run(_gather())
    write_json(data, Path(output))
    console.print(f"[green]JSON written → {output}")
    
    if pretty:
        console.print_json(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
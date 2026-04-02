"""
MCP server for the Generic Doc Grabber.

Exposes a `grab_documents` tool that Claude can call after finding good seed URLs
via web search. Claude acts as the search engine — finding resource pages, download
portals, and documentation hubs — then hands the seeds to this tool for scraping
and downloading.

Usage:
    python mcp_server.py
"""

from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "doc-grabber",
    instructions=(
        "Use this server to download documents (PDF, DOCX, XLS, etc.) from web pages. "
        "First use web search to find high-quality seed URLs — pages that LINK TO documents, "
        "not direct document URLs. Look for official 'Downloads', 'Resources', 'Documentation', "
        "or 'Support' pages. Then call grab_documents with those seed URLs."
    ),
)

DEFAULT_PATTERN = r"\.(pdf|docx?|xlsx?|csv|pptx?|txt|rtf|odt|ods|epub)$"
DEFAULT_OUTDIR = os.path.join(os.path.expanduser("~"), "Desktop", "DownloadedDocs")


def _fetch_links(page_url: str, timeout: int = 30) -> list[dict]:
    """Fetch a page and return all links with their anchor text."""
    resp = requests.get(page_url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (compatible; DocGrabber/1.0)"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(page_url, href)
        text = a.get_text(strip=True)
        links.append({"url": full, "text": text})
    return links


def _same_domain(url: str, allowed: set[str] | None) -> bool:
    if not allowed:
        return True
    netloc = urlparse(url).netloc.lower()
    return any(netloc.endswith(d.lower()) for d in allowed)


def _filter_links(
    links: list[dict],
    pattern: re.Pattern,
    keyword: str | None,
    domains: set[str] | None,
) -> list[dict]:
    filtered = []
    seen = set()
    for link in links:
        url = link["url"]
        text = link["text"]
        if keyword and keyword.lower() not in url.lower() and keyword.lower() not in text.lower():
            continue
        if not pattern.search(url):
            continue
        if not _same_domain(url, domains):
            continue
        if url not in seen:
            seen.add(url)
            filtered.append(link)
    return filtered


def _download(url: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    filename = os.path.basename(urlparse(url).path) or "document"
    # Remove query params from filename
    filename = filename.split("?")[0]
    path = os.path.join(dest_dir, filename)
    with requests.get(url, stream=True, timeout=60, headers={
        "User-Agent": "Mozilla/5.0 (compatible; DocGrabber/1.0)"
    }) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return path


@mcp.tool()
def grab_documents(
    seeds: list[str],
    pattern: str = DEFAULT_PATTERN,
    keyword: str | None = None,
    domains: list[str] | None = None,
    outdir: str = DEFAULT_OUTDIR,
    dry_run: bool = False,
) -> str:
    """Scrape seed URLs for document links and download matching files.

    Use this AFTER finding good seed URLs via web search. Seeds should be
    pages that LINK TO documents (e.g. a company's Downloads page), not
    direct URLs to individual documents.

    Args:
        seeds: List of seed page URLs to scan for document links.
               These should be resource/download pages, NOT direct doc URLs.
        pattern: Regex pattern for matching document links.
                 Default matches PDF, DOCX, XLSX, CSV, PPTX, TXT, RTF, ODT, ODS, EPUB.
        keyword: Optional keyword that must appear in the link text or URL.
                 Useful for filtering to specific topics (e.g. "manual", "datasheet").
        domains: Optional list of allowed domains. Only documents hosted on
                 these domains will be downloaded. Useful for staying on official sources.
        outdir: Output directory for downloaded files.
                Default: ~/Desktop/DownloadedDocs
        dry_run: If True, only list matching document links without downloading.
                 Useful for previewing what would be grabbed before committing.
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    domain_set = set(domains) if domains else None
    all_candidates: list[dict] = []
    scan_results = []

    for seed in seeds:
        try:
            links = _fetch_links(seed)
            all_candidates.extend(links)
            scan_results.append(f"Scanned {seed}: found {len(links)} links")
        except Exception as e:
            scan_results.append(f"FAILED {seed}: {e}")

    targets = _filter_links(all_candidates, compiled, keyword, domain_set)

    if dry_run:
        lines = scan_results + [f"\nMatched {len(targets)} document(s):"]
        for t in targets:
            lines.append(f"  - {t['text'][:80] or '(no text)'} → {t['url']}")
        return "\n".join(lines)

    download_results = []
    for t in targets:
        try:
            path = _download(t["url"], outdir)
            download_results.append(f"✔ {os.path.basename(path)}")
        except Exception as e:
            download_results.append(f"✖ {t['url']} ({e})")

    lines = scan_results + [
        f"\nMatched {len(targets)} document(s). Downloaded to {outdir}:",
    ] + download_results

    return "\n".join(lines)


@mcp.tool()
def preview_seed(url: str) -> str:
    """Preview what documents a seed URL contains before downloading.

    Use this to evaluate whether a URL is a good seed — it shows all
    links on the page that look like documents. Helps you pick the
    best seeds before calling grab_documents.

    Args:
        url: A candidate seed URL to preview.
    """
    try:
        links = _fetch_links(url)
    except Exception as e:
        return f"Failed to fetch {url}: {e}"

    compiled = re.compile(DEFAULT_PATTERN, re.IGNORECASE)
    doc_links = [l for l in links if compiled.search(l["url"])]
    all_links_count = len(links)

    lines = [
        f"Page: {url}",
        f"Total links: {all_links_count}",
        f"Document links: {len(doc_links)}",
        "",
    ]

    if doc_links:
        lines.append("Documents found:")
        for l in doc_links[:50]:  # Cap at 50 to avoid huge output
            name = os.path.basename(urlparse(l["url"]).path)
            text = l["text"][:60] or "(no text)"
            lines.append(f"  - [{text}] → {name}")
        if len(doc_links) > 50:
            lines.append(f"  ... and {len(doc_links) - 50} more")
    else:
        lines.append("No document links found on this page.")
        # Show a sample of what IS there
        sample = links[:10]
        if sample:
            lines.append("\nSample links on page:")
            for l in sample:
                lines.append(f"  - {l['text'][:60] or '(no text)'} → {l['url'][:80]}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()

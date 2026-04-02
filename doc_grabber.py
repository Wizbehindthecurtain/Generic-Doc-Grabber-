"""
Generic document scraper/downloader.

Given one or more seed URLs, the script fetches each page, extracts links,
filters them to likely document files (PDF/DOC/DOCX/XLS/XLSX by default, or a
custom regex), and downloads the matches into a folder on your Desktop.

Usage examples (PowerShell or bash):
    python doc_grabber.py --urls https://example.com/manuals
    python doc_grabber.py --urls https://example.com/a https://example.com/b \
        --pattern "(datasheet|manual).*\.pdf$" --domains generac.com
    python doc_grabber.py --urls-file seeds.txt --outdir "C:/Users/you/Desktop/Docs"

Dependencies:
    pip install requests beautifulsoup4
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_PATTERN = r"\.(pdf|docx?|xls[x]?)$"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generic document downloader")
    parser.add_argument(
        "--urls",
        nargs="*",
        help="Seed page URLs to scan for document links",
    )
    parser.add_argument(
        "--urls-file",
        help="Path to a text file containing one seed URL per line",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"Regex for matching document links (default: {DEFAULT_PATTERN})",
    )
    parser.add_argument(
        "--keyword",
        help="Optional keyword that must appear in the link text or URL",
    )
    parser.add_argument(
        "--domains",
        nargs="*",
        help="Optional allowlist of domain(s); only links on these domains will be downloaded",
    )
    parser.add_argument(
        "--outdir",
        default=os.path.join(os.path.expanduser("~"), "Desktop", "DownloadedDocs"),
        help="Output directory (default: ~/Desktop/DownloadedDocs)",
    )
    return parser.parse_args(argv)


def load_seed_urls(args: argparse.Namespace) -> list[str]:
    seeds: list[str] = []
    if args.urls:
        seeds.extend(args.urls)
    if args.urls_file:
        with open(args.urls_file, "r", encoding="utf-8") as f:
            seeds.extend(line.strip() for line in f if line.strip())
    return seeds


def same_domain(url: str, allowed: set[str] | None) -> bool:
    if not allowed:
        return True
    netloc = urlparse(url).netloc.lower()
    return any(netloc.endswith(d.lower()) for d in allowed)


def fetch_links(page_url: str) -> list[str]:
    resp = requests.get(page_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(page_url, href)
        links.append(full)
    return links


def filter_links(
    links: list[str], pattern: re.Pattern, keyword: str | None, domains: set[str] | None
) -> list[str]:
    filtered: list[str] = []
    for link in links:
        if keyword and keyword.lower() not in link.lower():
            continue
        if not pattern.search(link):
            continue
        if not same_domain(link, domains):
            continue
        filtered.append(link)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for link in filtered:
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


def download(url: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    filename = os.path.basename(url.split("?")[0]) or "document"
    path = os.path.join(dest_dir, filename)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return path


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    seeds = load_seed_urls(args)
    if not seeds:
        print("Provide at least one seed URL via --urls or --urls-file", file=sys.stderr)
        return 1

    pattern = re.compile(args.pattern, re.IGNORECASE)
    domains = set(args.domains) if args.domains else None
    all_candidates: list[str] = []

    for seed in seeds:
        try:
            links = fetch_links(seed)
            all_candidates.extend(links)
            print(f"Scanned {seed}: found {len(links)} links")
        except Exception as e:  # noqa: BLE001
            print(f"Failed to scan {seed}: {e}", file=sys.stderr)

    targets = filter_links(all_candidates, pattern, args.keyword, domains)
    print(f"Matched {len(targets)} document link(s). Downloading to {args.outdir} ...")

    for url in targets:
        try:
            path = download(url, args.outdir)
            print(f"✔ {path}")
        except Exception as e:  # noqa: BLE001
            print(f"✖ {url} ({e})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

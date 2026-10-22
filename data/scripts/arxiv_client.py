"""Minimal arXiv Atom API client. Standard library only.

The arXiv API is a public, documented interface intended for programmatic use.
This client follows arXiv's stated etiquette: a descriptive User-Agent, one
request at a time, and a delay between requests. Do not remove the delay.
"""
from __future__ import annotations
import time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict

API = "http://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
UA = "paper-credibility-check-data/0.2 (research tool; contact via repo)"
MIN_INTERVAL = 3.0          # seconds between requests, per arXiv guidance
_last_call = [0.0]


@dataclass
class ArxivEntry:
    arxiv_id: str = ""
    arxiv_version: str = ""
    title: str = ""
    authors: list = field(default_factory=list)
    abstract: str = ""
    published: str = ""
    updated: str = ""
    comment: str | None = None
    doi: str | None = None
    primary_category: str = ""
    categories: list = field(default_factory=list)
    abs_url: str = ""
    pdf_url: str = ""

    def as_dict(self):
        return asdict(self)


def _sleep():
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def fetch_raw(params: dict, retries: int = 4) -> str:
    url = API + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        _sleep()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception as exc:                      # network, 5xx, timeout
            last = exc
            time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"arXiv API failed after {retries} attempts: {last}\nURL: {url}")


def _text(node, path):
    el = node.find(path, NS)
    return el.text.strip() if el is not None and el.text else None


def parse(xml_text: str) -> list[ArxivEntry]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        raw_id = _text(e, "a:id") or ""
        # https://arxiv.org/abs/2307.08122v3  ->  ("2307.08122", "v3")
        tail = raw_id.rsplit("/abs/", 1)[-1]
        if "v" in tail and tail.rsplit("v", 1)[-1].isdigit():
            base, ver = tail.rsplit("v", 1)
            ver = "v" + ver
        else:
            base, ver = tail, ""
        if not base:
            continue
        title = " ".join((_text(e, "a:title") or "").split())
        summary = " ".join((_text(e, "a:summary") or "").split())
        authors = [" ".join((a.text or "").split())
                   for a in e.findall("a:author/a:name", NS) if a.text]
        cats = [c.get("term") for c in e.findall("a:category", NS) if c.get("term")]
        prim = e.find("x:primary_category", NS)
        pdf = ""
        for ln in e.findall("a:link", NS):
            if ln.get("title") == "pdf":
                pdf = ln.get("href") or ""
        out.append(ArxivEntry(
            arxiv_id=base, arxiv_version=ver, title=title, authors=authors,
            abstract=summary, published=_text(e, "a:published") or "",
            updated=_text(e, "a:updated") or "",
            comment=_text(e, "x:comment"), doi=_text(e, "x:doi"),
            primary_category=(prim.get("term") if prim is not None else ""),
            categories=cats,
            abs_url=f"https://arxiv.org/abs/{base}",
            pdf_url=pdf or f"https://arxiv.org/pdf/{base}{ver}",
        ))
    return out


def search(query: str, start: int = 0, max_results: int = 100) -> list[ArxivEntry]:
    return parse(fetch_raw({
        "search_query": query, "start": start, "max_results": max_results,
        "sortBy": "submittedDate", "sortOrder": "descending",
    }))


def by_ids(ids: list[str]) -> list[ArxivEntry]:
    return parse(fetch_raw({"id_list": ",".join(ids), "max_results": len(ids)}))

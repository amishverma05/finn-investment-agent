"""PDF text extraction, chunking and keyword retrieval (BM25).

We don't send whole 300-page annual reports to a model. We split them into chunks
and pick the few that answer the question, which keeps token use (and cost) low.
"""

import hashlib
import json
import logging
import re

import pymupdf
from rank_bm25 import BM25Okapi

from app import net
from app.config import DATA_DIR
from app.obs import short_url, timed

log = logging.getLogger("finn.docs")

TEXT_DIR = DATA_DIR / "text"
TEXT_DIR.mkdir(parents=True, exist_ok=True)

_TOKEN = re.compile(r"[a-z0-9]+")


def pages(url: str, max_pages: int | None = None) -> list[str]:
    """Text of each page of a PDF, cached on disk."""
    path = TEXT_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if path.exists():
        out = json.loads(path.read_text())
        return out[:max_pages] if max_pages else out
    data = net.get_pdf(url)
    if not data:
        return []
    with timed(log, "pdf text extract", url=short_url(url, 60), mb=round(len(data) / 1e6, 1)) as t:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:  # noqa: BLE001
            t["error"] = type(e).__name__
            return []
        out = [re.sub(r"[ \t]+", " ", p.get_text()) for p in doc]
        t["pages"] = len(out)
        t["chars"] = sum(len(p) for p in out)
    path.write_text(json.dumps(out))
    return out[:max_pages] if max_pages else out


def best_pages(page_texts: list[str], keywords: list[str], k: int = 3, prefer: str | None = None) -> list[tuple[int, str]]:
    """Pick the k pages that mention the most keywords; returned in page order."""
    scored = []
    for i, t in enumerate(page_texts):
        low = t.lower()
        s = sum(low.count(kw) for kw in keywords)
        if prefer and prefer in low:
            s *= 1.5
        if s:
            scored.append((s, i))
    top = sorted(sorted(scored, reverse=True)[:k], key=lambda x: x[1])
    return [(i, page_texts[i]) for _, i in top]


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def chunk(page_texts: list[str], source: str, size: int = 1400) -> list[dict]:
    """Split pages into roughly `size`-character chunks, keeping page numbers for citations."""
    out = []
    for pno, text in enumerate(page_texts, start=1):
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf = ""
        for para in paras:
            if len(buf) + len(para) > size and buf:
                out.append({"source": source, "page": pno, "text": buf})
                buf = ""
            buf += para + "\n"
        if len(buf) > 80:
            out.append({"source": source, "page": pno, "text": buf})
    return out


class Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        with timed(log, "bm25 index build", chunks=len(chunks)):
            self.bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks]) if chunks else None

    def search(self, query: str, k: int = 6) -> list[dict]:
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self.chunks[i] for i in ranked if scores[i] > 0]

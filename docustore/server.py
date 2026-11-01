"""Browse and search the documents in the docs directory."""

import csv
import html
import io
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from typing import TypedDict
from urllib.parse import parse_qs, quote, unquote, urlparse

from huggingface_hub import HfApi, get_token, snapshot_download
from markdown_it import MarkdownIt


@dataclass
class Config:
    docs: Path = Path(os.environ.get("DOCUSTORE_DOCS", Path.home() / ".local/share/docustore"))


cfg = Config()
REPO = os.environ.get("DOCUSTORE_REPO", "")
SYNC_OFF = "set DOCUSTORE_REPO to a Hugging Face repo id, and HF_TOKEN or hf auth login, to sync"
PORT = int(os.environ.get("DOCUSTORE_PORT", "8020"))
TEXT_EXT = {".md", ".txt", ".csv", ".xml", ".json", ".html", ".yaml", ".yml", ".toml"}
_BLOB_LEN = 80
_SNIPPET_BEFORE = 100
_SNIPPET_AFTER = 160
logger = logging.getLogger("docustore")
md = MarkdownIt("commonmark", {"html": False}).enable("table")


def files() -> list[Path]:
    out = []
    for root, dirs, names in os.walk(cfg.docs):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        out += [Path(root, n).relative_to(cfg.docs) for n in names if not n.startswith(".")]
    return sorted(out)


def inside(p: Path) -> bool:
    return p.resolve().is_relative_to(cfg.docs.resolve())


def safe(rel: str) -> Path | None:
    p = (cfg.docs / rel).resolve()
    return p if p.is_file() and inside(p) else None


def tilde(p: Path) -> str:
    return str(p).replace(str(Path.home()), "~", 1)


def sync_on() -> bool:
    return bool(REPO and get_token())


def push(message: str) -> None:
    api = HfApi()
    api.create_repo(REPO, private=True, exist_ok=True)
    api.upload_folder(
        repo_id=REPO,
        folder_path=cfg.docs,
        repo_type="model",
        commit_message=message,
        commit_description=f"from {socket.gethostname()} {datetime.now().astimezone().isoformat(timespec='seconds')}",
    )


def pull() -> None:
    snapshot_download(REPO, repo_type="model", local_dir=cfg.docs)


_sync_lock = threading.Lock()


def sync() -> bool:
    """Push local changes, then pull new files; return False when a sync is already running."""
    if not _sync_lock.acquire(blocking=False):
        return False
    try:
        # push first: after it the repo holds every local edit, so the pull can only add files
        push(f"Sync from {socket.gethostname()}")
        pull()
    finally:
        _sync_lock.release()
    return True


_text_cache: dict[Path, tuple[float, str]] = {}


def raw_text(p: Path) -> str:
    """Text of a document: text files verbatim, PDFs via pdftotext, everything else empty."""
    if p.suffix.lower() in TEXT_EXT:
        return p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".pdf":
        return subprocess.run(
            ["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=60, check=False
        ).stdout
    return ""


def text_of(rel: Path) -> str:
    """Searchable text: raw_text with whitespace collapsed and long blobs dropped, cached by mtime."""
    p = cfg.docs / rel
    mtime = p.stat().st_mtime
    hit = _text_cache.get(p)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = raw_text(p)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("text_of %s: %s", rel, e)
        text = ""
    text = re.sub(rf"\S{{{_BLOB_LEN},}}", " ", re.sub(r"\s+", " ", text))
    _text_cache[p] = (mtime, text)
    return text


class Hit(TypedDict):
    path: str
    snippet: str


def search(query: str) -> list[Hit]:
    terms = query.lower().split()
    rels = files()
    with ThreadPoolExecutor() as pool:
        texts = list(pool.map(text_of, rels))
    out: list[Hit] = []
    for rel, text in zip(rels, texts, strict=True):
        hay = f"{rel} {text}"
        low = hay.lower()
        if not all(t in low for t in terms):
            continue
        i = low.find(terms[0], len(str(rel)) + 1)
        snippet = ""
        if i >= 0:
            s, e = max(0, i - _SNIPPET_BEFORE), min(len(hay), i + _SNIPPET_AFTER)
            snippet = ("…" if s else "") + hay[s:e] + ("…" if e < len(hay) else "")
        out.append({"path": str(rel), "snippet": snippet})
    return out


def mark(text: str, terms: list[str]) -> str:
    if not terms:
        return html.escape(text)
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    parts = re.split(f"({alternation})", text, flags=re.IGNORECASE)
    return "".join(f"<mark>{html.escape(p)}</mark>" if i % 2 else html.escape(p) for i, p in enumerate(parts))


Tree = dict[str, "Tree | Path"]


def _tree() -> Tree:
    """docs/ as nested dicts: folder -> dict, file -> relative Path."""
    root: Tree = {}
    for rel in files():
        node = root
        for part in rel.parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[rel.name] = rel
    return root


def _sorted(node: Tree) -> list[tuple[str, Tree | Path]]:
    return sorted(node.items(), key=lambda kv: (not isinstance(kv[1], dict), kv[0].lower()))


def tree_html(current: str = "", *, bullets: bool = False) -> str:
    """Render docs/ as nested <details>, opening the path to `current`."""
    root = _tree()

    def render(node: Tree, prefix: str) -> str:
        out, files_ = [], []
        for name, v in _sorted(node):
            if isinstance(v, dict):
                folder = f"{prefix}{name}/"
                opened = " open" if current.startswith(folder) else ""
                out.append(f"<details{opened}><summary>{html.escape(name)}</summary>{render(v, folder)}</details>")
            else:
                on = ' class="on"' if str(v) == current else ""
                files_.append(f'<a{on} href="/doc/{quote(str(v))}">{html.escape(name)}</a>')
        if bullets and files_:
            return "".join(out) + "<ul>" + "".join(f"<li>{a}</li>" for a in files_) + "</ul>"
        return "".join(out + files_)

    return (
        '<div class="tree"><button class="tree-toggle" onclick="toggleTree(this)">Expand all</button>'
        f"{render(root, '')}</div>"
    )


def doc_html(rel: Path) -> str:
    p, raw = cfg.docs / rel, f"/raw/{quote(str(rel))}"
    ext, size = p.suffix.lower(), p.stat().st_size
    head = (
        f'<h1 class="title">{html.escape(rel.name)}</h1><p class="meta">{html.escape(str(rel.parent))} · '
        f'{size:,} B · <a href="{raw}" download>download</a> · <a href="{raw}" target="_blank">open raw</a></p>'
    )
    if ext == ".pdf":
        body = f'<iframe class="viewer" src="{raw}"></iframe>'
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        body = f'<img class="viewer-img" src="{raw}" alt="{html.escape(rel.name)}">'
    elif ext == ".csv":
        rows = list(csv.reader(io.StringIO(raw_text(p))))
        body = (
            "<table>"
            + "".join(
                "<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in row) + "</tr>"
                for tag, row in zip(["th"] + ["td"] * len(rows), rows, strict=False)
            )
            + "</table>"
        )
    elif ext == ".md":
        body = f'<div class="md">{md.render(raw_text(p))}</div>'
    elif ext in TEXT_EXT:
        body = f"<pre>{html.escape(raw_text(p))}</pre>"
    else:
        body = "<p>No inline viewer for this type — use download.</p>"
    return head + body


def search_html(q: str) -> str:
    terms = q.lower().split()
    res = search(q) if terms else []
    count = f"{len(res)} result" + ("" if len(res) == 1 else "s")
    body = f'<h1 class="title">Search: {html.escape(q)}</h1><p class="meta">{count}</p>'
    for r in res:
        body += (
            f'<div class="hit"><a href="/doc/{quote(r["path"])}">{mark(r["path"], terms)}</a>'
            f'<div class="snippet">{mark(r["snippet"], terms)}</div></div>'
        )
    return body


def history_html() -> str:
    if not sync_on():
        return f'<h1 class="title">History</h1><p class="meta">Sync is off: {html.escape(SYNC_OFF)}.</p>'
    base = f"https://huggingface.co/{REPO}"
    rows = "".join(
        f"<tr><td>{c.created_at:%Y-%m-%d %H:%M}</td>"
        f'<td><a href="{base}/tree/{c.commit_id}" target="_blank"><code>{c.commit_id[:7]}</code></a></td>'
        f"<td>{html.escape(c.title)}</td>"
        f'<td><a href="{base}/commit/{c.commit_id}" target="_blank">diff</a></td></tr>'
        for c in HfApi().list_repo_commits(REPO)
    )
    return (
        f'<h1 class="title">History</h1><p class="meta">Commits of '
        f'<a href="{base}/commits/main" target="_blank">{REPO}</a>'
        f" — every <code>docustore push</code> is one commit. The id opens the files as of that commit; "
        f"HF cannot render the diff of the large initial sync.</p>"
        f"<table><tr><th>when</th><th>files</th><th>message</th><th></th></tr>{rows}</table>"
    )


PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")


def render(title: str, body: str, current: str = "") -> str:
    sync_button = (
        '<button class="hbtn" onclick="doSync(this)" title="Upload local changes, then download new ones">Sync</button>'
        if sync_on()
        else f'<button class="hbtn" disabled title="{html.escape(SYNC_OFF)}">Sync off</button>'
    )
    parts = {"title": html.escape(title), "tree": tree_html(current), "body": body, "sync": sync_button}
    return re.sub(r"\{\{(title|tree|body|sync)\}\}", lambda m: parts[m[1]], PAGE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        start = time.monotonic()
        url = urlparse(self.path)
        path, params = unquote(url.path), parse_qs(url.query)
        try:
            if path in ("", "/"):
                notes_md = cfg.docs / "notes.md"
                notes = (
                    f'<div class="notes">{md.render(notes_md.read_text(encoding="utf-8"))}</div>'
                    if notes_md.is_file()
                    else ""
                )
                self.page(
                    "docustore",
                    f'<h1 class="title">docustore</h1><p class="meta">{html.escape(tilde(cfg.docs))}, '
                    + (
                        f'mirrored to <a href="https://huggingface.co/{REPO}" target="_blank">{REPO}</a>.</p>'
                        if sync_on()
                        else f"not synced: {html.escape(SYNC_OFF)}.</p>"
                    )
                    + notes
                    + "<h2>Documents</h2>"
                    + tree_html(bullets=True),
                )
            elif path.startswith("/doc/"):
                p = safe(path[5:])
                self.page(path[5:], doc_html(p.relative_to(cfg.docs.resolve())) if p else "<p>Not found.</p>", path[5:])
            elif path.startswith("/raw/"):
                p = safe(path[5:])
                if p:
                    self.send_response(200)
                    self.send_header("Content-Type", guess_type(p.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(p.stat().st_size))
                    self.end_headers()
                    with p.open("rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                else:
                    self.send_error(404)
            elif path == "/search":
                q = params.get("q", [""])[0]
                self.page(f"Search: {q}", search_html(q))
            elif path == "/history":
                self.page("History", history_html())
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("GET %s", path)
            self.send_error(500)
        logger.info("GET %s — %.1fms", self.path, (time.monotonic() - start) * 1000)

    def do_POST(self) -> None:
        if self.path != "/sync":
            return self.send_error(404)
        # a cross-site form cannot set custom headers, so other web pages cannot trigger a sync
        if self.headers.get("X-Docustore") != "1":
            return self.send_error(403)
        if not sync_on():
            return self.send(SYNC_OFF.encode(), "text/plain; charset=utf-8", 400)
        try:
            synced = sync()
        except Exception as e:
            logger.exception("sync failed")
            return self.send(str(e)[:500].encode(), "text/plain; charset=utf-8", 502)
        logger.info("POST /sync — %s", "synced" if synced else "already running")
        return self.send(
            b"synced" if synced else b"a sync is already running", "text/plain; charset=utf-8", 200 if synced else 409
        )

    def send(self, data: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def page(self, title: str, body: str, current: str = "") -> None:
        self.send(render(title, body, current).encode("utf-8"), "text/html; charset=utf-8")


def serve() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)  # personal documents: localhost only
    logger.info("docustore at http://localhost:%d  (docs: %s)", PORT, cfg.docs)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

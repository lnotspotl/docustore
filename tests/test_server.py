import io
import logging
import os
import re
from pathlib import Path

import pytest

from docustore import server


def test_files_lists_sorted_relative_paths_and_skips_dot_entries(store):
    store("b.txt", "")
    store("a/c.txt", "")
    store(".hidden/x.txt", "")
    store("a/.dot.txt", "")
    assert server.files() == [Path("a/c.txt"), Path("b.txt")]


def test_files_lists_docs_that_live_under_a_dot_directory(tmp_path, monkeypatch):
    docs = tmp_path / ".tb" / "docs"
    (docs / "a").mkdir(parents=True)
    (docs / "a" / "b.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(server.cfg, "docs", docs)
    assert server.files() == [Path("a/b.txt")]


def test_safe_resolves_existing_files_inside_docs_only(store, tmp_path):
    store("a.txt", "")
    (tmp_path.parent / "pyproject.toml").write_text("")
    assert server.safe("a.txt") == (tmp_path / "a.txt").resolve()
    assert server.safe("missing.txt") is None
    assert server.safe("../pyproject.toml") is None


def test_text_of_collapses_whitespace_drops_blobs_and_ignores_unknown_types(store):
    store("a.txt", "hello\n\n  world " + "x" * 80 + " end")
    store("a.bin", "hello")
    assert server.text_of(Path("a.txt")) == "hello world   end"
    assert server.text_of(Path("a.bin")) == ""


def test_text_of_rereads_only_when_the_file_changes(store, monkeypatch):
    p = store("a.txt", "first")
    reads = []
    read_text = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self)
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    assert server.text_of(Path("a.txt")) == "first"
    assert server.text_of(Path("a.txt")) == "first"
    assert reads == [p]
    p.write_text("second")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
    assert server.text_of(Path("a.txt")) == "second"
    assert reads == [p, p]


def test_search_requires_every_term_across_path_and_text(store):
    store("notes/alpha.txt", "beta gamma")
    store("other.txt", "beta")
    assert [h["path"] for h in server.search("alpha beta")] == ["notes/alpha.txt"]
    assert [h["path"] for h in server.search("beta")] == ["notes/alpha.txt", "other.txt"]
    assert server.search("alpha delta") == []


def test_search_snippet_carries_ellipses_only_where_it_was_cut(store):
    store("a.txt", "needle at the start")
    store("b.txt", "x " * 200 + "needle " + "y " * 200)
    snippets = {h["path"]: h["snippet"] for h in server.search("needle")}
    assert snippets["a.txt"] == "a.txt needle at the start"
    assert snippets["b.txt"] == "…" + "x " * 50 + "needle " + "y " * 76 + "y…"


def test_search_term_matching_only_the_path_gives_an_empty_snippet(store):
    store("invoice.txt", "some words")
    assert server.search("invoice") == [{"path": "invoice.txt", "snippet": ""}]


def test_search_html_with_an_empty_query_shows_zero_results_without_searching(monkeypatch):
    monkeypatch.setattr(server, "search", lambda _q: pytest.fail("search called"))
    assert "0 results" in server.search_html("")


def test_mark_escapes_html_and_wraps_terms_case_insensitively():
    assert server.mark("<b>Alpha</b> alpha", ["alpha"]) == "&lt;b&gt;<mark>Alpha</mark>&lt;/b&gt; <mark>alpha</mark>"
    assert server.mark("a&b <mark>", ["amp", "mark"]) == "a&amp;b &lt;<mark>mark</mark>&gt;"


def test_tree_html_puts_folders_first_and_opens_only_the_path_to_current(store):
    for rel in ("a.txt", "m/inner.txt", "m/zdeep/x.txt", "n/other.txt"):
        store(rel, "")
    html = server.tree_html("m/zdeep/x.txt")
    assert html.index("<summary>m</summary>") < html.index("<summary>n</summary>") < html.index("a.txt")
    assert html.index("<summary>zdeep</summary>") < html.index("inner.txt")
    assert "<details open><summary>m</summary>" in html
    assert "<details open><summary>zdeep</summary>" in html
    assert "<details><summary>n</summary>" in html
    assert html.count('class="on"') == 1
    assert '<a class="on" href="/doc/m/zdeep/x.txt">x.txt</a>' in html


def test_tree_html_bullets_wrap_a_folders_files_in_a_list(store):
    store("a/one.txt", "")
    store("a/two.txt", "")
    files = '<li><a href="/doc/a/one.txt">one.txt</a></li><li><a href="/doc/a/two.txt">two.txt</a></li>'
    assert f"<ul>{files}</ul>" in server.tree_html(bullets=True)
    assert "<ul>" not in server.tree_html()


def test_doc_html_renders_each_type(store):
    store("a.md", "# Title")
    store("b.txt", "<x>")
    store("c.pdf", "")
    store("d.bin", "")
    store("e.csv", "a,b\n1,<2>\n")
    assert "<h1>Title</h1>" in server.doc_html(Path("a.md"))
    assert "<pre>&lt;x&gt;</pre>" in server.doc_html(Path("b.txt"))
    assert '<iframe class="viewer" src="/raw/c.pdf"></iframe>' in server.doc_html(Path("c.pdf"))
    assert "No inline viewer" in server.doc_html(Path("d.bin"))
    assert "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>&lt;2&gt;</td></tr></table>" in server.doc_html(
        Path("e.csv")
    )


def test_render_escapes_the_title_and_inserts_body_and_tree(store):
    store("a.txt", "")
    page = server.render("<t>", "<b>body</b>")
    assert "<title>&lt;t&gt; - docustore</title>" in page
    assert "<b>body</b>" in page
    assert 'href="/doc/a.txt"' in page


def _get(path: str) -> tuple[int, str, bytes]:
    h = server.Handler.__new__(server.Handler)
    h.request_version, h.requestline, h.command, h.client_address = "HTTP/1.0", "", "GET", ("", 0)
    h.wfile, h.path = io.BytesIO(), path
    h.do_GET()
    head, body = h.wfile.getvalue().split(b"\r\n\r\n", 1)
    status_line, *header_lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in header_lines)
    return int(status_line.split(" ")[1]), headers.get("Content-Type", ""), body


def test_get_index_has_the_tree_and_the_notes(store):
    store("a.txt", "")
    store("notes.md", "# Hello notes")
    status, ctype, body = _get("/")
    assert (status, ctype) == (200, "text/html; charset=utf-8")
    assert '<div class="notes"><h1>Hello notes</h1>' in body.decode()
    assert '<li><a href="/doc/a.txt">a.txt</a></li>' in body.decode()


def test_get_logs_each_request_with_its_duration(store, caplog):
    store("a.txt", "")
    with caplog.at_level(logging.INFO, logger="docustore"):
        _get("/raw/missing")
        _get("/")
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("GET ")]
    assert re.fullmatch(r"GET /raw/missing — \d+\.\dms", messages[0])
    assert re.fullmatch(r"GET / — \d+\.\dms", messages[1])


def test_get_doc_and_search_pages(store):
    store("a.txt", "alpha")
    assert _get("/doc/a.txt")[0] == 200
    status, _, body = _get("/search?q=alpha")
    assert status == 200
    assert "1 result<" in body.decode()


def test_get_raw_serves_the_exact_bytes_with_the_guessed_type(store):
    store("a.txt", "raw bytes")
    assert _get("/raw/a.txt") == (200, "text/plain", b"raw bytes")


def test_get_rejects_missing_escaping_and_unknown_paths(store):
    store("a.txt", "")
    assert _get("/raw/missing")[0] == 404
    assert _get("/raw/../pyproject.toml")[0] == 404
    assert _get("/nope")[0] == 404

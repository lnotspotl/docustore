import pytest

from docustore import server


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the server at an empty temp docs dir; returns write(relative_path, text) -> Path."""
    monkeypatch.setattr(server.cfg, "docs", tmp_path)
    server._text_cache.clear()

    def write(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    return write

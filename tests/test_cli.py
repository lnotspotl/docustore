import json

import pytest

from docustore import __main__ as cli
from docustore import server


def test_sync_commands_exit_with_the_setup_hint_when_no_repo_is_set(monkeypatch):
    monkeypatch.setattr(server, "REPO", "")
    with pytest.raises(SystemExit, match="set DOCUSTORE_REPO"):
        cli.log()


def test_status_json_reports_server_docs_and_sync(store, monkeypatch, capsys):
    store("a.txt", "")
    monkeypatch.setattr(server, "REPO", "")
    monkeypatch.setattr(cli, "up", lambda: False)
    monkeypatch.setattr(cli, "unit_active", lambda: False)
    cli.status(as_json=True)
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["server"] == {"name": "server", "on": False, "detail": "not running"}
    assert rows["docs"]["detail"].endswith("(1 files)")
    assert rows["sync"]["on"] is False


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_completion_lists_every_command(shell):
    script = cli.completion_script(shell)
    assert all(name in script for name in cli.COMMANDS)
    assert "@" not in script


def test_list_json_gives_relative_and_absolute_paths(store, capsys):
    p = store("a/b.txt", "hello")
    cli.list_docs(as_json=True)
    [row] = json.loads(capsys.readouterr().out)
    assert (row["path"], row["file"], row["size"]) == ("a/b.txt", str(p), 5)


def test_search_json_returns_the_matching_documents(store, capsys):
    store("lease.txt", "the lease ends 2027-03-31")
    store("other.txt", "nothing")
    cli.search("lease 2027", as_json=True)
    assert [h["path"] for h in json.loads(capsys.readouterr().out)] == ["lease.txt"]


def test_show_prints_the_text_and_rejects_unknown_or_escaping_paths(store, capsys):
    store("a.txt", "line one\nline two")
    cli.show("a.txt")
    assert capsys.readouterr().out == "line one\nline two\n"
    for rel in ("missing.txt", "../x.txt"):
        with pytest.raises(SystemExit, match="no document"):
            cli.show(rel)


def test_add_copies_into_a_folder_and_never_overwrites(store, tmp_path_factory, capsys):
    store("keep.txt", "")
    src = tmp_path_factory.mktemp("in") / "invoice.txt"
    src.write_text("new", encoding="utf-8")
    cli.add(src, "invoices/2026")
    assert capsys.readouterr().out == "invoices/2026/invoice.txt\n"
    assert server.safe("invoices/2026/invoice.txt") is not None
    with pytest.raises(SystemExit, match="already exists"):
        cli.add(src, "invoices/2026")
    with pytest.raises(SystemExit, match="outside"):
        cli.add(src, "../..")

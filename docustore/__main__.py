"""docustore CLI: serve the document store and sync it with a private Hugging Face repo."""

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from huggingface_hub import HfApi

from docustore import docx2pdf, server

URL = f"http://localhost:{server.PORT}"
UNIT = f"docustore-{server.PORT}"


def up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", server.PORT), timeout=0.2).close()
    except OSError:
        return False
    return True


def unit_active() -> bool:
    return subprocess.run(["systemctl", "--user", "--quiet", "is-active", UNIT], check=False).returncode == 0


def hf() -> HfApi:
    if not server.sync_on():
        sys.exit(f"docustore: {server.SYNC_OFF}")
    return HfApi()


def pull() -> None:
    hf()
    server.pull()


def ensure_docs() -> None:
    if server.cfg.docs.is_dir():
        return
    if server.sync_on():
        pull()
    else:
        server.cfg.docs.mkdir(parents=True)


def start(*, browser: bool) -> None:
    ensure_docs()
    if up():
        print(f"docustore: already running on {URL}")
    else:
        # systemd starts services with an empty environment; search needs PATH (pdftotext), history HF_TOKEN
        passed = ("DOCUSTORE_", "HF_", "SSL_CERT_", "REQUESTS_CA_BUNDLE", "PATH", "HOME")
        env = [f"--setenv={k}={v}" for k, v in os.environ.items() if k.startswith(passed)]
        subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--unit={UNIT}",
                f"--description=docustore on :{server.PORT}",
                "--collect",
                "--quiet",
                *env,
                sys.executable,
                "-m",
                "docustore",
                "serve",
                "--no-browser",
            ],
            check=True,
        )
        deadline = time.monotonic() + 30
        while not up():
            if time.monotonic() > deadline:
                sys.exit(f"docustore: {UNIT} did not come up in 30s; see journalctl --user -u {UNIT}")
            time.sleep(0.1)
        print(f"docustore: started {UNIT} on {URL}")
    if browser:
        webbrowser.open(URL)


def serve(*, browser: bool) -> None:
    ensure_docs()
    if browser:
        webbrowser.open(URL)
    server.serve()


def stop() -> None:
    if unit_active():
        subprocess.run(["systemctl", "--user", "stop", UNIT], check=True)
        print(f"docustore: stopped {UNIT}")
    elif up():
        sys.exit(
            f"docustore: the server on port {server.PORT} was not started with `docustore start`; stop it where it runs"
        )
    else:
        print("docustore: not running")


def push(message: str) -> None:
    hf()
    server.push(message)


def sync() -> None:
    hf()
    print("synced" if server.sync() else "docustore: a sync is already running in this process")


def log() -> None:
    for c in hf().list_repo_commits(server.REPO):
        print(f"{c.created_at:%Y-%m-%d %H:%M} {c.commit_id[:7]} {c.title}")


def info() -> None:
    i = hf().model_info(server.REPO)
    print(
        f"private = {i.private} | files = {len(i.siblings or [])} | https://huggingface.co/{server.REPO}/commits/main"
    )


def status_rows() -> list[dict[str, object]]:
    on, docs, synced = up(), server.cfg.docs, server.sync_on()
    origin = f"({UNIT})" if unit_active() else "(not started by docustore start)"
    count = len(server.files()) if docs.is_dir() else 0
    return [
        {"name": "server", "on": on, "detail": f"{URL} {origin}" if on else "not running"},
        {
            "name": "docs",
            "on": docs.is_dir(),
            "detail": f"{server.tilde(docs)} ({count} files)",
        },
        {
            "name": "sync",
            "on": synced,
            "detail": f"https://huggingface.co/{server.REPO}" if synced else server.SYNC_OFF,
        },
    ]


def status(*, as_json: bool) -> None:
    rows = status_rows()
    if as_json:
        print(json.dumps(rows, separators=(",", ":")))
        return
    for r in rows:
        print(f"{r['name']:<6}  {'on' if r['on'] else 'off':<3}  {r['detail']}")


COMPLETIONS = {
    "zsh": """_docustore() {
  if (( CURRENT == 2 )); then
    local -a entries=(@ENTRIES@)
    _describe command entries
  else
    _files
  fi
}
compdef _docustore docustore
""",
    "bash": """_docustore() {
  (( COMP_CWORD == 1 )) && COMPREPLY=($(compgen -W "@NAMES@" -- "${COMP_WORDS[COMP_CWORD]}"))
}
complete -o default -F _docustore docustore
""",
}


def list_docs(*, as_json: bool) -> None:
    docs = server.cfg.docs
    stats = [(rel, (docs / rel).stat()) for rel in server.files()]
    if as_json:
        rows = [{"path": str(r), "file": str(docs / r), "size": st.st_size, "mtime": st.st_mtime} for r, st in stats]
        print(json.dumps(rows, separators=(",", ":")))
        return
    for rel, st in stats:
        print(f"{rel}\t{st.st_size}\t{datetime.fromtimestamp(st.st_mtime).astimezone():%Y-%m-%d %H:%M}")


def search(query: str, *, as_json: bool) -> None:
    hits = server.search(query)
    if as_json:
        print(json.dumps(hits, separators=(",", ":"), ensure_ascii=False))
        return
    for h in hits:
        print(f"{h['path']}\t{h['snippet']}")


def show(rel: str) -> None:
    p = server.safe(rel)
    if not p:
        sys.exit(f"docustore: no document {rel!r}; see docustore list")
    text = server.raw_text(p)
    if not text.strip():
        sys.exit(f"docustore: no text in {rel}; open the file itself: {p}")
    print(text, end="" if text.endswith("\n") else "\n")


def add(src: Path, folder: str) -> None:
    dest = server.cfg.docs / folder / src.name
    if not server.inside(dest):
        sys.exit(f"docustore: {folder!r} is outside the documents directory")
    if dest.exists():
        sys.exit(f"docustore: {dest.relative_to(server.cfg.docs)} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(dest.relative_to(server.cfg.docs))


HELP_EXAMPLES = """examples:
  $ docustore start --no-browser
  $ docustore search lease 2027 --json
  $ docustore show contracts/lease.pdf
  $ docustore add ~/Downloads/invoice.pdf invoices/2026
  $ docustore push "add March invoice"

Every command runs without the server; list, search and status take --json."""
COMMANDS = {
    "start": "serve in the background, or reuse a running one, and open it (the default)",
    "serve": "serve in the foreground and open it",
    "stop": "stop the background server",
    "status": "is the server up, where the documents are, and is sync on",
    "list": "list documents: path, size, date (--json adds the absolute file path)",
    "search": "find documents containing every term, in the path or the text (PDFs included)",
    "show": "print a document's text (text files verbatim, PDFs extracted)",
    "add": "copy a file into the documents, optionally under FOLDER; prints its path",
    "sync": "push local changes as one commit, then pull new files (the page's Sync button)",
    "pull": "download the Hugging Face repo into the documents directory",
    "push": "upload the documents to the Hugging Face repo as one commit",
    "log": "commit history of the documents (provenance)",
    "info": "visibility, file count, link to history",
    "docx2pdf": "convert a folder of .docx (Google Docs exports) to .pdf, mirroring the tree",
    "completion": 'print shell completion; add eval "$(docustore completion zsh)" to ~/.zshrc',
    "help": "print this help",
}


def completion_script(shell: str) -> str:
    entries = " ".join(shlex.quote(f"{name}:{text}") for name, text in COMMANDS.items())
    return COMPLETIONS[shell].replace("@ENTRIES@", entries).replace("@NAMES@", " ".join(COMMANDS))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="docustore",
        description="Personal document store: browse and search your documents from a localhost page or this CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EXAMPLES
        + "\n\nConfig: DOCUSTORE_DOCS (documents directory), DOCUSTORE_PORT, DOCUSTORE_REPO (Hugging Face repo id "
        "to sync; also needs HF_TOKEN or hf auth login).",
    )
    parser.set_defaults(run=lambda _: start(browser=True))
    sub = parser.add_subparsers(metavar="<command>")
    cmd = {name: sub.add_parser(name, help=text) for name, text in COMMANDS.items()}
    for name, fn in (("start", start), ("serve", serve)):
        cmd[name].add_argument("--no-browser", action="store_true", help="don't open it")
        cmd[name].set_defaults(run=lambda a, fn=fn: fn(browser=not a.no_browser))
    cmd["stop"].set_defaults(run=lambda _: stop())
    cmd["status"].set_defaults(run=lambda a: status(as_json=a.json))
    for name in ("status", "list", "search"):
        cmd[name].add_argument("--json", action="store_true", help="print JSON")
    cmd["list"].set_defaults(run=lambda a: list_docs(as_json=a.json))
    cmd["search"].add_argument("query", nargs="+")
    cmd["search"].set_defaults(run=lambda a: search(" ".join(a.query), as_json=a.json))
    cmd["show"].add_argument("path", help="document path as docustore list prints it")
    cmd["show"].set_defaults(run=lambda a: show(a.path))
    cmd["add"].add_argument("src", type=Path)
    cmd["add"].add_argument("folder", nargs="?", default="")
    cmd["add"].set_defaults(run=lambda a: add(a.src, a.folder))
    cmd["sync"].set_defaults(run=lambda _: sync())
    cmd["pull"].set_defaults(run=lambda _: pull())
    cmd["push"].add_argument("message")
    cmd["push"].set_defaults(run=lambda a: push(a.message))
    cmd["log"].set_defaults(run=lambda _: log())
    cmd["info"].set_defaults(run=lambda _: info())
    cmd["docx2pdf"].add_argument("src", type=Path)
    cmd["docx2pdf"].add_argument("dst", type=Path)
    cmd["docx2pdf"].set_defaults(run=lambda a: docx2pdf.main(a.src, a.dst))
    cmd["completion"].add_argument("shell", choices=COMPLETIONS)
    cmd["completion"].set_defaults(run=lambda a: print(completion_script(a.shell), end=""))
    cmd["help"].set_defaults(run=lambda _: parser.print_help())
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()

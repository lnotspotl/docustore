# docustore

Personal document store. The docs directory (`DOCUSTORE_DOCS`, default `~/.local/share/docustore`, mirrored to the HF repo `DOCUSTORE_REPO`) holds the files; git tracks only the `docustore` package: `server.py` (browse, search), `page.html` (the UI shell), `docx2pdf.py`, and `__main__.py` (the `docustore` CLI). `install.sh` installs the latest release wheel with `uv tool install`; a `v<version>` tag releases one; dev tasks run through `pixi run lint|fmt|test`.

## Code style

`pixi run lint` gates CI: `ruff format --check`, `ruff check` with `select = ["ALL"]` at 120 columns, and `mypy --strict`. `pixi run fmt` applies the autofixes. `.claude/hooks/lint-py.sh` runs the same three on every `.py` file an agent edits and feeds the remaining violations back, so fix the code instead of reaching for `noqa` or `type: ignore`. A suppression that survives review names the rule code and a reason the code cannot change. `__main__.py` and `docx2pdf.py` are CLI commands: `print` is their output.

Comments: none by default. Names, types, and asserts carry the meaning. A comment that narrates what the next lines do (`# Build TOC`, `# Phase 1: load pages`) is deleted in review. Keep a comment only for a non-obvious *why* the code cannot express (a protocol quirk, a workaround pinned to an upstream bug and its link). Commented-out code is a lint error (ERA001). Docstrings are optional; when present, one imperative line in Google style.

Tests live in `tests/` and run with `pixi run test`. The docs directory holds personal documents: tests and agents never read it; fixtures build their own tree under `tmp_path`.

This repo is public: no personal names, ids, paths or repo ids in code, tests, fixtures or commit messages. The CLI is agent-facing too: every command works without the server, takes `--json` where it lists, and exits non-zero with the reason.

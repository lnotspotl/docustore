# docustore

[![CI](https://github.com/lnotspotl/docustore/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/lnotspotl/docustore/actions/workflows/ci.yml)
[![tests](https://img.shields.io/github/check-runs/lnotspotl/docustore/main?nameFilter=test&label=tests)](https://github.com/lnotspotl/docustore/actions/workflows/ci.yml)

A personal document store: a folder of documents you browse and search from a localhost page, or hand to Claude Code
or Codex through an agent-friendly CLI, mirrored to a private Hugging Face repo whose commits are the provenance.

![docustore: CLI, browse, search, and Claude Code answering from the documents](https://github.com/user-attachments/assets/bfb09b1d-1988-4904-a946-1f698c29e5f0)

### Install
    curl -LsSf https://raw.githubusercontent.com/lnotspotl/docustore/main/install.sh | sh
### Use
    docustore start       # serve in the background (or reuse), open http://localhost:8020
    docustore stop        # stop it; `docustore serve` runs it in the foreground
    docustore status      # is the server up, where the documents are, is sync on
    docustore help        # every command

For completion, add `eval "$(docustore completion zsh)"` to `~/.zshrc` (or `bash` to `~/.bashrc`).

Documents live in `~/.local/share/docustore/` (`DOCUSTORE_DOCS`). A `notes.md` at the top is shown on the front page.
The page binds to localhost only.

### Sync
Set `DOCUSTORE_REPO` to a Hugging Face repo id (`you/docs`) and give `HF_TOKEN` or `hf auth login`; without both, sync
is off. The page's Sync button, or `docustore sync`, uploads local changes as one commit and then downloads new files.
`docustore push "message"` uploads with your own message (creating the repo private), `docustore pull` downloads, and
`docustore log` and the History tab list the commits. `push` never deletes; remove files on HF by
hand. `start` and `serve` pull first when the documents directory is missing.

### Agents
To ask questions about your documents, run Claude Code or Codex and tell it to use `docustore`. Every command runs
without the server, prints plain lines or `--json`, and exits non-zero with the reason on stderr:

    docustore list --json                          # [{"path", "file", "size", "mtime"}]
    docustore search lease 2027 --json             # documents with every term: [{"path", "snippet"}]
    docustore show contracts/lease.pdf             # the text, PDFs extracted
    docustore add ~/Downloads/invoice.pdf invoices # file a document; prints its path, never overwrites

`docustore docx2pdf SRC DST` converts a tree of Google Docs `.docx` exports to PDF with LibreOffice, fixing the
`Arial MT` font and floating invoice tables on temp copies. PDF text needs `pdftotext` (poppler-utils) on `PATH`.

Development: `pixi run test`, `pixi run lint`, `pixi run fmt`; `bin/docustore` runs the CLI from a clone.
`pixi run wheel` builds the wheel; pushing a `v<version>` tag matching `pyproject.toml` releases it.

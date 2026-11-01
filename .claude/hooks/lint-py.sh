#!/bin/sh
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in *.py) ;; *) exit 0 ;; esac
cd "$CLAUDE_PROJECT_DIR" || exit 0
pixi run -q ruff format -q "$f"
pixi run -q ruff check -q --fix "$f" >&2 && pixi run -q mypy "$f" >&2 && exit 0
exit 2

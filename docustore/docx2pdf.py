"""docx -> pdf with headless LibreOffice, mirroring SRC_DIR into DST_DIR.

Two fixes for Google Docs exports, applied to a temp copy (the .docx files are left untouched):
- "Arial MT" (Google's name for Arial) is aliased to the metric-compatible Liberation Sans; otherwise
  LibreOffice falls back to Noto Sans and every line reflows.
- The invoice items table is a floating table meant to sit inside the empty body row of the full-page
  invoice frame table; LibreOffice draws such floats at their anchor and pushes the frame below them.
  It is nested into that body cell (the frame's tallest row) as an inline table instead.
"""

import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <alias binding="same"><family>Arial MT</family><accept><family>Liberation Sans</family></accept></alias>
</fontconfig>
"""
_EXPECTED_TABLES = 2
_TOP_MARGIN_PT = 26
_TWIPS_PER_PT = 20
_SLACK_TWIPS = 200
_DEFAULT_OVERFLOW_TWIPS = 400
_MAX_SHRINK_PASSES = 3


def top_level_tables(xml: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth, start = 0, 0
    for m in re.finditer(r"<w:tbl>|</w:tbl>", xml):
        if m.group() == "<w:tbl>":
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                spans.append((start, m.end()))
    return spans


def patch_document(xml: str, shrink: int = 0) -> str:
    spans = top_level_tables(xml)
    if len(spans) != _EXPECTED_TABLES:
        return xml
    (s0, e0), (s1, e1) = spans
    float_tbl, frame = xml[s0:e0], xml[s1:e1]
    if "<w:tblpPr" not in float_tbl or "<w:tblpPr" in frame or frame.count("<w:tbl>") != 1:
        return xml
    inline = re.sub(r"<w:tblpPr[^>]*/>", "", float_tbl)
    rows = list(re.finditer(r"<w:tr(?:\s[^>]*)?>.*?</w:tr>", frame, re.DOTALL))

    def height(m: re.Match[str]) -> int:
        h = re.search(r'<w:trHeight [^>]*w:val="(\d+)"', m.group())
        return int(h.group(1)) if h else 0

    body = max(rows, key=height)
    row = body.group()
    if shrink:  # the body row is filler: give back what LibreOffice's taller rows need to stay on one page
        row = re.sub(
            r'(<w:trHeight [^>]*w:val=")(\d+)', lambda m: m.group(1) + str(max(height(body) - shrink, 0)), row, count=1
        )
    last_p = list(re.finditer(r"<w:p(?:\s[^>]*)?>", row))[-1].start()  # a cell must end with a paragraph
    row = row[:last_p] + inline + row[last_p:]
    frame = frame[: body.start()] + row + frame[body.end() :]
    return xml[:s0] + xml[e0:s1] + frame + xml[e1:]


def patched_copy(src: Path, dst: Path, shrink: int = 0) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = patch_document(data.decode("utf-8"), shrink).encode("utf-8")
            zout.writestr(item, data)


def pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=False).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    if m is None:
        raise RuntimeError(f"pdfinfo reported no page count for {pdf}")
    return int(m.group(1))


def overflow_twips(pdf: Path) -> int:
    """Height of the content that spilled onto page 2, in twips (+ a little slack)."""
    xml = subprocess.run(
        ["pdftotext", "-f", "2", "-l", "2", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=False
    ).stdout
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', xml)]
    return int((max(ys) - _TOP_MARGIN_PT) * _TWIPS_PER_PT) + _SLACK_TWIPS if ys else _DEFAULT_OVERFLOW_TWIPS


def convert(src: Path, out_dir: Path, tmp: Path, env: dict[str, str], shrink: int = 0) -> Path:
    copy = tmp / "in" / src.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    patched_copy(src, copy, shrink)
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation=file://{tmp}/profile",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(copy),
        ],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=300,
    )
    return out_dir / src.with_suffix(".pdf").name


def main(src: Path, dst: Path) -> None:
    files = sorted(src.rglob("*.docx"))
    if not files:
        sys.exit(f"no .docx under {src}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        (tmp / "fonts.conf").write_text(FONTS_CONF)
        env = {**os.environ, "FONTCONFIG_FILE": str(tmp / "fonts.conf")}
        for f in files:
            out_dir = dst / f.relative_to(src).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf, shrink = convert(f, out_dir, tmp, env), 0
            n = pages(pdf)
            for _ in range(_MAX_SHRINK_PASSES):
                if n == 1:
                    break
                shrink += overflow_twips(pdf)
                pdf = convert(f, out_dir, tmp, env, shrink)
                n = pages(pdf)
            print(f"{f.relative_to(src)} -> {n} page(s)" + (f", body row -{shrink} twips" if shrink else ""))

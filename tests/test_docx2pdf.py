from docustore.docx2pdf import patch_document, top_level_tables

P = "<w:p></w:p>"
FLOAT = f'<w:tbl><w:tblPr><w:tblpPr w:tblpX="1"/></w:tblPr><w:tr><w:tc>{P}</w:tc></w:tr></w:tbl>'
INLINE = FLOAT.replace('<w:tblpPr w:tblpX="1"/>', "")


def row(height: int, cell: str) -> str:
    return f'<w:tr><w:trPr><w:trHeight w:val="{height}"/></w:trPr><w:tc>{cell}</w:tc></w:tr>'


HEADER = row(300, P)
FRAME = f"<w:tbl><w:tblPr/>{HEADER}{row(9000, P + P)}</w:tbl>"
PATCHED_FRAME = f"<w:tbl><w:tblPr/>{HEADER}{row(9000, P + INLINE + P)}</w:tbl>"


def test_top_level_tables_spans_siblings_and_counts_a_nested_table_once():
    xml = "<w:body>" + INLINE + P + FRAME + "</w:body>"
    i, j = xml.index(INLINE), xml.index(FRAME)
    assert top_level_tables(xml) == [(i, i + len(INLINE)), (j, j + len(FRAME))]
    nested = "<w:tbl><w:tr><w:tc>" + INLINE + "</w:tc></w:tr></w:tbl>"
    assert top_level_tables(P + nested) == [(len(P), len(P) + len(nested))]


def test_patch_document_leaves_unrecognised_layouts_alone():
    already_nested = FRAME.replace(P + P, P + INLINE + P)
    for xml in (FRAME, FLOAT + FRAME + FRAME, INLINE + FRAME, FLOAT + already_nested):
        assert patch_document(xml) == xml


def test_patch_document_nests_the_float_inline_before_the_last_paragraph_of_the_tallest_row():
    out = patch_document("<w:body>" + FLOAT + P + FRAME + "</w:body>")
    assert out == "<w:body>" + P + PATCHED_FRAME + "</w:body>"
    assert "<w:tblpPr" not in out


def test_patch_document_shrink_lowers_the_body_row_height_floored_at_zero():
    doc = FLOAT + FRAME
    assert patch_document(doc, shrink=500) == PATCHED_FRAME.replace('w:val="9000"', 'w:val="8500"')
    assert patch_document(doc, shrink=99999) == PATCHED_FRAME.replace('w:val="9000"', 'w:val="0"')

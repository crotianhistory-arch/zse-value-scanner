from zse_tool.eho import extract_document_urls


def test_extract_document_urls_recursive_and_dedup():
    raw={"document":"https://eho.zse.hr//fileadmin/a.xlsx","nested":{"files":["https://x/y.pdf","https://x/page"]},"again":"https://eho.zse.hr//fileadmin/a.xlsx"}
    assert extract_document_urls(raw)==["https://eho.zse.hr/fileadmin/a.xlsx","https://x/y.pdf"]


def test_extract_relative_eho_document_url():
    assert extract_document_urls({"document":"/fileadmin/issuers/ABC/report.xlsx"}) == [
        "https://eho.zse.hr/fileadmin/issuers/ABC/report.xlsx"
    ]

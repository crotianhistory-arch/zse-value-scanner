from pathlib import Path
from zse_tool.models import EhoItem
from zse_tool.storage import Database


def test_storage_upsert_and_pending(tmp_path: Path):
    db=Database(tmp_path/"x.sqlite")
    item=EhoItem("1","financialReports","ABC","ABC d.d.","report","2025-01-01","https://eho/view/1",{},["https://eho/a.xlsx"])
    assert db.upsert_items([item])==1
    rows=db.pending_documents(ticker="ABC",limit=10)
    assert len(rows)==1 and rows[0]["document_type"]=="xlsx"
    assert db.stats()["feed_items"]==1

import requests
import pytest
from pathlib import Path
from zse_tool.config import Settings
from zse_tool.errors import AccessBlocked
from zse_tool.http_client import RespectfulHttpClient


def test_http_429_detected(tmp_path: Path):
    c=RespectfulHttpClient(Settings(tmp_path,tmp_path/"x.sqlite",min_request_interval_seconds=0))
    r=requests.Response(); r.status_code=429; r.url="https://example.test"; r._content=b"too many"; r.headers["content-type"]="text/plain"
    with pytest.raises(AccessBlocked): c._check_response(r)

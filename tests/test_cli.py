from zse_tool.cli import main


def test_inspect_cli(sample_xlsx, capsys):
    rc=main(["inspect-xlsx",str(sample_xlsx),"--json"])
    out=capsys.readouterr().out
    assert rc==0
    assert '"issuer": "TESTCO d.d."' in out
    assert '"net_debt_ex_other": 20.0' in out

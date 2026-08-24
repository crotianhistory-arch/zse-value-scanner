from zse_tool.cli import build_parser


def test_cli_exposes_company_intelligence_commands():
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices
    for name in (
        "taxonomy", "profile-seed", "profile-validate", "profile-import",
        "company-profile", "activities", "segments", "profile-history", "profile-quality", "peer-candidates",
    ):
        assert name in choices

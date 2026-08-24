param([string]$Ticker = "KOEI")
$ErrorActionPreference = "Stop"
python -m zse_tool probe --ticker $Ticker
python -m zse_tool list --ticker $Ticker --limit 3
python -m zse_tool db-stats

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re


SHEET_CHOICES = (
    "general_data",
    "balance_sheet",
    "income_statement",
    "cash_flow_direct",
    "cash_flow_indirect",
    "changes_in_equity",
    "notes",
    "unknown",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(slots=True)
class SheetMapping:
    mapping: str
    confidence: float
    reason: str = ""


class OllamaSchemaMapper:
    def __init__(self, manager, status, data_dir: Path, min_confidence: float = 0.95):
        self.manager = manager
        self.status = status
        self.data_dir = Path(data_dir)
        self.min_confidence = min_confidence
        self.alias_path = self.data_dir / "learned_sheet_aliases.json"
        self.review_path = self.data_dir / "review_queue.jsonl"
        self._aliases = self._load_aliases()

    def _load_aliases(self) -> dict[str, str]:
        if not self.alias_path.exists():
            return {}
        try:
            raw = json.loads(self.alias_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in raw.items() if v in SHEET_CHOICES}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_aliases(self) -> None:
        self.alias_path.parent.mkdir(parents=True, exist_ok=True)
        self.alias_path.write_text(
            json.dumps(dict(sorted(self._aliases.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _queue(self, *, text: str, reason: str, guess: SheetMapping | None = None) -> None:
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": "sheet_name",
            "text": text,
            "reason": reason,
            "guess": None if guess is None else {
                "mapping": guess.mapping,
                "confidence": guess.confidence,
                "reason": guess.reason,
            },
        }
        with self.review_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def map_sheet_name(self, sheet_name: str) -> str | None:
        key = _norm(sheet_name)
        cached = self._aliases.get(key)
        if cached:
            return cached
        if not self.status.selected_model:
            self._queue(text=sheet_name, reason="LLM unavailable")
            return None

        schema = {
            "type": "object",
            "properties": {
                "mapping": {"type": "string", "enum": list(SHEET_CHOICES)},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["mapping", "confidence", "reason"],
            "additionalProperties": False,
        }
        prompt = (
            "Classify this spreadsheet sheet title from a Croatian/English financial report.\n"
            f"Sheet title: {sheet_name!r}\n"
            "Choose only from the schema enum. Do not infer financial values."
        )
        system = (
            "You are a conservative accounting-schema classifier. Croatian and English labels are common. "
            "Use unknown when uncertain. Confidence must reflect uncertainty."
        )
        try:
            raw = self.manager.client.generate_structured(
                model=self.status.selected_model,
                prompt=prompt,
                system=system,
                schema=schema,
                context_length=self.manager.settings.context_length,
                keep_alive=self.manager.settings.keep_alive,
            )
            guess = SheetMapping(
                mapping=str(raw.get("mapping", "unknown")),
                confidence=float(raw.get("confidence", 0.0)),
                reason=str(raw.get("reason", "")),
            )
        except Exception as exc:  # LLM failures must never break deterministic parsing.
            self._queue(text=sheet_name, reason=f"LLM request failed: {exc}")
            return None

        if guess.mapping == "unknown" or guess.confidence < self.min_confidence:
            self._queue(text=sheet_name, reason="Low-confidence LLM mapping", guess=guess)
            return None

        self._aliases[key] = guess.mapping
        self._save_aliases()
        return guess.mapping

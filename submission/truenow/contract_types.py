"""The harness contract types (Answer, Conflict, Source).

The harness imports `contract` before loading the submission, so the import
below returns its classes. Outside the harness the module is looked up in a
parent `harness/` folder, and as a last resort equivalent classes are defined
here so the package still imports.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from contract import Answer, Conflict, Source  # noqa: F401
except ImportError:
    _harness = next((p / "harness" for p in Path(__file__).resolve().parents
                     if (p / "harness" / "contract.py").exists()), None)
    if _harness is not None:
        sys.path.insert(0, str(_harness))
        from contract import Answer, Conflict, Source  # noqa: F401
    else:
        from dataclasses import dataclass, field
        from typing import Optional

        @dataclass
        class Source:  # type: ignore[no-redef]
            url: str
            snapshot_id: Optional[str] = None
            quote: Optional[str] = None

        @dataclass
        class Answer:  # type: ignore[no-redef]
            text: str
            sources: list = field(default_factory=list)
            confident: bool = True
            conflict_note: Optional[str] = None

        @dataclass
        class Conflict:  # type: ignore[no-redef]
            topic: str
            values: list
            sources: list
            resolution: Optional[str] = None
            reason: Optional[str] = None

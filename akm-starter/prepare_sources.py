"""Reassemble source files distributed as numbered text parts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for prefix in ("journal/akm_journal.py", "journal/test_journal.py", "signals/akm_signals.py", "signals/test_akm_signals.py"):
    dest = ROOT / prefix
    parts = sorted(dest.parent.glob(dest.name + ".part*"), key=lambda p: int(p.name.rsplit(".part", 1)[1]))
    if not parts or [int(p.name.rsplit(".part", 1)[1]) for p in parts] != list(range(1, len(parts) + 1)):
        raise RuntimeError("Missing source parts for " + prefix)
    content = "".join(p.read_text(encoding="utf-8") for p in parts)
    compile(content, str(dest), "exec")
    dest.write_text(content, encoding="utf-8")
    print("Prepared", prefix)

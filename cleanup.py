import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BATCHES = ROOT / "state" / "batches"

def dir_size_bytes(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total

def batches_stats():
    if not BATCHES.exists():
        return {"folders": 0, "mb": 0.0}
    dirs = [d for d in BATCHES.iterdir() if d.is_dir()]
    total_bytes = sum(dir_size_bytes(d) for d in dirs)
    return {"folders": len(dirs), "mb": round(total_bytes / (1024 * 1024), 2)}

def prune_batches(days: int = 3):
    if not BATCHES.exists():
        return {"deleted": 0, "freed_mb": 0.0}
    cutoff = time.time() - (days * 86400)
    deleted = 0
    freed = 0
    for d in BATCHES.iterdir():
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                sz = dir_size_bytes(d)
                shutil.rmtree(d, ignore_errors=False)
                deleted += 1
                freed += sz
        except OSError:
            pass
    return {"deleted": deleted, "freed_mb": round(freed / (1024 * 1024), 2)}

if __name__ == "__main__":
    print(prune_batches(days=3))

import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import cleanup
import common

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PAUSE_FLAG = STATE / "paused.flag"
DB_PATH = STATE / "ledger.sqlite"
RETENTION_DAYS = 3


def check_http(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def check_worker() -> bool:
    try:
        tok = common.env("WORKER_TOKEN")
        common.req("http://127.0.0.1:8785/status", data={}, headers={"X-Worker-Key": tok}, timeout=2)
        return True
    except Exception:
        return False


def read_db_stats():
    stats = {"pool_new": 0, "pool_total": 0, "scheduled": 0, "published": 0}
    if not DB_PATH.exists():
        return stats
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        cur = conn.cursor()
        try:
            stats["pool_new"] = cur.execute("SELECT COUNT(*) FROM candidates WHERE status='new'").fetchone()[0]
            stats["pool_total"] = cur.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        except sqlite3.Error:
            pass
        try:
            for st, cnt in cur.execute("SELECT state, COUNT(*) FROM posts GROUP BY state").fetchall():
                if st in stats:
                    stats[st] = cnt
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error:
        pass
    return stats


class KreegsPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kreegs Replays — Control Panel")
        self.geometry("500x450")
        self.resizable(False, False)
        self.configure(bg="#ECE9D8")

        self.font_ui = ("Segoe UI", 9)
        self.font_bold = ("Segoe UI", 9, "bold")
        self.font_mono = ("Consolas", 9)

        # Top Banner
        header = tk.Frame(self, bg="#245EDC", padx=10, pady=8)
        header.pack(fill="x")
        tk.Label(header, text="KREEGS REPLAYS // 24/7 PIPELINE", bg="#245EDC", fg="white",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.state_badge = tk.Label(header, text="CHECKING...", bg="#FFFFFF", fg="#111111",
                                    font=("Consolas", 9, "bold"), padx=8, pady=2, relief="sunken", bd=1)
        self.state_badge.pack(side="right")

        body = tk.Frame(self, bg="#ECE9D8", padx=12, pady=10)
        body.pack(fill="both", expand=True)

        # Services Frame
        svc_frame = tk.LabelFrame(body, text=" Services ", bg="#ECE9D8", font=self.font_bold, padx=8, pady=6)
        svc_frame.pack(fill="x", pady=(0, 8))
        self.lbl_worker = self._status_row(svc_frame, "Python Worker (:8785)", 0)
        self.lbl_ollama = self._status_row(svc_frame, "Ollama Local LLM (:11434)", 1)
        self.lbl_n8n = self._status_row(svc_frame, "n8n Scheduler (:5678)", 2)

        # Metrics Frame
        met_frame = tk.LabelFrame(body, text=" Pool, Queue & Storage ", bg="#ECE9D8", font=self.font_bold, padx=8, pady=6)
        met_frame.pack(fill="x", pady=(0, 8))
        self.lbl_pool = self._metric_row(met_frame, "Candidate Pool:", 0)
        self.lbl_queue = self._metric_row(met_frame, "Buffer Posts:", 1)
        self.lbl_disk = self._metric_row(met_frame, f"Batches Disk (Keep {RETENTION_DAYS}d):", 2)

        # Controls Frame
        ctl_frame = tk.LabelFrame(body, text=" Controls ", bg="#ECE9D8", font=self.font_bold, padx=8, pady=8)
        ctl_frame.pack(fill="x", pady=(0, 8))

        btn_cfg = {"font": self.font_ui, "relief": "raised", "bd": 2, "bg": "#F4F3EE", "cursor": "hand2", "width": 19}
        tk.Button(ctl_frame, text="Start Worker", command=self.start_worker, **btn_cfg).grid(row=0, column=0, padx=4, pady=3)
        tk.Button(ctl_frame, text="Stop Worker", command=self.stop_worker, **btn_cfg).grid(row=0, column=1, padx=4, pady=3)
        self.btn_pause = tk.Button(ctl_frame, text="Pause Pipeline", command=self.toggle_pause, **btn_cfg)
        self.btn_pause.grid(row=1, column=0, padx=4, pady=3)
        tk.Button(ctl_frame, text="Run Local Scout Now", command=self.run_scout, **btn_cfg).grid(row=1, column=1, padx=4, pady=3)
        tk.Button(ctl_frame, text=f"Clean Batches (>{RETENTION_DAYS}d)", command=self.run_cleanup, **btn_cfg).grid(row=2, column=0, padx=4, pady=3)
        tk.Button(ctl_frame, text="Open n8n Dashboard", command=lambda: webbrowser.open("http://localhost:5678"), **btn_cfg).grid(row=2, column=1, padx=4, pady=3)

        # Bottom utility row
        util_row = tk.Frame(body, bg="#ECE9D8")
        util_row.pack(fill="x", pady=(0, 6))
        tk.Button(util_row, text="Open Batches Folder", font=self.font_ui, relief="raised", bd=2, bg="#F4F3EE",
                  command=lambda: os.startfile(str(STATE / "batches")) if (STATE / "batches").exists() else None).pack(side="left")
        tk.Button(util_row, text="Refresh Now", font=self.font_ui, relief="raised", bd=2, bg="#F4F3EE",
                  command=self.refresh_async).pack(side="right")

        # Sunken Log Box
        self.log_box = tk.Text(body, height=5, bg="#FFFFFF", fg="#111111", font=self.font_mono,
                               relief="sunken", bd=2, state="disabled")
        self.log_box.pack(fill="both", expand=True)

        self.log("Control Panel ready.")
        self.refresh_async()
        self.after(5000, self._poll_loop)

    def _status_row(self, parent, label, row):
        tk.Label(parent, text=label, bg="#ECE9D8", font=self.font_ui).grid(row=row, column=0, sticky="w", pady=1)
        val = tk.Label(parent, text="...", bg="#ECE9D8", font=self.font_mono)
        val.grid(row=row, column=1, sticky="e", padx=(20, 0), pady=1)
        parent.grid_columnconfigure(1, weight=1)
        return val

    def _metric_row(self, parent, label, row):
        tk.Label(parent, text=label, bg="#ECE9D8", font=self.font_ui).grid(row=row, column=0, sticky="w", pady=1)
        val = tk.Label(parent, text="...", bg="#ECE9D8", font=self.font_mono)
        val.grid(row=row, column=1, sticky="e", padx=(20, 0), pady=1)
        parent.grid_columnconfigure(1, weight=1)
        return val

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_loop(self):
        self.refresh_async()
        self.after(5000, self._poll_loop)

    def refresh_async(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        w_ok = check_worker()
        o_ok = check_http("http://127.0.0.1:11434/api/tags")
        n_ok = check_http("http://127.0.0.1:5678/healthz")
        db = read_db_stats()
        disk = cleanup.batches_stats()
        paused = PAUSE_FLAG.exists()
        self.after(0, lambda: self._apply_state(w_ok, o_ok, n_ok, db, disk, paused))

    def _apply_state(self, w_ok, o_ok, n_ok, db, disk, paused):
        self.lbl_worker.config(text="● ONLINE" if w_ok else "● OFFLINE", fg="#107C10" if w_ok else "#C50F1F")
        self.lbl_ollama.config(text="● ONLINE" if o_ok else "● OFFLINE", fg="#107C10" if o_ok else "#C50F1F")
        self.lbl_n8n.config(text="● ONLINE" if n_ok else "● OFFLINE", fg="#107C10" if n_ok else "#C50F1F")

        self.lbl_pool.config(text=f"{db['pool_new']} new / {db['pool_total']} total")
        self.lbl_queue.config(text=f"{db['scheduled']} scheduled / {db['published']} published")
        self.lbl_disk.config(text=f"{disk['folders']} folders ({disk['mb']} MB)")

        if paused:
            self.state_badge.config(text="PAUSED", bg="#FFF4CE", fg="#8A5700")
            self.btn_pause.config(text="Resume Pipeline")
        elif w_ok:
            self.state_badge.config(text="ACTIVE", bg="#DFF6DD", fg="#107C10")
            self.btn_pause.config(text="Pause Pipeline")
        else:
            self.state_badge.config(text="STOPPED", bg="#FDE7E9", fg="#C50F1F")
            self.btn_pause.config(text="Pause Pipeline")

    def start_worker(self):
        def _run():
            if check_worker():
                self.after(0, lambda: self.log("Worker is already running."))
                return
            subprocess.Popen(
                ["cmd.exe", "/c", str(ROOT / "start-worker.cmd")],
                cwd=str(ROOT),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(2)
            self.after(0, lambda: self.log("Started Python worker."))
            self.refresh_async()
        threading.Thread(target=_run, daemon=True).start()

    def stop_worker(self):
        def _run():
            ps = "Get-NetTCPConnection -LocalPort 8785 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(1)
            self.after(0, lambda: self.log("Stopped Python worker."))
            self.refresh_async()
        threading.Thread(target=_run, daemon=True).start()

    def toggle_pause(self):
        STATE.mkdir(parents=True, exist_ok=True)
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink(missing_ok=True)
            self.log("Pipeline RESUMED.")
        else:
            PAUSE_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
            self.log("Pipeline PAUSED (scheduled batches & Buffer ticks halted).")
        self.refresh_async()

    def run_scout(self):
        self.log("Triggering /scout-local ...")
        def _run():
            try:
                tok = common.env("WORKER_TOKEN")
                res = common.req("http://127.0.0.1:8785/scout-local", data={}, headers={"X-Worker-Key": tok}, timeout=120)
                self.after(0, lambda: self.log(f"Scout done: {res}"))
            except Exception as e:
                self.after(0, lambda: self.log(f"Scout error: {e}"))
            self.refresh_async()
        threading.Thread(target=_run, daemon=True).start()

    def run_cleanup(self):
        res = cleanup.prune_batches(days=RETENTION_DAYS)
        self.log(f"Cleanup (>{RETENTION_DAYS}d): deleted {res['deleted']} folders, freed {res['freed_mb']} MB.")
        self.refresh_async()


if __name__ == "__main__":
    app = KreegsPanel()
    app.mainloop()

# Automated Twitch-to-Shorts Clipping Pipeline (Windows)

A 24/7 local Windows pipeline that scouts Twitch clips, scores them locally with Ollama, edits/QAs vertical Shorts with FFmpeg + Gemini, and schedules them to YouTube Shorts, TikTok, and Instagram via Buffer and n8n.

## Quick Setup (Windows 10/11)

1. **Download & Run Installer:** Clone or unzip this repo, open PowerShell inside the folder, and run:
   `powershell -ExecutionPolicy Bypass -File .\setup.ps1`
2. **Add API Keys:** Open `.env` and fill in your Twitch, Gemini, Buffer, and S3/R2 keys.
3. **Configure Roster:** Open `config.json` to set `creators`, `focus_games`/`focus_keywords`, `channels`, and `timezone`.
4. **Start n8n:** Launch Docker Desktop, run `docker compose up -d`, open `http://localhost:5678`, import `workflows/clipping-pipeline.json`, paste your `WORKER_TOKEN` into the HTTP nodes, and toggle **Active**.
5. **Run:** Double-click **Clipping Control Panel** on your Desktop and click **Start Worker**. Use **Pause Pipeline** when gaming.

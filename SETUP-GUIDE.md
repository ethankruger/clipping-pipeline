# Complete Step-by-Step Setup Guide (Windows 10/11)

This guide assumes zero prior setup. Follow each phase in order to go from a fresh Windows PC to a 24/7 automated Twitch-to-Shorts pipeline.

## System Requirements
* **OS:** Windows 10 (64-bit) or Windows 11
* **RAM:** 16 GB+ recommended (for Docker + Ollama)
* **Storage:** ~15 GB free space (for Docker, Ollama models, and temporary clip batches)
* **GPU:** NVIDIA/AMD GPU with 6 GB+ VRAM recommended for fast local Ollama scoring (CPU fallback works, just slower)

---

## Phase 1: Create Your Social Media Channels

1. **YouTube:** Create a YouTube channel (a Brand Account lets you use any channel name). Go to **YouTube Studio → Settings → Channel → Feature eligibility** and verify your phone number so you have no upload limits.
2. **TikTok:** Create a TikTok account for your clipping page.
3. **Instagram:** Create an Instagram account, then switch it to a **Professional Account**:
   * Open Instagram → **Settings → Account type and tools → Switch to professional account** → choose **Creator** or **Business**.
   * *Buffer requires a Professional Instagram account to auto-publish Reels.*

---

## Phase 2: Connect Channels to Buffer & Get Your Buffer Keys

Buffer handles the scheduled publishing to YouTube, TikTok, and Instagram.

1. Create an account at [buffer.com](https://buffer.com).
2. Connect your **YouTube**, **TikTok**, and **Instagram** channels inside Buffer.
3. For each channel, open **Publishing → Settings → Posting Schedule** and set the daily times you want Shorts to go live.
4. Collect these values (save them in a temporary Notepad file):
   * **`BUFFER_TOKEN`**: Your Buffer API access token.
   * **`BUFFER_ORG_ID`**: Your Buffer Organization ID.
   * **Channel IDs**: The ID for each connected channel (YouTube, TikTok, Instagram) — you will paste these into `config.json` in Phase 5.

---

## Phase 3: Get Your API Keys & Media Storage

Add these to your temporary Notepad file:

### 1. Twitch API (Free)
1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) and log in with your Twitch account.
2. Click **Register Your Application**:
   * **Name:** Anything unique (e.g., `MyClippingPipeline`)
   * **OAuth Redirect URLs:** `http://localhost`
   * **Category:** `Application Integration`
3. Click **Create**, then click **Manage** on your app.
4. Copy **Client ID** (`TWITCH_CLIENT_ID`).
5. Click **New Secret** and copy **Client Secret** (`TWITCH_CLIENT_SECRET`).

### 2. Google Gemini API
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with Google.
2. Click **Create API key** and copy it (`GEMINI_API_KEY`).

### 3. Cloudflare R2 (Free Tier Media Hosting)
Buffer needs a public URL to download your rendered `.mp4` Shorts.
1. Create a free account at [dash.cloudflare.com](https://dash.cloudflare.com) and open **R2 Object Storage** in the left sidebar.
2. Click **Create bucket** (e.g., `clipping-media`) and note the bucket name (`S3_BUCKET`).
3. Open your new bucket → **Settings** → enable **Public Development URL** (or attach a custom domain) and copy that URL (`MEDIA_BASE_URL`, e.g., `https://pub-xxxx.r2.dev`).
4. Go back to **R2 Overview → Manage R2 API Tokens → Create API Token**:
   * Permissions: **Object Read & Write** (apply to your bucket).
   * Copy **Access Key ID** (`S3_ACCESS_KEY`), **Secret Access Key** (`S3_SECRET_KEY`), and the **S3 API Endpoint** (`S3_ENDPOINT`, e.g., `https://<account_id>.r2.cloudflarestorage.com`).
   * Keep `S3_REGION=auto`.

---

## Phase 4: Download & Run the One-Click Installer

1. Download this repository:
   * Click the green **Code** button on GitHub → **Download ZIP**, then right-click the ZIP → **Extract All** (for example, to `C:\Users\YourName\Desktop\Clipping-Pipeline`).
2. Open PowerShell inside that extracted folder:
   * Open the folder in File Explorer, click the address bar at the top, type `powershell`, and press **Enter**.
3. Run the installer:

       powershell -ExecutionPolicy Bypass -File .\setup.ps1

   This automatically installs:
   * **Python 3.12** + all required Python packages inside `.venv`
   * **FFmpeg** (video rendering)
   * **Ollama** + local scoring models (`qwen2.5:7b` and `qwen2.5:3b`)
   * **Docker Desktop** (runs n8n)
   * Generates your `.env` (with a fresh random `WORKER_TOKEN`), `config.json`, and a **Clipping Control Panel** shortcut on your Desktop.
4. **Restart your PC** if Docker Desktop prompts you to reboot after installing WSL 2.

---

## Phase 5: Configure `.env` and `config.json`

### 1. Edit `.env`
Right-click `.env` → **Open with Notepad** (or VS Code) and paste in the keys from Phases 2 and 3.
* **Keep `WORKER_TOKEN` as-is** — `setup.ps1` already generated a secure random token. Copy it down for Phase 6.

### 2. Edit `config.json`
Open `config.json` and update:
* **`creators`**: Lowercase Twitch usernames to scout (e.g., `["lacy", "clix", "jasontheween"]`).
* **`focus_games` / `focus_keywords`**: Leave both as `[]` to scout all games, or add a game title/keywords (e.g., `"Grand Theft Auto VI"`) to filter clips.
* **`channels`**: Paste your Buffer Channel IDs for YouTube, TikTok, and Instagram.
* **`timezone`**: Your IANA timezone (e.g., `America/Chicago`, `America/New_York`, `America/Los_Angeles`).
* Also open `docker-compose.yml` and set `TZ` and `GENERIC_TIMEZONE` to the same timezone.

---

## Phase 6: Set Up Docker Desktop & n8n (The 24/7 Scheduler)

n8n runs locally on your PC inside Docker for free (no cloud subscription needed).

### 1. Start Docker Desktop
1. Open **Docker Desktop** from the Windows Start Menu.
2. Accept the service agreement if prompted and choose **Use recommended settings** (WSL 2 backend). You do not need to sign in to a Docker account — click **Skip** if asked.
3. Wait until the bottom-left bar turns green and says **Engine running**.

### 2. Launch n8n with Docker Compose
1. Open PowerShell inside your `Clipping-Pipeline` folder.
2. Run:

       docker compose up -d

   *(The first run downloads the official n8n image and takes 1–2 minutes.)*
3. Confirm n8n is running:

       docker ps

   You should see a container named `clipping-n8n` with status `Up`.

### 3. Create Your Local n8n Account & Import the Workflow
1. Open your browser and go to **[http://localhost:5678](http://localhost:5678)**.
2. Fill out the local owner account form (this stays on your PC only).
3. Click **Create Workflow** (top right).
4. Click the **⋯** menu (top right of the editor) → **Import from File** → select `workflows\clipping-pipeline.json`.

### 4. Connect Your `WORKER_TOKEN` in n8n (One-Time Setup)
1. Double-click any HTTP Request node (for example, **Gemini • scout and watch clips**).
2. Under **Credential for Header Auth**, click the dropdown → **Create New Credential** (or click the pencil icon next to `Header Auth account`).
3. Fill in:
   * **Name:** `X-Worker-Key`
   * **Value:** Paste the exact `WORKER_TOKEN` from your `.env` file.
4. Click **Save**, then close the node.
5. Check the other HTTP Request nodes (including **Local Scout (Every 2h)**) to confirm they are set to your `Header Auth account` credential.
6. Switch the **Active** toggle in the top-right corner of n8n to **ON (green)** and press **Ctrl + S** to save.

---

## Phase 7: Launch, Test, and Go Live

1. **Check Ollama:** Make sure the small Ollama llama icon is running in your Windows system tray (bottom-right). If not, launch **Ollama** from the Start Menu.
2. **Open the Control Panel:** Double-click **Clipping Control Panel** on your Desktop.
3. **Start the Worker:** Click **Start Worker**. Confirm all three status lights (**Worker**, **Ollama**, **n8n**) turn **green**.
4. **Run a Safe End-to-End Test (No Live Posts):**
   * In n8n (`http://localhost:5678`), click **Test one clip • no posts**.
   * Watch it run through `scout` → `brief` → `edit` → `plan`.
   * Because it is a `test-*` run, it builds a preview schedule without posting anything to Buffer.
5. **Keep Your PC Awake:**
   * Open Windows **Settings → System → Power & battery → Screen and sleep**.
   * Set **When plugged in, put my device to sleep after** to **Never** so overnight batches run uninterrupted.

---

## Phase 8: Everyday Use & Troubleshooting

### Playing Games
Click **Pause Pipeline** in the **Clipping Control Panel** before launching a game so Ollama and FFmpeg don't spike your GPU/CPU mid-match. Click **Resume Pipeline** when you're done. (You don't need to stop the worker.)

### Common Issues
* **n8n shows `401 Unauthorized`:** The `Value` in your n8n `Header Auth account` credential does not match `WORKER_TOKEN` in `.env`. Copy-paste it again and restart the worker.
* **n8n shows `ECONNREFUSED` on `host.docker.internal:8785`:** Make sure the Python worker is running (click **Start Worker** in the Clipping Control Panel) and that `BIND_HOST` in `.env` allows local connections.
* **n8n won't load at `localhost:5678`:** Open Docker Desktop and make sure the engine is running, then run `docker compose up -d` again.
* **Disk space filling up:** Batch folders older than 3 days are automatically deleted every 2 hours, or anytime by clicking **Clean Batches (>3d)** in the Control Panel.

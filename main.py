import os
import hashlib
import time
import threading
import subprocess
import urllib.parse

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import json

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("API_KEY", "526dcdb0e21f7a515ebcf12a89d865d5bf5af0b16cd9f8400639de0ff0951d87")
PORT    = int(os.environ.get("PORT", 8000))
OUT_DIR = "/tmp/media-dl"
os.makedirs(OUT_DIR, exist_ok=True)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Media Downloader API", version="4.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ─────────────────────────────────────────────────────────────────────
active_jobs: dict[str, dict] = {}

SUPPORTED_SITES = [
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "soundcloud.com", "facebook.com", "fb.watch", "vimeo.com",
    "dailymotion.com", "twitch.tv", "twitter.com", "x.com", "reddit.com",
]

AUDIO_FORMATS = ["mp3", "wav", "ogg", "m4a"]
VIDEO_FORMATS = ["mp4"]
FORMATS       = AUDIO_FORMATS + VIDEO_FORMATS
QUALITIES     = {"64": "9", "128": "5", "192": "3", "320": "0"}

# ── Args anti-bot ─────────────────────────────────────────────────────────────
# bgutil-ytdlp-pot-provider est détecté automatiquement par yt-dlp
# Il gère les PO Tokens YouTube sans configuration manuelle
def ytdlp_common_args() -> list[str]:
    return [
        "--no-warnings",
        "--sleep-interval", "1",
    ]

# ── Helpers ───────────────────────────────────────────────────────────────────
def detect_site(url: str) -> str:
    mapping = {
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "tiktok.com": "TikTok", "instagram.com": "Instagram",
        "soundcloud.com": "SoundCloud", "facebook.com": "Facebook",
        "fb.watch": "Facebook", "vimeo.com": "Vimeo",
        "dailymotion.com": "Dailymotion", "twitch.tv": "Twitch",
        "twitter.com": "Twitter/X", "x.com": "Twitter/X",
        "reddit.com": "Reddit",
    }
    for domain, name in mapping.items():
        if domain in url:
            return name
    return "Unknown"

def is_supported(url: str) -> bool:
    return any(s in url for s in SUPPORTED_SITES)

def clean_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    if "youtube.com" in url or "youtu.be" in url:
        vid = params.get("v")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        path = parsed.path.strip("/")
        if path:
            return f"https://www.youtube.com/watch?v={path}"
    return url

def check_api_key(x_api_key: str = Header(default="")) -> bool:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# ── Download worker ───────────────────────────────────────────────────────────
def run_download(job_id: str, url: str, fmt: str, quality: str):
    job = active_jobs[job_id]
    job["status"] = "downloading"
    job["progress"] = 5

    audio_quality = QUALITIES.get(quality, "5")
    out_template  = os.path.join(OUT_DIR, f"{job_id}_%(title)s.%(ext)s")
    common        = ytdlp_common_args()

    if fmt in VIDEO_FORMATS:
        if quality == "360":
            fmt_sel = "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]"
        elif quality == "720":
            fmt_sel = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
        elif quality == "1080":
            fmt_sel = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
        else:
            fmt_sel = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        cmd = [
            "yt-dlp", "--no-playlist",
            "-f", fmt_sel,
            "--merge-output-format", "mp4",
            *common,
            "-o", out_template,
            url,
        ]
    else:
        cmd = [
            "yt-dlp", "--no-playlist",
            "-x", "--audio-format", fmt,
            "--audio-quality", audio_quality,
            *common,
            "-o", out_template,
            url,
        ]

    print(f"[CMD] {' '.join(cmd)}", flush=True)

    # Fake progressive progress
    def fake_progress():
        for pct in range(10, 90, 5):
            time.sleep(1.5)
            if active_jobs.get(job_id, {}).get("status") != "downloading":
                break
            active_jobs[job_id]["progress"] = pct

    threading.Thread(target=fake_progress, daemon=True).start()

    result = subprocess.run(cmd, capture_output=True, text=True)

    print(f"[CODE] {result.returncode}", flush=True)
    if result.stderr:
        print(f"[STDERR] {result.stderr[-1000:]}", flush=True)

    if result.returncode == 0:
        found = next(
            (os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR) if f.startswith(job_id)),
            None,
        )
        if found:
            job.update(status="done", progress=100, file=found, filename=os.path.basename(found))
        else:
            job.update(status="error", error="Fichier introuvable après téléchargement")
    else:
        err = result.stderr[-400:] or f"exit code {result.returncode}"
        job.update(status="error", error=err)

# ── Cleanup loop ──────────────────────────────────────────────────────────────
def cleanup_loop():
    while True:
        time.sleep(600)
        now = time.time()
        for f in os.listdir(OUT_DIR):
            path = os.path.join(OUT_DIR, f)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 1800:
                try:
                    os.remove(path)
                except:
                    pass

threading.Thread(target=cleanup_loop, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "name": "Media Downloader API",
        "version": "4.2.0",
        "supported_sites": SUPPORTED_SITES,
        "formats": FORMATS,
    }

@app.get("/ping")
def ping():
    return {"ok": True, "version": "4.2.0"}

@app.get("/info")
async def get_info(url: str = Query(...), _: bool = Depends(check_api_key)):
    if not is_supported(url):
        raise HTTPException(400, "Site non supporté")

    info_cmd = [
        "yt-dlp", "--dump-json", "--no-playlist",
        *ytdlp_common_args(),
        url,
    ]

    try:
        result = subprocess.run(
            info_cmd,
            capture_output=True, text=True, timeout=20
        )

        if result.returncode != 0:
            print(f"[INFO ERROR] {result.stderr[-400:]}", flush=True)
            raise HTTPException(400, "Impossible de récupérer les infos")

        data = json.loads(result.stdout)
        return {
            "title":     data.get("title", ""),
            "author":    data.get("uploader") or data.get("channel", ""),
            "thumbnail": data.get("thumbnail", ""),
            "duration":  data.get("duration"),
            "site":      data.get("extractor_key", detect_site(url)),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout")
    except json.JSONDecodeError:
        raise HTTPException(500, "Réponse invalide de yt-dlp")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class DownloadRequest(BaseModel):
    url: str
    format: str = "mp3"
    quality: str = "128"


@app.post("/download")
def download(req: DownloadRequest, _: bool = Depends(check_api_key)):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "URL manquante")
    if not is_supported(url):
        raise HTTPException(400, f"Site non supporté. Sites: {', '.join(SUPPORTED_SITES)}")
    if req.format not in FORMATS:
        raise HTTPException(400, f"Format non supporté. Formats: {', '.join(FORMATS)}")

    url = clean_url(url)
    job_id = hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:12]

    active_jobs[job_id] = {
        "id": job_id,
        "url": url,
        "format": req.format,
        "quality": req.quality,
        "site": detect_site(url),
        "status": "queued",
        "progress": 0,
        "file": None,
        "filename": None,
        "error": None,
        "created_at": time.time(),
    }

    threading.Thread(
        target=run_download,
        args=(job_id, url, req.format, req.quality),
        daemon=True,
    ).start()

    return {"ok": True, "job_id": job_id, "site": detect_site(url)}


@app.get("/status")
def status(id: str = Query(...), _: bool = Depends(check_api_key)):
    job = active_jobs.get(id)
    if not job:
        raise HTTPException(404, "Job introuvable")
    return {k: v for k, v in job.items() if k != "file"}


@app.get("/file")
def get_file(id: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Unauthorized")
    job = active_jobs.get(id)
    if not job:
        raise HTTPException(404, "Job introuvable")
    if job["status"] != "done":
        raise HTTPException(400, f"Not ready: {job['status']}")
    filepath = job.get("file")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(404, "File not found")

    def delete_after():
        time.sleep(5)
        try:
            os.remove(filepath)
        except:
            pass
        active_jobs.pop(id, None)

    threading.Thread(target=delete_after, daemon=True).start()
    return FileResponse(
        path=filepath,
        filename=job["filename"],
        media_type="application/octet-stream",
    )


@app.get("/jobs")
def list_jobs(_: bool = Depends(check_api_key)):
    jobs = [
        {k: v for k, v in job.items() if k != "file"}
        for job in active_jobs.values()
    ]
    return {"jobs": sorted(jobs, key=lambda j: j.get("created_at", 0), reverse=True)[:20]}
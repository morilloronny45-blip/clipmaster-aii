import os, uuid, subprocess, shutil
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
UPLOADS, OUTPUTS = ROOT / "uploads", ROOT / "outputs"
UPLOADS.mkdir(exist_ok=True); OUTPUTS.mkdir(exist_ok=True)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024
ALLOWED = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
HOSTS = {"youtube.com","www.youtube.com","youtu.be","m.youtube.com","facebook.com",
         "www.facebook.com","m.facebook.com","fb.watch","tiktok.com","www.tiktok.com",
         "vm.tiktok.com","vt.tiktok.com"}

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "Error de procesamiento")[-1600:])
    return p.stdout

def get_duration(path):
    return float(run(["ffprobe","-v","error","-show_entries","format=duration",
                      "-of","default=noprint_wrappers=1:nokey=1",str(path)]).strip())

def download_url(url, dest):
    parsed = urlparse(url)
    if parsed.scheme not in ("http","https") or (parsed.hostname or "").lower() not in HOSTS:
        raise ValueError("Solo se aceptan enlaces de YouTube, Facebook o TikTok.")
    run(["yt-dlp","--no-playlist","--max-filesize","2G","-f","bv*+ba/b",
         "--merge-output-format","mp4","-o",str(dest),url])
    candidates = list(dest.parent.glob(dest.stem + ".*"))
    if not candidates:
        raise RuntimeError("No se pudo obtener el video. Verifica que sea público y compatible.")
    return candidates[0]

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.post("/api/process")
def process():
    job = uuid.uuid4().hex[:10]
    work, outdir = UPLOADS/job, OUTPUTS/job
    work.mkdir(parents=True, exist_ok=True); outdir.mkdir(parents=True, exist_ok=True)
    try:
        url = (request.form.get("url") or "").strip()
        f = request.files.get("video")
        if bool(url) == bool(f and f.filename):
            return jsonify(error="Adjunta un video o pega un enlace, pero no ambos."), 400
        if url:
            source = download_url(url, work/"source.mp4")
        else:
            name = secure_filename(f.filename)
            if Path(name).suffix.lower() not in ALLOWED:
                raise ValueError("Formato no compatible. Usa MP4, MOV, MKV o WEBM.")
            source = work/name
            f.save(source)
        total = get_duration(source)
        if total <= 0 or total > 14400:
            raise ValueError("El video debe durar menos de 4 horas.")
        try: segment = int(request.form.get("segment","60"))
        except ValueError: segment = 60
        if segment not in (30,60,90): segment = 60
        clips, start, index = [], 0.0, 1
        while start < total:
            length = min(segment, total-start)
            if length < 2: break
            filename = f"clip_{index:02d}.mp4"
            target = outdir/filename
            run(["ffmpeg","-y","-ss",str(start),"-i",str(source),"-t",str(length),
                 "-map","0:v:0","-map","0:a?","-c:v","libx264","-preset","veryfast",
                 "-crf","23","-c:a","aac","-movflags","+faststart",str(target)])
            clips.append({"name":filename,"url":f"/download/{job}/{filename}",
                          "start":round(start),"duration":round(length)})
            start += segment; index += 1
        return jsonify(job=job,duration=round(total),clips=clips,
                       note="Segmentos consecutivos. La selección automática con IA aún está en desarrollo.")
    except Exception as e:
        shutil.rmtree(work,ignore_errors=True); shutil.rmtree(outdir,ignore_errors=True)
        return jsonify(error=str(e)), 400

@app.get("/download/<job>/<path:filename>")
def download(job, filename):
    if not job.isalnum() or not (OUTPUTS/job).is_dir():
        return jsonify(error="Clip no encontrado."), 404
    return send_from_directory(OUTPUTS/job, filename, as_attachment=True)

@app.errorhandler(413)
def too_large(_):
    return jsonify(error="El archivo supera el límite de 2 GB."), 413

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","5000")), debug=False)

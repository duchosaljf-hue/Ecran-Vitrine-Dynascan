"""Traitement des fichiers : PDF -> images, images, vidéos, miniatures, QR codes."""
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import CFG

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm"}
PDF_EXT = {".pdf"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT | PDF_EXT


class MediaError(Exception):
    pass


def natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def sniff_kind(path: Path):
    """Détermine le type réel d'un fichier à partir de son contenu : 'pdf', 'image' ou 'video'."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"GIF8") or \
            (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        return "image"
    # vidéo : on laisse ffprobe décider
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return "video"
    except (OSError, subprocess.SubprocessError):
        pass
    raise MediaError("Format de fichier non pris en charge (PDF, image JPG/PNG/WebP ou vidéo MP4).")


def run(cmd, timeout=600):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise MediaError(f"Outil manquant : {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise MediaError("Le traitement du fichier a pris trop de temps.")
    if result.returncode != 0:
        raise MediaError((result.stderr or result.stdout or "erreur").strip()[-300:])
    return result


def probe_video(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=codec_name,width,height,pix_fmt", "-of", "json", str(path)], timeout=60)
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise MediaError("Aucune piste vidéo trouvée.")
    return streams[0]


def probe_image(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "json", str(path)], timeout=60)
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise MediaError("Image illisible.")
    return int(streams[0]["width"]), int(streams[0]["height"])


def make_thumb(src, dest):
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
         "-vf", "scale=420:-2", "-q:v", "5", str(dest)], timeout=120)


def video_thumb(src, dest):
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(src), "-frames:v", "1",
         "-vf", "scale=420:-2", "-q:v", "5", str(dest)], timeout=120)
    if not Path(dest).exists():  # vidéo plus courte qu'une seconde
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=420:-2", "-q:v", "5", str(dest)], timeout=120)


def item_dir(item_id, rev):
    return CFG.media_dir / str(item_id) / f"r{rev}"


def url_for(item_id, rev, name):
    return f"/files/{item_id}/r{rev}/{name}"


def process_pdf(src: Path, out_dir: Path, item_id, rev):
    pages_prefix = out_dir / "page"
    run(["pdftoppm", "-jpeg", "-jpegopt", "quality=88", "-scale-to", "2000",
         "-l", str(CFG.max_pdf_pages), str(src), str(pages_prefix)], timeout=600)
    pages = sorted(out_dir.glob("page-*.jpg"), key=lambda p: natural_key(p.name))
    if not pages:
        raise MediaError("Le PDF ne contient aucune page lisible.")
    slides = [{"type": "image", "src": url_for(item_id, rev, p.name)} for p in pages]
    make_thumb(pages[0], out_dir / "thumb.jpg")
    return slides


def process_image(src: Path, out_dir: Path, item_id, rev):
    width, height = probe_image(src)
    size_mb = src.stat().st_size / 1e6
    ext = src.suffix.lower()
    if max(width, height) > 3000 or size_mb > 6 or ext not in IMAGE_EXT:
        dest = out_dir / "slide.jpg"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=w='min(2560,iw)':h='min(2560,ih)':force_original_aspect_ratio=decrease",
             "-q:v", "3", str(dest)], timeout=120)
    else:
        dest = out_dir / ("slide" + (".jpg" if ext == ".jpeg" else ext))
        shutil.copyfile(src, dest)
    make_thumb(dest, out_dir / "thumb.jpg")
    return [{"type": "image", "src": url_for(item_id, rev, dest.name)}]


def process_video(src: Path, out_dir: Path, item_id, rev):
    info = probe_video(src)
    dest = out_dir / "video.mp4"
    if info.get("codec_name") == "h264" and info.get("pix_fmt") == "yuv420p":
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "copy", "-an",
             "-movflags", "+faststart", str(dest)], timeout=1800)
    else:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-pix_fmt", "yuv420p", "-vf", "scale=w='min(1920,iw)':h=-2", "-an",
             "-movflags", "+faststart", str(dest)], timeout=3 * 3600)
    video_thumb(dest, out_dir / "thumb.jpg")
    return [{"type": "video", "src": url_for(item_id, rev, dest.name)}]


def process_file(src: Path, item_id, rev):
    """Traite un fichier envoyé. Retourne (kind, slides, thumb_url, info)."""
    kind = sniff_kind(src)
    out_dir = item_dir(item_id, rev)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = {}
    try:
        if kind == "pdf":
            keep = out_dir / "original.pdf"
            shutil.copyfile(src, keep)
            slides = process_pdf(keep, out_dir, item_id, rev)
            info = extract_pdf_info(keep)
            result_kind = "poster"
        elif kind == "image":
            slides = process_image(src, out_dir, item_id, rev)
            result_kind = "poster"
        else:
            slides = process_video(src, out_dir, item_id, rev)
            result_kind = "video"
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return result_kind, slides, url_for(item_id, rev, "thumb.jpg"), info


def cleanup_old_revisions(item_id, keep_revs):
    base = CFG.media_dir / str(item_id)
    if not base.is_dir():
        return
    keep = {f"r{r}" for r in keep_revs}
    for child in base.iterdir():
        if child.is_dir() and child.name not in keep and re.fullmatch(r"r\d+", child.name):
            shutil.rmtree(child, ignore_errors=True)


def delete_item_files(item_id):
    shutil.rmtree(CFG.media_dir / str(item_id), ignore_errors=True)


# ------------------------------------------------------------------ texte du PDF
PROPERTY_TYPES = r"(Maison|Appartement|Terrain|Immeuble|Local|Villa|Studio|Loft|Duplex|Programme|Parking|Fonds de commerce)"
PRICE_RE = re.compile(r"(\d{1,3}(?:[   .]\d{3})+|\d{4,8})\s*(?:€|euros?)", re.IGNORECASE)
COMMUNE_RE = re.compile(r"([A-ZÀ-Ý][A-ZÀ-Ý'’\- ]{2,}?)\s*\((\d{5})\)")
TITLE_RE = re.compile(PROPERTY_TYPES + r"\s*(?:(\d+)\s*pi[eè]ces?)?\s*(\d+(?:[.,]\d+)?\s*m[²2])?", re.IGNORECASE)


def extract_pdf_info(pdf: Path):
    """Lit le texte de la première page pour pré-remplir le titre et le prix (si le PDF contient du texte)."""
    try:
        out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf), "-"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    text = re.sub(r"\s+", " ", out.stdout or "").strip()
    if not text:
        return {}
    info = {}
    for match in PRICE_RE.finditer(text):
        value = int(re.sub(r"\D", "", match.group(1)))
        if 30000 <= value <= 50_000_000:
            info["price"] = value
            break
    parts = []
    tm = TITLE_RE.search(text)
    if tm:
        label = tm.group(1).capitalize()
        if tm.group(2):
            label += f" {tm.group(2)} pièces"
        if tm.group(3):
            label += " " + re.sub(r"\s+", " ", tm.group(3)).replace("m2", "m²")
        parts.append(label)
    cm = COMMUNE_RE.search(text)
    if cm:
        parts.append(cm.group(1).strip().title())
    if parts:
        info["title"] = " - ".join(parts)
    return info


# ------------------------------------------------------------------ QR code
def make_qr(url, dest: Path):
    import segno
    segno.make(url, error="m").save(str(dest), kind="svg", scale=12, border=2, dark="#000000", light="#ffffff")

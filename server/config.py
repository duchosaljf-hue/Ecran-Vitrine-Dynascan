"""Configuration : variables d'environnement, avec lecture de config.env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "2.0"


def load_env_file(path):
    """Charge un fichier KEY=value sans écraser les variables déjà définies."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ROOT / "config.env")


def env(name, default):
    return os.environ.get(name, default)


class Config:
    def __init__(self):
        self.data_dir = Path(env("VITRINE_DATA_DIR", str(ROOT / "data"))).resolve()
        self.media_dir = self.data_dir / "media"
        self.tmp_dir = self.data_dir / "tmp"
        self.backup_dir = self.data_dir / "backups"
        self.inbox_dir = self.data_dir / "inbox"  # ancien dossier de synchronisation Drive
        self.db_path = self.data_dir / "vitrine.db"
        self.host = env("VITRINE_HOST", "127.0.0.1")
        self.port = int(env("VITRINE_PORT", "8080"))
        self.player_dir = ROOT / "player"
        self.web_dir = ROOT / "web"
        self.session_hours = int(env("SESSION_HOURS", "168"))  # 7 jours
        self.max_upload_mb = int(env("MAX_UPLOAD_MB", "400"))
        self.max_pdf_pages = int(env("MAX_PDF_PAGES", "30"))
        self.temp_warn_c = float(env("TEMP_WARN_C", "80"))
        # Valeurs initiales (modifiables ensuite dans la page Réglages)
        self.city = env("CITY", "Carquefou")
        self.latitude = env("LATITUDE", "47.2978")
        self.longitude = env("LONGITUDE", "-1.4903")
        self.timezone = env("TIMEZONE", "Europe/Paris")
        self.default_duration = int(env("IMAGE_SECONDS", "10"))
        self.weather_every = int(env("WEATHER_EVERY", "3"))
        self.weather_seconds = int(env("WEATHER_SECONDS", "10"))
        self.corner_clock = env("CORNER_CLOCK", "0") == "1"

    def ensure_dirs(self):
        for d in (self.data_dir, self.media_dir, self.tmp_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)


CFG = Config()

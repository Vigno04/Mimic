import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

class Settings:
    APP_NAME: str = "Mimic"
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR / 'mimic.db'}")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "mimic_secret_key_default")
    TIMEZONE: str = os.getenv("TIMEZONE", os.getenv("TZ", "UTC"))
    DATA_DIR: Path = DATA_DIR

settings = Settings()

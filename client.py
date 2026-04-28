import os
from pathlib import Path
import yaml
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
ENC_PATH = ROOT / "config" / "integrations.enc"


def load_config() -> dict:
    key = os.environ.get("SELF_HEALING_MASTER_KEY")
    if not key:
        raise RuntimeError("SELF_HEALING_MASTER_KEY env var is required")
    f = Fernet(key.encode("utf-8"))
    plaintext = f.decrypt(ENC_PATH.read_bytes()).decode("utf-8")
    return yaml.safe_load(plaintext)

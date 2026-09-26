"""Dashboard static files and their cache-busting versions."""
import hashlib
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def asset_version(name: str) -> str:
    """Short content hash of a static asset, used as a cache-busting query."""
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]


def versioned_html(page: str, assets: tuple[str, ...]) -> str:
    """The page with /static/<asset> references pointing at their current version."""
    html = (STATIC_DIR / page).read_text(encoding="utf-8")
    for name in assets:
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={asset_version(name)}"')
    return html

"""
modules/zip_builder.py
----------------------
Downloads product images from remote URLs and packages them into a ZIP.

Design:
  - Uses concurrent.futures.ThreadPoolExecutor for parallel downloads
  - Writes directly into a BytesIO ZIP (no temp files on disk)
  - Skips broken / unreachable URLs silently
  - Deduplicates by URL
  - Returns (BytesIO, stats_dict)
"""

import io
import os
import re
import sys
import zipfile
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Image URL resolver (same logic as img_url_filter in app.py) ───────────────

def resolve_image_url(path: str) -> str:
    """Convert a DB image path to a full URL."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    clean = path.lstrip("/")
    if clean.startswith("uploads/"):
        return f"https://novxcloud.com/{clean}"
    if clean.startswith("img/"):
        return f"https://novxcloud.com/uploads/{clean}"
    if "/" not in clean:
        return f"https://novxcloud.com/uploads/img/{clean}"
    return f"https://novxcloud.com/{clean}"


def _safe_filename(product_name: str, url: str, index: int) -> str:
    """
    Build a safe ZIP entry filename from product name + original extension.
    e.g. "China Hookah Small_001.jpg"
    """
    # Get extension from URL
    ext = os.path.splitext(url.split("?")[0])[-1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"

    # Sanitize product name
    safe_name = re.sub(r'[^\w\s-]', '', product_name).strip()
    safe_name = re.sub(r'\s+', '_', safe_name)[:60]
    return f"{safe_name}_{index:03d}{ext}"


def _download_one(item: Dict) -> Tuple[str, bytes | None, str]:
    """
    Download a single image.
    Returns (filename, bytes_or_None, error_message)
    """
    url      = item["url"]
    filename = item["filename"]

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 FuzzySearch/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        return filename, data, ""
    except urllib.error.HTTPError as e:
        return filename, None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return filename, None, f"URL error: {e.reason}"
    except Exception as e:
        return filename, None, str(e)


def build_zip(products: List[Dict], max_workers: int = 8) -> Tuple[io.BytesIO, Dict]:
    """
    Download images for the given products and return a ZIP in memory.

    Parameters
    ----------
    products : list of dicts, each with keys:
        id          — product id
        name        — product name
        image       — raw DB image path
        main_image  — raw DB main_image path (preferred)

    Returns
    -------
    (zip_buffer, stats)
        zip_buffer — io.BytesIO ready to send
        stats      — {"total": n, "downloaded": n, "skipped": n, "errors": [...]}
    """
    # ── Build download list (deduplicated by URL) ─────────────────────────────
    seen_urls = set()
    download_items = []

    for idx, p in enumerate(products, start=1):
        raw_path = p.get("main_image") or p.get("image") or ""
        url = resolve_image_url(raw_path)

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        download_items.append({
            "url":      url,
            "filename": _safe_filename(p.get("name", f"product_{idx}"), url, idx),
            "name":     p.get("name", ""),
        })

    stats = {
        "total":      len(download_items),
        "downloaded": 0,
        "skipped":    0,
        "errors":     [],
    }

    # ── Download concurrently ─────────────────────────────────────────────────
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_download_one, item): item
                for item in download_items
            }

            for future in as_completed(futures):
                filename, data, error = future.result()
                if data:
                    zf.writestr(filename, data)
                    stats["downloaded"] += 1
                else:
                    stats["skipped"] += 1
                    stats["errors"].append({"file": filename, "error": error})

    zip_buffer.seek(0)
    return zip_buffer, stats

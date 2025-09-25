import os
import json
import uuid
import urllib.parse
from urllib.parse import urljoin, urlparse
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup, Tag, NavigableString
import logging
import requests

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# ======== Wix config (env) ========
WIX_API_KEY = os.getenv("WIX_API_KEY")
WIX_SITE_ID = os.getenv("WIX_SITE_ID")

# ========= Helpers =========
def generate_id():
    return str(uuid.uuid4())[:8]

def empty_paragraph():
    return {"type": "PARAGRAPH", "id": generate_id(), "nodes": [], "style": {}}

def wrap_image(url, alt=""):
    return {
        "type": "IMAGE",
        "id": generate_id(),
        "imageData": {
            "containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER", "textWrap": True},
            "image": {"src": {"url": url}, "metadata": {"altText": alt}}
        }
    }

def is_absolute_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return bool(p.scheme) and bool(p.netloc)
    except Exception:
        return False

def resolve_image_src(src: str, base_url: str | None, image_url_map: dict | None):
    if not src:
        return None
    if image_url_map and (src in image_url_map or os.path.basename(src) in image_url_map):
        return image_url_map.get(src) or image_url_map.get(os.path.basename(src))
    if is_absolute_url(src):
        return src
    if base_url:
        return urljoin(base_url, src)
    return src

# ========= Wix upload =========
def wix_import_file_by_url(file_url: str, display_name: str = None):
    if not WIX_API_KEY or not WIX_SITE_ID:
        raise RuntimeError("Missing WIX_API_KEY or WIX_SITE_ID")

    url = "https://www.wixapis.com/site-media/v1/files/import"
    headers = {
        "Authorization": WIX_API_KEY,
        "wix-site-id": WIX_SITE_ID,
        "Content-Type": "application/json",
    }
    payload = {"url": file_url}
    if display_name:
        payload["displayName"] = display_name

    r = requests.post(url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()

    if "files" in data and data["files"]:
        return data["files"][0].get("url")
    return None

def build_wix_image_map_from_html(html_string: str, base_url: str | None):
    soup = BeautifulSoup(html_string, "html.parser")
    final_map = {}
    for im in soup.find_all("img"):
        src = im.get("src")
        if not src:
            continue
        resolved = resolve_image_src(src, base_url, None)
        if not resolved:
            continue
        try:
            wix_url = wix_import_file_by_url(resolved, display_name=os.path.basename(src))
            if wix_url:
                final_map[src] = wix_url
                final_map[os.path.basename(src)] = wix_url
        except Exception as e:
            logging.error(f"Wix upload failed for {src}: {e}")
    return final_map

# ========= HTML → Ricos JSON (simplified) =========
def html_string_to_ricos(html_string: str, base_url=None, image_url_map=None):
    soup = BeautifulSoup(html_string, "html.parser")
    body = soup.body or soup
    nodes = []

    for elem in body.find_all(recursive=True):
        if elem.name == "img" and elem.get("src"):
            resolved = resolve_image_src(elem["src"], base_url, image_url_map)
            if resolved:
                nodes.append(wrap_image(resolved, elem.get("alt", "")))

    return {"nodes": nodes}

# ========= HTTP endpoint =========
@app.route("/convert-html", methods=["POST"])
def convert_html():
    content_type = request.headers.get("Content-Type", "")
    try:
        payload = {}

        if "application/json" in content_type:
            payload = request.get_json(silent=True) or {}
        else:
            raw = request.data.decode("utf-8", errors="ignore").strip()
            if raw.startswith("<html") or raw.lower().startswith("<!doctype"):
                payload = {"html": raw}
            else:
                payload = request.form.to_dict()

        html_string = payload.get("html")
        base_url = payload.get("base_url")
        image_url_map = payload.get("image_url_map") or {}
        wix_upload = bool(payload.get("wix_upload", False))

        if not html_string:
            return jsonify({"error": "Missing 'html' field"}), 400

        if wix_upload:
            wix_map = build_wix_image_map_from_html(html_string, base_url)
            image_url_map.update(wix_map)

        result = html_string_to_ricos(html_string, base_url=base_url, image_url_map=image_url_map)
        return jsonify(result)

    except Exception as e:
        logging.exception("Error converting HTML")
        return jsonify({"error": "Failed", "details": str(e)}), 500

@app.route("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

# app.py
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

# ========= Ricos helpers =========

def generate_id():
    return str(uuid.uuid4())[:8]

def empty_paragraph():
    return {"type": "PARAGRAPH", "id": generate_id(), "nodes": [], "style": {}}

def format_decorations(is_bold=False, is_link=False, link_url=None, is_underline=False):
    dec = []
    if is_bold or is_link:
        dec.append({"type": "BOLD", "fontWeightValue": 700})
    dec.append({
        "type": "COLOR",
        "colorData": {"foreground": "#084EBD" if is_link else "rgb(0, 0, 0)", "background": "transparent"}
    })
    if is_link and link_url:
        dec.append({
            "type": "LINK",
            "linkData": {
                "link": {"url": link_url, "target": "BLANK", "rel": {"noreferrer": True}}
            }
        })
    if is_underline:
        dec.append({"type": "UNDERLINE"})
    return dec

def build_text_node(text, bold=False, link=None, underline=False, extra_decorations=None):
    decorations = format_decorations(bold, bool(link), link, underline)
    if extra_decorations:
        decorations.extend(extra_decorations)
    return {"type": "TEXT", "id": "", "textData": {"text": text, "decorations": decorations}}

def wrap_paragraph_nodes(nodes):
    return {"type": "PARAGRAPH", "id": generate_id(), "nodes": nodes, "style": {}}

def wrap_heading(text, level=2):
    decorations = []
    if level == 3:
        decorations.append({"type": "FONT_SIZE", "fontSizeData": {"unit": "PX", "value": 22}})
    return {
        "type": "HEADING",
        "id": generate_id(),
        "nodes": [build_text_node(text, bold=True, extra_decorations=decorations)],
        "style": {},
        "headingData": {"level": level, "textStyle": {"textAlignment": "AUTO"}}
    }

def wrap_list(items, ordered=False):
    return {
        "type": "ORDERED_LIST" if ordered else "BULLETED_LIST",
        "id": generate_id(),
        "nodes": [{
            "type": "LIST_ITEM",
            "id": generate_id(),
            "nodes": [{
                "type": "PARAGRAPH",
                "id": generate_id(),
                "nodes": item,
                "style": {"paddingTop": "0px", "paddingBottom": "0px"},
                "paragraphData": {"textStyle": {"lineHeight": "2"}}
            }]
        } for item in items]
    }

def wrap_table(table_data):
    num_rows = len(table_data)
    num_cols = max(len(row) for row in table_data) if table_data else 0
    return {
        "type": "TABLE",
        "id": generate_id(),
        "nodes": [{
            "type": "TABLE_ROW",
            "id": generate_id(),
            "nodes": [{
                "type": "TABLE_CELL",
                "id": generate_id(),
                "nodes": [wrap_paragraph_nodes([
                    build_text_node(node["textData"]["text"])
                    for node in cell if node.get("type") == "TEXT"
                ])],
                "tableCellData": {"cellStyle": {}}
            } for cell in row]
        } for row in table_data],
        "tableData": {
            "dimensions": {
                "colsWidthRatio": [754] * num_cols,
                "rowsHeight": [47] * num_rows,
                "colsMinWidth": [120] * num_cols
            }
        }
    }

def wrap_image(url, alt=""):
    return {
        "type": "IMAGE",
        "id": generate_id(),
        "imageData": {
            "containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER", "textWrap": True},
            "image": {"src": {"url": url}, "metadata": {"altText": alt}}
        }
    }

# ========= URL resolution =========

def is_absolute_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return bool(p.scheme) and bool(p.netloc)
    except Exception:
        return False

def resolve_image_src(src: str, base_url: str | None, image_url_map: dict | None, images_fifo: list | None):
    if not src:
        return None
    if image_url_map:
        if src in image_url_map:
            return image_url_map[src]
        base = os.path.basename(src)
        if base in image_url_map:
            return image_url_map[base]
    if images_fifo is not None and len(images_fifo) > 0:
        return images_fifo.pop(0)
    if is_absolute_url(src):
        return src
    if base_url:
        return urljoin(base_url, src)
    return src

# ========= Wix helpers =========

def wix_import_file_by_url(file_url: str, display_name: str = None, parent_folder_id: str = None, mime_type: str = None):
    if not WIX_API_KEY or not WIX_SITE_ID:
        raise RuntimeError("WIX_API_KEY and WIX_SITE_ID must be set in environment to upload to Wix.")
    url = "https://www.wixapis.com/site-media/v1/files/import"
    headers = {
        "Authorization": WIX_API_KEY,
        "wix-site-id": WIX_SITE_ID,
        "Content-Type": "application/json",
    }
    payload = {"url": file_url}
    if display_name:
        payload["displayName"] = display_name
    if parent_folder_id:
        payload["parentFolderId"] = parent_folder_id
    if mime_type:
        payload["mimeType"] = mime_type
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()

def _pick_wix_url_and_id(resp: dict):
    candidates = []
    if isinstance(resp, dict):
        if isinstance(resp.get("files"), list):
            candidates = resp["files"]
        elif isinstance(resp.get("uploadedFiles"), list):
            candidates = resp["uploadedFiles"]
        elif isinstance(resp.get("file"), dict):
            candidates = [resp["file"]]
        else:
            candidates = [resp]
    elif isinstance(resp, list):
        candidates = resp
    url = None
    file_id = None
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        if not url:
            for k in ("url", "mediaUrl", "fileUrl", "path"):
                if obj.get(k):
                    url = obj[k]
                    break
        if not file_id:
            for k in ("id", "_id", "mediaId", "documentId"):
                if obj.get(k):
                    file_id = obj[k]
                    break
        if url and file_id:
            break
    return url, file_id

def build_wix_image_map_from_html(html_string: str, base_url: str | None, provided_map: dict | None, parent_folder_id: str | None):
    soup = BeautifulSoup(html_string, "html.parser")
    seen = set()
    ordered_imgs = []
    for im in soup.find_all("img"):
        src = im.get("src")
        if not src:
            continue
        base = os.path.basename(src)
        key = (src, base)
        if key in seen:
            continue
        seen.add(key)
        ordered_imgs.append((src, base))
    final_map = {}
    uploads = []
    for src, base in ordered_imgs:
        source_url = provided_map.get(src) or provided_map.get(base) if provided_map else None
        if not source_url:
            if is_absolute_url(src):
                source_url = src
            elif base_url:
                source_url = urljoin(base_url, src)
        if not source_url:
            continue
        try:
            resp = wix_import_file_by_url(source_url, display_name=base, parent_folder_id=parent_folder_id)
            wix_url, wix_id = _pick_wix_url_and_id(resp)
            if wix_url:
                final_map[src] = wix_url
                final_map[base] = wix_url
                uploads.append({"name": base, "originalSrc": src, "wixUrl": wix_url, "wixId": wix_id})
            else:
                final_map[src] = source_url
                final_map[base] = source_url
        except Exception as e:
            final_map[src] = source_url
            final_map[base] = source_url
            uploads.append({"name": base, "originalSrc": src, "error": str(e)})
    return final_map, uploads

# ========= HTML -> Ricos =========

def extract_parts(tag, bold_class, base_url, image_url_map, images_fifo):
    parts = []
    for item in tag.children:
        if isinstance(item, NavigableString):
            txt = str(item)
            if txt.strip():
                parts.append(build_text_node(txt))
        elif isinstance(item, Tag):
            if item.name == "img" and item.get("src"):
                resolved = resolve_image_src(item["src"], base_url, image_url_map, images_fifo)
                if resolved:
                    parts.append(wrap_image(resolved, item.get("alt", "")))
            elif item.name == "a" and item.get("href"):
                href = urllib.parse.unquote(item["href"])
                parts.append(build_text_node(item.get_text(), link=href, underline=True))
            else:
                parts.extend(extract_parts(item, bold_class, base_url, image_url_map, images_fifo))
    return parts

def html_string_to_ricos(html_string: str, base_url: str | None = None,
                         image_url_map: dict | None = None,
                         images_fifo: list | None = None):
    soup = BeautifulSoup(html_string, "html.parser")
    body = soup.body or soup  # <-- აქ ვიღებთ მთელ დოკუმენტს, თუ body არაა
    nodes = []
    for elem in body.find_all(recursive=False):
        tag = elem.name
        if tag == "img" and elem.get("src"):
            resolved = resolve_image_src(elem["src"], base_url, image_url_map, images_fifo)
            if resolved:
                nodes.append(wrap_image(resolved, elem.get("alt", "")))
        elif tag in ["h1", "h2", "h3", "h4"]:
            level = int(tag[1])
            txt = elem.get_text(strip=True)
            if txt:
                nodes.append(wrap_heading(txt, level))
        elif tag == "p":
            imgs = elem.find_all("img", recursive=False)
            if imgs:
                for im in imgs:
                    resolved = resolve_image_src(im["src"], base_url, image_url_map, images_fifo)
                    if resolved:
                        nodes.append(wrap_image(resolved, im.get("alt", "")))
            else:
                parts = extract_parts(elem, None, base_url, image_url_map, images_fifo)
                if parts:
                    nodes.append(wrap_paragraph_nodes(parts))
    return {"nodes": nodes}

# ========= HTTP endpoint =========

@app.route("/convert-html", methods=["POST"])
def convert_html():
    try:
        content_type = request.headers.get("Content-Type", "")
        payload = {}
        if "application/json" in content_type:
            payload = request.get_json(silent=True) or {}
            html_string = payload.get("html")
            base_url = payload.get("base_url")
            image_url_map = payload.get("image_url_map") or {}
            images_fifo = payload.get("images") or None
            wix_upload = bool(payload.get("wix_upload", False))
            wix_parent_folder_id = payload.get("wix_parent_folder_id")
        else:
            raw = request.data.decode("utf-8", errors="ignore").strip()
            if raw.startswith("<html") or raw.lower().startswith("<!doctype"):
                html_string = raw
                base_url = None
                image_url_map = {}
                images_fifo = None
                wix_upload = False
                wix_parent_folder_id = None
            else:
                html_string = request.form.get("html")
                base_url = request.form.get("base_url")
                image_url_map = json.loads(request.form.get("image_url_map", "{}"))
                images_fifo = json.loads(request.form.get("images", "[]"))
                wix_upload = request.form.get("wix_upload") in ("1", "true", "True")
                wix_parent_folder_id = request.form.get("wix_parent_folder_id")

        if not html_string:
            return jsonify({"error": "Missing 'html' field with HTML content"}), 400

        wix_diag = None
        if wix_upload:
            wix_map, uploads = build_wix_image_map_from_html(
                html_string, base_url=base_url,
                provided_map=image_url_map, parent_folder_id=wix_parent_folder_id
            )
            image_url_map = wix_map
            wix_diag = {"uploaded": uploads}

        result = html_string_to_ricos(
            html_string,
            base_url=base_url,
            image_url_map=image_url_map,
            images_fifo=list(images_fifo) if images_fifo else None
        )
        if wix_diag:
            result["wix"] = wix_diag
        return jsonify(result)

    except Exception as e:
        logging.exception("Error converting HTML")
        return jsonify({"error": "Failed to convert HTML", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

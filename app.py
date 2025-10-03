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

# ======== Wix Config (აუცილებელია დააყენოთ გარემოს ცვლადები) ========
WIX_API_KEY = os.getenv("WIX_API_KEY")
WIX_SITE_ID = os.getenv("WIX_SITE_ID")

# =========================
# Helpers
# =========================

def generate_id():
    return str(uuid.uuid4())[:8]

def empty_paragraph():
    return {"type": "PARAGRAPH", "id": generate_id(), "nodes": [], "style": {}}

def format_decorations(is_bold=False, is_link=False, link_url=None, is_underline=False):
    dec = []
    if is_bold or is_link:
        dec.append({"type": "BOLD", "fontWeightValue": 700})
    if is_link:
        dec.append({"type": "COLOR", "colorData": {"foreground": "#3A11AE", "background": "transparent"}})
    else:
        dec.append({"type": "COLOR", "colorData": {"foreground": "rgb(0, 0, 0)", "background": "transparent"}})
    if is_link and link_url:
        dec.append({
            "type": "LINK",
            "linkData": {"link": {"url": link_url, "target": "BLANK", "rel": {"noreferrer": True}}}
        })
    if is_underline:
        dec.append({"type": "UNDERLINE"})
    return dec

def build_text_node(text, bold=False, link=None, underline=False, extra_decorations=None):
    decorations = format_decorations(bold, bool(link), link, underline)
    if extra_decorations:
        decorations.extend([d for d in extra_decorations if d])
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
        "nodes": [
            {"type": "LIST_ITEM", "id": generate_id(), "nodes": [
                {"type": "PARAGRAPH", "id": generate_id(), "nodes": item,
                 "style": {"paddingTop": "0px", "paddingBottom": "0px"},
                 "paragraphData": {"textStyle": {"lineHeight": "2"}}}
            ]} for item in items
        ]
    }

def wrap_table(table_data):
    num_rows = len(table_data)
    num_cols = max(len(row) for row in table_data) if table_data else 0
    highlight_style = {"verticalAlignment": "TOP", "backgroundColor": "#CAB8FF"}
    return {
        "type": "TABLE",
        "id": generate_id(),
        "nodes": [
            {"type": "TABLE_ROW", "id": generate_id(), "nodes": [
                {"type": "TABLE_CELL", "id": generate_id(), "nodes": [
                    wrap_paragraph_nodes([
                        build_text_node(
                            node["textData"]["text"],
                            extra_decorations=[{"type": "FONT_SIZE", "fontSizeData": {"unit": "PX", "value": 16}}]
                            if r_idx > 0 and c_idx > 0 else None
                        ) for node in cell if node["type"] == "TEXT"
                    ])
                ],
                 "tableCellData": {"cellStyle": highlight_style if r_idx == 0 or c_idx == 0 else {}}}
                for c_idx, cell in enumerate(row)
            ]} for r_idx, row in enumerate(table_data)
        ],
        "tableData": {"dimensions": {
            "colsWidthRatio": [754] * num_cols,
            "rowsHeight": [47] * num_rows,
            "colsMinWidth": [120] * num_cols
        }}
    }
    
def wrap_image(wix_image_data, alt=""):
    """Creates a valid Ricos image node from data returned by the Wix API."""
    if not wix_image_data or not wix_image_data.get("id"):
        return None

    src_obj = {
        "id": wix_image_data["id"],
        "file_name": wix_image_data.get("file_name", "image.jpg")
    }
    
    if "width" in wix_image_data and "height" in wix_image_data:
        src_obj["width"] = wix_image_data["width"]
        src_obj["height"] = wix_image_data["height"]

    return {
        "type": "IMAGE",
        "id": generate_id(),
        "nodes": [],
        "imageData": {
            "containerData": {
                "width": {"size": "CONTENT"},
                "alignment": "CENTER",
                "textWrap": True
            },
            "image": {
                "src": src_obj,
                "metadata": {"altText": alt}
            }
        }
    }

def is_absolute_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return bool(p.scheme) and bool(p.netloc)
    except Exception:
        return False
        
def wix_import_file_by_url(file_url: str, display_name: str = None):
    if not WIX_API_KEY or not WIX_SITE_ID:
        raise RuntimeError("WIX_API_KEY and WIX_SITE_ID must be set.")

    url = "https://www.wixapis.com/site-media/v1/files/import"
    headers = {
        "Authorization": WIX_API_KEY,
        "wix-site-id": WIX_SITE_ID,
        "Content-Type": "application/json",
    }
    payload = {"url": file_url}
    if display_name:
        payload["displayName"] = display_name

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        
        file_data = r.json().get("file", {})
        media_info = file_data.get("media", {})
        
        return {
            "id": file_data.get("id"),
            "file_name": file_data.get("displayName", "image.jpg"),
            "width": media_info.get("width"),
            "height": media_info.get("height"),
        }
    except requests.RequestException as e:
        logging.error("Wix import request failed for URL %s: %s", file_url, e)
        return None

def build_wix_image_map_from_html(html_string, base_url):
    soup = BeautifulSoup(html_string, "html.parser")
    image_map = {}
    seen_urls = set()

    for im in soup.find_all("img"):
        src = im.get("src")
        if not src:
            continue

        source_url = None
        if is_absolute_url(src):
            source_url = src
        elif base_url:
            source_url = urljoin(base_url, src)

        if not source_url or source_url in seen_urls:
            continue
        
        seen_urls.add(source_url)
        
        logging.info("Uploading image to Wix from URL: %s", source_url)
        wix_data = wix_import_file_by_url(source_url, display_name=os.path.basename(src))
        
        if wix_data and wix_data.get("id"):
            image_map[src] = wix_data
        else:
            logging.warning("Failed to upload or get data for image: %s", src)

    return image_map


# =========================
# HTML → Ricos
# =========================

def html_to_ricos(html_string, base_url=None, image_url_map=None):
    soup = BeautifulSoup(html_string, "html.parser")
    body = soup.body or soup
    nodes = []
    bold_class = None

    style_tag = soup.find("style")
    if style_tag and style_tag.string:
        for ln in style_tag.string.split("}"):
            if "font-weight:700" in ln:
                cls = ln.split("{")[0].strip()
                if cls.startswith("."):
                    bold_class = cls[1:]
                    break

    def add_node(node, block_type, prev_type=None):
        if node is None:
            return prev_type
        # Spacing logic...
        nodes.append(node)
        return block_type

    def extract_parts(tag):
        parts = []
        for item in tag.children:
            if isinstance(item, NavigableString):
                txt = str(item)
                if txt.strip():
                    is_bold = item.parent.name == "span" and bold_class and bold_class in item.parent.get("class", [])
                    parts.append(build_text_node(txt, bold=is_bold))
            elif isinstance(item, Tag):
                if item.name in ["br", "img"]:
                    continue
                if item.name == "a" and item.get("href"):
                    href = urllib.parse.unquote(item["href"].split("q=")[-1].split("&")[0]) if "google.com/url?q=" in item["href"] else urllib.parse.unquote(item["href"])
                    is_bold = any(c.name == "span" and bold_class and bold_class in c.get("class", []) for c in item.descendants if isinstance(c, Tag))
                    parts.append(build_text_node(item.get_text(), bold=is_bold, link=href, underline=True))
                else:
                    parts.extend(extract_parts(item))
        return parts

    prev = None
    for elem in body.find_all(recursive=False):
        tag = elem.name
        
        if tag == "img" and elem.get("src"):
            wix_data = image_url_map.get(elem["src"])
            prev = add_node(wrap_image(wix_data, elem.get("alt", "")), "IMAGE", prev)
        elif tag in ["h1", "h2", "h3", "h4"]:
            level = int(tag[1])
            for im in elem.find_all("img"):
                wix_data = image_url_map.get(im["src"])
                prev = add_node(wrap_image(wix_data, im.get("alt", "")), "IMAGE", prev)
                im.decompose()
            txt = elem.get_text(strip=True)
            if txt:
                prev = add_node(wrap_heading(txt, level), f"H{level}", prev)
        elif tag == "p":
            for im in elem.find_all("img"):
                wix_data = image_url_map.get(im["src"])
                prev = add_node(wrap_image(wix_data, im.get("alt", "")), "IMAGE", prev)
                im.decompose()
            parts = extract_parts(elem)
            if parts:
                prev = add_node(wrap_paragraph_nodes(parts), "PARAGRAPH", prev)
        elif tag in ["ul", "ol"]:
            items = [extract_parts(li) for li in elem.find_all("li", recursive=False) if li]
            if items:
                prev = add_node(wrap_list(items, ordered=(tag == "ol")), "ORDERED_LIST" if tag == "ol" else "BULLETED_LIST", prev)
        elif tag == "table":
            table = [[extract_parts(td) for td in tr.find_all(["td", "th"])] for tr in elem.find_all("tr")]
            if table:
                prev = add_node(wrap_table(table), "TABLE", prev)
    
    # ... Simplified spacing for clarity
    
    return {"nodes": nodes}

# =========================
# Flask Endpoint
# =========================

@app.route("/convert-html", methods=["POST"])
def convert_html():
    data = request.get_json()
    html_string = data.get("html")
    base_url = data.get("base_url")
    wix_upload = data.get("wix_upload", False)

    if not html_string:
        return jsonify({"error": "Missing 'html' in request body"}), 400

    image_url_map = {}
    if wix_upload:
        try:
            image_url_map = build_wix_image_map_from_html(html_string, base_url)
        except Exception as e:
            logging.exception("Wix image upload process failed.")
            return jsonify({"error": "Failed during Wix image upload", "details": str(e)}), 500
    else:
        # Fallback to manually provided map if not uploading
        image_url_map = data.get("image_url_map", {})

    result = html_to_ricos(
        html_string,
        base_url=base_url,
        image_url_map=image_url_map
    )
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

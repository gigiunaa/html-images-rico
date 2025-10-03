import os
import json
import uuid
import urllib.parse
from urllib.parse import urljoin
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup, Tag, NavigableString
import logging

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

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
    return {"type": "TEXT", "id": generate_id(), "textData": {"text": text, "decorations": decorations}}

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

def _normalize_img_obj(obj):
    raw_media_id_source = None
    width = None
    height = None
    file_name = "image.jpg"

    if isinstance(obj, dict):
        raw_media_id_source = obj.get("id") or obj.get("ID") or obj.get("mediaId")
        width = obj.get("width")
        height = obj.get("height")
        file_name = obj.get("name", file_name)
    elif isinstance(obj, str):
        raw_media_id_source = obj
    
    if not raw_media_id_source:
        return None

    raw_media_id = raw_media_id_source
    if "static.wixstatic.com/media/" in raw_media_id:
        try:
            raw_media_id = raw_media_id.split('/media/')[1].split('/')[0]
        except IndexError:
            return None
    
    if not width or not height:
        width, height = 800, 600
        logging.warning("Using placeholder dimensions (800x600) for image ID %s.", raw_media_id)

    return {"id": raw_media_id, "width": int(width), "height": int(height), "file_name": file_name}

def wrap_image(img_obj, alt=""):
    norm = _normalize_img_obj(img_obj)
    if not norm:
        return None

    src_obj = {
        "id": norm["id"],
        "width": norm["width"],
        "height": norm["height"],
        "file_name": norm.get("file_name", "image.jpg")
    }

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

def resolve_image_src(src: str, image_url_map: dict | None):
    if not src or not image_url_map:
        return None
    
    if src in image_url_map:
        return image_url_map[src]
    base = os.path.basename(src)
    if base in image_url_map:
        return image_url_map[base]

    return None

def extract_parts(tag, bold_class):
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
            elif item.name == "a" and item.get("href"):
                href = item["href"]
                if "google.com/url?q=" in href:
                    href = urllib.parse.unquote(href.split("q=")[1].split("&")[0])
                else:
                    href = urllib.parse.unquote(href)
                is_bold = any(
                    child.name == "span" and bold_class and bold_class in child.get("class", [])
                    for child in item.descendants if isinstance(child, Tag)
                )
                parts.append(build_text_node(item.get_text(), bold=is_bold, link=href, underline=True))
            else:
                parts.extend(extract_parts(item, bold_class))
    return parts

# =========================
# HTML → Ricos
# =========================

def html_to_ricos(html_string, image_url_map=None):
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

    for elem in body.find_all(recursive=False):
        tag = elem.name
        
        def add_node_with_spacing(node):
            if not node: return
            if nodes and nodes[-1]['type'] != 'PARAGRAPH' and node['type'] != 'PARAGRAPH':
                nodes.append(empty_paragraph())
            nodes.append(node)

        if tag == "img" and elem.get("src"):
            img_obj = resolve_image_src(elem["src"], image_url_map)
            add_node_with_spacing(wrap_image(img_obj, elem.get("alt", "")))
        elif tag in ["h1", "h2", "h3", "h4"]:
            level = int(tag[1])
            for im in elem.find_all("img"):
                img_obj = resolve_image_src(im["src"], image_url_map)
                add_node_with_spacing(wrap_image(img_obj, im.get("alt", "")))
                im.decompose()
            txt = elem.get_text(strip=True)
            if txt:
                add_node_with_spacing(wrap_heading(txt, level))
        elif tag == "p":
            for im in elem.find_all("img"):
                img_obj = resolve_image_src(im["src"], image_url_map)
                add_node_with_spacing(wrap_image(img_obj, im.get("alt", "")))
                im.decompose()
            parts = extract_parts(elem, bold_class)
            if parts:
                add_node_with_spacing(wrap_paragraph_nodes(parts))
        elif tag in ["ul", "ol"]:
            items = [extract_parts(li, bold_class) for li in elem.find_all("li", recursive=False) if li.get_text(strip=True)]
            if items:
                add_node_with_spacing(wrap_list(items, ordered=(tag == "ol")))
        elif tag == "table":
            table = [[extract_parts(td, bold_class) for td in tr.find_all(["td", "th"])] for tr in elem.find_all("tr")]
            if table:
                add_node_with_spacing(wrap_table(table))

    return {"nodes": nodes}

# =========================
# Flask Endpoint
# =========================

def preprocess_image_url_map(image_map: dict):
    if not image_map:
        return {}
    
    processed_map = {}
    for key, value in image_map.items():
        if isinstance(value, str):
            # FIX: Extract Media ID from full URL
            if "static.wixstatic.com/media/" in value:
                try:
                    media_id = value.split('/media/')[1].split('/')[0]
                    processed_map[key] = {"id": media_id}
                except IndexError:
                    # If URL is malformed, skip it
                    logging.warning("Skipping malformed Wix URL for key '%s': %s", key, value)
                    continue
            else:
                # If it's not a Wix URL, assume it's a raw ID
                processed_map[key] = {"id": value}
        elif isinstance(value, dict) and "id" in value:
            # Value is already an object, which is the preferred format
            processed_map[key] = value
    
    return processed_map

@app.route("/convert-html", methods=["POST"])
def convert_html():
    data = request.get_json()

    html_string = data.get("html")
    if not html_string:
        return jsonify({"error": "Missing 'html' in request body"}), 400

    image_url_map = data.get("image_url_map") or {}
    processed_map = preprocess_image_url_map(image_url_map)

    result = html_to_ricos(
        html_string,
        image_url_map=processed_map
    )
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

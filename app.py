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
            ]}
            for r_idx, row in enumerate(table_data)
        ],
        "tableData": {"dimensions": {
            "colsWidthRatio": [754] * num_cols,
            "rowsHeight": [47] * num_rows,
            "colsMinWidth": [120] * num_cols
        }}
    }


def wrap_image(url, alt=""):
    return {"type": "IMAGE", "id": generate_id(),
            "imageData": {"containerData": {"width": {"size": "CONTENT"}, "alignment": "CENTER", "textWrap": True},
                          "image": {"src": {"url": url}, "metadata": {"altText": alt}}}}


def is_absolute_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://") or url.startswith("//")


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


def apply_spacing(nodes, block_type):
    before = {"H2": 2, "H3": 1, "H4": 1, "ORDERED_LIST": 1, "BULLETED_LIST": 1, "PARAGRAPH": 1, "IMAGE": 1}
    after = {"H2": 1, "H3": 1, "H4": 1, "ORDERED_LIST": 1, "BULLETED_LIST": 1, "PARAGRAPH": 1, "IMAGE": 1, "TABLE": 2}
    return before.get(block_type, 0), after.get(block_type, 0)


def count_trailing_empty_paragraphs(nodes):
    cnt = 0
    for n in reversed(nodes):
        if n["type"] == "PARAGRAPH" and not n["nodes"]:
            cnt += 1
        else:
            break
    return cnt


def ensure_spacing(nodes, required):
    current = count_trailing_empty_paragraphs(nodes)
    while current < required:
        nodes.append(empty_paragraph()); current += 1
    while current > required:
        nodes.pop(); current -= 1


def extract_parts(tag, bold_class, base_url, image_url_map, images_fifo):
    parts = []
    for item in tag.children:
        if isinstance(item, NavigableString):
            txt = str(item)
            if txt.strip():
                is_bold = item.parent.name == "span" and bold_class and bold_class in item.parent.get("class", [])
                parts.append(build_text_node(txt, bold=is_bold))
        elif isinstance(item, Tag):
            if item.name == "br":
                continue
            if item.name == "img" and item.get("src"):
                url = resolve_image_src(item["src"], base_url, image_url_map, images_fifo)
                if url:
                    parts.append(wrap_image(url, item.get("alt", "")))
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
                parts.extend(extract_parts(item, bold_class, base_url, image_url_map, images_fifo))
    return parts


# =========================
# HTML → Ricos
# =========================

def html_to_ricos(html_path, base_url=None, image_url_map=None, images_fifo=None):
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

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
        b, a = apply_spacing(nodes, block_type)
        if block_type == "H2" and prev_type == "IMAGE":
            b = 1
        ensure_spacing(nodes, b)
        nodes.append(node)
        needed = a - count_trailing_empty_paragraphs(nodes)
        for _ in range(max(0, needed)):
            nodes.append(empty_paragraph())
        return block_type

    prev = None
    for elem in body.find_all(recursive=False):
        tag = elem.name
        if tag == "img" and elem.get("src"):
            url = resolve_image_src(elem["src"], base_url, image_url_map, images_fifo)
            if url:
                prev = add_node(wrap_image(url, elem.get("alt", "")), "IMAGE", prev)
        elif tag in ["h2", "h3", "h4"]:
            level = int(tag[1])
            for im in elem.find_all("img"):
                u = resolve_image_src(im["src"], base_url, image_url_map, images_fifo)
                if u:
                    prev = add_node(wrap_image(u, im.get("alt", "")), "IMAGE", prev)
            txt = elem.get_text(strip=True)
            if txt:
                prev = add_node(wrap_heading(txt, level), f"H{level}", prev)
        elif tag == "p":
            imgs = elem.find_all("img", recursive=False)
            if imgs:
                for im in imgs:
                    u = resolve_image_src(im["src"], base_url, image_url_map, images_fifo)
                    if u:
                        prev = add_node(wrap_image(u, im.get("alt", "")), "IMAGE", prev)
            else:
                parts = extract_parts(elem, bold_class, base_url, image_url_map, images_fifo)
                if parts:
                    prev = add_node(wrap_paragraph_nodes(parts), "PARAGRAPH", prev)
        elif tag in ["ul", "ol"]:
            items = [extract_parts(li, bold_class, base_url, image_url_map, images_fifo)
                     for li in elem.find_all("li", recursive=False)]
            items = [i for i in items if i]
            if items:
                tp = "ORDERED_LIST" if tag == "ol" else "BULLETED_LIST"
                prev = add_node(wrap_list(items, ordered=(tag == "ol")), tp, prev)
        elif tag == "table":
            table = [
                [extract_parts(td, bold_class, base_url, image_url_map, images_fifo) for td in tr.find_all(["td", "th"])]
                for tr in elem.find_all("tr")
            ]
            if table:
                table_node = wrap_table(table)
                prev = add_node(table_node, "TABLE", prev)

    return {"nodes": nodes}


# =========================
# Flask Endpoint
# =========================

@app.route("/convert-html", methods=["POST"])
def convert_html():
    data = request.get_json()

    html_path = data.get("html_path")
    base_url = data.get("base_url")

    # JSON array → {filename -> wixUrl} map
    image_url_map = None
    if "uploaded_array" in data:
        uploaded = data["uploaded_array"]
        image_url_map = {item["name"]: item["data"] for item in uploaded}

    # fallback: პირდაპირ map-იც შეგიძლია მიაწოდო
    if not image_url_map and "image_url_map" in data:
        image_url_map = data["image_url_map"]

    images_fifo = data.get("images_fifo")

    result = html_to_ricos(
        html_path,
        base_url=base_url,
        image_url_map=image_url_map,
        images_fifo=images_fifo
    )
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

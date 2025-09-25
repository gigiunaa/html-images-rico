def _parse_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False

def _parse_json_field(payload, key, default):
    """
    იღებს ველს როგორც dict/list ან JSON-სტრინგს.
    შეცდომისას უბრუნებს default-ს.
    """
    val = payload.get(key, default)
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return default


@app.route("/convert-html", methods=["POST"])
def convert_html():
    """
    იღებს:
      • application/json: {"html": "<html>...</html>", "image_url_map": {...}, ...}
      • multipart/form-data ან x-www-form-urlencoded: ველები html, image_url_map, images, ...
      • text/html ან text/plain: ნედლი HTML სხეული (სურვილისამებრ შეგიძლიათ გამოიყენოთ ?base_url=... query-param)
    აბრუნებს: Ricos JSON-ს, სადაც <img> src-ები ჩანაცვლებულია image_url_map-ით (basename ან სრული გზა).
    """
    try:
        content_type = (request.headers.get("Content-Type") or "").lower()
        raw_body = request.get_data(cache=False, as_text=True) or ""
        payload = {}
        html_string = None

        # 1) JSON
        if "application/json" in content_type:
            payload = request.get_json(silent=True) or {}
            html_string = payload.get("html")
            # თუ html ვერ მოიტანეს, მაგრამ სხეული იწყება <html>-ით → ჩავთვალოთ ნედლი HTML
            if (not html_string or not str(html_string).strip()) and raw_body.lstrip().startswith("<"):
                html_string = raw_body

        # 2) multipart/form-data ან x-www-form-urlencoded
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            payload = request.form.to_dict()
            html_string = payload.get("html")
            # form ველებში JSON-სტრინგების განხილვა ქვემოთ ვქნათ (_parse_json_field-ით)

        # 3) text/html ან text/plain → ნედლი HTML სხეული
        else:
            if raw_body.lstrip().startswith("<"):
                html_string = raw_body
                # ancillary ველები optional-ად query-დან
                payload = {
                    "base_url": request.args.get("base_url"),
                    "wix_upload": request.args.get("wix_upload"),
                    "wix_parent_folder_id": request.args.get("wix_parent_folder_id"),
                    "image_url_map": request.args.get("image_url_map"),
                    "images": request.args.get("images"),
                }
            else:
                # fallback: ვცადოთ form
                payload = request.form.to_dict()
                html_string = payload.get("html")

        if not html_string or not str(html_string).strip():
            return jsonify({"error": "Missing 'html' field"}), 400

        # ველები
        base_url = payload.get("base_url")
        wix_upload = _parse_bool(payload.get("wix_upload"))
        wix_parent_folder_id = payload.get("wix_parent_folder_id")

        image_url_map = _parse_json_field(payload, "image_url_map", {})
        images_fifo  = _parse_json_field(payload, "images", None)

        # საჭიროების შემთხვევაში — ატვირთვა Wix-ზე და დაბრუნებული URL-ებით ჩანაცვლება
        wix_diag = None
        if wix_upload:
            wix_map, uploads = build_wix_image_map_from_html(
                html_string,
                base_url=base_url,
                provided_map=image_url_map,
                parent_folder_id=wix_parent_folder_id
            )
            image_url_map = wix_map
            wix_diag = {"uploaded": uploads}

        # HTML → Ricos
        result = html_string_to_ricos(
            str(html_string),
            base_url=base_url,
            image_url_map=image_url_map,
            images_fifo=list(images_fifo) if isinstance(images_fifo, list) else None
        )

        if wix_diag:
            result["wix"] = wix_diag

        return jsonify(result), 200

    except Exception as e:
        logging.exception("Error converting HTML")
        return jsonify({"error": "Failed to convert HTML", "details": str(e)}), 500

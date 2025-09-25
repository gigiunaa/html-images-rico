
# Wix Ricos Converter (HTML → Ricos JSON)

Flask service that converts an HTML file into Ricos JSON (Wix Rich Content). Images are resolved via:
1) `image_url_map` (filename → URL),
2) `images_fifo` (sequential),
3) absolute/relative fallback with `base_url`.

## Deploy on Render
- Use this repo/zip, or supply `render.yaml`.
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`

## API
`POST /convert-html`

**Body**
```json
{
  "html_path": "article.html",
  "base_url": "https://example.com",
  "uploaded_array": [
    {"name": "image1.png", "data": "https://static.wixstatic.com/media/...image1.png"},
    {"name": "image5.jpg", "data": "https://static.wixstatic.com/media/...image5.jpg"}
  ]
}
```

You can also pass `image_url_map` directly, or `images_fifo`:

```json
{
  "html_path": "article.html",
  "images_fifo": [
    "https://static.wixstatic.com/media/one.jpg",
    "https://static.wixstatic.com/media/two.jpg"
  ]
}
```

**Response**
```json
{ "nodes": [ /* Ricos nodes */ ] }
```

## Local run
```bash
pip install -r requirements.txt
python app.py
# POST to http://localhost:5000/convert-html
```

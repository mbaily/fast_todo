# html_tailwind

This folder contains a minimal Jinja-compatible `index.html` that uses Tailwind via the Play CDN for rapid prototyping.

How to serve this template from the existing FastAPI app:

1. Ensure `fastapi` and `jinja2` (or `aiofiles` if serving static files) are installed. FastAPI usually pulls `jinja2` via `python-multipart` or you can `pip install jinja2`.

2. In your FastAPI app (example snippet):

```py
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app = FastAPI()
templates = Jinja2Templates(directory="html_tailwind")

@app.get("/tailwind", response_class=HTMLResponse)
async def tailwind_index(request: Request):
    # pass template variables as needed
    return templates.TemplateResponse('index.html', {"request": request, "title": "Fast Todo Tailwind", "todos": []})
```

3. Tailwind is included with the CDN script tag in `index.html`. For production, generate a compiled CSS with Tailwind CLI / build pipeline and serve the generated CSS instead of the CDN.

4. To extend: add other Jinja templates, partials, or static assets in a `static/` directory and configure `StaticFiles` in FastAPI.

That's it — `index.html` is ready to be used as a starting point.

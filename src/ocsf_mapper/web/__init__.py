"""Local web UI for ocsf-mapper (Phase B).

FastAPI + Jinja2 + HTMX, file-backed, bound to 127.0.0.1.

    from ocsf_mapper.web import create_app
    app = create_app()                    # default mappings/, samples/, catalog.json
    app = create_app(root="/some/path")   # override the repo root

Or run via the CLI:

    ocsf-mapper serve [--port 8000] [--host 127.0.0.1]
"""

from ocsf_mapper.web.app import create_app

__all__ = ["create_app"]

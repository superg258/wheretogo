"""WSGI entrypoint for the Hugging Face Space Docker container.

gunicorn imports ``app`` from this module. We build the Flask app once at
import time so that announcement/ranking parsing happens during container
boot rather than on the first user request.
"""

from rmuc_analyzer.web import create_app

app = create_app("config/config.json")

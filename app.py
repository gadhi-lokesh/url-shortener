"""
app.py — Entry-point for the URL Shortener & Link Management System.

Run locally:
    python app.py

Production (gunicorn):
    gunicorn app:app --bind 0.0.0.0:8080

The Telegram bot (bot.py) is a separate process and is unaffected by this
Flask application.
"""

import os

from url_shortener import create_app

# Build the Flask application using the factory.
app = create_app()

if __name__ == "__main__":
    # Development server — do NOT use in production.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")

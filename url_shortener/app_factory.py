"""
app_factory.py — Flask application factory.

Keeps create_app() separate from run logic so tests can import the factory
without side-effects.
"""

import os

from flask import Flask

from .database import close_db, init_db
from .routes import bp


def create_app(test_config: dict = None) -> Flask:
    """
    Create and configure the Flask application.

    Parameters
    ----------
    test_config : dict, optional
        Configuration overrides used by the test suite (e.g. an in-memory
        or temporary database path).

    Returns
    -------
    Flask
        A fully configured application instance.
    """
    app = Flask(
        __name__,
        # Tell Flask to find templates inside url_shortener/templates/
        template_folder="templates",
    )

    # ------------------------------------------------------------------
    # Default configuration
    # ------------------------------------------------------------------
    app.config.from_mapping(
        # The SQLite file lives next to the project root by default.
        DATABASE=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),  # project root
            "instance",
            "url_shortener.db",
        ),
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-prod"),
        JSON_SORT_KEYS=False,
    )

    # ------------------------------------------------------------------
    # Override with test config if provided
    # ------------------------------------------------------------------
    if test_config is not None:
        app.config.update(test_config)

    # ------------------------------------------------------------------
    # Initialise the database schema (idempotent)
    # ------------------------------------------------------------------
    init_db(app)

    # ------------------------------------------------------------------
    # Register teardown: close DB connection after every request
    # ------------------------------------------------------------------
    app.teardown_appcontext(close_db)

    # ------------------------------------------------------------------
    # Register the main blueprint (all routes live there)
    # ------------------------------------------------------------------
    app.register_blueprint(bp)

    return app

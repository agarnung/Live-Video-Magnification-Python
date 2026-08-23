#!/usr/bin/env python3
"""
Application entry point.

    python main.py
"""

from ui.main_window import run_app

if __name__ == "__main__":
    # run_app() already calls sys.exit(app.exec()) internally.
    run_app()

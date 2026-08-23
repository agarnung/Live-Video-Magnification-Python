#!/usr/bin/env python3
"""
Punto de entrada de la aplicación.

    python main.py
"""

from ui.main_window import run_app

if __name__ == "__main__":
    # run_app() ya invoca sys.exit(app.exec()) internamente.
    run_app()

"""Launch the E4 234 Load Flow Simulator GUI.

Run from project root:
    python -m GUI.main
"""

import os
import sys
from pathlib import Path

# Matplotlib Qt backend
os.environ.setdefault("QT_API", "pyside6")

# Add project root so both GUI and NR packages are importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from GUI.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
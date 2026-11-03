"""Launch the E4 234 Load Flow Simulator GUI:   python main.py"""
import os
import sys

os.environ.setdefault("QT_API", "pyside6")           # for matplotlib's Qt backend
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # makes `engine` and `app` importable

from PySide6.QtWidgets import QApplication            # noqa: E402

from main_window import MainWindow                # noqa: E402


def main():
    qt = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()


import sys
import os

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from app import MainWindow


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("SQL Server Docker Manager")
    app.setOrganizationName("MSSQL-Docker-Tools")

    style = """
    QGroupBox {
        font-weight: bold;
        border: 1px solid #cccccc;
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 16px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 2px 8px;
    }
    QPushButton {
        padding: 4px 12px;
    }
    QListWidget {
        border: 1px solid #cccccc;
        border-radius: 3px;
    }
    """
    app.setStyleSheet(style)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

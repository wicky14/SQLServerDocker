import sys
import os
import traceback

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon

from app import MainWindow
from app.installer import handle as installer_handle


def exception_hook(exctype, value, tb):
    msg = "".join(traceback.format_exception(exctype, value, tb))
    try:
        error_box = QMessageBox()
        error_box.setIcon(QMessageBox.Critical)
        error_box.setWindowTitle("Unhandled Error")
        error_box.setText(str(value))
        error_box.setDetailedText(msg)
        error_box.exec_()
    except Exception:
        print(msg, file=sys.stderr)
    sys.exit(1)


def get_icon_path():
    if hasattr(sys, '_MEIPASS'):
        p = os.path.join(sys._MEIPASS, "app", "icon", "icon.png")
        if os.path.exists(p):
            return p
    p = os.path.join(os.path.dirname(__file__), "app", "icon", "icon.png")
    if os.path.exists(p):
        return p
    return None


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    sys.excepthook = exception_hook
    app.setApplicationName("SQL Server Docker Manager")
    app.setOrganizationName("MSSQL-Docker-Tools")

    icon_path = get_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    if installer_handle():
        sys.exit(0)

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

import os

from PyQt6.QtWidgets import QDialog
from PyQt6 import uic

# This loads your .ui file so that PyQt can populate your plugin
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'QPegel_dialog_base.ui'))


class QPegelDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super(QPegelDialog, self).__init__(parent)

        self.setupUi(self)

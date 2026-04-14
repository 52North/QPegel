"""
***************************************************************************
QPegel
QGIS plugin

        Begin                : February 2026
        Copyright            : (C) 2026 52°North GmbH
        Email                : j.rotert@52north.org

***************************************************************************

***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 3 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************
"""


import os

from PyQt6.QtWidgets import QDialog, QDockWidget
from PyQt6 import uic

# This loads your .ui file so that PyQt can populate your plugin
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'QPegel_dialog_base_dockWidget.ui'))


class QPegelDialog(QDockWidget, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super(QPegelDialog, self).__init__(parent)

        self.setupUi(self)

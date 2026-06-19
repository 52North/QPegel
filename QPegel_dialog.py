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

from qgis.PyQt.QtWidgets import QDialog, QDockWidget
from qgis.PyQt import uic

# This loads your .ui file so that PyQt can populate your plugin
if Qgis.QGIS_VERSION_INT >= 40000:
    FORM_CLASS, _ = uic.loadUiType(os.path.join(
        os.path.dirname(__file__), 'QPegel_dialog_base_dockWidget.ui'))
else:
    FORM_CLASS, _ = uic.loadUiType(os.path.join(
        os.path.dirname(__file__), 'QPegel_dialog_base_dockWidget_qt5.ui'))


class QPegelDialog(QDockWidget, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super(QPegelDialog, self).__init__(parent)

        self.setupUi(self)

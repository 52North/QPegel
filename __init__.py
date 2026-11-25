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
import sys

# We bundle pure-Python dependencies in ext_libs/
_EXT_LIBS = os.path.join(os.path.dirname(__file__), "ext_libs")
if os.path.isdir(_EXT_LIBS) and _EXT_LIBS not in sys.path:
    sys.path.insert(0, _EXT_LIBS)


def classFactory(iface):
    try:
        from .QPegel import QPegel
    except ImportError as e:
        raise ImportError(
            f"QPegel could not be loaded: {e}. pandas and matplotlib must be available in the "
            "QGIS Python environment (on Linux install e.g. python3-pandas and python3-matplotlib)."
        ) from e
    return QPegel(iface)

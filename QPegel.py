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


from qgis.PyQt.QtGui import *
from qgis.PyQt.QtWidgets import *
from qgis.PyQt.QtCore import *

from qgis.core import *
from qgis import processing

from PyQt6.QtCore import *
from PyQt6.QtWidgets import QMessageBox

import os
import requests
import json
from datetime import datetime

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, \
    NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .QPegel_dialog import QPegelDialog
from .mqtt_connector import EDISConnector

from .login import hostname, port, username, password


class QPegel(object):
    reader: EDISConnector

    def __init__(self, iface):
        # initialize the QGIS interface
        self.canvas = iface.mainWindow()
        self.iface = iface

        # initialize the dialog
        self.dlg = QPegelDialog()
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dlg)
        # self.dlg.setParent(iface.mainWindow(), Qt.WindowType.Window)
        # self.dlg.setWindowFlags(Qt.WindowType.Tool)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "Logo.png")), 'QPegel', self.canvas)

        # initialize variables
        # TODO: delete data from login-file
        self.dlg.lineEditHostname.setText(hostname)
        self.dlg.lineEditPort.setText(str(port))
        self.dlg.lineEditUsername.setText(username)
        self.dlg.mLineEditPassword.setText(password)
        # request
        self.reader = None
        self.bbox = None
        self.polygon = None
        self.polygon_layer: QgsVectorLayer = None
        self.polygon_layer_id: str = ""
        self.stations_layer: QgsVectorLayer = None
        self.stations_layer_id: str = ""
        self.stations_found: bool = False
        self.base_url: str = "https://dict-api.pegelonline.wsv.de/search?"
        self.url_parameters: dict[str, str] = {}
        self.request_url: str = ""
        self.group_name: str = ""
        self.group : QgsLayerTreeGroup = None
        self.response_json: dict[str, Any] = None
        self.station_index: dict[str, Any] = {}
        # layers
        self.stationlayer_mapping: dict[
            str, dict[str, Any]] = {}  # {name: {id: str, active: bool}}
        self.root = QgsProject.instance().layerTreeRoot()
        # plots
        self.plot_layer: QgsVectorLayer = None
        self.plot_mapping: dict[str, dict[str, dict[str, Any]]] = {} # {layername: {unitlongname: {data: [{timestamp: time, value: float}, ...], unit: str, type: str, unitactive: bool}}}
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.dlg.verticalLayoutPlot.addWidget(self.canvas)
        self.toolbar = NavigationToolbar(self.canvas, self.iface.mainWindow())
        self.dlg.verticalLayoutPlot.addWidget(self.toolbar)

    def initGui(self):
        # required to get a toolbar button
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "Logo.png")), 'QPegel', self.canvas)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

        self.dlg.pushButtonAddPolygon.setIcon(QIcon(os.path.join(self.plugin_dir, "img/polygon.svg")))
        self.dlg.pushButtonRemovePolygon.setIcon(QIcon(os.path.join(self.plugin_dir, "img/remove_polygon.svg")))
        #self.dlg.pushButtonSelectAll.setIcon(QIcon(os.path.join(self.plugin_dir, "img/select_all.png")))
        #self.dlg.pushButtonUnselectAll.setIcon(QIcon(os.path.join(self.plugin_dir, "img/unselect_all.png")))

        # connect the buttons
        # self.dlg.rejected.connect(self.closebtn_clicked)
        self.dlg.pushButtonConnect.clicked.connect(self.connectbtn_clicked)
        self.dlg.pushButtonDisconnect.clicked.connect(self.disconnectbtn_clicked)
        self.dlg.pushButtonAddPolygon.clicked.connect(self.polygonbtn_clicked)
        self.dlg.pushButtonRemovePolygon.clicked.connect(self.handle_remove_polygon)
        self.dlg.pushButtonSend.clicked.connect(self.sendbtn_clicked)
        self.dlg.pushButtonSelectAll.clicked.connect(self.selectallbtn_clicked)
        self.dlg.pushButtonUnselectAll.clicked.connect(self.unselectallbtn_clicked)
        self.dlg.pushButtonRemoveStation.clicked.connect(self.removestationbtn_clicked)
        self.dlg.pushButtonSubscribe.clicked.connect(self.subscribebtn_clicked)
        self.dlg.pushButtonUnsubscribe.clicked.connect(self.unsubscribebtn_clicked)
        self.dlg.pushButtonHelp.clicked.connect(self.open_help)
        self.dlg.pushButtonQuitSession.clicked.connect(self.quitsessionbtn_clicked)

        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        self.change_status("disconnected", "gray")
        self.dlg.mMapLayerComboBox.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.filter_layers()
        # QgsProject.instance().layerWasAdded.connect(self.filter_layers)
        self.dlg.lineEditStation.editingFinished.connect(self.update_request)
        self.dlg.lineEditGewaesser.editingFinished.connect(self.update_request)
        self.dlg.lineEditParameter.editingFinished.connect(self.update_request)
        self.dlg.lineEditQ.editingFinished.connect(self.update_request)
        self.dlg.tabWidget.currentChanged.connect(self.on_main_tab_change)
        self.dlg.mMapLayerComboBox.layerChanged.connect(self.prepare_plot)
        self.dlg.checkBoxHistorical.checkStateChanged.connect(self.on_checkbox_historical_change)
        self.dlg.mComboBoxUnit.checkedItemsChanged.connect(self.on_checked_unit_change)

        QgsProject.instance().layerRemoved.connect(self.on_layer_removed)

    # important function - otherwise the toolbar button is added each time when reloading happens -> multiple Buttons
    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    # function to show the dialog
    def run(self):
        self.dlg.show()

    def open_help(self):
        # open documentation
        webbrowser.open("https://Juliarotert.github.io/QPegel/")

    def change_status(self, status : str, color:str):
        self.dlg.widgetStatus.setStyleSheet(f"background-color: {color}; border-radius: 10px")
        self.dlg.labelStatus.setText(status)

    ### Authentification & Connection
    def connectbtn_clicked(self):
        #self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        # declare userdata
        hostname = self.dlg.lineEditHostname.text()
        port = int(self.dlg.lineEditPort.text())
        username = self.dlg.lineEditUsername.text()
        password = self.dlg.mLineEditPassword.text()
        # create reader
        try:
            self.reader = EDISConnector(parent=self.dlg, hostname=hostname, port=port, username=username,
                                        password=password)
            # receive and handle messages by reader
            self.reader.status_msg.connect(self.handle_status)
            self.reader.new_message.connect(self.handle_message)
            self.reader.error_msg.connect(print)
            self.reader.start()
        except Exception as e:
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error", f"Connection Error: \n{e}")

    def create_session_group(self):
        self.group_name = "Session - " + str(datetime.now().replace(microsecond=0))
        self.group = QgsLayerTreeGroup(self.group_name)
        self.root.insertChildNode(0, self.group)

    def handle_status(self, msg):
        # only start and enable next steps with success message
        if msg == "Success":
            if self.group_name == "":
                self.create_session_group()
                self.dlg.pushButtonAddPolygon.setEnabled(True)
            elif self.group is None:
                self.create_session_group()
            #self.dlg.textEditRequest.setPlainText("")
            self.dlg.mGroupBoxUserAuthentification.setCollapsed(True)
            self.dlg.tab1Request.setEnabled(True)
            self.dlg.pushButtonConnect.setEnabled(False)
            self.dlg.pushButtonDisconnect.setEnabled(True)
            self.change_status("connected", "green")
            # layer styles
            if len(self.stationlayer_mapping) > 0:
                for name, info in self.stationlayer_mapping.items():
                    if info["active"] == True:
                        self.reader.subscribe(self.station_index[name]["mqtttopic"])
                self.change_session_station_styles("active")
        elif msg == "Bad user name or password":
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error", f"Invalid User Data: \n{msg}")
        elif msg == "Opening Connection to edis.pegelonline-int.wsv.de":
            pass
        elif msg == "Keep alive timeout":
            QMessageBox.warning(None, "Error:", f"Error: \n{msg} \n check your internet connection")
            self.disconnectbtn_clicked()
        elif msg == "Normal disconnection":
            pass
        else:
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error:", f"Error: \n{msg}")

    def disconnectbtn_clicked(self):
        # stop reader
        if self.reader:
            if self.reader.isRunning():
                self.reader.stop()
                self.reader.wait(1000)
                # symbols
                self.change_status("disconnected", "gray")
                self.change_session_station_styles("inactive")
                # reset buttons
                self.dlg.tab1Request.setEnabled(False)
                self.dlg.pushButtonConnect.setEnabled(True)
                self.dlg.pushButtonDisconnect.setEnabled(False)
        else:
            pass

    def change_session_station_styles(self, type: str):
        if self.group is not None:
            for child in self.group.children():
                layer = child.layer()
                if isinstance(layer, QgsVectorLayer):
                    field_names = [field.name() for field in layer.fields()]
                    if all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                        if self.stationlayer_mapping[layer.name()]["active"] is True:
                            if type == "active":
                                layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                            elif type == "inactive":
                                layer.loadNamedStyle(
                                    os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                            else:
                                QMessageBox.warning(None, "Error:", f"Error: \n wrong layer style type input or error")

    ### Polygon Selection
    def polygonbtn_clicked(self):
        self.iface.messageBar().pushMessage(
            "Start Polygon Selection",
            "draw a polygon in the map",
            level=Qgis.MessageLevel.Info,
            duration=3,
        )
        self.dlg.pushButtonAddPolygon.setEnabled(False)

        if self.group is None:
            self.create_session_group()
            group = self.root.findGroup(self.group_name)
        # create new vector layer and add it to the map
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        self.polygon_layer_id = self.polygon_layer.id()
        QgsProject.instance().addMapLayer(self.polygon_layer, False)
        self.group.insertChildNode(0, QgsLayerTreeLayer(self.polygon_layer))
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_polygons.qml"))

        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature and send signal when a feature is added
        self.iface.actionAddFeature().trigger()
        self.polygon_layer.featureAdded.connect(self.on_feature_added)

    # slot to save automatically when the first feature is added
    def on_feature_added(self, feature_id):
        self.dlg.pushButtonRemovePolygon.setEnabled(True)
        exporter = QgsJsonExporter(self.polygon_layer)
        data = exporter.exportFeatures(self.polygon_layer.getFeatures())
        data_json = json.loads(data)
        # extract bbox from json (for API request)
        self.bbox = data_json["features"][0]["bbox"]
        self.polygon = next(self.polygon_layer.getFeatures()).geometry()
        self.finish_polygon()

    def finish_polygon(self):
        # disconnect from signal and stop editing
        self.polygon_layer.featureAdded.disconnect(self.on_feature_added)
        if self.polygon_layer.isEditable():
            self.polygon_layer.commitChanges()
        # update request with finished polygon
        self.update_request()

    def update_request(self):
        if self.bbox is None:
            bbox_str = ""
        else:
            bbox_str = str(self.bbox).replace("[", "").replace("]", "")
        self.url_parameters["bbox"] = bbox_str
        self.url_parameters["q"] = self.dlg.lineEditQ.text()
        self.url_parameters["gewaesser"] = self.dlg.lineEditGewaesser.text()
        self.url_parameters["station"] = self.dlg.lineEditStation.text()
        self.url_parameters["parameter"] = self.dlg.lineEditParameter.text()
        parameters = ["bbox", "station", "gewaesser", "parameter", "q"]
        for param in parameters:
            if self.url_parameters[param] == "":
                self.url_parameters.pop(param)

    def sendbtn_clicked(self):
        # API request with current parameters
        request = requests.get(self.base_url, self.url_parameters)
        # check if request was successful or print error
        if request.status_code == 200:
            self.on_response(request)
            self.dlg.textEditRequest.setPlainText(request.url)
        else:
            QMessageBox.warning(None, "Error:", f"Error: {response.status_code}")

    def on_response(self, response):
        # convert response to json and check length
        response_json = response.json()
        if len(response_json["stations"]) > 0:
            self.response_json = response_json
            self.add_station_points()
        else:
            self.dlg.lineEditResponse.setText("0 stations found")

    def add_station_points(self):
        if self.group is None:
            self.create_session_group()
            group = self.root.findGroup(self.group_name)
        # create and add layer for station points
        if self.stations_layer is None:
            self.stations_layer = QgsVectorLayer("Point?crs=EPSG:25832", "Stations", "memory")
            self.stations_layer_id = self.stations_layer.id()
            QgsProject.instance().addMapLayer(self.stations_layer, False)
            self.group.insertChildNode(0, QgsLayerTreeLayer(self.stations_layer))
            self.stations_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_stations.qml"))
            # add attributes to layer
            self.stations_layer.dataProvider().addAttributes([QgsField("uuid2", QVariant.String),
                                                             QgsField("number", QVariant.String),
                                                             QgsField("shortname", QVariant.String),
                                                             QgsField("km", QVariant.Int),
                                                             QgsField("water_shortn", QVariant.String),
                                                             QgsField("water_longn", QVariant.String),
                                                             QgsField("agency", QVariant.String),
                                                             QgsField("land", QVariant.String),
                                                             QgsField("kreis", QVariant.String),
                                                             QgsField("einzugsgebiet", QVariant.String),
                                                             QgsField("mqtttopic", QVariant.String)])
            self.stations_layer.updateFields()

        self.iface.setActiveLayer(self.stations_layer)
        # set transformation parameters for reprojection
        source_crs = QgsCoordinateReferenceSystem(4326)
        target_crs = QgsCoordinateReferenceSystem(25832)
        transform_parameters = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        # add data from response
        features = self.stations_layer.getFeatures()
        feature_shortnames = []
        for feature in features:
            feature_shortnames.append(feature["shortname"])
        for station in self.response_json["stations"]:
            if station["shortname"] not in feature_shortnames:
                self.station_index[station["shortname"]] = station
                point = QgsPointXY(station["longitude"], station["latitude"])
                point_reprojected = transform_parameters.transform(point)
                point_geometry = QgsGeometry.fromPointXY(point_reprojected)
                if not self.polygon or self.polygon.intersects(point_geometry):
                    feature = QgsFeature()
                    feature.setGeometry(point_geometry)
                    feature.setAttributes([station["uuid"],
                                           station["number"],
                                           station["shortname"],
                                           station.get("km", 0),
                                           station["water"]["shortname"],
                                           station["water"]["longname"],
                                           station.get("agency"),
                                           station.get("land", ""),
                                           station.get("kreis", ""),
                                           station.get("einzugsgebiet", ""),
                                           station["mqtttopic"]])
                    self.stations_layer.dataProvider().addFeatures([feature])
                    # create checkable items and add them to QListWidget
                    item = QListWidgetItem(station["shortname"])
                    item.setIcon(QIcon(os.path.join(self.plugin_dir, "img/circle_red.svg")))
                    # item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    # item.setCheckState(Qt.CheckState.Checked)
                    self.dlg.listWidgetLayers.addItem(item)
        self.iface.mapCanvas().refreshAllLayers()

        # check if stations exist
        if len(self.stations_layer) > 0:
            self.stations_found = True
            self.dlg.pushButtonSubscribe.setEnabled(True)
            self.dlg.pushButtonUnsubscribe.setEnabled(True)
            self.dlg.pushButtonRemoveStation.setEnabled(True)
        # response message variants
        if len(self.stations_layer) == 1:
            self.dlg.lineEditResponse.setText(str(len(self.stations_layer)) + " station found")
        elif len(self.stations_layer) > 1:
            self.dlg.lineEditResponse.setText(str(len(self.stations_layer)) + " stations found")
        # zoom to station layer
        self.iface.setActiveLayer(self.stations_layer)
        self.iface.actionZoomToLayer().trigger()
        # start the "identify features" button after finishing to view the stations attributes on click
        self.iface.actionIdentify().trigger()

    def selectallbtn_clicked(self):
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            item.setSelected(True)

    def unselectallbtn_clicked(self):
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            item.setSelected(False)

    def subscribebtn_clicked(self):
        subscribed_list = []
        # listWidget is not iterable!! -> range
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            if item.isSelected():
                exists = False
                already_subscribed = False
                item.setIcon(QIcon(os.path.join(self.plugin_dir, "img/circle_green.svg")))
                for name, info in self.stationlayer_mapping.items():
                    id, active = info["id"], info["active"]
                    if item.text() == name:
                        layer = QgsProject.instance().mapLayersByName(item.text())[0]
                        already_subscribed = active
                        # Layer already exists
                        exists = True
                        break

                if not exists:
                    # create new layer with attributes to group if checked
                    layer = QgsVectorLayer("Point?crs=EPSG:25832", item.text(), "memory")
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_closed.qml"))
                    QgsProject.instance().addMapLayer(layer, False)
                    self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
                    self.group.insertChildNode(0, QgsLayerTreeLayer(layer))
                    layer.dataProvider().addAttributes([QgsField("timestamp", QVariant.String),
                                                        QgsField("longname", QVariant.String),
                                                        QgsField("value", QVariant.Double),
                                                        QgsField("unit", QVariant.String),
                                                        QgsField("type", QVariant.String)])
                    layer.updateFields()
                    # add layer to dict {"shortname": QgsVectorLayer}
                    already_subscribed = False
                    self.plot_mapping[item.text()] = {}

                # subscribe topic
                if not already_subscribed:
                    subscribed_list.append(layer.name())
                    self.reader.subscribe(self.station_index[layer.name()]["mqtttopic"])
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                    self.stationlayer_mapping[layer.name()] = {"id": layer.id(), "active": True}
                    self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))

                # self.dlg.listWidgetLayers.deleteItem(item)

        if len(subscribed_list) > 0:
            message = str(', '.join(subscribed_list))
        else:
            message = "No station subscribed"
        self.iface.messageBar().pushMessage(
        "Subscribed: ",
        message,
        level=Qgis.MessageLevel.Info,
        duration=5)

    def handle_message(self, msg: dict):
        # get layer fitting to message
        for name in self.stationlayer_mapping.keys():
            if msg["shortname"] == name:
                mapping_layer = QgsProject.instance().mapLayersByName(name)[0]
                break

        # plot_mapping initialization
        entry = None
        key = str(mapping_layer.name())
        if key not in self.plot_mapping.keys():
            self.plot_mapping[key] = {}

        # check/ add unit longnames to station
        longname = msg["timeseries"]["longname"]
        if longname not in self.plot_mapping[key].keys():
            entry = {
                "data": [],
                "timestamps": [],
                "unit": msg["timeseries"]["unit"],
                "type": msg["timeseries"]["measurement"].get("type", "measurement"),
                "active": True
            }
            self.plot_mapping[key][longname] = entry
        else:
            entry = self.plot_mapping[key][longname]

        # add data if timestamp is new
        timestamp = pd.to_datetime(msg["timeseries"]["measurement"]["timestamp"])
        if timestamp not in entry["timestamps"]:
            # plot_mapping data storage
            entry["timestamps"].append(timestamp)
            entry["data"].append({
                "timestamp": timestamp,
                "value": msg["timeseries"]["measurement"]["value"],
            })
            self.dlg.textEditPlotMapping.setPlainText(str(self.plot_mapping))

            # map layer data storage
            feature = QgsFeature()
            feature.setAttributes([msg["timeseries"]["measurement"]["timestamp"],
                                   msg["timeseries"]["longname"],
                                   msg["timeseries"]["measurement"]["value"],
                                   msg["timeseries"]["unit"],
                                   msg["timeseries"]["measurement"].get("type", "measurement")
                                   ])
            mapping_layer.dataProvider().addFeature(feature)
            # reload/repaint to show live changes in labels and attribute tables
            mapping_layer.reload()
            self.stations_layer.triggerRepaint()
            # QgsProject.instance().reloadAllLayers()

            # create new plot on message for message layer
            if mapping_layer == self.plot_layer:
                self.prepare_plot()

    def unsubscribebtn_clicked(self):
        unsubscribed_list = []
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            if item.isSelected():
                for name, info in self.stationlayer_mapping.items():
                    id, active = info["id"], info["active"]
                    if item.text() == name and active:
                        layer = QgsProject.instance().mapLayersByName(item.text())[0]
                        item.setIcon(QIcon(os.path.join(self.plugin_dir, "img/circle_orange.svg")))
                        unsubscribed_list.append(name)
                        # Layer already exists
                        self.reader.unsubscribe(self.station_index[name]["mqtttopic"])
                        self.stationlayer_mapping[name] = {"id": id, "active": False}
                        self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                        break

        if len(unsubscribed_list) > 0:
            message = str(', '.join(unsubscribed_list))
        else:
            message = "No station unsubscribed"
        self.iface.messageBar().pushMessage(
        "Unsubscribed: ",
        message,
        level=Qgis.MessageLevel.Info,
        duration=5)

    def check_listwidget(self):
        if self.dlg.listWidgetLayers.count() == 0:
            self.dlg.pushButtonSubscribe.setEnabled(False)
            self.dlg.pushButtonUnsubscribe.setEnabled(False)
            self.dlg.pushButtonRemoveStation.setEnabled(False)

    def removestationbtn_clicked(self):
        delete_layer_list = []
        delete_station_list = []
        delete_unsubscribed_list = []
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            if item.isSelected():
                if self.stations_layer is None:
                    self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
                elif item.text() not in self.stationlayer_mapping.keys():
                    delete_station_list.append(item.text())
                    delete_unsubscribed_list.append(item)
                elif len(self.stationlayer_mapping) == 0:
                    pass
                else:
                    for name, info in self.stationlayer_mapping.items():
                        id, active = info["id"], info["active"]
                        layer = QgsProject.instance().mapLayersByName(item.text())[0]
                        if item.text() == name:
                            delete_station_list.append(name)
                            delete_layer_list.append(layer)

        # triggers on_layer_removed and handles the removal of all relevant parts
        for layer in delete_layer_list:
            QgsProject.instance().removeMapLayer(layer)
        self.check_listwidget()
        for item in delete_unsubscribed_list:
            self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
            with edit(self.stations_layer):
                request = QgsFeatureRequest().setFilterExpression(f'"shortname" = \'{item.text()}\'')
                for feature in self.stations_layer.getFeatures(request):
                    self.stations_layer.deleteFeature(feature.id())

        self.iface.messageBar().pushMessage(
            "Deleted",
            str(', '.join(delete_station_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    def on_layer_removed(self, removed_layer_id):
        remove_list = []
        if removed_layer_id == self.stations_layer_id:
            QMessageBox.warning(None, "Warning:", f"Warning: \n removed layer is stations layer \n ")
            self.stations_layer = None
            self.quitsessionbtn_clicked()
        if removed_layer_id == self.polygon_layer_id:
            self.polygon_layer = None
            self.handle_remove_polygon()
        for name, info in self.stationlayer_mapping.items():
            id, active = info["id"], info["active"]
            if removed_layer_id == id:
                # remove from stationlayer_mapping
                if name in self.stationlayer_mapping.keys():
                    remove_list.append(name)
                # remove from listWidgetLayers
                if self.dlg.listWidgetLayers.count() > 0:
                    item = self.dlg.listWidgetLayers.findItems(name, Qt.MatchFlag.MatchContains)[0]
                    self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
                # unsubscribe
                self.reader.unsubscribe(self.station_index[name]["mqtttopic"])
                # remove from self.stations_layer
                if self.stations_layer is not None:
                    with edit(self.stations_layer):
                        request = QgsFeatureRequest().setFilterExpression(f'"shortname" = \'{name}\'')
                        for feature in self.stations_layer.getFeatures(request):
                            self.stations_layer.deleteFeature(feature.id())

        for name in remove_list:
            self.stationlayer_mapping.pop(name)
            if name in self.plot_mapping.keys():
                self.plot_mapping.pop(name)

        self.check_listwidget()
        self.dlg.textEditPlotMapping.setPlainText(str(self.plot_mapping))
        self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))

        if self.group is None:
            self.quitsessionbtn_clicked()



    ### View Data
    def on_main_tab_change(self):
        # initial layer filtering and plot
        if self.dlg.tabWidget.currentIndex() == 1:
            self.filter_layers()
            self.prepare_plot()

    def filter_layers(self):
        excepted_layers = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                field_names = [field.name() for field in layer.fields()]
                if not all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                    excepted_layers.append(layer)
            else:
                excepted_layers.append(layer)

        self.dlg.mMapLayerComboBox.setExceptedLayerList(excepted_layers)

    def prepare_plot(self):
        #print("plot_mapping: ", self.plot_mapping)
        #print("stationlayer_mapping: ", self.stationlayer_mapping)
        #print("station_index: ", self.station_index)
        # check for next steps
        if self.dlg.mMapLayerComboBox:
            self.plot_layer = self.dlg.mMapLayerComboBox.currentLayer()
            if self.plot_layer is not None:
                if len(self.plot_layer) > 0:
                    if self.plot_layer.name() in self.plot_mapping.keys():
                        self.update_unit_checkbox()
                    else:
                        self.prepare_closed_layer_plot()
                        self.update_unit_checkbox()
                else:
                    self.initial_plot()

    def initial_plot(self):
        # initial empty plot
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        if "[CLOSED]" in self.plot_layer.name():
            ax.set_title("No data available.")
        else:
            ax.set_title("Waiting for data...")
        self.canvas.draw()

    def prepare_closed_layer_plot(self):
        print("prepare closed layer: ")
        mapping = {}
        for feature in self.plot_layer.getFeatures():
            d = None
            if feature["longname"] not in mapping:
                d = {
                    "data": [],
                    "unit": feature["unit"],
                    "type": feature["type"],
                    "active": True
                }
                mapping[feature["longname"]] = d
            else:
                d = mapping[feature["longname"]]
            # Parse data
            d["data"].append(
                {
                "timestamp": pd.to_datetime(feature["timestamp"]),
                "value": feature["value"],
                }
            )
        self.plot_mapping[self.plot_layer.name()] = mapping
        self.dlg.textEditPlotMapping.setPlainText(str(self.plot_mapping))

    def on_checkbox_historical_change(self, state):
        station = self.plot_layer.name()
        if state == Qt.CheckState.Checked:
            # ToDo: request and add historical data
            pass
        else:
            # ToDo: remove historical data if existing
            pass

    def on_checked_unit_change(self, items):
        for key in self.plot_mapping[self.plot_layer.name()]:
            if key not in items:
                self.plot_mapping[self.plot_layer.name()][key]["active"] = False
            else: self.plot_mapping[self.plot_layer.name()][key]["active"] = True
        self.update_unit_checkbox()
        self.dlg.textEditPlotMapping.setPlainText(str(self.plot_mapping))

    def update_unit_checkbox(self):
        mapping = self.plot_mapping[self.plot_layer.name()]
        self.dlg.mComboBoxUnit.clear()
        for key, value in mapping.items():
            # add unit-names to combobox
            self.dlg.mComboBoxUnit.addItemWithCheckState(key, Qt.CheckState.Checked if value[
                "active"] else Qt.CheckState.Unchecked)
        self.update_plot()

    def update_plot(self):
        if not self.plot_mapping[self.plot_layer.name()]:
            print(f"no plot_mapping for layer: {self.plot_layer}")
            return
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()

        colors = plt.cm.tab10.colors
        for i, longname in enumerate(self.dlg.mComboBoxUnit.checkedItems()):
            color = colors[i]
            df = pd.DataFrame(self.plot_mapping[self.plot_layer.name()][longname]["data"], columns=['timestamp', 'value'])
            ax.plot(df["timestamp"], df["value"], label=longname, color=color, marker='o', markersize=2)

        ax.set_xlabel("Time")
        ax.set_title(self.dlg.mMapLayerComboBox.currentText())
        self.canvas.figure.autofmt_xdate()
        ax.legend()
        ax.grid(True)
        self.canvas.draw()


    '''
    def plot_data(self):
        self.canvas.figure.clf()
        ax_main = self.figure.add_subplot(1, 1, 1)
        ax_main.clear()
        ax_main.set_xlabel("Time")
        ax_main.set_title(self.dlg.mMapLayerComboBox.currentText())
        # define important values
        colors = plt.cm.tab10.colors
        checked_longnames: list = self.dlg.mComboBoxUnit.checkedItems()
        try:
            # create axes and plots/lines for each existing unit
            for i, longname in enumerate(checked_longnames):
                label = longname + " [" + self.units[longname] + "]"
                data = self.plottable_values[longname]
                color = colors[i % len(colors)]
                if i == 0:
                    curr_ax = ax_main
                else:
                    curr_ax = ax_main.twinx()
                    if i > 1:
                        offset = (i - 1) * 60
                        self.canvas.figure.subplots_adjust(right=0.7)  # TODO: find right adjustment for more than 3 axes
                        curr_ax.spines['right'].set_position(('outward', offset))
                # fill
                curr_ax.set_ylabel(label)
                curr_ax.yaxis.label.set_color(color)
                plot = curr_ax.plot(self.timestamps, data, label=longname, color=color, marker='o', markersize=2)

            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%y.%m.%d. %H:%M'))
            ax_main.xaxis.set_major_locator(mdates.AutoDateLocator())
            self.canvas.figure.autofmt_xdate()
            # ax_main.legend( loc='upper mid')
            # ax_main.legend(lines, [l.get_label() for l in lines], bbox_to_anchor=(0., 1.02, 1., .102), loc=3, ncol=len(self.checked_longnames[layer]), mode="expand", borderaxespad=0.)
            # self.canvas.figure.subplots_adjust(right=0.8)
        except Exception as e:
            print("Exception plot_data: ", e)
        self.canvas.draw()
    '''

    ### reset/restart/quit
    # reset for polygon removal
    def handle_remove_polygon(self):
        if self.polygon_layer is not None:
            QgsProject.instance().removeMapLayer(self.polygon_layer)
            self.polygon_layer = None
        self.bbox = None
        self.polygon = None
        self.iface.actionPan().trigger()
        # reset buttons
        self.dlg.pushButtonAddPolygon.setEnabled(True)
        self.dlg.pushButtonRemovePolygon.setEnabled(False)
        self.check_listwidget()
        self.update_request()

    def quitsessionbtn_clicked(self):
        self.handle_remove_polygon()
        if self.group is not None:
            # remove group if task is undone
            if self.stations_layer is None:
                if self.group:
                    self.root.removeChildNode(self.group)
            # change layer styles, states & unsubscribe
            else:
                self.stations_layer = None
                station_exists = False
                for child in self.group.children():
                    layer = child.layer()
                    if isinstance(layer, QgsVectorLayer):
                        field_names = [field.name() for field in layer.fields()]
                        if all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                            station_exists = True
                            if self.stationlayer_mapping[layer.name()]["active"] is True:
                                self.reader.unsubscribe(self.station_index[layer.name()]["mqtttopic"])
                                self.stationlayer_mapping[layer.name()] = {"id": layer.id(), "active": False}
                            layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_closed.qml"))
                            layer.setName("[CLOSED] " + layer.name())
                if station_exists is False:
                    self.root.removeChildNode(self.group)
        # reset steps
        self.dlg.mGroupBoxUserAuthentification.setCollapsed(False)
        self.dlg.tab1Request.setEnabled(False)
        self.dlg.textEditRequest.setPlainText("")
        self.dlg.lineEditResponse.setText("")
        self.dlg.lineEditQ.setText("")
        self.dlg.lineEditGewaesser.setText("")
        self.dlg.lineEditStation.setText("")
        self.dlg.lineEditParameter.setText("")
        self.bbox = None
        self.polygon = None
        self.polygon_layer: QgsVectorLayer = None
        self.polygon_layer_id: str = ""
        self.stations_layer: QgsVectorLayer = None
        self.stations_layer_id: str = ""
        self.stations_found: bool = False
        self.url_parameters: dict[str, str] = {}
        self.request_url: str = ""
        self.group_name: str = ""
        self.group: QgsLayerTreeGroup = None
        self.response_json: dict[str, Any] = None
        self.station_index: dict[str, Any] = {}
        self.stationlayer_mapping: dict[str, dict[str, Any]] = {}
        self.root = QgsProject.instance().layerTreeRoot()
        self.plot_layer: QgsVectorLayer = None
        self.plot_mapping: dict[str, dict[dict[str, Any]]] = {}
        self.dlg.listWidgetLayers.clear()
        # disconnect & close
        self.disconnectbtn_clicked()
        self.dlg.close()

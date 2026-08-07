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
from qgis.PyQt.QtCore import QT_VERSION_STR

from qgis.core import *

import os
import webbrowser
import requests
import json
from datetime import datetime

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, \
    NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

import pandas as pd

from .QPegel_dialog import QPegelDialog
from .mqtt_connector import EDISConnector



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
        self.plugin_dir : str = os.path.dirname(os.path.abspath(__file__))
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "Logo.png")), 'QPegel', self.canvas)

        # initialize variables
        # request
        self.reader: EDISConnector = None
        self.bbox: list[float] = []
        self.polygon: QgsGeometry = None
        self.polygon_layer: QgsVectorLayer = None
        self.polygon_layer_id: str = ""
        self.stations_layer: QgsVectorLayer = None
        self.stations_layer_id: str = ""
        self.stations_found: bool = False
        self.base_url: str = "https://dict-api.pegelonline.wsv.de/search?"
        self.url_parameters: dict[str, str] = {}
        self.request_url: str = ""
        self.group_name: str = ""
        self.group: QgsLayerTreeGroup = None
        self.response_json: dict[str, Any] = {}
        self.station_index: dict[str, Any] = {}
        self.msg_counter: int = 0
        # layers
        # self.stationlayer_mapping = {uuid: {layer_name: str, layer_id: str, active: bool}}
        self.stationlayer_mapping: dict[str, dict[str, Any]] = {}
        self.root = QgsProject.instance().layerTreeRoot()
        # plots
        self.plot_layer: QgsVectorLayer = None
        self.figure: Figure = Figure()
        self.canvas: FigureCanvas = FigureCanvas(self.figure)

        # add some additional UI elements
        self.dlg.verticalLayoutPlot.addWidget(self.canvas)
        self.toolbar: NavigationToolbar = NavigationToolbar(self.canvas, self.iface.mainWindow())
        self.dlg.verticalLayoutPlot.addWidget(self.toolbar)

    def initGui(self):
        # required to get a toolbar button
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "Logo.png")), 'QPegel', self.canvas)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

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

        # initial steps & signal-slot connections
        self.dlg.pushButtonAddPolygon.setIcon(QIcon(os.path.join(self.plugin_dir, "img_ui/polygon.svg")))
        self.dlg.pushButtonRemovePolygon.setIcon(QIcon(os.path.join(self.plugin_dir, "img_ui/remove_polygon.svg")))
        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        self.change_status("disconnected", "gray")
        self.dlg.mMapLayerComboBox.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.filter_layers()
        self.dlg.lineEditStation.editingFinished.connect(self.update_request)
        self.dlg.lineEditGewaesser.editingFinished.connect(self.update_request)
        self.dlg.lineEditParameter.editingFinished.connect(self.update_request)
        self.dlg.lineEditQ.editingFinished.connect(self.update_request)
        self.dlg.tabWidget.currentChanged.connect(self.refresh_view_data_tab)
        self.dlg.mMapLayerComboBox.layerChanged.connect(self.prepare_plot)
        self.dlg.mGroupBoxParameter.collapsedStateChanged.connect(self.parameter_info)

        if QT_VERSION_STR.startswith('6'):
            # self.dlg.checkBoxHistorical.checkStateChanged.connect(self.on_checkbox_historical_change)
            self.dlg.checkBoxOnlySubscribed.checkStateChanged.connect(self.refresh_view_data_tab)
        else:
            # self.dlg.checkBoxHistorical.stateChanged.connect(self.on_checkbox_historical_change)
            self.dlg.checkBoxOnlySubscribed.stateChanged.connect(self.refresh_view_data_tab)

        self.dlg.mComboBoxUnit.checkedItemsChanged.connect(self.on_checked_unit_change)
        QgsProject.instance().layerRemoved.connect(self.on_layer_removed)
        QgsProject().instance().aboutToBeCleared.connect(self.quitsessionbtn_clicked)

    # base function to avoid multiple toolbar buttons
    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    # function to show the dialog
    def run(self):
        self.dlg.show()

    # opens help/ documentation docs
    def open_help(self):
        webbrowser.open("https://github.com/52North/QPegel/blob/master/README.md")

    # fast style changes of connection info
    def change_status(self, status : str, color:str):
        self.dlg.widgetStatus.setStyleSheet(f"background-color: {color}; border-radius: 10px")
        self.dlg.labelStatus.setText(status)

    # creates a new group for session layers
    def create_session_group(self):
        self.group_name = "Session - " + str(datetime.now().replace(microsecond=0))
        self.group = QgsLayerTreeGroup(self.group_name)
        self.root.insertChildNode(0, self.group)



    ### Authentification & Connection

    # connects to reader with user data
    def connectbtn_clicked(self):
        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        try:
            # declare userdata
            hostname = self.dlg.lineEditHostname.text()
            port = int(self.dlg.lineEditPort.text())
            username = self.dlg.lineEditUsername.text()
            password = self.dlg.mLineEditPassword.text()

            # create reader
            self.reader = EDISConnector(parent=self.dlg, hostname=hostname, port=port, username=username,
                                        password=password)
            # receive and handle messages by reader
            self.reader.status_msg.connect(self.handle_status)
            self.reader.new_message.connect(self.handle_message)
            self.reader.error_msg.connect(print)
            self.reader.start()
        except Exception as e:
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error", f"Connection Error: \n{e}, check your user data")

    # handle incoming connection status messages
    def handle_status(self, msg : str):
        # only start and enable next steps with success message
        if msg == "Success":
            self.iface.messageBar().pushMessage(
                "Connected",
                level=Qgis.MessageLevel.Info,
                duration=2)
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
                for uuid, info in self.stationlayer_mapping.items():
                    name, layer_id, active = info["name"], info["layer_id"], info["active"]
                    if info["active"] == True:
                        self.reader.subscribe(self.station_index[name]["mqtttopic"])
                self.change_session_station_styles("active")
        # handle different status cases
        elif msg == "Bad user name or password":
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error", f"Invalid User Data: \n{msg}")
        elif "Opening Connection to" in msg:
            pass
        elif msg == "Keep alive timeout":
            QMessageBox.warning(None, "Error:", f"Error: \n{msg} \ncheck your internet connection")
            self.disconnectbtn_clicked()
        elif msg == "Normal disconnection":
            self.iface.messageBar().pushMessage(
                "Disconnected",
                level=Qgis.MessageLevel.Info,
                duration=2)
        else:
            self.disconnectbtn_clicked()
            self.change_status("error", "red")
            QMessageBox.warning(None, "Error:", f"Error: \n{msg}")

    # disconnect from reader
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

    # change layer styles
    def change_session_station_styles(self, state: str):
        if self.group is not None:
            for uuid, info in self.stationlayer_mapping.items():
                layer = QgsProject.instance().mapLayer(info["layer_id"])
                if info["active"] is True:
                    if state == "active":
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                    elif state == "inactive":
                        layer.loadNamedStyle(
                            os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                    else:
                        QMessageBox.warning(None, "Error:", f"Error: \n wrong layer style type input or error")



    ### Station Selection

    # start polygon editing for map-based search
    def polygonbtn_clicked(self):
        # inform about possibility to start drawing
        self.iface.messageBar().pushMessage(
            "Start Polygon Selection",
            "draw a polygon in the map",
            level=Qgis.MessageLevel.Info,
            duration=3,
        )
        self.dlg.pushButtonAddPolygon.setEnabled(False)
        self.dlg.pushButtonSend.setEnabled(False)
        # create group if not existing
        if self.group is None:
            self.create_session_group()
        # create new vector layer and add it to the map
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        self.polygon_layer_id = self.polygon_layer.id()
        QgsProject.instance().addMapLayer(self.polygon_layer, False)
        self.group.insertChildNode(-1, QgsLayerTreeLayer(self.polygon_layer))
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_polygons.qml"))

        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature and send signal when a feature is added
        self.iface.actionAddFeature().trigger()
        self.polygon_layer.featureAdded.connect(self.on_feature_added)

    # slot to save automatically when the first feature is added
    def on_feature_added(self):
        self.dlg.pushButtonRemovePolygon.setEnabled(True)
        self.dlg.pushButtonSend.setEnabled(True)
        exporter = QgsJsonExporter(self.polygon_layer)
        data = exporter.exportFeatures(self.polygon_layer.getFeatures())
        data_json = json.loads(data)
        # extract bbox from json (for API request)
        self.bbox = data_json["features"][0]["bbox"]
        self.polygon = next(self.polygon_layer.getFeatures()).geometry()
        self.finish_polygon()

    # stops edit mode when first polygon feature is added
    def finish_polygon(self):
        # disconnect from signal and stop editing
        self.polygon_layer.featureAdded.disconnect(self.on_feature_added)
        if self.polygon_layer.isEditable():
            self.polygon_layer.commitChanges()
        # update request with finished polygon
        self.update_request()

    # informs the user if parameters are active while the groupbox is collapsed
    def parameter_info(self):
        base_title = "Additional Parameters"
        if self.dlg.mGroupBoxParameter.isCollapsed():
            textfields = [self.dlg.lineEditStation.text(), self.dlg.lineEditGewaesser.text(), self.dlg.lineEditParameter.text(), self.dlg.lineEditQ.text()]
            filled_count = sum(1 for field in textfields if field.strip())
            if not all(not field.strip() for field in textfields):
                self.dlg.mGroupBoxParameter.setTitle(f"{base_title} [{filled_count} active]")
            else: self.dlg.mGroupBoxParameter.setTitle(base_title)
        else: self.dlg.mGroupBoxParameter.setTitle(base_title)


    # gets the latest request parameters
    def update_request(self):
        if len(self.bbox) == 0:
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

    # starts the request, receives & transmits response
    def sendbtn_clicked(self):
        # API request with current parameters
        response = requests.get(self.base_url, self.url_parameters)
        # check if request was successful or show error
        if response.status_code == 200:
            self.on_response(response)
            self.dlg.textEditRequest.setPlainText(response.url)
        else:
            QMessageBox.warning(None, "Error:", f"Error: {response.status_code}")

    # converts response to json & decides for next steps
    def on_response(self, response):
        # convert response to JSON and check length
        response_json = response.json()
        if len(response_json["stations"]) > 0:
            self.response_json = response_json
            self.add_station_points()
        else:
            self.dlg.lineEditResponse.setText("0 stations found")

    # add new stations to station_layer
    def add_station_points(self):
        if self.group is None:
            self.create_session_group()
        # create and add layer for station points
        if self.stations_layer is None:
            self.stations_layer = QgsVectorLayer("Point?crs=EPSG:25832", "Stations", "memory")
            self.stations_layer_id = self.stations_layer.id()
            QgsProject.instance().addMapLayer(self.stations_layer, False)
            self.group.insertChildNode(0, QgsLayerTreeLayer(self.stations_layer))
            self.stations_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_stations.qml"))
            # add attributes to layer
            self.stations_layer.dataProvider().addAttributes([QgsField("uuid", QVariant.String),
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
        feature_uuids = []
        for feature in features:
            feature_uuids.append(feature["uuid"])
        # check if station is new
        for station in self.response_json["stations"]:
            if station["uuid"] not in feature_uuids:
                self.station_index[station["uuid"]] = station
                point = QgsPointXY(station["longitude"], station["latitude"])
                point_reprojected = transform_parameters.transform(point)
                point_geometry = QgsGeometry.fromPointXY(point_reprojected)
                # only add data if no polygon exists or if the station intersects the polygon
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
                    item.setIcon(QIcon(os.path.join(self.plugin_dir, "img_ui/circle_red.svg")))
                    item.setData(Qt.ItemDataRole.UserRole, station["uuid"])
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

    # select all stations
    def selectallbtn_clicked(self):
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            item.setSelected(True)

    # unselect all stations
    def unselectallbtn_clicked(self):
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            item.setSelected(False)



    ### Station Subscription & Data Stream Handling

    # subscribe selected, not existing or already subscribed stations
    def subscribebtn_clicked(self):
        subscribed_list = []
        # listWidget is not iterable!! -> range
        for i in reversed(range(self.dlg.listWidgetLayers.count())):
            item = self.dlg.listWidgetLayers.item(i)
            if item.isSelected():
                exists = False
                already_subscribed = False
                uuid = item.data(Qt.ItemDataRole.UserRole)
                # check if station exists
                # set exists true if existing in stationlayer_mapping
                if uuid in self.stationlayer_mapping:
                    info = self.stationlayer_mapping[uuid]
                    layer = QgsProject.instance().mapLayer(info["layer_id"])
                    already_subscribed = info["active"]
                    exists = True

                if not exists:
                    # create new layer attributes to group if checked
                    layer = QgsVectorLayer("Point?crs=EPSG:25832", item.text(), "memory")
                    QgsProject.instance().addMapLayer(layer, False)
                    self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
                    self.group.insertChildNode(0, QgsLayerTreeLayer(layer))
                    layer.dataProvider().addAttributes([QgsField("timestamp", QVariant.String),
                                                        QgsField("longname", QVariant.String),
                                                        QgsField("value", QVariant.Double),
                                                        QgsField("unit", QVariant.String),
                                                        QgsField("type", QVariant.String)])
                    layer.updateFields()
                    self.stations_layer.triggerRepaint()
                    # add layer to dict {"shortname": QgsVectorLayer}
                    already_subscribed = False
                    # initialize station in dict
                    self.stationlayer_mapping[uuid] = {'name': layer.name(),
                                                      'layer_id': layer.id(),
                                                      'active': already_subscribed,
                                                      'data': {}}

                    # set uuid in layer metadata
                    for feature in self.stations_layer.getFeatures():
                        if feature["uuid"] == uuid:
                            metadata = layer.metadata()
                            uuid = str(feature["uuid"])
                            metadata.setIdentifier(uuid)
                            layer.setMetadata(metadata)
                            layer.triggerRepaint()
                            break

                # subscribe topic and set styles & state
                if not already_subscribed:
                    subscribed_list.append(layer.id())
                    self.reader.subscribe(self.station_index[uuid]["mqtttopic"])
                    item.setIcon(QIcon(os.path.join(self.plugin_dir, "img_ui/circle_green.svg")))
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                    # first assignment of information to station uuid
                    self.stationlayer_mapping[uuid]["active"] = True
                    self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))

        # inform about subscribed stations
        if len(subscribed_list) > 0:
            message = f"{len(subscribed_list)} stations - Receiving data can take a few minutes, please wait..."
        else:
            message = "No station subscribed"
        self.iface.messageBar().pushMessage(
        "Subscribed: ",
        message,
        level=Qgis.MessageLevel.Info,
        duration=10)

    # assign incoming data to the right layer
    def handle_message(self, msg: dict):
        self.msg_counter += 1
        self.dlg.labelMessageCount.setText(f"Total messages received: {self.msg_counter}")
        # get layer fitting to message
        message_layer = None
        uuid = msg["uuid"]
        for layer in QgsProject.instance().mapLayers().values():
            if layer.metadata().identifier() == uuid:
                message_layer = layer
                break
        if message_layer is None:
            print(f"could not handle message {msg} - could not find associated layer")
            return

        # plot data initialization
        entry = None
        # check/ add unit longnames to station
        longname = msg["timeseries"]["longname"]
        if longname not in self.stationlayer_mapping[uuid]["data"].keys():
            entry = {
                "values": [],
                "timestamps": [],
                "unit": msg["timeseries"]["unit"],
                "type": msg["timeseries"]["measurement"].get("type", "measurement"),
                "active": True
            }
            self.stationlayer_mapping[uuid]["data"][longname] = entry
        else:
            entry = self.stationlayer_mapping[uuid]["data"][longname]

        # add data if timestamp is new
        timestamp = pd.to_datetime(msg["timeseries"]["measurement"]["timestamp"])
        if timestamp not in entry["timestamps"]:
            # plot data storage
            entry["timestamps"].append(timestamp)
            entry["values"].append({
                "timestamp": timestamp,
                "value": msg["timeseries"]["measurement"]["value"],
            })
            self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
            # map layer data storage
            feature = QgsFeature()
            feature.setAttributes([msg["timeseries"]["measurement"]["timestamp"],
                                   msg["timeseries"]["longname"],
                                   msg["timeseries"]["measurement"]["value"],
                                   msg["timeseries"]["unit"],
                                   msg["timeseries"]["measurement"].get("type", "measurement")
                                   ])
            message_layer.dataProvider().addFeature(feature)
            # reload/repaint to show live changes in labels and attribute tables
            message_layer.reload()
            self.stations_layer.triggerRepaint()
            # QgsProject.instance().reloadAllLayers()

            # create new plot on message for message layer
            if message_layer == self.plot_layer:
                self.prepare_plot()

    # handles unsubscription of selected layers
    def unsubscribebtn_clicked(self):
        unsubscribed_list = []
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            if item.isSelected():
                for uuid, info in self.stationlayer_mapping.items():
                    # unsubscribe if currently subscribed
                    if item.data(Qt.ItemDataRole.UserRole) == uuid and info["active"]:
                        unsubscribed_list.append(uuid)
                        self.reader.unsubscribe(self.station_index[uuid]["mqtttopic"])
                        # styles
                        layer = QgsProject.instance().mapLayer(info["layer_id"])
                        item.setIcon(QIcon(os.path.join(self.plugin_dir, "img_ui/circle_orange.svg")))
                        self.stationlayer_mapping[uuid]["active"] = False
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                        self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
                        break

        self.check_for_subscribed()

        if len(unsubscribed_list) > 0:
            message = str(', '.join(unsubscribed_list))
        else:
            message = "No station unsubscribed"
        self.iface.messageBar().pushMessage(
        "Unsubscribed: ",
        message,
        level=Qgis.MessageLevel.Info,
        duration=5)

    # check for right button enabling
    def check_listwidget(self):
        if self.dlg.listWidgetLayers.count() == 0:
            self.dlg.pushButtonSubscribe.setEnabled(False)
            self.dlg.pushButtonUnsubscribe.setEnabled(False)
            self.dlg.pushButtonRemoveStation.setEnabled(False)



    ### Station/Layer Remove Handling

    # remove handling from station list
    def removestationbtn_clicked(self):
        # initialize lists
        delete_layer_list = []
        delete_station_list = []
        delete_unsubscribed_list = []
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            # only remove selected stations
            if item.isSelected():
                # check for right handling and execute necessary steps
                if self.stations_layer is None:
                    self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
                # collect all stations only in list-widget
                elif item.data(Qt.ItemDataRole.UserRole) not in self.stationlayer_mapping.keys():
                    delete_station_list.append(item.text())
                    delete_unsubscribed_list.append(item)
                # collect all stations available as layers & in stationlayer_mapping
                else:
                    for uuid, info in self.stationlayer_mapping.items():
                        # ToDo: replace by uuid metadata
                        layer = QgsProject.instance().mapLayer(info["layer_id"])
                        if item.data(Qt.ItemDataRole.UserRole) == uuid:
                            delete_station_list.append(info["name"])
                            delete_layer_list.append(layer)

        # triggers on_layer_removed and handles the removal of all relevant parts
        for layer in delete_layer_list:
            QgsProject.instance().removeMapLayer(layer)
        self.check_listwidget()
        # handles delete steps for unsubscribed stations
        for item in delete_unsubscribed_list:
            self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
            with edit(self.stations_layer):
                request = QgsFeatureRequest().setFilterExpression(f'"uuid" = \'{item.data(Qt.ItemDataRole.UserRole)}\'')
                for feature in self.stations_layer.getFeatures(request):
                    self.stations_layer.deleteFeature(feature.id())

        # informs about all deleted stations
        self.iface.messageBar().pushMessage(
            "Deleted",
            str(', '.join(delete_station_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    # different remove actions for different layers
    def on_layer_removed(self, removed_layer_id):
        self.refresh_view_data_tab()
        remove_list = []
        # check kind of layer and handle individual removal steps
        if removed_layer_id == self.stations_layer_id:
            QMessageBox.warning(None, "Warning:", f"Warning: \n removed layer is stations layer \n ")
            self.stations_layer = None
        if removed_layer_id == self.polygon_layer_id:
            self.polygon_layer = None
            self.handle_remove_polygon()
        # deleting steps for stations
        for uuid, info in self.stationlayer_mapping.items():
            name = info["name"]
            if removed_layer_id == info["layer_id"]:
                # collect stations to delete from mappings
                if uuid in self.stationlayer_mapping.keys():
                    remove_list.append(uuid)
                # remove from listWidgetLayers
                if self.dlg.listWidgetLayers.count() > 0:
                    items = self.dlg.listWidgetLayers.findItems("name", Qt.MatchFlag.MatchContains)
                    for item in items:
                        if item.data(Qt.ItemDataRole.UserRole) == uuid:
                            remove_item = item
                            break
                    self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(remove_item))
                # unsubscribe
                self.reader.unsubscribe(self.station_index[uuid]["mqtttopic"])
                # remove feature from self.stations_layer
                if self.stations_layer is not None:
                    with edit(self.stations_layer):
                        request = QgsFeatureRequest().setFilterExpression(f'"shortname" = \'{name}\'')
                        for feature in self.stations_layer.getFeatures(request):
                            self.stations_layer.deleteFeature(feature.id())

        # remove from stationlayer_mapping
        for uuid in remove_list:
            self.stationlayer_mapping.pop(uuid)

        self.check_listwidget()
        self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))



    ### View Data

    # initial layer filtering and plot
    def on_tab_change(self):
        if self.dlg.tabWidget.currentIndex() == 1:
            self.filter_layers()
            self.prepare_plot()

    # clear unit checkbox and figure
    def clear_plot_contents(self):
        self.dlg.mComboBoxUnit.clear()
        self.canvas.figure.clf()
        self.canvas.draw()

    # filter and plot again
    def refresh_view_data_tab(self):
        self.filter_layers()
        if self.dlg.checkBoxOnlySubscribed.checkState() == Qt.CheckState.Checked:
            self.plot_layer = self.dlg.mMapLayerComboBox.currentLayer()
            if self.plot_layer is not None:
                self.prepare_plot()
            else:
                self.clear_plot_contents()
        else:
            self.prepare_plot()

    # filter which layers should appear in the layer selection (only stations)
    def filter_layers(self):
        excepted_layers = []
        for layer in QgsProject.instance().mapLayers().values():
            # check if layer is of type vector and contains the typical station attributes
            if isinstance(layer, QgsVectorLayer):
                field_names = [field.name() for field in layer.fields()]
                if not all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                    excepted_layers.append(layer)
                # add all inactive layers
                if self.dlg.checkBoxOnlySubscribed.checkState() == Qt.CheckState.Checked:
                    if layer.metadata().identifier() not in self.stationlayer_mapping.keys():
                        excepted_layers.append(layer)
                    elif self.stationlayer_mapping[layer.metadata().identifier()]["active"] == False:
                        excepted_layers.append(layer)
            else:
                excepted_layers.append(layer)
        self.dlg.mMapLayerComboBox.setExceptedLayerList(excepted_layers)

    # check if any station is subscribed & handle plot if not
    def check_for_subscribed(self):
        active_list = []
        for uuid, info in self.stationlayer_mapping.items():
            if info["active"]:
                active_list.append(uuid)
        if self.stationlayer_mapping is not None:
            if len(self.stationlayer_mapping) == 0 or len(active_list) == 0:
                self.refresh_view_data_tab()

    # check the state of data and decide for plot variant
    def prepare_plot(self):
        if self.dlg.mMapLayerComboBox:
            self.plot_layer = self.dlg.mMapLayerComboBox.currentLayer()
            if self.plot_layer is not None:
                if len(self.plot_layer) > 0:
                    if self.plot_layer.metadata().identifier() in self.stationlayer_mapping.keys():
                        self.update_unit_checkbox()
                    else:
                        self.prepare_closed_layer_plot()
                        self.update_unit_checkbox()
                else:
                    self.dlg.mComboBoxUnit.clear()
                    self.initial_plot()
            else:
                self.dlg.mComboBoxUnit.clear()
                self.figure.clear()

    # initial empty plot
    def initial_plot(self):
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        if "[CLOSED]" in self.plot_layer.metadata().identifier() or self.plot_layer.metadata().identifier() not in self.stationlayer_mapping:
            ax.set_title("No data available.")
        elif self.plot_layer.metadata().identifier() in self.stationlayer_mapping.keys():
            ax.set_title("No data available, station not subscribed")
        else:
            ax.set_title("Waiting for data...")
        self.canvas.draw()

    # prepare plot if data needs to be fetched from a closed layer
    def prepare_closed_layer_plot(self):
        mapping = {}
        try:
            for feature in self.plot_layer.getFeatures():
                d = None
                if feature["uuid"]:
                    if feature["uuid"] not in mapping:
                        d = {
                            "values": [],
                            "unit": feature["unit"],
                            "type": feature["type"],
                            "active": True
                        }
                        mapping[feature["uuid"]]["data"] = d
                    else:
                        d = mapping[feature["uuid"]]["data"]
                # Parse data
                d["values"].append(
                    {
                    "timestamp": pd.to_datetime(feature["timestamp"]),
                    "value": feature["value"],
                    }
                )
            self.stationlayer_mapping[self.plot_layer.metadata().identifier()] = mapping
            self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))
        except Exception as e:
            print("Exception prepare_closed_layer_plot: ", e)

    # set active state in stationlayer_mapping
    def on_checked_unit_change(self, units):
        for unit in self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"]:
            if unit not in units:
                self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"] = False
            else: self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"] = True
        self.update_unit_checkbox()
        self.dlg.textEditStationlayerMapping.setPlainText(str(self.stationlayer_mapping))

    # updates checkboxes by state & plots when checked units change
    def update_unit_checkbox(self):
        print("plot_layer: " + self.plot_layer.name())
        if not self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"]:
            print("no data existing" + self.plot_layer.name())
        mapping = self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"]
        self.dlg.mComboBoxUnit.clear()
        for unit, info in mapping.items():
            # add unit-names to combobox
            self.dlg.mComboBoxUnit.addItemWithCheckState(unit, Qt.CheckState.Checked if info[
                "active"] else Qt.CheckState.Unchecked)
        self.update_plot()

    # updates plots by new incoming data or checked unit changes
    def update_plot(self):
        # initialization
        self.canvas.figure.clf()
        ax_main = self.figure.add_subplot(1, 1, 1)
        axes = [ax_main]
        colors = plt.cm.tab10.colors

        # plot df of each checked unit
        for i, unit_longname in enumerate(self.dlg.mComboBoxUnit.checkedItems()):
            data = self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"][unit_longname]["values"]
            df = pd.DataFrame(data, columns=['timestamp', 'value'])
            df = df.sort_values(by=['timestamp'])
            unit_shortname = self.stationlayer_mapping[self.plot_layer.metadata().identifier()]["data"][unit_longname]["unit"]
            color = colors[i]
            ylabel = f"{unit_longname} [{unit_shortname}]"
            # simple plot for 1 checked unit
            if i == 0:
                curr_ax = ax_main
                curr_ax.set_ylabel(ylabel, color=color)
                self.canvas.figure.subplots_adjust(right=0.9)
            # set ax settings for current unit
            else:
                curr_ax = ax_main.twinx()
                axes.append(curr_ax)
                if i > 0:
                    offset = (i - 1) * 60
                    margin = max(0.2, 0.85 - (i * 0.08))
                    self.canvas.figure.subplots_adjust(right=margin)
                    curr_ax.spines['right'].set_position(('outward', offset))
                curr_ax.set_ylabel(ylabel, color=color)
            # add current ax to plot
            curr_ax.plot(df["timestamp"], df["value"], label=longname, color=color, marker='o', markersize=2)
            curr_ax.tick_params(axis='y', labelcolor=color)

        # general lables and title variations
        ax_main.set_xlabel("Time")
        station_name = self.dlg.mMapLayerComboBox.currentText()

        for uuid, info in self.stationlayer_mapping:
            if info["layer_id"] == self.dlg.mMapLayerComboBox.currentLayer().id():
                station_uuid = uuid
                break

        station_id = self.dlg.mMapLayerComboBox.id()
        status = ""
        if station_name in self.stationlayer_mapping:
            status = " (subscribed)" if self.stationlayer_mapping[station_uuid]["active"] else " (not subscribed)"
        ax_main.set_title(f"{station_name}{status}")
        # plot appearence settings
        self.canvas.figure.autofmt_xdate()
        '''
        # legend enty for each unit
        lines = []
        labels = []
        for ax in axes:
            l, lab = ax.get_legend_handles_labels()
            lines.extend(l)
            labels.extend(lab)
        ax_main.legend(lines, labels, loc='upper left', fontsize='small')
        '''
        ax_main.grid(True)

        self.canvas.draw()



    ### reset/restart/quit

    # reset for polygon removal
    def handle_remove_polygon(self):
        # remove if exists, refresh & set states
        if self.polygon_layer is not None:
            QgsProject.instance().removeMapLayer(self.polygon_layer)
            self.polygon_layer = None
            self.iface.mapCanvas().refresh()
            self.bbox = []
            self.polygon = None
            self.iface.actionPan().trigger()
            # reset buttons
            self.dlg.pushButtonAddPolygon.setEnabled(True)
            self.dlg.pushButtonRemovePolygon.setEnabled(False)
            self.check_listwidget()
            self.update_request()

    # quits the session, resets & closes the plugin
    def quitsessionbtn_clicked(self):
        if self.group is not None:
            # remove group if task is undone
            if self.stations_layer is None or len(self.stationlayer_mapping) == 0:
                self.root.removeChildNode(self.group)
            # change layer styles, states & unsubscribe
            else:
                self.group.setItemVisibilityChecked(False)
                self.group.setExpanded(False)
                self.stations_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_stations_closed.qml"))
                self.stations_layer = None
                for uuid, info in self.stationlayer_mapping.items():
                    layer = QgsProject.instance().mapLayer(info["layer_id"])
                    if info["active"] is True:
                        self.reader.unsubscribe(self.station_index[uuid]["mqtttopic"])
                        self.stationlayer_mapping[uuid]["active"] = False
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_closed.qml"))
                    layer.setName("[CLOSED] " + layer.name())

        # reset steps
        self.dlg.mGroupBoxUserAuthentification.setCollapsed(False)
        self.dlg.tab1Request.setEnabled(False)
        self.dlg.textEditRequest.setPlainText("")
        self.dlg.lineEditResponse.setText("")
        self.dlg.lineEditQ.setText("")
        self.dlg.lineEditGewaesser.setText("")
        self.dlg.lineEditStation.setText("")
        self.dlg.lineEditParameter.setText("")
        self.bbox = []
        self.polygon = None
        self.polygon_layer = None
        self.polygon_layer_id = ""
        self.stations_layer = None
        self.stations_layer_id = ""
        self.stations_found = False
        self.url_parameters = {}
        self.request_url = ""
        self.group_name = ""
        self.group = None
        self.response_json = {}
        self.station_index = {}
        self.stationlayer_mapping = {}
        self.msg_counter = 0
        self.plot_layer = None
        self.dlg.listWidgetLayers.clear()

        # disconnect & close
        self.disconnectbtn_clicked()
        self.dlg.close()

        self.iface.messageBar().pushMessage(
            "Session quitted",
            level=Qgis.MessageLevel.Info,
            duration=3)
        self.handle_remove_polygon()

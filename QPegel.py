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

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT as NavigationToolbar
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

        # initialize the dialog & set it always on top & name it
        self.dlg = QPegelDialog()
        self.msg = QMessageBox()
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dlg)
        #self.dlg.setParent(iface.mainWindow(), Qt.WindowType.Window)
        #self.dlg.setWindowFlags(Qt.WindowType.Tool)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "PegelOnlineLogo.png")), 'QPegel', self.canvas)

        # initialize variables
        # TODO: delete data from login-file
        self.dlg.lineEditHostname.setText(hostname)
        self.dlg.lineEditPort.setText(str(port))
        self.dlg.lineEditUsername.setText(username)
        self.dlg.mLineEditPassword.setText(password)
        # request
        self.bbox = None
        self.polygon = None
        self.polygon_layer : QgsVectorLayer = None
        self.polygon_layer_id : str = ""
        self.station_layer : QgsVectorLayer = None
        self.station_layer_id : str = ""
        self.stations_found : bool = False
        self.base_url : str = "https://dict-api.pegelonline.wsv.de/search?"
        self.url_parameters : dict[str, str] = {}
        self.request_url : str = ""
        self.group_name : str = ""
        self.response_json : dict[str, Any] = None
        self.station_index : dict[str, Any] = {}
        # layers
        self.measurement_mapping : dict[str, (QgsVectorLayer, boolean)] = {} # stores all active station layers for data adding
        self.layer_mapping : list[str] = [] # stores all active station layers for deleted layer handling
        self.root = QgsProject.instance().layerTreeRoot()
        # plots
        self.plot_data_mapping : dict[str, dict[str, Any]] = {} # {"layer": {"df": Dataframe, "units": {'Wassertemperatur': {"short": '°C', "active: True}, {'Wasserstand': {"short": 'cm', "active": False}}}}
        self.excepted_layers : list = []
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.dlg.verticalLayoutPlot.addWidget(self.canvas)
        self.toolbar = NavigationToolbar(self.canvas, self.iface.mainWindow())
        self.dlg.verticalLayoutPlot.addWidget(self.toolbar)


    def initGui(self):
        # required to get a toolbar button
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "PegelOnlineLogo.png")), 'QPegel', self.canvas)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

        # connect the buttons
        #self.dlg.rejected.connect(self.closebtn_clicked)
        self.dlg.pushButtonConnect.clicked.connect(self.connectbtn_clicked)
        self.dlg.pushButtonDisconnect.clicked.connect(self.disconnectbtn_clicked)
        self.dlg.pushButtonAddPolygon.clicked.connect(self.polygonbtn_clicked)
        self.dlg.pushButtonRemovePolygon.clicked.connect(self.handle_reset_actions)
        self.dlg.pushButtonSend.clicked.connect(self.sendbtn_clicked)
        self.dlg.pushButtonSubscribe.clicked.connect(self.subscribebtn_clicked)
        self.dlg.pushButtonUnsubscribe.clicked.connect(self.unsubscribebtn_clicked)
        self.dlg.pushButtonRemoveStation.clicked.connect(self.removestationbtn_clicked)
        self.dlg.pushButtonQuitSession.clicked.connect(self.quitsessionbtn_clicked)

        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
        self.dlg.mMapLayerComboBox.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.filter_layers()
        #QgsProject.instance().layerWasAdded.connect(self.filter_layers)
        self.dlg.lineEditStation.editingFinished.connect(self.update_request)
        self.dlg.lineEditGewaesser.editingFinished.connect(self.update_request)
        self.dlg.lineEditParameter.editingFinished.connect(self.update_request)
        self.dlg.lineEditQ.editingFinished.connect(self.update_request)
        self.dlg.tabWidget.currentChanged.connect(self.on_main_tab_change)
        self.dlg.mMapLayerComboBox.layerChanged.connect(self.update_plot)
        self.dlg.mComboBoxUnit.checkedItemsChanged.connect(self.on_checked_unit_change)

        QgsProject.instance().layerRemoved.connect(self.on_layer_removed)
        #self.dlg.mMapLayerComboBox.currentLayer().featureAdded.connect(self.on_layer_features_change)


    # important function - otherwise the toolbar button is added each time when reloading happens -> multiple Buttons
    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    # function to show the dialog
    def run(self):
        self.dlg.show()


    ### Authentification & Connection
    def connectbtn_clicked(self):
        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        # declare userdata
        hostname = self.dlg.lineEditHostname.text()
        port = int(self.dlg.lineEditPort.text())
        username = self.dlg.lineEditUsername.text()
        password = self.dlg.mLineEditPassword.text()
        # create reader
        try:
            self.reader = EDISConnector(parent=self.dlg, hostname=hostname, port=port, username=username, password=password)
            # receive and handle messages by reader
            self.reader.status_msg.connect(self.handle_status)
            self.reader.new_message.connect(self.handle_message)
            self.reader.error_msg.connect(print)
            self.reader.start()
        except Exception as e:
            print("Exception connectbtn_clicked: ", e)
            QMessageBox.information(None, "Error:", "Connection Error")

    def create_session_group(self):
        self.group_name = "Session - " + str(datetime.now().replace(microsecond=0))
        group = QgsLayerTreeGroup(self.group_name)
        self.root.insertChildNode(0, group)

    def handle_status(self, msg):
        # only start and enable next steps with success message
        if msg == "Success":
            if self.group_name == "":
                self.create_session_group()
                self.dlg.pushButtonAddPolygon.setEnabled(True)
                self.dlg.lineEditGewaesser.setEnabled(True)
            elif self.root.findGroup(self.group_name) is None:
                self.create_session_group()

            self.dlg.textEditRequest.setPlainText(self.base_url)
            self.dlg.mGroupBoxUserAuthentification.setCollapsed(True)
            self.dlg.tab1Request.setEnabled(True)
            self.dlg.pushButtonConnect.setEnabled(False)
            self.dlg.pushButtonDisconnect.setEnabled(True)
            self.dlg.widgetStatus.setStyleSheet("background-color: green; border-radius: 10px")
            # layer styles
            self.change_session_station_styles("active")

        elif msg == "Bad user name or password":
            self.reader.stop()
            self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
            QMessageBox.information(None, "Invalid User Data:")
        elif msg == "Opening Connection to edis.pegelonline-int.wsv.de":
            pass
        else:
            self.reader.stop()
            self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
            QMessageBox.information(None, "Error:", str(msg))

    def disconnectbtn_clicked(self):
        # stop reader
        try:
            if self.reader and self.reader.isRunning():
                self.reader.stop()
                self.reader.wait(1000)
                self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
                # reset buttons
                self.dlg.tab1Request.setEnabled(False)
                self.dlg.pushButtonConnect.setEnabled(True)
                self.dlg.pushButtonDisconnect.setEnabled(False)
                # layer styles
                self.change_session_station_styles("inactive")
        except Exception as e:
            print("Exception disconnectbtn_clicked: ", e)

    def change_session_station_styles(self, type : str):
        try:
            group = self.root.findGroup(self.group_name)
            if group is not None:
                for child in group.children():
                    layer = child.layer()
                    if isinstance(layer, QgsVectorLayer):
                        field_names = [field.name() for field in layer.fields()]
                        if all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                            if self.measurement_mapping[layer.name()][1] is True:
                                if type == "active":
                                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                                elif type == "inactive":
                                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                                else: print("wrong layer style type input or error")
        except Exception as e:
            print("Exception change_session_station_styles: ", e)

    ### Polygon Selection
    def polygonbtn_clicked(self):
        self.iface.messageBar().pushMessage(
            "Start Polygon Selection",
            "draw a polygon in the map",
            level=Qgis.MessageLevel.Info,
            duration=3,
        )
        self.dlg.pushButtonRemovePolygon.setEnabled(True)
        self.dlg.pushButtonAddPolygon.setEnabled(False)

        group = self.root.findGroup(self.group_name)
        if group is None:
            self.create_session_group()
            group = self.root.findGroup(self.group_name)
        # create new vector layer and add it to the map
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        self.polygon_layer_id = self.polygon_layer.id()
        QgsProject.instance().addMapLayer(self.polygon_layer, False)
        group.insertChildNode(0, QgsLayerTreeLayer(self.polygon_layer))
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_polygons.qml"))

        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature and send signal when a feature is added
        self.iface.actionAddFeature().trigger()
        self.polygon_layer.featureAdded.connect(self.on_feature_added)

    # slot to save automatically when the first feature is added
    def on_feature_added(self, feature_id):
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
        request = requests.get(self.base_url, self.url_parameters)
        self.dlg.textEditRequest.setPlainText(request.url)
        if len(self.url_parameters.keys()) == 0:
            self.dlg.pushButtonSend.setEnabled(False)
        else:
            self.dlg.pushButtonSend.setEnabled(True)

    def sendbtn_clicked(self):
        # API request with current parameters
        response = requests.get(self.base_url, self.url_parameters)
        # check if request was successful or print error
        if response.status_code == 200:
            self.on_response(response)
        else:
            print(f"Error: {response.status_code}")

    def on_response(self, response):
        # convert response to json and check length
        response_json = response.json()
        if len(response_json["stations"]) > 0:
            self.response_json = response_json
            self.add_station_points()
        else:
            self.dlg.lineEditResponse.setText("0 stations found")

    def add_station_points(self):
        group = self.root.findGroup(self.group_name)
        if group is None:
            self.create_session_group()
            group = self.root.findGroup(self.group_name)
        self.iface.setActiveLayer(self.station_layer)
        # create and add layer for station points
        if self.station_layer is None:
            self.station_layer = QgsVectorLayer("Point?crs=EPSG:25832", "Stations", "memory")
            self.station_layer_id = self.station_layer.id()
            QgsProject.instance().addMapLayer(self.station_layer, False)
            group.insertChildNode(0, QgsLayerTreeLayer(self.station_layer))
            self.station_layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_stations.qml"))
            # add attributes to layer
            self.station_layer.dataProvider().addAttributes([QgsField("uuid2", QVariant.String),
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
            self.station_layer.updateFields()

        # set transformation parameters for reprojection
        source_crs = QgsCoordinateReferenceSystem(4326)
        target_crs = QgsCoordinateReferenceSystem(25832)
        transform_parameters = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        # add data from response
        features = self.station_layer.getFeatures()
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
                                           station.get("km", ""),
                                           station["water"]["shortname"],
                                           station["water"]["longname"],
                                           station["agency"],
                                           station["land"],
                                           station["kreis"],
                                           station["einzugsgebiet"],
                                           station["mqtttopic"]])
                    self.station_layer.dataProvider().addFeatures([feature])
                    # create checkable items and add them to QListWidget
                    item = QListWidgetItem(station["shortname"])
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    self.dlg.listWidgetLayers.addItem(item)
        self.iface.mapCanvas().refreshAllLayers()

        # check if stations exist
        if len(self.station_layer) > 0:
            self.stations_found = True
            self.dlg.pushButtonSubscribe.setEnabled(True)
            self.dlg.pushButtonUnsubscribe.setEnabled(True)
            self.dlg.pushButtonRemoveStation.setEnabled(True)
        # response message variants
        if len(self.station_layer) == 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " station found")
        elif len(self.station_layer) > 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " stations found")
        # zoom to station layer
        self.iface.setActiveLayer(self.station_layer)
        self.iface.actionZoomToLayer().trigger()
        # start the "identify features" button after finishing to view the stations attributes on click
        self.iface.actionIdentify().trigger()

    def subscribebtn_clicked(self):
        subscribed_list = []
        try:
            # listWidget is not iterable!! -> range
            for i in range(self.dlg.listWidgetLayers.count()):
                item = self.dlg.listWidgetLayers.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    exists = False
                    single_station_layer = None
                    already_subscribed = False
                    for station, info in self.measurement_mapping.items():
                        layer, active = info
                        if item.text() == layer.name():
                            already_subscribed = active
                            # Layer already exists
                            exists = True
                            single_station_layer = layer
                            break

                    if not exists:
                        # create new layer with attributes to group if checked
                        layer = QgsVectorLayer("Point?crs=EPSG:25832", item.text(), "memory")
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_closed.qml"))
                        QgsProject.instance().addMapLayer(layer, False)
                        self.layer_mapping.append((layer.name(), layer.id()))
                        group = self.root.findGroup(self.group_name)
                        group.insertChildNode(0, QgsLayerTreeLayer(layer))
                        layer.dataProvider().addAttributes([QgsField("timestamp", QVariant.String),
                                                                           QgsField("longname", QVariant.String),
                                                                           QgsField("value", QVariant.Double),
                                                                           QgsField("unit", QVariant.String),
                                                                           QgsField("type", QVariant.String)])
                        layer.updateFields()
                        # add layer to dict {"shortname": QgsVectorLayer}
                        already_subscribed = False
                        single_station_layer = layer
                        self.checked_units[layer] = False

                    # subscribe topic
                    if not already_subscribed:
                        subscribed_list.append(layer.name())
                        self.reader.subscribe(self.station_index[layer.name()]["mqtttopic"])
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_active.qml"))
                        self.measurement_mapping[layer.name()] = (layer, True)
                    #self.dlg.listWidgetLayers.deleteItem(item)
        except Exception as e:
            print("Exception subscribebtn_clicked: ", e)

        self.iface.messageBar().pushMessage(
            "Subscribed",
            str(', '.join(subscribed_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    def handle_message(self, msg: dict):
        # get layer fitting to message
        try:
            for station, info in self.measurement_mapping.items():
                layer, active = info
                if msg["shortname"] == layer.name():
                    mapping_layer = layer
                    break

            # create feature and add data from message
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
            self.station_layer.triggerRepaint()
            # QgsProject.instance().reloadAllLayers()
        except Exception as e:
            print("Exception handle_message: ", e)

        # create new plot on message for message layer
        if mapping_layer == self.dlg.mMapLayerComboBox.currentLayer():
            self.update_plot()
            # TODO check if layer in checked_longnames instead
            #if self.checked_units[layer] is False:
                #print("layer_prepared?: ", self.checked_units[layer])
                #self.update_plot()
            #else:
                #self.collect_data()
                #self.plot_data()

    def unsubscribebtn_clicked(self):
        unsubscribed_list = []
        try:
            for i in range(self.dlg.listWidgetLayers.count()):
                item = self.dlg.listWidgetLayers.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    for station, info in self.measurement_mapping.items():
                        layer, active = info
                        if item.text() == layer.name() and active:
                            unsubscribed_list.append(layer.name())
                            # Layer already exists
                            self.reader.unsubscribe(self.station_index[layer.name()]["mqtttopic"])
                            self.measurement_mapping[layer.name()] = (layer, False)
                            layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_inactive.qml"))
                            break
        except Exception as e:
            print("Exception unsubscribebtn_clicked: ", e)

        self.iface.messageBar().pushMessage(
            "Unsubscribed",
            str(', '.join(unsubscribed_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    def removestationbtn_clicked(self):
        try:
            delete_layer_list = []
            delete_station_list = []
            for i in range(self.dlg.listWidgetLayers.count()):
                item = self.dlg.listWidgetLayers.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    if len(self.measurement_mapping) > 0:
                        for station, info in self.measurement_mapping.items():
                            layer, active = info
                            if item.text() == layer.name():
                                delete_station_list.append(station)
                                delete_layer_list.append(layer)
            for layer in delete_layer_list:
                QgsProject.instance().removeMapLayer(layer)
            self.check_listwidget()
            self.iface.messageBar().pushMessage(
                "Deleted",
                str(', '.join(delete_station_list)),
                level=Qgis.MessageLevel.Info,
                duration=5,
            )
        except Exception as e:
            print("Exception removestationbtn_clicked: ", e)

    def check_listwidget(self):
        if self.dlg.listWidgetLayers.count() == 0:
            self.dlg.pushButtonSubscribe.setEnabled(False)
            self.dlg.pushButtonUnsubscribe.setEnabled(False)
            self.dlg.pushButtonRemoveStation.setEnabled(False)

    def on_layer_removed(self, removed_layer_id):
        if removed_layer_id == self.station_layer_id:
            self.station_layer = None
            self.quitsessionbtn_clicked()
        if removed_layer_id == self.polygon_layer_id:
            self.polygon_layer = None
            self.handle_reset_actions()
        for name, id in self.layer_mapping:
            if removed_layer_id == id:
                # remove from measurement_mapping
                if name in self.measurement_mapping.keys():
                    self.measurement_mapping.pop(name)
                # remove from listWidgetLayers
                if self.dlg.listWidgetLayers.count() > 0:
                    item = self.dlg.listWidgetLayers.findItems(name, Qt.MatchFlag.MatchContains)[0]
                    self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
                # unsubscribe
                self.reader.unsubscribe(self.station_index[name]["mqtttopic"])
                # remove from self.station_layer
                if self.station_layer is not None:
                    with edit(self.station_layer):
                        request = QgsFeatureRequest().setFilterExpression(f'"shortname" = \'{name}\'')
                        for feature in self.station_layer.getFeatures(request):
                            self.station_layer.deleteFeature(feature.id())
        group = self.root.findGroup(self.group_name)
        if group is None:
            self.quitsessionbtn_clicked()



    ### View Data
    def on_main_tab_change(self):
        # initial layer filtering and plot
        if self.dlg.tabWidget.currentIndex() == 1:
            self.filter_layers()
            self.update_plot()

    def filter_layers(self):
        try:
            for layer in QgsProject.instance().mapLayers().values():
                if isinstance(layer, QgsVectorLayer):
                    field_names = [field.name() for field in layer.fields()]
                    if not all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                        self.excepted_layers.append(layer)
                else:
                    self.excepted_layers.append(layer)
        except Exception as e:
            print("Exception filter_layers: ", e)

        self.dlg.mMapLayerComboBox.setExceptedLayerList(self.excepted_layers)

    def update_plot(self):
        #self.plottable_values = {}
        #self.units = {}
        # clear unit selection
        #self.dlg.mComboBoxUnit.clear()
        # dict of value lists for each unit
        # check for next steps
        if self.dlg.mMapLayerComboBox:
            layer = self.dlg.mMapLayerComboBox.currentLayer()
            if len(layer) > 0:
                #self.get_unique_units(layer)
                #self.collect_data()
                #self.plot_data()
                self.plot_dataframe()
            else:
                self.initial_plot(layer)

    def initial_plot(self, layer):
        # initial empty plot
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        if "[CLOSED]" in layer.name():
            ax.set_title("No data available.")
        else:
            ax.set_title("Waiting for data...")
        self.canvas.draw()

    def get_unique_units(self, layer):
        try:
            # get unique units
            # if current_layer not in self.checked_longnames:
            # print("no checked_longname list for this layer found: ", current_layer)
            params: dict = {
                'INPUT': layer,
                'FIELDS': ['longname'],
                'OUTPUT': 'TEMPORARY_OUTPUT',
                'OUTPUT_HTML_FILE': 'TEMPORARY_OUTPUT'
            }
            result_unique_units: str = processing.run("qgis:listuniquevalues", params)
            unique_value_list = result_unique_units['UNIQUE_VALUES'].split(";")
            unique_value_list.sort()
        except Exception as e:
            print("Exception get_unique_units: ", e)

        self.fill_unit_checkbox(unique_value_list, layer)

    def fill_unit_checkbox(self, unique_value_list, layer):
        try:
            for longname in unique_value_list:
                # add unit-names to combobox
                self.dlg.mComboBoxUnit.addItemWithCheckState(longname, Qt.CheckState.Checked)
                self.plottable_values[longname] = []
                # get unit names and store them for labels
                request = QgsFeatureRequest().setFilterExpression(f'"longname" = \'{longname}\'')
                feature = layer.dataProvider().getFeatures(request).__next__()
                # store unit for each unit-longname {"Wassertemperatur": "C°", ...}
                if feature:
                    self.units[longname] = feature.attribute("unit")
                    self.checked_units[layer] = True
        except Exception as e:
            print("Exception fill_unit_checkbox: ", e)

    def collect_data(self):
        layer = self.dlg.mMapLayerComboBox.currentLayer()

        try:
            # store/ update all plottable values
            self.timestamps = []
            for feature in layer.dataProvider().getFeatures():
                ts_obj = datetime.fromisoformat(feature.attribute("timestamp"))
                # add (only not already existing) timestamps
                if ts_obj not in self.timestamps:
                    self.timestamps.append(ts_obj)
                # add values to each unit
                for longname, list in self.plottable_values.items():
                    if feature.attribute("longname") == longname:
                        list.append(feature.attribute("value"))
        except Exception as e:
            print("Exception collect_data: ", e)

        self.dlg.mComboBoxUnit.checkedItemsChanged.connect(self.plot_data)

        # Plotly Testing
        #df = self.df_from_layer(layer)
        #fig = px.line(df, x="timestamp", y="value", title='Test Plotly', color="unit", facet_row="unit", markers=True)
        #fig.show()

    def plot_data(self):
        layer = self.dlg.mMapLayerComboBox.currentLayer()

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

    def df_from_layer(self, layer, time_field_name="timestamp"):
        # Original Trajectools 2.7 version
        try:
            names = [field.name() for field in layer.fields()]
            data = []
            for feature in layer.getFeatures():
                my_dict = {}
                for i, a in enumerate(feature.attributes()):
                    if names[i] == time_field_name and isinstance(a, QDateTime):
                        a = a.toPyDateTime()
                    my_dict[names[i]] = a
                data.append(my_dict)
            df = pd.DataFrame(data)
            return df
        except Exception as e:
            print("Exception df_from_layer: ", e)

    def plot_dataframe(self):
        layer = self.dlg.mMapLayerComboBox.currentLayer()
        df = self.df_from_layer(layer)
        unique_unit_longnames = df['longname'].unique()
        unique_units = df['unit'].unique()

        # fill checkableComboBox
        self.dlg.mComboBoxUnit.clear()

        unit_dict = {}
        if len(unique_unit_longnames) == len(unique_units):
            for i in range(len(unique_unit_longnames)):
                longname = unique_unit_longnames[i]
                unit = unique_units[i]
                print(longname, unit)
                unit_dict[longname] = [unit, True]
            self.unit_collection[layer.name()] = unit_dict
            print("unit collection: ", self.unit_collection)

            for longname in self.unit_collection[layer.name()].keys():
                # add unit-names to combobox
                if self.unit_collection[layer.name()][longname][1] == True:
                    self.dlg.mComboBoxUnit.addItemWithCheckState(longname, Qt.CheckState.Checked)
                else:
                    self.dlg.mComboBoxUnit.addItemWithCheckState(longname, Qt.CheckState.Unchecked)

        #print("dataframe of Layer: ", layer)
        #print(df)
        #df.plot()
        #plt.show()

    def on_checked_unit_change(self, items):
        print("checked units changed: ", items)
        print("before: ", self.unit_collection[layer.name()])

        layer = self.dlg.mMapLayerComboBox.currentLayer()
        longnames = []
        for longname in self.unit_collection[layer.name()].keys():
            longnames.append(longname)
        for longname in longnames:
            if longname not in items:
                print("item unchecked: ", longname)
                self.unit_collection[layer.name()][longname][1] = False
        print("after: ", self.unit_collection[layer.name()])


        ### reset/restart/quit
    # reset and remove logic when restarting/closing
    def handle_reset_actions(self):
        if self.polygon_layer is not None:
            QgsProject.instance().removeMapLayer(self.polygon_layer)
            self.polygon_layer = None
        self.bbox = None
        self.polygon = None
        self.iface.actionPan().trigger()
        # reset buttons
        self.dlg.pushButtonAddPolygon.setEnabled(True)
        self.dlg.pushButtonRemovePolygon.setEnabled(False)
        self.dlg.pushButtonSend.setEnabled(False)
        self.dlg.pushButtonSubscribe.setEnabled(False)
        self.dlg.pushButtonUnsubscribe.setEnabled(False)
        self.dlg.pushButtonRemoveStation.setEnabled(False)
        self.update_request()

    def handle_session_ending(self):
        group = self.root.findGroup(self.group_name)
        if group is not None:
            # remove group if task is undone
            if self.station_layer is None:
                self.root.removeChildNode(group)
            else:
                self.station_layer = None
                station_exists = False
                for child in group.children():
                    layer = child.layer()
                    if isinstance(layer, QgsVectorLayer):
                        field_names = [field.name() for field in layer.fields()]
                        if all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                            station_exists = True
                            if self.measurement_mapping[layer.name()][1] is True:
                                self.reader.unsubscribe(self.station_index[layer.name()]["mqtttopic"])
                                self.measurement_mapping[layer.name()] = (layer, False)
                            layer.loadNamedStyle(os.path.join(self.plugin_dir, "layer-styles/style_closed.qml"))
                            layer.setName("[CLOSED] " + layer.name())
                if station_exists is False:
                    self.root.removeChildNode(group)
        else: pass

    # quit session
    def quitsessionbtn_clicked(self):
        self.handle_reset_actions()
        self.disconnectbtn_clicked()
        self.dlg.mGroupBoxUserAuthentification.setCollapsed(False)
        self.dlg.tab1Request.setEnabled(False)
        self.dlg.listWidgetLayers.clear()
        self.handle_session_ending()
        self.group_name = ""

        self.dlg.close()

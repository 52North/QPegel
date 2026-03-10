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

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from .QPegel_dialog import QPegelDialog
from .mqtt_connector import EDISConnector


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
        self.bbox = None
        self.polygon = None
        self.polygon_layer : QgsVectorLayer = None
        self.station_layer : QgsVectorLayer = None
        self.stations_found : bool = False
        self.bbox_url : str = ""
        self.url : str = ""
        self.group_name : str = "Request - "
        self.response_json : dict[str, Any] = None
        self.station_index : dict[str, Any] = {}
        self.measurement_mapping : dict[str, QgsVectorLayer] = {}
        self.plottable_values : dict[str, list[float]] = {}
        self.timestamps : list[str] = []
        self.excepted_layers: list = []

        self.root = QgsProject.instance().layerTreeRoot()

        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.dlg.verticalLayoutPlot.addWidget(self.canvas)

    def initGui(self):
        # required to get a toolbar button
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "PegelOnlineLogo.png")), 'QPegel', self.canvas)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

        # connect the buttons
        #self.dlg.rejected.connect(self.closebtn_clicked)
        self.dlg.pushButtonClose.clicked.connect(self.closebtn_clicked)
        self.dlg.pushButtonPolygon.clicked.connect(self.polygonbtn_clicked)
        self.dlg.pushButtonSend.clicked.connect(self.sendbtn_clicked)
        self.dlg.pushButtonRestart.clicked.connect(self.restartbtn_clicked)
        self.dlg.pushButtonConnect.clicked.connect(self.connectbtn_clicked)
        self.dlg.pushButtonDisconnect.clicked.connect(self.disconnectbtn_clicked)
        self.dlg.pushButtonSubscribe.clicked.connect(self.subscribebtn_clicked)

        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        self.dlg.widgetStatus.setStyleSheet("background-color: grey; border-radius: 10px")
        self.dlg.mMapLayerComboBox.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.filter_layers()
        #QgsProject.instance().layerWasAdded.connect(self.filter_layers)
        self.dlg.tabWidget.currentChanged.connect(self.on_main_tab_change)
        self.dlg.mMapLayerComboBox.currentIndexChanged.connect(self.prepare_plot)
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
            self.reader.new_message.connect(self.handle_message)
            self.reader.status_msg.connect(self.handle_status)
            self.reader.error_msg.connect(print)
            self.reader.start()
        except Exception as e:
            QMessageBox.information(None, "Error:", "Connection Error")

    def handle_status(self, msg):
        # only start and enable next steps with success message
        if msg == "Success":
            self.dlg.mGroupBoxUserAuthentification.setCollapsed(True)
            self.dlg.tabWidget.setEnabled(True)
            self.dlg.pushButtonPolygon.setEnabled(True)
            self.dlg.pushButtonConnect.setEnabled(False)
            self.dlg.pushButtonDisconnect.setEnabled(True)
            self.dlg.textEditRequest.setEnabled(True)
            self.dlg.widgetStatus.setStyleSheet("background-color: green; border-radius: 10px")
        elif msg == "Bad user name or password":
            self.reader.stop()
            self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
            QMessageBox.information(None, "Error:", str(msg))
        else:
            pass

    def disconnectbtn_clicked(self):
        # stop reader
        try:
            if self.reader and self.reader.isRunning():
                self.reader.stop()
                self.reader.wait(1000)
                self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
                # reset buttons
                self.dlg.pushButtonConnect.setEnabled(True)
                self.dlg.pushButtonDisconnect.setEnabled(False)
                self.dlg.pushButtonPolygon.setEnabled(False)
        except Exception as e:
            print(e)

    ### Polygon Selection
    def polygonbtn_clicked(self):
        self.dlg.pushButtonRestart.setEnabled(True)
        self.dlg.pushButtonPolygon.setEnabled(False)

        # group = self.root.addGroup(self.group_name)
        group = QgsLayerTreeGroup(self.group_name)
        self.root.insertChildNode(0, group)

        # create new vector layer and add it to the map
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        QgsProject.instance().addMapLayer(self.polygon_layer, False)
        group.insertChildNode(1, QgsLayerTreeLayer(self.polygon_layer))
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_polygons.qml"))
        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature and send signal when a feature is added
        self.iface.actionAddFeature().trigger()
        self.polygon_layer.featureAdded.connect(self.on_feature_added)

    # slot to save automatically when the first feature is added
    def on_feature_added(self, feature_id):
        self.dlg.lineEditqParameter.setEnabled(True)
        self.dlg.textEditRequest.setEnabled(True)
        # convert polygon to json
        exporter = QgsJsonExporter(self.polygon_layer)
        data = exporter.exportFeatures(self.polygon_layer.getFeatures())
        data_json = json.loads(data)
        # extract bbox from json (for API request)
        self.bbox = data_json["features"][0]["bbox"]
        self.polygon = next(self.polygon_layer.getFeatures()).geometry()

        self.create_request_url()

    def create_request_url(self):
        # disconnect from signal and stop editing
        self.polygon_layer.featureAdded.disconnect(self.on_feature_added)
        if self.polygon_layer.isEditable():
            self.polygon_layer.commitChanges()

        # format bbox and stick it to the request url
        bbox_str = str(self.bbox).replace("[", "").replace("]", "")
        self.bbox_url = 'https://dict-api.pegelonline.wsv.de/search?bbox=' + bbox_str
        self.url = self.bbox_url
        # set text for url review and enable send button
        self.dlg.textEditRequest.setPlainText(self.bbox_url)
        self.dlg.pushButtonSend.setEnabled(True)
        # send signal if q text changed to edit url
        self.dlg.lineEditqParameter.textChanged.connect(self.on_q_changed)

    def on_q_changed(self, q):
        # create url depending on q input
        if self.dlg.lineEditqParameter.text() != "":
            qurl = self.bbox_url + "&q=" + self.dlg.lineEditqParameter.text()
            self.dlg.textEditRequest.setPlainText(qurl)
            self.url = qurl
        else:
            self.dlg.textEditRequest.setPlainText(self.bbox_url)
            self.url = self.bbox_url

    def sendbtn_clicked(self):
        self.dlg.pushButtonSend.setEnabled(False)
        # extend group name by timestamp
        group = self.root.findGroup(self.group_name)
        self.group_name = "Request - " + str(datetime.now())
        group.setName(self.group_name)
        # API request
        if self.url:
            # initialize response dicts
            payload = {}
            headers = {}
            # execute request/ get response
            response = requests.request("GET", self.url, headers=headers, data=payload)
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
            self.dlg.lineEditResponse.setText("0 stations found in this area")

    def add_station_points(self):
        group = self.root.findGroup(self.group_name)
        # create and add layer for station points
        self.station_layer = QgsVectorLayer("Point?crs=EPSG:25832", "Stations", "memory")
        QgsProject.instance().addMapLayer(self.station_layer, False)
        group.insertChildNode(0, QgsLayerTreeLayer(self.station_layer))
        self.station_layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_stations.qml"))
        self.iface.setActiveLayer(self.station_layer)
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
        # get data from response
        for station in self.response_json["stations"]:
            self.station_index[station["shortname"]] = station
            point = QgsPointXY(station["longitude"], station["latitude"])
            point_reprojected = transform_parameters.transform(point)
            point_geometry = QgsGeometry.fromPointXY(point_reprojected)
            if self.polygon.intersects(point_geometry):
                print("polygon intersects point")
                feature = QgsFeature()
                feature.setGeometry(point_geometry)
                feature.setAttributes([station["uuid"],
                                       station["number"],
                                       station["shortname"],
                                       station["km"],
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

        # check if stations exist
        if len(self.station_layer) > 0:
            self.stations_found = True
            self.dlg.pushButtonSubscribe.setEnabled(True)
        # response message variants
        if len(self.station_layer) == 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " station found in this area")
        elif len(self.station_layer) > 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " stations found in this area")
        # zoom to station layer
        self.iface.actionZoomToLayer().trigger()
        # start the "identify features" button after finishing to view the stations attributes on click
        self.iface.actionIdentify().trigger()

    def subscribebtn_clicked(self):
        # listWidget is not iterable!! -> range
        for i in range(self.dlg.listWidgetLayers.count()):
            item = self.dlg.listWidgetLayers.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                # create new layer with attributes to group if checked
                single_station_layer = QgsVectorLayer("Point?crs=EPSG:25832", item.text(), "memory")
                QgsProject.instance().addMapLayer(single_station_layer, False)
                group = self.root.findGroup(self.group_name)
                group.insertChildNode(0, QgsLayerTreeLayer(single_station_layer))
                single_station_layer.dataProvider().addAttributes([QgsField("timestamp", QVariant.String),
                                                                   QgsField("longname", QVariant.String),
                                                                   QgsField("value", QVariant.Double),
                                                                   QgsField("unit", QVariant.String),
                                                                   QgsField("type", QVariant.String)])
                single_station_layer.updateFields()
                # add layer to dict {"shortname": QgsVectorLayer}
                self.measurement_mapping[item.text()] = single_station_layer
                # subscribe topic
                self.reader.subscribe(self.station_index[item.text()]["mqtttopic"])
                print("subscribed to " + item.text() + ": " + self.station_index[item.text()]["mqtttopic"])

    def handle_message(self, msg: dict):
        # get layer fitting to message
        mapping_layer = self.measurement_mapping.get(msg["shortname"])
        if mapping_layer is None:
            # TODO: überarbeiten
            print("!!!!!! MSG FOR INVALID LAYER !!!!")

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

        if mapping_layer == self.dlg.mMapLayerComboBox.currentLayer():
            self.prepare_plot()


    ### View Data
    def filter_layers(self):
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                field_names = [field.name() for field in layer.fields()]
                if not all(field_name in field_names for field_name in ["timestamp", "longname", "value"]):
                    self.excepted_layers.append(layer)
            else:
                self.excepted_layers.append(layer)

        self.dlg.mMapLayerComboBox.setExceptedLayerList(self.excepted_layers)

    def on_main_tab_change(self):
        #
        if self.dlg.tabWidget.currentIndex() == 1:
            self.filter_layers()
            self.prepare_plot()

    def on_layer_features_change(self):
        print("Layer Attributes Changed")

    def prepare_plot(self):
        self.plottable_values = {}
        self.dlg.mComboBoxValueType.clear()
        try:
            if self.dlg.mMapLayerComboBox:
                self.show_data()
        except Exception as e:
            print(e)

    def show_data(self):
        layer : QgsMapLayer = self.dlg.mMapLayerComboBox.currentLayer()
        # plot
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        ax.set_title("Data:")

        if len(layer) > 0:
            params : dict = {
                'INPUT': layer,
                'FIELDS': ['longname'],
                'OUTPUT': 'TEMPORARY_OUTPUT',
                'OUTPUT_HTML_FILE': 'TEMPORARY_OUTPUT'
            }
            result_unique_values : str = processing.run("qgis:listuniquevalues", params)
            unique_value_list = result_unique_values['UNIQUE_VALUES'].split(";")

            for longname in unique_value_list:
                self.dlg.mComboBoxValueType.addItemWithCheckState(longname, Qt.CheckState.Checked)
                self.plottable_values[longname] = []

            print(self.plottable_values)
            self.timestamps = []
            for feature in layer.dataProvider().getFeatures():
                if feature.attribute("timestamp") not in self.timestamps:
                    self.timestamps.append(feature.attribute("timestamp"))
                for longname, list in self.plottable_values.items():
                    if feature.attribute("longname") == longname:
                        list.append(feature.attribute("value"))
            #print(self.plottable_values)

            # plot
            for longname, list in self.plottable_values.items():
                ax.plot(self.timestamps, list, label=longname)
            ax.legend()

            self.dlg.mComboBoxValueType.checkedItemsChanged.connect(self.update_plot)

        self.canvas.draw()

    def update_plot(self):
        print("checked changed")
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()
        for longname, list in self.plottable_values.items():
            if longname in self.dlg.mComboBoxValueType.checkedItems():
                ax.plot(self.timestamps, list, label=longname)
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        ax.legend()
        self.canvas.draw()


    ### reset/restart/close
    # reset and remove logic when restarting/closing
    def handle_reset_actions(self):
        group = self.root.findGroup(self.group_name)
        self.iface.actionPan().trigger()
        # reset buttons
        self.dlg.layoutFunctionality.setEnabled(False)
        self.dlg.pushButtonPolygon.setEnabled(True)
        self.dlg.pushButtonSend.setEnabled(False)
        self.dlg.pushButtonSubscribe.setEnabled(False)
        # reset text fields
        self.dlg.lineEditqParameter.setText("")
        self.dlg.textEditRequest.setPlainText("")
        self.dlg.lineEditResponse.setText("")
        self.dlg.listWidgetLayers.clear()
        # remove group if task is undone
        if self.station_layer is None:
            self.root.removeChildNode(group)

    # reset and start the polygon selection again
    def restartbtn_clicked(self):
        self.handle_reset_actions()
        self.group_name = "Request - "
        self.station_layer = None

    # reset and close
    def closebtn_clicked(self):
        self.handle_reset_actions()
        self.disconnectbtn_clicked()
        self.dlg.mGroupBoxUserAuthentification.setCollapsed(False)
        self.dlg.close()

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

        self.bbox = None
        self.polygon = None
        self.polygon_layer : QgsVectorLayer = None
        self.station_layer : QgsVectorLayer = None
        self.stations_found : bool = False
        self.base_url : str = "https://dict-api.pegelonline.wsv.de/search?"
        self.bbox_url : str = ""
        self.url : str = ""
        self.group_name : str = ""
        self.response_json : dict[str, Any] = None
        self.station_index : dict[str, Any] = {}
        self.measurement_mapping : dict[str, (QgsVectorLayer, boolean)] = {}
        self.plottable_values : dict[str, list[float]] = {}
        self.units : dict[str, str] = {}
        self.timestamps : list[str] = []
        self.excepted_layers: list = []
        #self.checked_longnames : dict[QgsVectorLayer, list[str]] = {}

        self.root = QgsProject.instance().layerTreeRoot()



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
        self.dlg.pushButtonQuitSession.clicked.connect(self.quitsessionbtn_clicked)
        self.dlg.pushButtonPolygon.clicked.connect(self.polygonbtn_clicked)
        self.dlg.pushButtonSend.clicked.connect(self.sendbtn_clicked)
        self.dlg.pushButtonRestart.clicked.connect(self.restartbtn_clicked)
        self.dlg.pushButtonConnect.clicked.connect(self.connectbtn_clicked)
        self.dlg.pushButtonDisconnect.clicked.connect(self.disconnectbtn_clicked)
        self.dlg.pushButtonSubscribe.clicked.connect(self.subscribebtn_clicked)
        self.dlg.pushButtonUnsubscribe.clicked.connect(self.unsubscribebtn_clicked)
        self.dlg.pushButtonDelete.clicked.connect(self.deletebtn_clicked)

        self.dlg.tabWidget.setCurrentWidget(self.dlg.tabWidget.findChild(QWidget, "tab1Request"))
        self.dlg.widgetStatus.setStyleSheet("background-color: red; border-radius: 10px")
        self.dlg.mMapLayerComboBox.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.filter_layers()
        #QgsProject.instance().layerWasAdded.connect(self.filter_layers)
        self.dlg.tabWidget.currentChanged.connect(self.on_main_tab_change)
        self.dlg.mMapLayerComboBox.layerChanged.connect(self.prepare_plot)
        self.dlg.textEditRequest.textChanged.connect(self.on_request_change)
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
            self.group_name = "Session - " + str(datetime.now().date()) + str(datetime.now().time())
            group = QgsLayerTreeGroup(self.group_name)
            self.root.insertChildNode(0, group)

            self.dlg.mGroupBoxUserAuthentification.setCollapsed(True)
            self.dlg.tabWidget.setEnabled(True)
            self.dlg.tab1Request.setEnabled(True)
            self.dlg.pushButtonPolygon.setEnabled(True)
            self.dlg.pushButtonConnect.setEnabled(False)
            self.dlg.pushButtonDisconnect.setEnabled(True)
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
                self.dlg.tabWidget.setEnabled(False)
                self.dlg.pushButtonConnect.setEnabled(True)
                self.dlg.pushButtonDisconnect.setEnabled(False)
        except Exception as e:
            print("Exception: ", e)

    ### Polygon Selection
    def polygonbtn_clicked(self):
        self.iface.messageBar().pushMessage(
            "Start Polygon Selection",
            "draw a polygon in the map",
            level=Qgis.MessageLevel.Info,
            duration=5,
        )
        self.dlg.pushButtonRestart.setEnabled(True)
        self.dlg.pushButtonPolygon.setEnabled(False)

        group = self.root.findGroup(self.group_name)
        # create new vector layer and add it to the map
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        QgsProject.instance().addMapLayer(self.polygon_layer, False)
        group.insertChildNode(0, QgsLayerTreeLayer(self.polygon_layer))
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_polygons.qml"))

        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature and send signal when a feature is added
        self.iface.actionAddFeature().trigger()
        self.polygon_layer.featureAdded.connect(self.on_feature_added)

    # slot to save automatically when the first feature is added
    def on_feature_added(self, feature_id):
        #self.dlg.lineEditqParameter.setEnabled(True)
        # convert polygon to json
        exporter = QgsJsonExporter(self.polygon_layer)
        data = exporter.exportFeatures(self.polygon_layer.getFeatures())
        data_json = json.loads(data)
        # extract bbox from json (for API request)
        self.bbox = data_json["features"][0]["bbox"]
        self.polygon = next(self.polygon_layer.getFeatures()).geometry()

        self.create_request_url()

    def on_request_change(self):
        if self.base_url in self.dlg.textEditRequest.toPlainText():
            self.dlg.pushButtonSend.setEnabled(True)

    def create_request_url(self):
        # disconnect from signal and stop editing
        self.polygon_layer.featureAdded.disconnect(self.on_feature_added)
        if self.polygon_layer.isEditable():
            self.polygon_layer.commitChanges()

        # format bbox and stick it to the request url
        bbox_str = str(self.bbox).replace("[", "").replace("]", "")
        self.bbox_url = self.base_url + "bbox=" + bbox_str
        self.url = self.bbox_url
        # set text for url review and enable send button
        self.dlg.textEditRequest.setPlainText(self.bbox_url)
        # send signal if q text changed to edit url
        #self.dlg.lineEditqParameter.textChanged.connect(self.on_q_changed)
        self.dlg.lineEditGewaesserParameter.textChanged.connect(self.on_gewaesser_changed)

    """
    def on_q_changed(self, q):
        # create url depending on q input
        if self.dlg.lineEditqParameter.text() != "":
            qurl = self.bbox_url + "&q=" + self.dlg.lineEditqParameter.text()
            self.dlg.textEditRequest.setPlainText(qurl)
            self.url = qurl
        else:
            self.dlg.textEditRequest.setPlainText(self.bbox_url)
            self.url = self.bbox_url
    """

    def on_gewaesser_changed(self, gewaesser):
        # create url depending on q input
        if self.dlg.lineEditGewaesserParameter.text() != "":
            gewaesserurl = self.bbox_url + "&gewaesser=" + self.dlg.lineEditGewaesserParameter.text()
            self.dlg.textEditRequest.setPlainText(gewaesserurl)
            self.url = gewaesserurl
        else:
            self.dlg.textEditRequest.setPlainText(self.bbox_url)
            self.url = self.bbox_url

    def sendbtn_clicked(self):
        QgsProject.instance().removeMapLayer(self.polygon_layer)
        self.polygon_layer = None

        self.dlg.pushButtonSend.setEnabled(False)
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
        if self.station_layer is None:
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

        # check if stations exist
        if len(self.station_layer) > 0:
            self.stations_found = True
            self.dlg.pushButtonSubscribe.setEnabled(True)
            self.dlg.pushButtonUnsubscribe.setEnabled(True)
        # response message variants
        if len(self.station_layer) == 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " station found in the selected area(s)")
        elif len(self.station_layer) > 1:
            self.dlg.lineEditResponse.setText(str(len(self.station_layer)) + " stations found in the selected area(s)")
        # zoom to station layer
        self.iface.actionZoomToLayer().trigger()
        # start the "identify features" button after finishing to view the stations attributes on click
        self.iface.actionIdentify().trigger()

    def subscribebtn_clicked(self):
        subscribed_list = []
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
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_inactive.qml"))
                    QgsProject.instance().addMapLayer(layer, False)
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

                # subscribe topic
                if not already_subscribed:
                    subscribed_list.append(layer.name())
                    self.reader.subscribe(self.station_index[layer.name()]["mqtttopic"])
                    layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_active.qml"))
                    self.measurement_mapping[layer.name()] = (layer, True)
                #self.dlg.listWidgetLayers.deleteItem(item)

        self.iface.messageBar().pushMessage(
            "Subscribed",
            str(', '.join(subscribed_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    def unsubscribebtn_clicked(self):
        unsubscribed_list = []
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
                        layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_inactive.qml"))
                        break
        self.iface.messageBar().pushMessage(
            "Unsubscribed",
            str(', '.join(unsubscribed_list)),
            level=Qgis.MessageLevel.Info,
            duration=5,
        )

    def deletebtn_clicked(self):
        try:
            delete_item_list = []
            delete_layer_list = []
            delete_station_list = []
            for i in range(self.dlg.listWidgetLayers.count()):
                item = self.dlg.listWidgetLayers.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    delete_item_list.append(item)
                    self.reader.unsubscribe(self.station_index[item.text()]["mqtttopic"])
                    with edit(self.station_layer):
                        request = QgsFeatureRequest().setFilterExpression(f'"shortname" = \'{item.text()}\'')
                        for feature in self.station_layer.getFeatures(request):
                            self.station_layer.deleteFeature(feature.id())
                    if len(self.measurement_mapping) > 0:
                        for station, info in self.measurement_mapping.items():
                            layer, active = info
                            if item.text() == layer.name():
                                delete_station_list.append(station)
                                delete_layer_list.append(layer)

            for station in delete_station_list:
                self.measurement_mapping.pop(station)
            for item in delete_item_list:
                self.dlg.listWidgetLayers.takeItem(self.dlg.listWidgetLayers.row(item))
            for layer in delete_layer_list:
                QgsProject.instance().removeMapLayer(layer)

            self.iface.messageBar().pushMessage(
                "Deleted",
                str(', '.join(delete_station_list)),
                level=Qgis.MessageLevel.Info,
                duration=5,
            )

        except Exception as e:
            print("Exception: ", e)

    def handle_message(self, msg: dict):
        # get layer fitting to message
        try:
            for station, info in self.measurement_mapping.items():
                layer, active = info
                if msg["shortname"] == layer.name():
                    mapping_layer = layer
                    break

        except Exception as e:
            print("Exception: ", e)

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

        # create new plot on message for message layer
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
        # initial layer filtering and plot
        if self.dlg.tabWidget.currentIndex() == 1:
            self.filter_layers()
            self.prepare_plot()

    def prepare_plot(self):
        self.plottable_values = {}
        self.units = {}
        # clear unit selection
        self.dlg.mComboBoxValueType.clear()
        # dict of value lists for each unit
        try:
            if self.dlg.mMapLayerComboBox:
                current_layer = self.dlg.mMapLayerComboBox.currentLayer()
                if len(current_layer) > 0:
                    self.plot_data()
                else:
                    self.initial_plot()
        except Exception as e:
            print("Exception: ", e)

    def initial_plot(self):
        # initial empty plot
        self.canvas.figure.clf()
        ax = self.figure.add_subplot(1, 1, 1)
        ax.clear()
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        ax.set_title("Waiting for Data...")
        self.canvas.draw()

    def plot_data(self):
        layer = self.dlg.mMapLayerComboBox.currentLayer()

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

        for longname in unique_value_list:
            # add unit-names to combobox
            self.dlg.mComboBoxValueType.addItemWithCheckState(longname, Qt.CheckState.Checked)
            self.plottable_values[longname] = []
            # get unit names and store them for labels
            request = QgsFeatureRequest().setFilterExpression(f'"longname" = \'{longname}\'')
            feature = layer.dataProvider().getFeatures(request).__next__()
            if feature:
                self.units[longname] = feature.attribute("unit")

        # store
        self.timestamps = []
        for feature in layer.dataProvider().getFeatures():
            ts_obj = datetime.fromisoformat(feature.attribute("timestamp"))
            if ts_obj not in self.timestamps:
                self.timestamps.append(ts_obj)
            for longname, list in self.plottable_values.items():
                if feature.attribute("longname") == longname:
                    list.append(feature.attribute("value"))


        #self.checked_longnames[layer] = self.dlg.mComboBoxValueType.checkedItems()
        #print("plots: ", layer, self.checked_longnames[layer])
        # prepare the figure
        self.canvas.figure.clf()
        ax_main = self.figure.add_subplot(1, 1, 1)
        ax_main.clear()
        ax_main.set_xlabel("Time")
        ax_main.set_title(self.dlg.mMapLayerComboBox.currentText())
        # define important values
        colors = plt.cm.tab10.colors
        checked_longnames : list = self.dlg.mComboBoxValueType.checkedItems()
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
                    self.canvas.figure.subplots_adjust(right=0.7) # TODO: find right adjustment for more than 3 axes
                    curr_ax.spines['right'].set_position(('outward', offset))
            # fill
            curr_ax.set_ylabel(label)
            curr_ax.yaxis.label.set_color(color)
            plot = curr_ax.plot(self.timestamps, data, label=longname, color=color, marker='o', markersize=2)

        ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%y.%m.%d. %H:%M'))
        ax_main.xaxis.set_major_locator(mdates.AutoDateLocator())
        self.canvas.figure.autofmt_xdate()
        #ax_main.legend( loc='upper mid')
        #ax_main.legend(lines, [l.get_label() for l in lines], bbox_to_anchor=(0., 1.02, 1., .102), loc=3, ncol=len(self.checked_longnames[layer]), mode="expand", borderaxespad=0.)
        #self.canvas.figure.subplots_adjust(right=0.8)
        self.canvas.draw()

        self.dlg.mComboBoxValueType.checkedItemsChanged.connect(self.prepare_plot)

        # Plotly Testing
        #df = self.df_from_layer(layer)
        #fig = px.line(df, x="timestamp", y="value", title='Test Plotly', color="unit", facet_row="unit", markers=True)
        #fig.show()

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
            #print("df: ", layer)
            return df
        except Exception as e:
            print("Exception: ", e)


    ### reset/restart/quit
    # reset and remove logic when restarting/closing
    def handle_reset_actions(self):
        if self.polygon_layer:
            QgsProject.instance().removeMapLayer(self.polygon_layer)
            self.polygon_layer = None
        group = self.root.findGroup(self.group_name)
        self.iface.actionPan().trigger()
        # reset buttons
        self.dlg.pushButtonPolygon.setEnabled(True)
        self.dlg.pushButtonSend.setEnabled(False)
        self.dlg.pushButtonSubscribe.setEnabled(False)
        self.dlg.pushButtonUnsubscribe.setEnabled(False)
        self.dlg.pushButtonRestart.setEnabled(False)
        # reset text fields
        #self.dlg.lineEditqParameter.setText("")
        self.dlg.lineEditGewaesserParameter.setText("")
        self.dlg.textEditRequest.setPlainText("")
        self.dlg.lineEditResponse.setText("")

    # reset and start the polygon selection again
    def restartbtn_clicked(self):
        self.handle_reset_actions()
        #self.group_name = "Session - "
        #self.station_layer = None

    # quit session
    def quitsessionbtn_clicked(self):
        self.handle_reset_actions()
        self.disconnectbtn_clicked()
        self.dlg.mGroupBoxUserAuthentification.setCollapsed(False)
        self.dlg.tabWidget.setEnabled(False)
        self.dlg.listWidgetLayers.clear()
        group = self.root.findGroup(self.group_name)
        if group:
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
                                layer.loadNamedStyle(os.path.join(self.plugin_dir, "style_inactive.qml"))
                            layer.setName("[CLOSED] " + layer.name())
                if station_exists is False:
                    self.root.removeChildNode(group)

        self.dlg.close()

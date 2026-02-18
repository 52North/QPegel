from qgis.PyQt.QtGui import *
from qgis.PyQt.QtWidgets import *
from qgis.core import *
from qgis.PyQt.QtCore import *
from qgis import processing

from PyQt6.QtCore import *

import os
import requests
import json
from datetime import datetime

from .QPegel_dialog import QPegelDialog


class QPegel(object):
    def __init__(self, iface):
        # initialize the QGIS interface
        self.canvas = iface.mainWindow()
        self.iface = iface

        # initialize the dialog & set it always on top & name it
        self.dlg = QPegelDialog()
        self.dlg.setParent(iface.mainWindow(), Qt.WindowType.Window)
        self.dlg.setWindowFlags(Qt.WindowType.Tool)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "PegelOnlineLogo.png")), 'Renaming', self.canvas)

        # initialize variables
        self.bbox = None
        self.polygon = None
        self.polygon_layer = None
        self.station_layer = None
        self.url = ""

        self.root = QgsProject.instance().layerTreeRoot()
        self.group = self.root.insertGroup(0, "Request - ")

    def initGui(self):
        # required to get a toolbar button
        self.action = QAction(QIcon(os.path.join(self.plugin_dir, "PegelOnlineLogo.png")),'Renaming', self.canvas)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

        # connect the buttons
        self.dlg.rejected.connect(self.closebtn_clicked)
        self.dlg.pushButtonClose.clicked.connect(self.closebtn_clicked)
        self.dlg.pushButtonPolygon.clicked.connect(self.polygonbtn_clicked)
        self.dlg.pushButtonSend.clicked.connect(self.sendbtn_clicked)
        self.dlg.pushButtonRestart.clicked.connect(self.restartbtn_clicked)

    # important function - otherwise the toolbar button is added each time when reloading happens -> multiple Buttons
    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    # function to show the dialog
    def run(self):
        self.dlg.show()


    # Button functionalities
    def polygonbtn_clicked(self):
        self.dlg.pushButtonRestart.setEnabled(True)
        self.dlg.pushButtonPolygon.setEnabled(False)
        # if layer is existing: remove to start new
        if self.polygon_layer is not None:
            self.remove_layers()
        # create new vector layer
        self.polygon_layer = QgsVectorLayer("Polygon?crs=EPSG:25832", "Polygon", "memory")
        # add to map
        self.group.insertChildNode(2, QgsLayerTreeLayer(self.polygon_layer))
        QgsProject.instance().addMapLayer(self.polygon_layer)
        # set style
        self.polygon_layer.loadNamedStyle(os.path.join(self.plugin_dir, "polygon_style.qml"))
        # set layer active and start editing
        self.iface.setActiveLayer(self.polygon_layer)
        self.polygon_layer.startEditing()
        # activate adding a feature
        self.iface.actionAddFeature().trigger()

        self.polygon_layer.featureAdded.connect(self.on_feature_added)
        if self.polygon_layer.featureCount() > 0:
            if self.polygon_layer.isEditable():
                self.polygon_layer.commitChanges()

        # create new feature manually and set it's geometry
        #bbox = QgsGeometry.fromPolygonXY([[QgsPointXY(322971, 5730738), QgsPointXY(322971, 5730800), QgsPointXY(322800, 5730800), QgsPointXY(322800, 5730738)]])
        #feat = QgsFeature()
        #feat.setGeometry(bbox)
        # bbox.setAttributes()
        # add feature to layer
        #self.polygon_layer.dataProvider().addFeatures([feat])

    # slot to save automatically when the first feature is added
    def on_feature_added(self, feature_id):
        # ToDo: stop editing mode of polygon layer

        # convert polygon to json
        exporter = QgsJsonExporter(self.polygon_layer)
        data = exporter.exportFeatures(self.polygon_layer.getFeatures())
        data_json = json.loads(data)
        # extract bbox from json (for API request)
        self.bbox = data_json["features"][0]["bbox"]
        self.polygon = next(self.polygon_layer.getFeatures()).geometry()

        self.create_request_url()

    def create_request_url(self):
        # format bbox and stick it to the request url
        bbox_str = str(self.bbox).replace("[", "").replace("]", "")
        self.url = 'https://dict-api.pegelonline.wsv.de/search?bbox=' + bbox_str
        # set text for url review and enable send button
        self.dlg.textEditRequest.setPlainText(self.url)
        self.dlg.pushButtonSend.setEnabled(True)
        self.polygon_layer.featureAdded.disconnect(self.on_feature_added)
        if self.polygon_layer.isEditable():
            self.polygon_layer.commitChanges()

    def sendbtn_clicked(self):
        self.group.setName("Request - " + str(datetime.now()))
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
        # convert response to json and display it
        response_json = response.json()
        if len(response_json["stations"]) > 0:
            self.dlg.textEditResponse.setPlainText(str(response_json))
            self.add_station_points(response_json)
        else:
            self.dlg.textEditResponse.setPlainText("No stations found in this area")


    def add_station_points(self, response_json):
        # create and add layer for station points
        self.station_layer = QgsVectorLayer("Point?crs=EPSG:25832", "Stations", "memory")
        QgsProject.instance().addMapLayer(self.station_layer, False)
        self.group.insertChildNode(0, QgsLayerTreeLayer(self.station_layer))
        self.station_layer.loadNamedStyle(os.path.join(self.plugin_dir, "station_style.qml"))

        # add attributes to layer
        self.station_layer.dataProvider().addAttributes([QgsField("uuid2", QVariant.String),
                                                         QgsField("number", QVariant.String),
                                                         QgsField("shortname", QVariant.String),
                                                         QgsField("km",  QVariant.Int),
                                                         QgsField("water_shortn", QVariant.String),
                                                         QgsField("water_longn", QVariant.String),
                                                         QgsField("agency", QVariant.String),
                                                         QgsField("land", QVariant.String),
                                                         QgsField("kreis", QVariant.String),
                                                         QgsField("einzugsgebiet", QVariant.String)])
        self.station_layer.updateFields()
        # set transformation parameters for reprojection
        source_crs = QgsCoordinateReferenceSystem(4326)
        target_crs = QgsCoordinateReferenceSystem(25832)
        transform_parameters = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        # get data from response
        for station in response_json["stations"]:
            point = QgsPointXY(station["longitude"], station["latitude"])
            point_reprojected = transform_parameters.transform(point)
            point_geometry = QgsGeometry.fromPointXY(point_reprojected)
            if self.polygon.intersects(point_geometry):
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
                                       station["einzugsgebiet"]])
                self.station_layer.dataProvider().addFeatures([feature])
        self.iface.actionIdentify().trigger()


    def remove_layers(self):
        if self.polygon_layer is not None:
            self.polygon_layer.commitChanges()
            QgsProject.instance().removeMapLayer(self.polygon_layer)
            self.polygon_layer = None
        if self.station_layer is not None:
            QgsProject.instance().removeMapLayer(self.station_layer)
            self.station_layer = None
        if self.station_layer is None:
            return

    def restartbtn_clicked(self):
        self.remove_layers()
        self.dlg.pushButtonPolygon.setEnabled(True)
        self.dlg.pushButtonSend.setEnabled(False)
        self.dlg.textEditRequest.setPlainText("")
        self.dlg.textEditResponse.setPlainText("")

    def closebtn_clicked(self):
        self.restartbtn_clicked()
        self.dlg.close()
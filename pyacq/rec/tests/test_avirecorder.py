# -*- coding: utf-8 -*-
# Copyright (c) 2016, French National Center for Scientific Research (CNRS)
# Distributed under the (new) BSD License. See LICENSE for more info.

import pytest

from pyacq import create_manager
from pyacq.devices.webcam_av import WebCamAV, HAVE_AV
from pyacq.viewers.imageviewer import ImageViewer
from pyacq.rec.avirecorder import AviRecorder

import numpy as np

from pyqtgraph.Qt import QtCore, QtGui
import pyqtgraph as pg

import os
import shutil
import datetime


@pytest.mark.skipif(not HAVE_AV, reason='no have av')
def test_AviRecorder():
    app = pg.mkQApp()
    
    dev = WebCamAV(name='cam')
    dev.configure(camera_num=0)
    dev.output.configure(protocol='tcp', interface='127.0.0.1',transfermode='plaindata',)
    dev.initialize()

    dirname = './test_rec_avi'
    if os.path.exists(dirname):
        shutil.rmtree(dirname)
    
    rec = AviRecorder()
    #~ rec = ng1.create_node('RawRecorder')
    rec.configure(streams=[dev.output], autoconnect=True, dirname=dirname)
    rec.initialize()

    viewer = ImageViewer()
    viewer.configure()
    viewer.input.connect(dev.output)
    viewer.initialize()
    viewer.show()
    
    dev.start()
    rec.start()
    viewer.start()
    
    def terminate():
        viewer.stop()
        rec.stop()
        dev.stop()
        
        
        viewer.close()
        rec.close()
        dev.close()
        
        app.quit()
    
    # start for a while
    timer = QtCore.QTimer(singleShot=True, interval=10000)
    timer.timeout.connect(terminate)
    timer.start()
    app.exec_()





if __name__ == '__main__':
    test_AviRecorder()

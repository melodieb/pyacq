# -*- coding: utf-8 -*-
# Copyright (c) 2016, French National Center for Scientific Research (CNRS)
# Distributed under the (new) BSD License. See LICENSE for more info.

from pyqtgraph.Qt import QtCore, QtGui

import time

import pyacq
from pyacq.viewers import QOscilloscope
from pyacq import create_manager
from pyacq.devices.mcs_w2100 import MultiChannelSystemW2100, download_dll

import pytest


def test_download_dll():
    url_path = download_dll()
    print(url_path)


@pytest.mark.skipif(True, reason='Need MSC device for test')
def test_MultiChannelSystemW2100():
    # in main App
    app = QtGui.QApplication([])
    
    dev = MultiChannelSystemW2100()
    dev.configure(dll_path=None, use_digital_channel=False, sample_rate=2000.)
    dev.outputs['signals'].configure(protocol='tcp', interface='127.0.0.1',transfermode='plaindata',)
    dev.initialize()
    
    viewer = QOscilloscope()
    viewer.configure()
    viewer.input.connect(dev.outputs['signals'])
    viewer.initialize()
    viewer.show()
    
    dev.start()
    viewer.start()
    
    def terminate():
        viewer.stop()
        dev.stop()
        viewer.close()
        dev.close()
        app.quit()
    
    # start for a while
    timer = QtCore.QTimer(singleShot=True, interval=5000)
    timer.timeout.connect(terminate)
    #~ timer.start()
    
    app.exec_()

if __name__ == '__main__':
    #~ test_download_dll()
    test_MultiChannelSystemW2100()

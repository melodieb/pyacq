import time
import pytest
import numpy as np

from pyacq import create_manager
from pyacq.devices.comedidevice import ComediDevice, HAVE_PYCOMEDI

from pyqtgraph.Qt import QtCore, QtGui
from pyqtgraph.util.mutex import Mutex
import pyqtgraph as pg


#~ import logging
#~ logging.getLogger().level=logging.INFO


@pytest.mark.skipif(not HAVE_PYCOMEDI, reason = 'no have pycomedi')
def test_local_app():
    app = pg.mkQApp()
    
    dev  = ComediDevice()
    dev.configure(nb_channel = 2, sampling_rate =44100.,
                    input_device_index = 0, output_device_index = 0,
                    format = 'int16', chunksize = 1024)
    dev.output.configure(protocol = 'tcp', interface = '127.0.0.1', transfertmode = 'plaindata')
    dev.initialize()
    dev.start()
    
    def terminate():
        dev.stop()
        app.quit()
    
    # start for a while
    timer = QtCore.QTimer(singleShot = True, interval = 3000)
    timer.timeout.connect(terminate)
    timer.start()
    app.exec_()

    
if __name__ == '__main__':
    test_local_app()
    
 

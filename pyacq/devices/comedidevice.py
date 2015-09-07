import numpy as np
import mmap


from ..core import Node, register_node_type
from pyqtgraph.Qt import QtCore, QtGui
from pyqtgraph.util.mutex import Mutex

import pycomedi

try:
    import pycomedi
    from pycomedi.device import Device
    from pycomedi.subdevice import StreamingSubdevice
    from pycomedi.channel import AnalogChannel
    from pycomedi.chanspec import ChanSpec
    from pycomedi.constant import (AREF, CMDF, INSN, SUBDEVICE_TYPE, TRIG_SRC, UNIT)
    from pycomedi.utility import inttrig_insn, Reader, Writer, MMapReader
    from pycomedi import PyComediError
    from pycomedi.calibration import CalibratedConverter
    HAVE_PYCOMEDI = True
except ImportError:
    HAVE_PYCOMEDI = False

# FIXME : comedi have its own system of AD convertion with a calibration system.
# question : use it or not ?

#TODO digital device



#TODO gain/offest by channel or global


class ComediDevice(Node):
    """
    """
    _output_specs = {'signals' : dict(streamtype = 'analogsignal',dtype = 'int16',
                                                shape = (-1, 1), compression ='', time_axis=0,
                                                sampling_rate = 1000.,
                                                )}

    def __init__(self, **kargs):
        Node.__init__(self, **kargs)
        assert HAVE_PYCOMEDI, "ComediDevice node depends on the `pycomedi` package, but it could not be imported."

    def _configure(self, device_path = '/dev/comedi0', sampling_rate = 1000., subdevices_params = None):
        self.device_path = device_path
        self.sampling_rate= sampling_rate
        if subdevices_params is None:
            # scan_device_info give the default params
            subdevices_params = self.scan_device_info(device_path)
        self.subdevices_params = subdevices_params
        
        # open/prepare/close the device to get the real sampling_rate!!!
        print('sampling_rate wanted:', self.sampling_rate)
        dev = Device(self.device_path)
        dev.open()
        self.prepare_device(dev)
        dev.close()
        print('real_sampling_rate:', self.real_sampling_rate)


        self.outputs['signals'].spec['shape'] = (-1, self.nb_ai_channel)
        self.outputs['signals'].spec['dtype = '] = self.ai_dtype
        self.outputs['signals'].spec['sampling_rate'] = self.real_sampling_rate
        self.outputs['signals'].spec['gain'] = self.ai_gain
        self.outputs['signals'].spec['offset'] = self.ai_offset
        
    def check_input_specs(self):
        pass
    
    def check_output_specs(self):
        pass

    def _initialize(self):
        self.dev = Device(self.device_path)
        self.dev.open()
        self.prepare_device(self.dev)

        self.head = 0
        self.timer = QtCore.QTimer(singleShot = False, interval = 100)
        self.timer.timeout.connect(self.periodic_poll)

    def _start(self):
        self.last_index = 0
        self.ai_buffer = np.memmap(dev.file, dtype = dt, mode = 'r', shape = (self.internal_size, self.nb_ai_channel))
        self.timer.start()
        self.ai_subdevice.command()
        
    def _stop(self):
        self.timer.stop()
        self.ai_subdevice.cancel()
        del self.ai_buffer

    def _close(self):
        self.dev.close()

    def scan_device_info(self, device_path):
        
        info = { }
        dev = Device(device_path)
        dev.open()
        info['board_name'] = dev.get_board_name()
        info['device_params'] = {'sampling_rate' : 4000., 'device_path': device_path}
        info['subdevices'] = [ ]
        for sub in dev.subdevices():
            if sub.get_type() == SUBDEVICE_TYPE.ai:
                n = sub.get_n_channels()
                info_sub = {'type' : 'AnalogInput', 'nb_channel' : n, 'subdevice_params' :{ 'channel_range' : [-10., 10.]  }, 
                            'by_channel_params' : [ {'channel_index' : i, 'selected' : True, } for i in range(n)] }
                info['subdevices'].append(info_sub)
            #elif sub.get_type() ==  SUBDEVICE_TYPE.di:
                #TODO digital device
        
        dev.close()
        return info

    def prepare_device(self, dev ):
        #Note : only AI for the moment so the first of the list
        ai_params = self.subdevices_params[0]
        
        self.ai_subdevice = dev.find_subdevice_by_type(SUBDEVICE_TYPE.ai, factory=StreamingSubdevice)
        self.ai_dtype = np.dtype(self.ai_subdevice.get_dtype())
        
        aref = AREF.common # AREF.diff,   AREF.grounds
        
        # TODO range : check this with device
        rmin, rmax = channel_range = ai_params['subdevice_params']['channel_range']
        phys_range = rmax - rmin
        logic_range = np.iinfo(dt).max-np.iinfo(dt).min
        self.ai_gain = phys_range/logic_range
        if self.ai_dtype.name.startswith('u'):
            self.ai_offset = rmin
        else:
            self.ai_offset = 0.
        
        self.ai_channels = []
        for ai_param in ai_params:
            if ai_param['selected']:
                chan = self.ai_subdevice.channel(ai_param['channel_index'], factory=AnalogChannel, aref=aref)
                chan.range = chan.find_range(unit=UNIT.volt, min=rmin, max=rmax)
                self.ai_channels.append(chan)
        self.nb_ai_channel = len(self.ai_channels)
        
        # need to align to mmap page size
        
        itemsize = self.ai_dtype.itemsize
        self.internal_size = int(self.ai_subdevice.get_max_buffer_size()//self.nb_ai_channel//itemsize//mmap.PAGESIZE)* mmap.PAGESIZE
        self.ai_subdevice.set_buffer_size(self.internal_size*self.nb_ai_channel*itemsize)
        
        # make comedi comand
        scan_period_ns = int(1e9 / sampling_rate)
        ai_cmd = self.ai_subdevice.get_cmd_generic_timed(len(self.ai_channels), scan_period_ns)
        ai_cmd.chanlist = self.ai_channels
        ai_cmd.start_src = TRIG_SRC.now
        ai_cmd.start_arg = 0
        ai_cmd.stop_src = TRIG_SRC.none
        ai_cmd.stop_arg = 0
        self.ai_subdevice.cmd = ai_cmd
        
        # test cmd 3 times
        for i in range(3):
            rc = self.ai_subdevice.command_test()
            if rc is not None:
                print('Not able to command_test properly')
                return
        
        self.real_sampling_rate = 1.e9/self.ai_subdevice.cmd.convert_arg/len(self.ai_channels)
    
    def periodic_poll(self):
        new_bytes =  ai_subdevice.get_buffer_contents()
        remaining_bytes = new_bytes%(nb_ai_channel*itemsize)
        new_bytes = new_bytes - remaining_bytes
        
        index = (self.last_index + new_bytes//nb_ai_channel//itemsize)%internal_size
        
        if index == last_index :
            return
        
        if index< last_index:
            new_samp = self.internal_size - last_index
            self.head += new_samp
            self.outputs['signals'].send(self.head, self.ai_buffer[ last_index:internal_size, : ])
            last_index = 0

        new_samp = index - last_index
        self.head += new_samp
        self.outputs['signals'].send(self.head, self.ai_buffer[ last_index:index, :])
        
        self.last_index = index%self.internal_size
        
        ai_subdevice.mark_buffer_read(new_bytes)

    
register_node_type(ComediDevice)

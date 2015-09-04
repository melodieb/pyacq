import numpy as np
import mmap


from ..core import Node, register_node_type
from pyqtgraph.Qt import QtCore, QtGui
from pyqtgraph.util.mutex import Mutex

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

    def _configure(self, device_path = '/dev/comedi0', sampling_rate = 1000.):
        self.device_path = device_path
        self.sampling_rate= sampling_rate
        
        # open/prepare/close the device to get the real sampling_rate!!!
        print('sampling_rate wanted:', self.sampling_rate)
        dev = Device(self.device_path)
        dev.open()
        #TODO channel_indexes, self.ai_channel_ranges,
        self.ai_subdevice,  ai_channels, internal_size = prepare_device(dev, channel_indexes, self.ai_channel_ranges,  self.sampling_rate)
        dev.close()
        print('real_sampling_rate:', self.real_sampling_rate)


        self.outputs['signals'].spec['shape'] = (-1, self.nb_ai_channel)
        self.outputs['signals'].spec['dtype = '] = self.dtype
        self.outputs['signals'].spec['sampling_rate'] = self.real_sampling_rate
        self.outputs['signals'].spec['gain'] = 1.#TODO
        self.outputs['signals'].spec['offset'] = 0.#TODO
        
    def check_input_specs(self):
        pass
    
    def check_output_specs(self):
        pass

    def _initialize(self):
        self.dev = Device(self.device_path)
        self.dev.open()
        #TODO channel_indexes, self.ai_channel_ranges,
        self.ai_subdevice,  ai_channels, internal_size = prepare_device(dev, channel_indexes, self.ai_channel_ranges,  self.sampling_rate)

        self.head = 0
        self.timer = QtCore.QTimer(singleShot = False, interval = 100)
        self.timer.timeout.connect(self.periodic_poll)
        
        

        #~ try:
            #~ dev.parse_calibration()
            #~ for chan in ai_channels:
                #~ print chan.index, chan.range, chan.get_converter()
            #~ converters = [c.get_converter() for c in ai_channels]
            #~ print 'with comedi calibrate'
        #~ except PyComediError as e:
        #~ if 1:
            # if comedi calibrate not work we put manual pylynom
            #~ converters = [ ]
            #~ for chan in ai_channels:
                #~ phys_range = float(chan.range.max - chan.range.min)
                #~ logic_range = np.iinfo(dt).max-np.iinfo(dt).min
                #~ conv = CalibratedConverter(to_physical_coefficients=[-phys_range/logic_range*2., phys_range/logic_range,0.,0.],
                                            #~ to_physical_expansion_origin=logic_range//2-1,
                                            #~ )
               #~ converters.append(conv)
            #~ print 'manual callibration with linear polynom'


    def _start(self):
        self.ai_buffer = np.memmap(dev.file, dtype = dt, mode = 'r', shape = (self.internal_size, self.nb_ai_channel))
        self.timer.start()
        self.ai_subdevice.command()
        
        pass

    def _stop(self):
        self.timer.stop()
        self.ai_subdevice.cancel()
        del self.ai_buffer

    def _close(self):
        self.dev.close()

    def get_info(self, device_path):
        def create_analog_subdevice_param(n):
            d = {
                        'type' : 'AnalogInput',
                        'nb_channel' : n,
                        'params' :{  }, 
                        'by_channel_params' : [ {'channel_index' : i, 'selected' : True, range : [-10., 10.] } for i in range(n)],
                    }
            return d
        
        info = { }
        dev = Device(device_path)
        dev.open()
        info['board_name'] = dev.get_board_name()
        info['global_params'] = {'sampling_rate' : 4000., 'device_path': device_path}
        info['subdevices'] = [ ]
        for sub in dev.subdevices():
            if sub.get_type() == SUBDEVICE_TYPE.ai:
                n = sub.get_n_channels()
                info_sub = create_analog_subdevice_param(n)
                info['subdevices'].append(info_sub)
            #elif sub.get_type() ==  SUBDEVICE_TYPE.di:
                #TODO digital device
        
        dev.close()
        return info

    def prepare_device(self, dev, ai_channel_indexes, ai_channel_ranges, sampling_rate):
        self.nb_ai_channel = len(ai_channel_indexes)
        #~ dev.parse_calibration()
        
        self.ai_subdevice = dev.find_subdevice_by_type(SUBDEVICE_TYPE.ai, factory=StreamingSubdevice)
        aref = AREF.common # AREF.diff,   AREF.grounds
        
        self.ai_channels = [ self.ai_subdevice.channel(int(i), factory=AnalogChannel, aref=aref) for i in ai_channel_indexes]
        for chan, range_ in zip(self.ai_channels, ai_channel_ranges):
            chan.range = chan.find_range(unit=UNIT.volt, min=range_[0], max=range_[1])
        
        self.dtype = np.dtype(self.ai_subdevice.get_dtype())
        itemsize = self.dtype.itemsize
        
        # need to align to mmap page size
        resource.getpagesize()
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
        
        # test cmd
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
        
        index = (last_index + new_bytes//nb_ai_channel//itemsize)%internal_size
        
        if index == last_index :
            return
        
        if index< last_index:
            new_samp = self.internal_size - last_index
            self.head += new_samp
            self.outputs['signals'].send(self.head, self.ai_buffer[ last_index:internal_size, : ])
            
            #~ new_samp2 = min(new_samp, arr_ad.shape[1]-(pos+half_size))
            #~ for i,c in enumerate(converters):
                #~ arr_ad[i,pos:pos+new_samp] = c.to_physical(ai_buffer[ last_index:internal_size, i ])
                #~ arr_ad[i,pos+half_size:pos+new_samp2+half_size] = arr_ad[i,pos:pos+new_samp2]
            
            last_index = 0
            #~ abs_pos += int(new_samp)
            #~ pos = abs_pos%half_size

        new_samp = index - last_index
        #~ new_samp2 = min(new_samp, arr_ad.shape[1]-(pos+half_size))
        self.head += new_samp
        self.outputs['signals'].send(self.head, self.ai_buffer[ last_index:index, :])
        
        
        #Analog
        #~ for i,c in enumerate(converters):
            #~ arr_ad[i,pos:pos+new_samp] = c.to_physical(ai_buffer[ last_index:index, i ])
            #~ arr_ad[i,pos+half_size:pos+new_samp2+half_size] = arr_ad[i,pos:pos+new_samp2]
        
        #~ abs_pos += int(new_samp)
        #~ pos = abs_pos%half_size
        #~ last_index = index%internal_size
        
        #~ socketAD.send(msgpack.dumps(abs_pos))
        
        ai_subdevice.mark_buffer_read(new_bytes)

    
register_node_type(PyAudio)

from pyqtgraph.Qt import QtCore
import pyqtgraph as pg
import weakref
import numpy as np

from ..core import (Node, register_node_type, ThreadPollInput)
from ..core.stream import SharedArraySender

import distutils.version
try:
    import scipy.signal
    HAVE_SCIPY = True
    # scpy.signal.sosfilt was introduced in scipy 0.16
    assert distutils.version.LooseVersion(scipy.__version__)>'0.16'
except ImportError:
    HAVE_SCIPY = False

try:
    import pyopencl
    mf = pyopencl.mem_flags
    HAVE_PYOPENCL = True
except ImportError:
    HAVE_PYOPENCL = False


class SosFiltfilt_Scipy:
    """
    Implementation with scipy.
    """
    def __init__(self, coefficients, nb_channel, dtype, chunksize, overlapsize):
        self.coefficients = coefficients
        self.nb_section = coefficients.shape[0]
        self.nb_channel = nb_channel
        self.zi = np.zeros((self.nb_section, 2, self.nb_channel), dtype= dtype)
        self.chunksize = chunksize
        self.overlapsize = overlapsize
    
    def compute_forward(self, chunk):
        forward_chunk_filtered, self.zi = scipy.signal.sosfilt(self.coefficients, chunkk, zi=self.zi, axis=0)
        return forward_chunk_filtered
    
    def compute_backward(self, chunk):
        backward_filtered = scipy.signal.sosfilt(self.coefficients, chunk[::-1, :], zi=None, axis=0)
        backward_filtered = backward_filtered[::-1, :]
        return backward_filtered


class SosFiltfilt_OpenCl_Base:
    def __init__(self, coefficients, nb_channel, dtype, chunksize, overlapsize):
        self.dtype = np.dtype(dtype)
        assert self.dtype == np.dtype('float32')
        self.nb_channel = nb_channel
        self.chunksize = chunksize
        assert self.chunksize is not None, 'chunksize for opencl must be fixed'
        self.overlapsize = overlapsize
        
        self.coefficients = coefficients.astype(self.dtype)
        if self.coefficients.ndim==2: #(nb_section, 6) to (nb_channel, nb_section, 6)
            self.coefficients = np.tile(self.coefficients[None,:,:], (nb_channel, 1,1))
        if not self.coefficients.flags['C_CONTIGUOUS']:
            self.coefficients = self.coefficients.copy()
        self.nb_section = self.coefficients.shape[1]
        
        assert self.coefficients.shape[0]==self.nb_channel, 'wrong coefficients.shape'
        assert self.coefficients.shape[2]==6, 'wrong coefficients.shape'

        self.ctx = pyopencl.create_some_context()
        #TODO : add arguments gpu_platform_index/gpu_device_index
        #self.devices =  [pyopencl.get_platforms()[self.gpu_platform_index].get_devices()[self.gpu_device_index] ]
        #self.ctx = pyopencl.Context(self.devices)        
        self.queue = pyopencl.CommandQueue(self.ctx)

        #host arrays
        self.zi1 = np.zeros((nb_channel, self.n_section, 2), dtype= self.dtype)
        self.output1 = np.zeros((self.chunksize, self.nb_channel), dtype= self.dtype)
        self.output2 = np.zeros((self.chunksize2, self.nb_channel), dtype= self.dtype)
        
        #GPU buffers
        self.coefficients_cl = pyopencl.Buffer(self.ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=self.coefficients)
        self.zi1_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=self.zi1)
        self.zi2_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=self.zi2)
        self.input1_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE, size=self.output1.nbytes)
        self.output1_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE, size=self.output1.nbytes)
        self.input2_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE, size=self.output2.nbytes)
        self.output2_cl = pyopencl.Buffer(self.ctx, mf.READ_WRITE, size=self.output2.nbytes)

        #nb works
        kernel = self.kernel%dict(chunksize = self.chunksize, nb_section=self.nb_section, nb_channel=self.nb_channel)
        prg = pyopencl.Program(self.ctx, kernel)
        self.opencl_prg = prg.build(options='-cl-mad-enable')

class SosFilfilt_OpenCL_V1(SosFiltfilt_OpenCl_Base):
    def __init__(self, coefficients, nb_channel, dtype, chunksize, overlapsize):
        SosFiltfilt_OpenCl_Base.__init__(self, coefficients, nb_channel, dtype, chunksize)
        self.global_size = (self.nb_channel, )
        self.local_size = (self.nb_channel, )
        self.kernel_func_name1 = 'forward_filter'
        self.kernel_func_name2 = 'backward_filter'
        
    def compute_forward(self, chunk):
        if not chunk.flags['C_CONTIGUOUS']:
            chunk = chunk.copy()
        pyopencl.enqueue_copy(self.queue,  self.input1_cl, chunk)

        kern_call = getattr(self.opencl_prg, self.kernel_func_name1)
        event = kern_call(self.queue, self.global_size, self.local_size,
                                self.input1_cl, self.output1_cl, self.coefficients_cl, self.zi_cl)
        event.wait()
        
        pyopencl.enqueue_copy(self.queue,  self.output1, self.output1_cl)
        forward_chunk_filtered = self.output1
        return forward_chunk_filtered
        
    def compute_backward(self, chunk):
        if not chunk.flags['C_CONTIGUOUS']:
            chunk = chunk.copy()
        self.zi2[:]=0
        pyopencl.enqueue_copy(self.queue,  self.zi2_cl, self.zi2)
        pyopencl.enqueue_copy(self.queue,  self.input2_cl, chunk)

        kern_call = getattr(self.opencl_prg, self.kernel_func_name2)
        event = kern_call(self.queue, self.global_size, self.local_size,
                                self.input2_cl, self.output2_cl, self.coefficients_cl, self.zi_cl)
        event.wait()
        
        pyopencl.enqueue_copy(self.queue,  self.output2, self.output2_cl)
        forward_data_filtered = self.output2
        return forward_chunk_filtered
    
        kernel = """
    #define forward_chunksize %(forward_chunksize)d
    #define backward_chunksize %(backward_chunksize)d
    #define nb_section %(nb_section)d
    #define nb_channel %(nb_channel)d

    __kernel void sos_filter(__global  float *input, __global  float *output, __constant  float *coefficients, 
                                                                            __global float *zi, int chunksize, int direction) {

        int chan = get_global_id(0); //channel indice
        
        int offset_filt2;  //offset channel within section
        int offset_zi = chan*nb_section*2;
        
        int idx;

        float w0, w1,w2;
        float res;
        
        for (int section=0; section<nb_section; section++){
        
            offset_filt2 = chan*nb_section*6+section*6;
            
            w1 = zi[offset_zi+section*2+0];
            w2 = zi[offset_zi+section*2+1];
            
            for (int s=0; s<chunksize;s++){
                
                if (direction==1) {idx = s*nb_channel+chan;}
                else if (direction==-1) {idx = (chunksize-s-1)*nb_channel+chan;}
                
                if (section==0)  {w0 = input[idx];}
                else {w0 = output[idx];}
                
                w0 -= coefficients[offset_filt2+4] * w1;
                w0 -= coefficients[offset_filt2+5] * w2;
                res = coefficients[offset_filt2+0] * w0 + coefficients[offset_filt2+1] * w1 +  coefficients[offset_filt2+2] * w2;
                w2 = w1; w1 =w0;
                
                output[idx] = res;
            }
            
            zi[offset_zi+section*2+0] = w1;
            zi[offset_zi+section*2+1] = w2;

        }
       
    }
    
    __kernel void forward_filter(__global  float *input, __global  float *output, __constant  float *coefficients, __global float *zi){
        sos_filter(input, output, coefficients, zi, forward_chunksize, 1);
    }

    __kernel void backward_filter(__global  float *input, __global  float *output, __constant  float *coefficients, __global float *zi) {
        sos_filter(input, output, coefficients, zi, forward_chunksize, -1);
    }
    
    """
    

sosfiltfilt_engines = { 'scipy' : SosFiltfilt_Scipy, 'opencl' : SosFilfilt_OpenCL_V1 }
    


class SosFiltfiltThread(ThreadPollInput):
    def __init__(self, input_stream, output_stream, timeout = 200, parent = None):
        ThreadPollInput.__init__(self, input_stream, timeout = timeout, parent = parent)
        self.output_stream = output_stream

        #TODO when branch stream-performence is done
        self.forward_buffer = ArrayRingBuffer()        

    def process_data(self, pos, data):
        if data is None:
            #sharred_array case
            data =  self.input_stream().get_array_slice(pos, None)
        
        forward_chunk_filtered = self.filter_engine.compute_forward(data)
        self.forward_buffer.new_chunk(forward_data_filtered, pos)

        backward_chunk = self.forward_buffer[pos-self.chunksize-self.overlap:pos, :]
        backward_filtered = self.filter_engine.compute_forward(backward_chunk)
        backward_filtered = backward_filtered[:chunksize]
        
        self.output_stream.send(pos, backward_filtered)
        
    def set_params(self, engine, coefficients, nb_channel, dtype, chunksize, overlapsize):
        #TODO put mutex for self.filter_engine
        assert engine in sosfiltfilt_engines
        EngineClass = sosfiltfilt_engines[engine]
        self.filter_engine = EngineClass(coefficients, nb_channel, dtype, chunksize, overlapsize)



class OverlapFiltfilt(Node,  QtCore.QObject):
    """
    Node for filtering with forward-backward method (filtfilt).
    This use sliding overlap technics.
    
    The chunksize and the overlapsize are important for the accuracy of filtering.
    You need to study them carfully, otherwise the result should be the same as a
    real filtfilt ona long term signal. You must check the residual between real offline filtfitl
    and this online OverlapFiltfilt.
    Note that the chunksize have a strong effect on low frequency.
    
    This uses Second Order (sos) coeeficient.
    It internally use scipy.signal.sosfilt which is available only on scipy >0.16
    
    
    The chunksize need to be fixed.
    For overlapsize there are 2 cases:
      1-  overlapsize<chunksize/2 : natural case. each chunk partailly overlap. 
            The overlap are on sides, the central part come from one chunk.
      2 - overlapsize>chunksize/2: chunk are fully averlapping. There is no central part.
    In the 2 cases, for each arrival of new chunk at [-chunksize:], 
    the computed chunk at [-(chunksize+overlapsize):-overlapsize] is released.


    The coefficients.shape must be (n_section, 6).
    
    If pyopencl is avaible you can do SosFilter.configure(engine='opencl')
    In that cases the coefficients.shape can also be (n_channel, n_section, 6)
    this help for having different filter on each channels.
    
    The opencl engine prefer inernally (channel, sample) ordered.
    In case not a copy is done. So the input ordering do impact performences.
    
    
    
    """
    
    _input_specs = {'signals' : dict(streamtype = 'signals')}
    _output_specs = {'signals' : dict(streamtype = 'signals')}
    
    def __init__(self, parent = None, **kargs):
        QtCore.QObject.__init__(self, parent)
        Node.__init__(self, **kargs)
        assert HAVE_SCIPY, "SosFilter need scipy>0.16"
    
    def _configure(self, chunksize=1024, overlapsize=512, coefficients = None, engine='numpy'):
        """
        Set the coefficient of the filter.
        See http://scipy.github.io/devdocs/generated/scipy.signal.sosfilt.html for details.
        """
        self.chunksize = chunksize
        self.overlapsize = overlapsize
        self.engine = engine
        self.set_coefficients(coefficients)

    def after_input_connect(self, inputname):
        self.nb_channel = self.input.params['nb_channel']
        for k in ['sample_rate', 'dtype', 'nb_channel', 'shape', 'timeaxis']:
            self.output.spec[k] = self.input.params[k]
    
    def _initialize(self):
        self.thread = SosFiltfiltThread(self.input, self.output)
        self.thread.set_params(self.engine, self.coefficients, self.nb_channel,
                            self.output.params['dtype'], self.chunksize, self.overlapsize)
        
    
    def _start(self):
        self.thread.last_pos = None
        self.thread.start()
    
    def _stop(self):
        self.thread.stop()
        self.thread.wait()
    
    def set_coefficients(self, coefficients):
        self.coefficients = coefficients
        if self.initialized():
            self.thread.set_params(self.engine, self.coefficients, self.nb_channel,
                    self.output.params['dtype'], self.chunksize, self.overlapsize)

register_node_type(OverlapFiltfilt)
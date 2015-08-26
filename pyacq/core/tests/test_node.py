import time

from pyacq import create_manager

from pyqtgraph.Qt import QtCore, QtGui
from pyacq.core.tests.fakenodes import FakeSender, FakeReceiver, ReceiverWidget


def test_stream_between_local_nodes():
    # create local nodes in QApplication
    app = QtGui.QApplication([])

    sender = FakeSender()
    stream_spec = dict(protocol = 'tcp', interface = '127.0.0.1', port = '*',
                        transfertmode = 'plaindata', streamtype = 'analogsignal',
                        dtype = 'float32', shape = (-1, 16), compression ='',
                        scale = None, offset = None, units = '' )
    sender.configure(sample_interval = 0.001)
    sender.outputs['signals'].configure(**stream_spec)
    #sender.output.configure(**stream_spec)
    sender.initialize()
    
    receiver = FakeReceiver()
    receiver.configure()
    receiver.inputs['signals'].connect(sender.outputs['signals'])
    #receiver.input.connect(sender.output)
    receiver.initialize()
    
    # start them for a while
    sender.start()
    receiver.start()

    timer = QtCore.QTimer(singleShot = True, interval = 2000)
    timer.timeout.connect(app.quit)
    timer.start()
    
    app.exec_()
    

def test_stream_between_remote_nodes():
    # this is done at Manager level the manager do known the connection
    man = create_manager()
    nodegroup = man.create_nodegroup()
    
    nodegroup.register_node_from_module('pyacq.core.tests.fakenodes', 'FakeSender' )
    nodegroup.register_node_from_module('pyacq.core.tests.fakenodes', 'FakeReceiver' )
    
    # create ndoes
    sender = nodegroup.create_node('FakeSender', name = 'sender')
    stream_spec = dict(protocol = 'tcp', interface = '127.0.0.1', port = '*',
                        transfertmode = 'plaindata', streamtype = 'analogsignal',
                        dtype = 'float32', shape = (-1, 16), compression ='',
                        scale = None, offset = None, units = '' )
    sender.configure(sample_interval = 0.001)
    sender.outputs['signals'].configure(**stream_spec)
    sender.initialize()
    
    receiver = nodegroup.create_node('FakeReceiver', name = 'receiver')
    receiver.configure()
    receiver.inputs['signals'].connect(sender.outputs['signals'])
    receiver.initialize()
    
    # start them for a while
    sender.start()
    receiver.start()
    print(nodegroup.any_node_running())
    
    time.sleep(2.)
    
    sender.stop()
    receiver.stop()
    print(nodegroup.any_node_running())

def test_stream_between_local_and_remote_nodes():
    # this is done at Manager level the manager do known the connection
    man = create_manager()
    nodegroup = man.create_nodegroup()
    
    nodegroup.register_node_from_module('pyacq.core.tests.fakenodes', 'FakeSender' )
    
    # create ndoes
    sender = nodegroup.create_node('FakeSender', name = 'sender')
    stream_spec = dict(protocol = 'tcp', interface = '127.0.0.1', port = '*',
                        transfertmode = 'plaindata', streamtype = 'analogsignal',
                        dtype = 'float32', shape = (-1, 16), compression ='',
                        scale = None, offset = None, units = '' )
    sender.configure(sample_interval = 0.001)
    sender.output.configure(**stream_spec)
    sender.initialize()
    
    # create local nodes in QApplication
    app = QtGui.QApplication([])
    
    receiver = FakeReceiver()
    receiver.configure()
    receiver.input.connect(sender.output)
    receiver.initialize()
    
    # start them for a while
    sender.start()
    receiver.start()

    timer = QtCore.QTimer(singleShot = True, interval = 2000)
    timer.timeout.connect(app.quit)
    timer.start()
    
    app.exec_()
    
    sender.stop()
    receiver.stop()



def test_visual_node_both_in_main_qapp_and_remote_qapp():
    man = create_manager()
    nodegroup = man.create_nodegroup()
    
    nodegroup.register_node_from_module('pyacq.core.tests.fakenodes', 'FakeSender' )
    nodegroup.register_node_from_module('pyacq.core.tests.fakenodes', 'ReceiverWidget' )


    # create ndoes
    sender = nodegroup.create_node('FakeSender', name = 'sender')
    stream_spec = dict(protocol = 'tcp', interface = '127.0.0.1', port = '*',
                        transfertmode = 'plaindata', streamtype = 'analogsignal',
                        dtype = 'float32', shape = (-1, 16), compression ='',
                        scale = None, offset = None, units = '' )
    sender.configure(sample_interval = 0.001)
    sender.output.configure(**stream_spec)
    sender.initialize()
    
    #receiver0 is in remote QApp (in nodegroup)
    receiver0 = nodegroup.create_node('ReceiverWidget', name = 'receiver0', tag ='<b>I am in distant QApp</b>')
    receiver0.configure()
    receiver0.input.connect(sender.output)
    receiver0.initialize()
    receiver0.show()
    
    
    #receiver1 is in local QApp
    app = QtGui.QApplication([])
    receiver1 = ReceiverWidget(name = 'receiver1', tag ='<b>I am in local QApp</b>')
    receiver1.configure()
    receiver1.input.connect(sender.output)
    receiver1.initialize()
    receiver1.show()
    
    # start them for a while
    sender.start()
    receiver0.start()
    receiver1.start()
    print(nodegroup.any_node_running())
    
    timer = QtCore.QTimer(singleShot = True, interval = 7000)
    timer.timeout.connect(app.quit)
    timer.start()
    app.exec_()
    
    
    sender.stop()
    receiver0.stop()
    receiver1.stop()
    print(nodegroup.any_node_running())
    
    

if __name__ == '__main__':
    test_stream_between_local_nodes()
    test_stream_between_remote_nodes()
    test_stream_between_local_and_remote_nodes()
    test_visual_node_both_in_main_qapp_and_remote_qapp()



import os
import time
import weakref
import concurrent.futures
import threading
from logging import info
import zmq

from .serializer import MsgpackSerializer
from .proxy import ObjectProxy


class RPCClient(object):
    """Connection to an RPC server.
    
    This class is a proxy for methods provided by the remote server. It is
    meant to be used by accessing and calling remote procedure names::
    
        client = RPCClient('tcp://localhost:5274')
        future = client.my_remote_procedure_name(...)
        result = future.result()
        
    Parameters
    ----------
    address : URL
        Address of RPC server to connect to.
    """
    
    clients_by_thread = {}  # (thread_id, rpc_addr): client
    
    @staticmethod
    def get_client(address):
        """Return the RPC client for this thread and a given server address.
        
        If no client exists already, then a new one will be created. If the 
        server is running in the current thread, then return None.
        """
        try:
            return RPCClient.clients_by_thread[(threading.current_thread().ident, address)]
        except KeyError:
            from .server import RPCServer
            server = RPCServer.get_server()
            if server is not None and server.address == address:
                # address is for local RPC server; don't need client.
                return None
            else:
                # create a new client!
                return RPCClient(address)
    
    def __init__(self, address):
        key = (threading.current_thread().ident, address)
        if key in RPCClient.clients_by_thread:
            raise KeyError("An RPCClient instance already exists for this address."
                " Use RPCClient.get_client(address) instead.")
        
        # ROUTER is fully asynchronous and may connect to multiple endpoints.
        # We can use ROUTER to allow this socket to connect to multiple servers.
        # However this adds complexity with little benefit, as we can just use
        # a poller to check for messages on multiple sockets if desired.
        #self.socket = zmq.Context.instance().socket(zmq.ROUTER)
        #self._name = ('%d-%x' % (os.getpid(), id(self))).encode()
        #self.socket.setsockopt(zmq.IDENTITY, self._name)
        
        # DEALER is fully asynchronous--we can send or receive at any time, and
        # unlike ROUTER, it only connects to a single endpoint.
        self.socket = zmq.Context.instance().socket(zmq.DEALER)
        self.sock_name = ('%d-%x:%s' % (os.getpid(), threading.current_thread().ident, address.decode())).encode()
        self.socket.setsockopt(zmq.IDENTITY, self.sock_name)
        
        info("RPC connect %s => %s", self.sock_name, address)
        self.socket.connect(address)
        self.next_request_id = 0
        self.futures = weakref.WeakValueDictionary()
        
        RPCClient.clients_by_thread[key] = self
        
        self.connect_established = False
        self.establishing_connect = False

        # Proxies we have received from other machines. 
        self.proxies = {}

        # For unserializing results returned from servers. This cannot be
        # used to send proxies of local objects unless there is also a server
        # for this thread..
        self.serializer = MsgpackSerializer()
        
        self.ensure_connection()

    def __getitem__(self, name):
        return self.send('getitem', opts={'name': name}, call_sync='sync')

    def send(self, action, opts=None, return_type='auto', call_sync='sync', timeout=10.0):
        """Send a request to the remote process.
        
        Parameters
        ----------
        action : str
            The action to invoke on the remote process.
        opts : None or dict
            Extra options to be sent with the request. Each action requires a
            different set of options.
        return_type : 'auto' | 'proxy' | None
            If 'proxy', then the return value is sent by proxy. If 'auto', then
            the server decides based on the return type whether to send a proxy.
            If None, then no response will be sent.
        call_sync : str
            If 'sync', then block and return the result when it becomes available.
            If 'async', then return a Future instance immediately.
            If 'off', then ask the remote server NOT to send a response and
            return None immediately.
        timeout : float
            The amount of time to wait for a response when in synchronous
            operation (call_sync='sync').
        """
        
        cmd = {'action': action, 'return_type': return_type, 
               'opts': opts}
        if call_sync != 'off':
            req_id = self.next_request_id
            self.next_request_id += 1
            cmd['req_id'] = req_id
        info("RPC send req: %s => %s", self.socket.getsockopt(zmq.IDENTITY), cmd)
        
        # double-serialize opts to ensure that cmd can be read even if opts
        # cannot.
        # TODO: This might be expensive; a better way might be to send opts in
        # a subsequent packet, but this makes the protocol more complicated..
        cmd['opts'] = self.serializer.dumps(cmd['opts'])
        cmd = self.serializer.dumps(cmd)
        
        self.socket.send(cmd)
        
        # If using ROUTER, we have to include the name of the endpoint to which
        # we are sending
        #self.socket.send_multipart([name, cmd])
        if call_sync == 'off':
            return
        
        fut = Future(self, req_id)
        self.futures[req_id] = fut
        if call_sync == 'async':
            return fut
        elif call_sync == 'sync':
            return fut.result(timeout=timeout)
        else:
            raise ValueError('Invalid call_sync value: %s' % call_sync)

    def call_obj(self, obj, args=None, kwargs=None, **kwds):
        opts = {'obj': obj, 'args': args, 'kwargs': kwargs} 
        return self.send('call_obj', opts=opts, **kwds)

    #def get_obj_attr(self, obj_id, attributes, return_type='auto'):
        #opts = {'obj_id': obj_id, 'attributes': attributes}
        #return self.send('get_obj_attrs', return_type=return_type, opts=opts)

    def transfer(self, obj, **kwds):
        return self.send('transfer', opts={'obj': obj}, **kwds)

    def ensure_connection(self, timeout=1.0):
        """Make sure RPC server is connected and available.
        """
        if self.establishing_connect:
            return
        self.establishing_connect = True
        try:
            start = time.time()
            while time.time() < start + timeout:
                fut = self.send('ping', call_sync='async')
                try:
                    result = fut.result(timeout=0.1)
                    self.connect_established = True
                    return
                except TimeoutError:
                    continue
            raise TimeoutError("Could not establish connection with RPC server.")
        finally:
            self.establishing_connect = False

    def process(self):
        """Process all available incoming messages.
        
        Return immediately if no messages are available.
        """
        while True:
            try:
                # if using ROUTER, then we receive the name of the endpoint
                # followed by the message
                #name = self.socket.recv(zmq.NOBLOCK)
                #msg = self.socket.recv()
                
                msg = self.socket.recv(zmq.NOBLOCK)
                msg = self._serializer.loads(msg)
                self.process_msg(name, msg)
            except zmq.error.Again:
                break  # no messages left

    def process_until_future(self, future, timeout=None):
        """Process all incoming messages until receiving a result for *future*.
        
        If the future result is not raised before the timeout, then raise
        TimeoutError.
        """
        start = time.perf_counter()
        while not future.done():
            # wait patiently with blocking calls.
            if timeout is None:
                itimeout = -1
            else:
                dt = time.perf_counter() - start
                itimeout = int((timeout - dt) * 1000)
                if itimeout < 0:
                    raise TimeoutError("Timeout waiting for Future result.")
            try:
                self.socket.setsockopt(zmq.RCVTIMEO, itimeout)
                msg = self.socket.recv()
                msg = self.serializer.loads(msg)
            except zmq.error.Again:
                raise TimeoutError("Timeout waiting for Future result.")
            
            self.process_msg(msg)

    def process_msg(self, msg):
        """Handle one message received from the remote process.
        
        This takes care of assigning return values or exceptions to existing
        Future instances.
        """
        info("RPC recv res: %s", msg)
        if msg['action'] == 'return':
            req_id = msg['req_id']
            fut = self.futures.pop(req_id, None)
            if fut is None:
                return
            if msg['error'] is not None:
                exc = RemoteCallException(*msg['error'])
                fut.set_exception(exc)
            else:
                fut.set_result(msg['rval'])
        else:
            raise ValueError("Invalid action '%s'" % msg['action'])
    
    def close(self):
        # reference management is disabled for now..
        #self.send('release_all', return_type=None) 
        self.socket.close()

    def __del__(self):
        self.close()



class RemoteCallException(Exception):
    def __init__(self, type_str, tb_str):
        self.type_str = type_str
        self.tb_str = tb_str
        
    def __str__(self):
        msg = '\n===> Remote exception was:\n' + ''.join(self.tb_str)
        return msg


class Future(concurrent.futures.Future):
    """Represents a return value from a remote procedure call that has not
    yet arrived.
    
    Use `done()` to determine whether the return value (or an error message)
    has arrived, and `result()` to get the return value (or raise an
    exception).
    """
    def __init__(self, socket, call_id):
        concurrent.futures.Future.__init__(self)
        self.socket = socket
        self.call_id = call_id
    
    def cancel(self):
        return False

    def result(self, timeout=None):
        """Return the result of this Future.
        
        If the result is not yet available, then this call will block until
        the result has arrived or the timeout elapses.
        """
        self.socket.process_until_future(self, timeout=timeout)
        return concurrent.futures.Future.result(self)


# vim:set expandtab:
# vim:set foldmethod=indent:
# vim:set shiftwidth=4:
# vim:set tabstop=4:

from __future__ import generators
import errno
import os
import re
import select
import socket
import sys
import time
import isconf
from isconf.Globals import *
from isconf.GPG import GPG
from isconf.ISdlink1 import ISdlink1
import isconf.ISFS1
from isconf.Kernel import kernel, Event, Buffer
import rpc822

class EchoTest:

    def __init__(self,transport):
        self.transport=transport

    def run(self,*args,**kwargs):
        kernel.info("starting EchoTest.run")
        rxd = ''
        while True:
            yield None
            # rxd = self.transport.read(1)
            # self.transport.write(rxd)
            rxd += self.transport.read(1)
            if '\n' in rxd: 
                self.transport.write(rxd)
                rxd = ''
        return 

class Server:

    def __init__(self):
        self.varisconf = os.environ['VARISCONF']
        self.port = int(os.environ['ISCONF_PORT'])
        self.ctlpath = "%s/.ctl" % self.varisconf
        self.pidpath = "%s/.pid" % self.varisconf
        if not os.path.isdir(self.varisconf):
            os.makedirs(self.varisconf,0700)
        open(self.pidpath,'w').write("%d\n" % os.getpid())

    def serve(self):
        """be a server forever"""
        self.gpgsetup()
        kernel.run(self.init())

    def init(self):
        """parent of all tasks"""
        # set up FBP netlist 
        clin = Buffer()
        clout = Buffer()
        tofs = Buffer()
        frfs = Buffer()
        toca = Buffer()
        frca = Buffer()

        # kernel.spawn(UXmgr(frsock=clin,tosock=clout))
        # kernel.spawn(ISconf(cmd=clin,res=clout,fsreq=tofs,fsres=frfs))
        # kernel.spawn(ISFS(cmd=tofs,res=frfs,careq=toca,cares=frca))
        # cache = Cache(cmd=toca,res=frca,
        #         bcast=bcast,ucast=ucast,frnet=frnet
        #     )
        # kernel.spawn(cache)
        # kernel.spawn(UDPmgr(cmd=toca,res=frca,tonet=tonet,frnet=frnet))


        unix = UNIXServerFactory(path=self.ctlpath)
        yield kernel.sigspawn, unix.run()
        tcp = TCPServerFactory(port=self.port)
        yield kernel.sigspawn, tcp.run()
        while True:
            # periodic housekeeping
            print "mark", time.time()
            kernel.info(kernel.ps())
            yield kernel.sigsleep, 10

    def gpgsetup(self):
        gnupghome = "%s/.gnupg" % self.varisconf
        gpg = GPG(gnupghome=gnupghome)
        if not gpg.list_keys(secret=True):
            host = socket.gethostname()
            genkeyinput = """
                Key-Type: RSA
                Key-Length: 1024
                Name-Real: ISdlink Server on %s
                Name-Comment: Created by %s
                Name-Email: isdlink@%s
                Expire-Date: 0
                %%commit
            \n""" % (host, sys.argv[0], host)
            gpg.gen_key(genkeyinput)

class ServerFactory:

    def run(self,):
        global peers
        while True:
            yield None
            # accept new connections
            try:
                (peersock, address) = self.sock.accept()
                sock = ServerSocket(sock=peersock,address=address)

                # XXX this is a dynamic FBP net -- hook it into clin,
                # clout, do the mux/demux for it


                yield kernel.sigspawn, layer.run()
            except socket.error, (error, strerror):
                if not error == errno.EAGAIN:
                    raise
            
class ServerSocket:
    """a TCP or UNIX domain server socket"""

    def __init__(self,sock,address,chunksize=4096):
        self.chunksize = chunksize
        self.sock = sock
        self.address = address
        self.role = 'master'
        self.state = 'up'
        self.txd = ''
        self.rxd = ''
        self.protocol = None
    
    def abort(self,msg=''):
        self.write(msg + "\n")
        self.close()

    def msg(self,msg):
        self.write(msg + "\n")

    def close(self):
        self.state = 'closing'

    # figure out what protocol to route the data to
    def dispatch(self,rxd):
        if verbose: print "dispatcher running"
        if '\n' not in rxd and len(rxd) > 128:
            self.abort("subab newline expected -- stop babbling")
            return 

        match = re.match("isconf(\d+)\n", rxd)
        if match and match.group(1) == '4':
            self.read(len(match.group())) # throw away this line
            self.protocol = isconf.ISconf4.ISconf4(transport=self)
            kernel.spawn(self.protocol.run())
            if verbose: print "found isconf4"
            return 

        match = re.match("rpc822stream\n", rxd)
        if match:
            self.read(len(match.group())) # throw away this line
            self.protocol = Server.rpc822stream(self)
            kernel.spawn(self.protocol.start(self,address=self.address))
            if verbose: print "found rpc822stream"
            return 

        self.abort("supun protocol unsupported")

    def read(self,size):
        actual = min(size,len(self.rxd))
        if actual == 0:
            return ''
        rxd = self.rxd[:actual]
        # print "reading", rxd
        self.rxd = self.rxd[actual:]
        return rxd
    
    def write(self,data):
        # print "writing", repr(data)
        self.txd += data
    
    def run(self,*args,**kwargs):
        busy = False
        while True:
            if busy:
                yield kernel.sigbusy
            else:
                yield None
            # XXX peer timeout ck
            busy = False

            # find pending reads and writes 
            s = self.sock
            try:
                (readable, writeable, inerror) = \
                    select.select([s],[s],[s],0)
            except:
                inerror = [s]
        
            # handle errors
            if s in inerror or self.state == 'close':
                try:
                    s.close()
                except:
                    pass
                self.state = 'down'
                break

            # do reads
            if s in readable:
                # read a chunk
                try:
                    rxd = self.sock.recv(self.chunksize)
                except:
                    pass
                # print "receiving", rxd
                self.rxd += rxd
                if self.rxd:
                    busy = True
                else:
                    try:
                        s.shutdown(0)
                    except:
                        pass
                    self.state = 'closing'
                if self.state == 'up' and not self.protocol:
                    self.dispatch(self.rxd)

            # do writes
            if s in writeable:
                if len(self.txd) <= 0:
                    if self.state == 'closing':
                        self.state = 'close'
                    continue
                # print "sending", self.txd
                try:
                    sent = self.sock.send(self.txd)
                    # print "sent " + self.txd
                except:
                    try:
                        s.shutdown(1)
                    except:
                        pass
                    self.state = 'closing'
                if sent:
                    busy = True
                    # txd is a fifo -- clear as we send bytes off the front
                    self.txd = self.txd[sent:]
                
class TCPServerFactory(ServerFactory):

    def __init__(self, port, chunksize=4096):
        self.chunksize = chunksize
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.sock.setblocking(0)
        self.sock.bind(('', self.port))     
        self.sock.listen(5)
    
class UNIXServerFactory(ServerFactory):

    def __init__(self, path, chunksize=4096):
        self.chunksize = chunksize
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.sock.setblocking(0)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.sock.bind(self.path)
        self.sock.listen(5)
    

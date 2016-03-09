from __future__ import print_function, unicode_literals, absolute_import

import os
import tempfile
import unittest

import time

import itertools

import array
from binstar_client.tests import urlmock
from six import text_type
from six.moves import urllib

from binstar_build_client import BinstarBuildAPI
from binstar_build_client.worker.utils import build_log
from binstar_build_client.worker.utils.build_log import BuildLog
from binstar_build_client.worker.utils.generator_file import GeneratorFile


class TestBuildLog(unittest.TestCase):

    def add_log_entry(self, request):
        # werkzeug decodes form content with errors=replace, but urllib.parse.parse_qsl
        # returns `str` in Python 2. We should decode for consistent behavior.
        log = {
           key: value if isinstance(value, text_type) else value.decode('utf-8', 'replace')
           for key, value in urllib.parse.parse_qsl(request.body)
        }
        self.log_entries.append(log)

    def setUp(self):
        tempdir = tempfile.gettempdir()
        self.filepath = os.path.join(tempdir, 'build-log-test.txt')
        self.log_entries = []
        self.urls = urlmock.Registry()
        self.urls.__enter__()

        def side_effect():
            # TODO: there is no nice way to get the request in a side_effect
            # callback.
            request = collect._resps[-1][-1]
            self.add_log_entry(request)

        collect = self.urls.register(
            method='POST',
            path='/build-worker/user_name/queue_name/worker_id/jobs/123/tagged-log',
            content={'terminated':False},
            side_effect=side_effect)

    def tearDown(self):
        self.urls.__exit__()
        self.urls = None
        try:
            os.unlink(self.filepath)
        except (OSError, IOError):
            pass

    def mk_log(self, **kwargs):
        return BuildLog(
            BinstarBuildAPI(),
            "user_name",
            "queue_name",
            "worker_id",
            123,
            filename=self.filepath,
            **kwargs)

    def assertContentEqual(self, expected):
        with open(self.filepath, 'rb') as fd:
            local_log = fd.read()
        server_log = b''.join(log['msg'].encode('utf-8') for log in self.log_entries)

        self.assertEqual(expected, local_log)
        self.assertEqual(expected, server_log)

    def test_write(self):
        with self.mk_log() as log:
            self.assertTrue(log.writable())
            res = log.writeline(b'test\n')
            self.assertEqual(5, res)

            with self.assertRaises(TypeError):
                log.writeline('this is unicode')

        self.assertContentEqual(b'test\n')

    def test_write_invalid_unicode(self):
        with self.mk_log() as log:
            log.writeline(b'this can not be unicode \xe2\n')

        with open(self.filepath, 'rb') as fd:
            local_log = fd.read()
        server_log = b''.join(log['msg'].encode('utf-8') for log in self.log_entries)

        self.assertEqual(local_log, b'this can not be unicode \xe2\n')
        # the server replaces the bad byte with a unicode replacement character
        # (when performing form decoding)
        self.assertEqual(server_log, 'this can not be unicode \ufffd\n'.encode('utf-8'))

    def test_read(self):
        with self.mk_log() as log:
            self.assertFalse(log.readable())

    def test_context(self):
        log = self.mk_log()
        self.assertFalse(log.fd.closed)
        with log:
            self.assertFalse(log.fd.closed)
        self.assertTrue(log.fd.closed)

    def test_invalid_metadata_preserved(self):
        with self.mk_log() as log:
            log.writeline(build_log.METADATA_PREFIX + b'abcd')

        self.assertEqual(log.metadata, {'section': 'dequeue_build'})
        self.assertContentEqual(build_log.METADATA_PREFIX + b'abcd')

    def test_metadata_stripped(self):
        # The metadata should not be included in the file output or sent to
        # the server.

        with self.mk_log() as log:
            log.writeline(build_log.encode_metadata({'section': 'oops', 'command': 'echo "oops"'}))
            log.writeline(b'echo "oops"\n')
            log.writeline(b'oops\n')
            log.writeline(build_log.encode_metadata({'section': 'nope', 'command': 'echo "nope"'}))
            log.writeline(b'echo "nope"\n')
            log.writeline(b'nope\n')

        self.assertContentEqual(
            b'''\
echo "oops"
oops
echo "nope"
nope
''')

        for entry in self.log_entries:
            self.assertIn(entry['section'], entry['msg'])


    def test_loud_shows_cr(self):
        with self.mk_log() as log:
            log.writeline(b'this is some normal data\n')
            log.writeline(b'this is data that is overwriting\r')
            log.writeline(b'this is more normal data\n')

        self.assertContentEqual(
            b'this is some normal data\n'
            b'this is data that is overwriting\r'
            b'this is more normal data\n'
        )

    def test_quiet_hides_cr(self):
        with self.mk_log(quiet=True) as log:
            log.writeline(b'this is some normal data\n')
            log.writeline(b'this is data that is overwriting\r')
            log.writeline(b'this is more normal data\n')

        self.assertContentEqual(
            b'this is some normal data\n'
            b'this is more normal data\n'
        )

    def test_quiet_shows_crlf(self):
        with self.mk_log(quiet=True) as log:
            log.writeline(b'this is some normal data\n')
            log.writeline(b'this is some normal data with crlf\r\n')
            log.writeline(b'this is more normal data\n')

        self.assertContentEqual(
            b'this is some normal data\n'
            b'this is some normal data with crlf\r\n'
            b'this is more normal data\n'
        )


import io

def wrap_file(fd):
    buf = io.BufferedReader(fd)
    return io.TextIOWrapper(buf, encoding='utf-8', errors='replace', newline='')


class TestBuffering(unittest.TestCase):

    def test_wrapper(self):
        def output():
            yield b'Content\n'
            yield b'Content\rall\rin\ra single\rrow\n'
            yield b'Content '
            yield b'In a line\n'
            yield b'Windows output\r\n'
        fd = wrap_file(GeneratorFile(output()))

        lines = fd.readlines()

        self.assertEqual([
            'Content\n',
            'Content\r',
            'all\r',
            'in\r',
            'a single\r',
            'row\n',
            'Content In a line\n',
            'Windows output\r\n',
        ], lines)

    def test_generator_file(self):
        def output():
            yield b'Some '
            yield b'output that is larger than the buffer\n'
            yield b'And more'
        fd = GeneratorFile(output())

        bufs = []
        small_buf = bytearray(8)
        n = fd.readinto(small_buf)
        while n:
            bufs.append(bytes(small_buf[:n]))
            n = fd.readinto(small_buf)

        self.assertEqual(b''.join(bufs),
                         b'Some output that is larger than the buffer\nAnd more')

    def test_generator_file_array(self):
        def output():
            yield b'Some '
            yield b'output that is larger than the buffer\n'
            yield b'And more'
        fd = GeneratorFile(output())

        bufs = []
        small_buf = array.array('b', b'\x00' * 8)
        n = fd.readinto(small_buf)
        while n:
            bufs.append(small_buf.tostring()[:n])
            n = fd.readinto(small_buf)

        self.assertEqual(b''.join(bufs),
                         b'Some output that is larger than the buffer\nAnd more')



    def test_buffer_send_when_available(self):
        # how long to wait to send the second chunk of data
        sleep_time = .1  # seconds

        def output():
            yield b'Data\n'
            time.sleep(sleep_time)
            yield b'More data\n'

        fd = wrap_file(GeneratorFile(output()))

        time_0 = time.time()
        line_1, time_1 = (fd.readline(), time.time())
        line_2, time_2 = (fd.readline(), time.time())

        dur_1 = time_1 - time_0
        dur_2 = time_2 - time_1

        self.assertLess(dur_1, .1, "Should not wait for the first line")
        self.assertGreater(dur_2, .1, "Should wait for the second line")



if __name__ == '__main__':
    unittest.main()

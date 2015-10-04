import os
import time

try:
    from StringIO import StringIO
except:
    from io import StringIO

try:
    import unittest2 as unittest
except ImportError:
    import unittest

from cplay import cplay


class MockKeymap(object):
    def __init__(self, available=[]):
        self.available = available
        self.log = []

    def process(self, c):
        if c in self.available:
            self.log.append(c)
            return True


class MockMacro(object):
    def command_macro(self): pass
    def do_macro(self, ch): pass
    def run_macro(self, c): pass


class MockCounter(object):
    def counter(self, elapsed, remaining): pass
    def toggle_mode(self): pass


class MockProgress(object):
    def progress(self, value): pass


class MockFilelist(object):
    def __init__(self):
        self.cwd = ''
    def listdir(self, quiet=False, prevdir=None): pass
    def listdir_maybe(self, now=0): pass


class MockTimeout(object):
    def add(self, timeout, func, args=()): pass
    def remove(self, tid): pass
    def check(self, now): pass


class MockKeymapStack(object):
    def __init__(self):
        self.log = []
        self.stack = []

    def push(self, item):
        self.stack.append(item)

    def pop(self):
        return self.stack.pop()

    def process(self, code):
        self.log.append(code)


class MockPlaylist(object):
    def add(self, pathname, quiet=False): pass
    def command_delete_all(self): pass
    def command_toggle_random(self): pass
    def command_toggle_repeat(self): pass
    def change_active_entry(self, direction): pass

    # cplay.APP.playlist.stop ?


class MockApp(object):
    def __init__(self):
        self.restricted = None
        self.video = False
        self.fifo = '/tmp/cplay-test'
    def setup(self): pass
    def cleanup(self): pass
    def run(self): pass
    def cursor(self, visibility): pass
    def quit(self, status=0): pass


class MockStatus(object):
    def __init__(self):
        self.log = []

    def length(self):
        return 0

    def restore_default_status(self): pass
    def set_default_status(self, message): pass

    def status(self, message, duration=0):
        self.log.append(message)


class MockBackend(object): pass


class MockPlayer(object):
    def play(self, entry, offset=0): pass
    def toggle_pause(self): pass
    def toggle_stop(self): pass
    def seek(self, offset, relative): pass
    def next_prev_song(self, direction): pass
    def mixer(self, cmd, arg=None): pass
    def key_volume(self, ch): pass
    def next_prev_eq(self, direction): pass
    def incr_reset_decr_speed(self, signum): pass


def patch_services():
    cplay.APP = MockApp()
    cplay.APP.keymapstack = MockKeymapStack()
    # cplay.APP.window = MockWindow()
    cplay.APP.player = MockPlayer()
    cplay.APP.timeout = MockTimeout()
    cplay.APP.input = cplay.Input()
    cplay.APP.macro = MockMacro()
    cplay.APP.status = MockStatus()
    cplay.APP.progress = MockProgress()
    cplay.APP.counter = MockCounter()
    cplay.APP.playlist = MockPlaylist()
    cplay.APP.filelist = MockFilelist()


class TestCut(unittest.TestCase):
    def test_right(self):
        s = 'abcdefghijklmnopqrstuvwxyz'
        self.assertEqual(cplay.cut(s, 10), 'abcdefghi>')

    def test_left(self):
        s = 'abcdefghijklmnopqrstuvwxyz'
        self.assertEqual(cplay.cut(s, 10, left=True), '<rstuvwxyz')


class TestKeymapStack(unittest.TestCase):
    def setUp(self):
        self.keymapstack = cplay.KeymapStack()

    def test_process(self):
        keymap1 = MockKeymap('abc')
        self.keymapstack.push(keymap1)

        keymap2 = MockKeymap('cde')
        self.keymapstack.push(keymap2)

        self.keymapstack.process('a')
        self.keymapstack.process('a')
        self.keymapstack.process('b')
        self.keymapstack.process('c')
        self.keymapstack.process('d')
        self.keymapstack.process('x')
        self.keymapstack.pop()
        self.keymapstack.process('c')

        self.assertListEqual(keymap1.log, ['a', 'a', 'b', 'c'])
        self.assertListEqual(keymap2.log, ['c', 'd'])


class TestKeymap(unittest.TestCase):
    def setUp(self):
        self.keymap = cplay.Keymap()

    def test_empty(self):
        self.assertFalse(self.keymap.process(ord('a')))

    def test_bind(self):
        state = []
        self.keymap.bind('a', lambda key: state.append(key))
        self.assertFalse(self.keymap.process(ord('b')))
        self.assertFalse(self.keymap.process(9999999999999999))
        self.assertTrue(self.keymap.process(ord('a')))
        self.assertListEqual(state, [ord('a')])

    def test_bind_with_args(self):
        state = []
        self.keymap.bind('a', lambda x, y: state.append(x + y), [1, 2])
        self.assertTrue(self.keymap.process(ord('a')))
        self.assertListEqual(state, [3])

    def test_bind_multiple(self):
        state = []
        self.keymap.bind(['a', 'b'], lambda key: state.append(key))
        self.assertTrue(self.keymap.process(ord('a')))
        self.assertTrue(self.keymap.process(ord('b')))
        self.assertTrue(self.keymap.process(ord('a')))
        self.assertListEqual(state, [ord('a'), ord('b'), ord('a')])


class TestWindow(unittest.TestCase): pass
class TestProgressWindow(unittest.TestCase): pass
class TestStatusWindow(unittest.TestCase): pass
class TestCounterWindow(unittest.TestCase): pass
class TestRootWindow(unittest.TestCase): pass
class TestTabWindow(unittest.TestCase): pass
class TestListWindow(unittest.TestCase): pass
class TestHelpWindow(unittest.TestCase): pass
class TestListEntry(unittest.TestCase): pass
class TestPlaylistEntry(unittest.TestCase): pass
class TestTagListWindow(unittest.TestCase): pass
class TestFilelistWindow(unittest.TestCase): pass


class TestPlaylist(unittest.TestCase):
    files = {
        'dir/file1.mp3': '',
        'dir/file2.ogg': '',
        'dir/file3.wav': '',
        'playlist.pls': """[playlist]
NumberOfEntries=3

File1=dir/file1.mp3
File2=dir/file2.ogg
File3=dir/file3.wav""",
        'playlist.m3u': """# this is a playlist
dir/file1.mp3
dir/file2.ogg
dir/file3.wav""",
    }

    def generate_files(self):
        if self.path is None:
            self.path = '/tmp/cplay_test_%s' % time.time()
            os.mkdir(self.path)
            os.mkdir(os.path.join(self.path, 'dir'))
            for path, content in self.files.items():
                f = open(os.path.join(self.path, path), 'w')
                f.write(content)
                f.close()

    def delete_files(self):
        if self.path is not None:
            for path, content in self.files.items():
                os.unlink(os.path.join(self.path, path))
            os.rmdir(os.path.join(self.path, 'dir'))
            os.rmdir(self.path)
            self.path = None

    def load_data(self):
        paths = [
            'dir/file1.mp3',
            'dir/file2.ogg',
            'dir/file3.wav',
        ]
        for path in paths:
            self.playlist.add(path)

    def setUp(self):
        self.playlist = cplay.Playlist()
        self.path = None
        self.generate_files()

    def tearDown(self):
        self.delete_files()

    def test_add_single(self):
        songs = ['dir/file1.mp3', 'dir/file2.ogg', 'dir/file3.wav']
        non_songs = ['foo', 'foo.xyz', 'ftp://foo.com']
        for f in songs + non_songs:
            self.playlist.add(f)
        self.assertListEqual(
            [entry.pathname for entry in self.playlist.buffer], songs)

    def test_add_pls(self):
        self.playlist.add(os.path.join(self.path, 'playlist.pls'))
        self.assertListEqual(
            [entry.pathname for entry in self.playlist.buffer],
            ['dir/file1.mp3', 'dir/file2.ogg', 'dir/file3.wav'])

    def test_add_m3u(self):
        self.playlist.add(os.path.join(self.path, 'playlist.m3u'))
        expected = [os.path.join(self.path, p) for p in
                    ['dir/file1.mp3', 'dir/file2.ogg', 'dir/file3.wav']]
        actual = [entry.pathname for entry in self.playlist.buffer]
        self.assertListEqual(actual, expected)

    def test_add_dir(self):
        self.playlist.add(os.path.join(self.path, 'dir'))
        expected = [os.path.join(self.path, p) for p in
                    ['dir/file1.mp3', 'dir/file2.ogg', 'dir/file3.wav']]
        actual = [entry.pathname for entry in self.playlist.buffer]
        self.assertListEqual(actual, expected)

    def _test_change_active_entry(self):
        # when the list is empty, no entry should be active
        self.assertIsNone(self.playlist.change_active_entry(1))
        self.load_data()
        new = self.playlist.change_active_entry(1)
        self.assertIsNotNone(new)
        self.assertEqual(new, self.playlist.get_active_entry())
        self.assertNotEqual(new, self.playlist.change_active_entry(1))
        self.assertNotEqual(new, self.playlist.change_active_entry(1))
        # if repeat is False, we get None after changing four times
        self.assertIsNone(self.playlist.change_active_entry(1))
        self.assertIsNone(self.playlist.change_active_entry(1))
        self.playlist.repeat = True
        self.assertIsNotNone(self.playlist.change_active_entry(1))

    def test_change_active_entry(self):
        self.playlist.random = False
        self._test_change_active_entry()

    def test_change_active_entry_random(self):
        self.playlist.random = True
        self._test_change_active_entry()

    def test_command_delete_all(self):
        self.load_data()
        self.playlist.command_delete_all()
        actual = len(self.playlist.buffer)
        self.assertEqual(actual, 0)

    def test_command_shuffle(self):
        self.load_data()
        self.playlist.command_shuffle()
        actual = len(self.playlist.buffer)
        self.assertEqual(actual, 3)

    def test_command_sort(self):
        self.load_data()
        self.playlist.command_sort()
        actual = len(self.playlist.buffer)
        self.assertEqual(actual, 3)

    def test_command_toggle_repeat(self):
        self.playlist.repeat = False
        self.playlist.command_toggle_repeat()
        self.assertTrue(self.playlist.repeat)
        self.playlist.command_toggle_repeat()
        self.assertFalse(self.playlist.repeat)

    def test_command_toggle_random(self):
        self.playlist.random = False
        self.playlist.command_toggle_random()
        self.assertTrue(self.playlist.random)
        self.playlist.command_toggle_random()
        self.assertFalse(self.playlist.random)

    def test_command_toggle_stop(self):
        self.playlist.stop = False
        self.playlist.command_toggle_stop()
        self.assertTrue(self.playlist.stop)
        self.playlist.command_toggle_stop()
        self.assertFalse(self.playlist.stop)


class TestPlaylistWindow(unittest.TestCase): pass
class TestGetType(unittest.TestCase): pass
class TestGetTag(unittest.TestCase): pass


class TestBackend(unittest.TestCase):
    def setUp(self):
        self.backend = cplay.Backend('', '')

    def _test_parse_buf(self, buf, offset, length):
        self.backend.buf = buf
        self.backend.parse_buf()
        self.assertEqual(self.backend.offset, offset)
        self.assertEqual(self.backend.length, length)


class TestFrameOffsetBackend(TestBackend):
    def setUp(self):
        self.backend = cplay.FrameOffsetBackend('', '', 38.28)

    def test_parse_buf(self):
        buf = b'Frame#   520 [93576], Time: 00:13.58 [40:44.43], RVA:   off, Vol: 100(100)'
        self._test_parse_buf(buf, 13, 2457)


class TestFrameOffsetBackendMpp(TestBackend): pass


class TestTimeOffsetBackend(TestBackend):
    def setUp(self):
        self.backend = cplay.TimeOffsetBackend('', '')

    def test_parse_buf(self):
        self.backend.length = 2457
        buf = b'MPEG Audio Decoder 0.15.2 (beta) - Copyright (C) 2000-2004 Robert Leslie et al.\
          Title: Sometitle\
         Artist: Someartist\
-00:39:13 Layer III, 320 kbps, 44100 Hz, joint stereo (MS), no CRC'
        self._test_parse_buf(buf, 104, 2457)


class TestGSTBackend(TestBackend):
    def setUp(self):
        self.backend = cplay.GSTBackend('', '')

    def test_parse_buf(self):
        buf = b'Playing file:///some/file\n\nTime: 0:01:47.17 of 0:40:57.82'
        self._test_parse_buf(buf, 107, 2457)


class TestNoOffsetBackend(TestBackend):
    def setUp(self):
        self.backend = cplay.NoOffsetBackend('', '')

    def test_parse_buf(self):
        buf = b'In:3.47% 00:00:12.63 [00:05:51.84] Out:557k  [   ===|==-   ]        Clip:0 '
        self._test_parse_buf(buf, 1, 2)


class TestMPlayer(TestBackend): pass


class TestTimeout(unittest.TestCase):
    def setUp(self):
        self.timeout = cplay.Timeout()

    def test_timeout(self):
        state = []
        self.timeout.add(5, lambda: state.append('foo'))
        self.assertListEqual(state, [])

        self.timeout.check(time.time() + 5)
        self.assertListEqual(state, ['foo'])

    def test_timeout_with_args(self):
        state = []
        self.timeout.add(5, lambda x, y: state.append(x + y), (1, 2))
        self.timeout.check(time.time() + 5)
        self.assertListEqual(state, [3])


class TestFIFOControl(unittest.TestCase):
    def setUp(self):
        patch_services()
        self.control = cplay.FIFOControl()
        if self.control.fd is not None:
            self.control.fd.close()

    def test_command(self):
        state = []
        self.control.commands = {
            'test': [lambda x: state.append(x), ['foo']],
        }
        self.control.fd = StringIO('test\n')
        self.assertListEqual(state, [])
        self.control.handle_command()
        self.assertListEqual(state, ['foo'])

    def test_command_with_args(self):
        state = []
        self.control.commands = {
            'test': [lambda x: state.append(x), None],
        }
        self.control.fd = StringIO('test bar\n')
        self.assertListEqual(state, [])
        self.control.handle_command()
        self.assertListEqual(state, ['bar'])


class TestPlayer(unittest.TestCase): pass


class TestInput(unittest.TestCase):
    def setUp(self):
        self.input = cplay.Input()
        self.input.start()

    def test_input(self):
        self.input.do(ord('a'))
        self.assertEqual(self.input.string, 'a')

    def test_backspace(self):
        self.input.do(ord('a'))
        self.input.do(ord('b'))
        self.input.do(127)
        self.assertEqual(self.input.string, 'a')
        self.input.do(127)
        self.assertEqual(self.input.string, '')
        self.input.do(127)
        self.assertEqual(self.input.string, '')
        self.assertTrue(self.input.active)

    def test_clear(self):
        self.input.do(ord('a'))
        self.input.do(21)
        self.assertEqual(self.input.string, '')
        self.assertTrue(self.input.active)

    def test_clear_word(self):
        self.input.do(ord('a'))
        self.input.do(ord('a'))
        self.input.do(ord(' '))
        self.input.do(ord('b'))
        self.input.do(ord('b'))
        self.input.do(23)
        self.assertEqual(self.input.string, 'aa ')
        self.assertTrue(self.input.active)

    def test_cancel(self):
        self.input.do(ord('a'))
        self.input.cancel()
        self.assertEqual(self.input.string, '')
        self.assertFalse(self.input.active)

    def test_complete_hook(self):
        state = []
        self.input.complete_hook = lambda s: state.append(s)
        self.input.do(ord('a'))
        self.input.do(ord('b'))
        self.input.do(9)
        self.assertListEqual(state, ['ab'])
        self.assertTrue(self.input.active)

    def test_stop_hook(self):
        state = []
        self.input.stop_hook = lambda x, y: state.append(x + y)
        self.input.do(ord('a'))
        self.input.stop(1, 2)
        self.assertListEqual(state, [3])


class TestMacroController(unittest.TestCase):
    def setUp(self):
        patch_services()
        self.macro_controller = cplay.MacroController()
        cplay.MACRO = dict()

    def test_macro_controller(self):
        cplay.MACRO['a'] = 'abc'
        self.macro_controller.command_macro()
        cplay.APP.input.do(ord('a'))

        actual = cplay.APP.keymapstack.log
        expected = [ord(c) for c in 'abc']
        self.assertListEqual(actual, expected)


class TestApplication(unittest.TestCase): pass
class TestValidSong(unittest.TestCase): pass
class TestValidPlaylist(unittest.TestCase): pass


if __name__ == '__main__':
    unittest.main()

# vim: ts=4 sts=4 sw=4 et

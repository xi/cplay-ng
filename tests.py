import unittest
import time

try:
    from StringIO import StringIO
except:
    from io import StringIO

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
    def counter(self, values): pass
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

    # cplay.app.playlist.stop ?


class MockApp(object):
    def __init__(self):
        self.restricted = None
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
    cplay.app = MockApp()
    cplay.app.keymapstack = MockKeymapStack()
    # cplay.app.window = MockWindow()
    cplay.app.player = MockPlayer()
    cplay.app.timeout = MockTimeout()
    cplay.app.input = cplay.Input()
    cplay.app.macro = MockMacro()
    cplay.app.status = MockStatus()
    cplay.app.progress = MockProgress()
    cplay.app.counter = MockCounter()
    cplay.app.playlist = MockPlaylist()
    cplay.app.filelist = MockFilelist()


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
class TestPlaylistWindow(unittest.TestCase): pass
class TestGetType(unittest.TestCase): pass
class TestGetTag(unittest.TestCase): pass
class TestBackend(unittest.TestCase): pass
class TestFrameOffsetBackend(unittest.TestCase): pass
class TestFrameOffsetBackendMpp(unittest.TestCase): pass
class TestTimeOffsetBackend(unittest.TestCase): pass
class TestGSTBackend(unittest.TestCase): pass
class TestNoOffsetBackend(unittest.TestCase): pass
class TestMPlayer(unittest.TestCase): pass


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
        cplay.app.input.do(ord('a'))

        actual = cplay.app.keymapstack.log
        expected = [ord(c) for c in 'abc']
        self.assertListEqual(actual, expected)


class TestApplication(unittest.TestCase): pass
class TestValidSong(unittest.TestCase): pass
class TestValidPlaylist(unittest.TestCase): pass


if __name__ == '__main__':
    unittest.main()

# vim: ts=4 sts=4 sw=4 et

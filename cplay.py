import curses
import functools
import json
import os
import random
import re
import selectors
import signal
import socket
import subprocess
import sys
import termios
import time
from contextlib import contextmanager

__version__ = '5.4.0'

AUDIO_EXTENSIONS = [
    'mp3', 'ogg', 'oga', 'opus', 'flac', 'm4a', 'm4b', 'wav', 'mid', 'wma'
]

HELP = """Global
------
Up, k        : move to previous item
Down, j      : move to next item
PageUp, K    : move to previous page
PageDown, J  : move to next page
Home, g      : move to top
End, G       : move to bottom
Enter        : chdir or play
Tab          : switch between filelist/playlist
n            : next track
x, Space     : toggle play/pause
Left, Right  : seek backward/forward
/            : search
[, ]         : previous/next search match
Esc          : cancel
0..9         : volume control
h            : help
q, Q         : quit

Filelist
--------
a            : add to playlist
s            : recursive search
BS           : go to parent dir
r            : refresh

Playlist
--------
d, D         : delete item/all
m, M         : move item down/up
r, R         : toggle repeat/random
s, S         : shuffle/sort playlist
w            : enter filename for current playlist
C            : close current playlist
@            : jump to current track"""


def clamp(value, _min, _max):
    return max(_min, min(_max, value))


def space_between(a, b, n):
    d = n - (len(a) + len(b))
    if d >= 0:
        return a + ' ' * d + b
    else:
        return a[:d] + b


def format_time(total):
    h, s = divmod(total, 3600)
    m, s = divmod(s, 60)
    return '%02d:%02d:%02d' % (h, m, s)


def str_match(query, s):
    return all(q in s.lower() for q in query.lower().split())


def resize(*_args):
    os.write(app.resize_out, b'.')


def get_socket(path):
    while True:
        try:
            sock = socket.socket(family=socket.AF_UNIX)
            sock.connect(path)
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.1)
        else:
            return sock


@functools.cache
def get_mpv_version():
    p = subprocess.run(['mpv', '--version'], stdout=subprocess.PIPE)
    s = p.stdout.split(b' ', 2)[1].decode().lstrip('v')
    return tuple(int(i) for i in s.split('.'))


@functools.lru_cache
def relpath(path):
    if path.startswith('http'):
        return path
    elif path.startswith(filelist.path):
        return path[len(filelist.path):].lstrip('/')
    else:
        return os.path.relpath(path)


@contextmanager
def enable_ctrl_keys():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tcattr = termios.tcgetattr(fd)
        tcattr[0] = tcattr[0] & ~(termios.IXON)
        termios.tcsetattr(fd, termios.TCSANOW, tcattr)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def get_ext(path):
    return os.path.splitext(path)[1].lstrip('.')


def listdir(path):
    with os.scandir(path) as it:
        for entry in sorted(it, key=lambda e: e.name):
            if entry.name[0] != '.':
                yield (
                    entry.path,
                    get_ext(entry.name),
                    entry.is_dir(follow_symlinks=False),
                )


class Player:
    def __init__(self):
        self.path = None
        self.position = 0
        self.length = 0
        self.metadata = None
        self._seek_step = 0
        self._seek_timeout = None
        self.is_playing = False
        self._playing = 0
        self._buffer = b''

        self.socket_path = '%s/mpv-cplay-%i.sock' % (
            os.getenv('XDG_RUNTIME_DIR', '/tmp'), os.getpid()
        )
        self._proc = subprocess.Popen(
            [
                'mpv',
                f'--input-ipc-server={self.socket_path}',
                '--idle',
                '--audio-display=no',
                '--replaygain=track',
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.socket = get_socket(self.socket_path)

        self._ipc('observe_property', 1, 'time-pos')
        self._ipc('observe_property', 2, 'duration')
        self._ipc('observe_property', 3, 'metadata')

    def _ipc(self, cmd, *args):
        data = json.dumps({'command': [cmd, *args]})
        msg = data.encode('utf-8') + b'\n'
        self.socket.send(msg)

    def handle_ipc(self, data):
        if data.get('event') == 'property-change' and data['id'] == 1:
            if data.get('data') is not None and not self._seek_step:
                self.position = data['data']
        elif data.get('event') == 'property-change' and data['id'] == 2:
            if data.get('data') is not None:
                self.length = data['data']
        elif data.get('event') == 'property-change' and data['id'] == 3:
            self.metadata = data.get('data')
        elif data.get('event') == 'end-file':
            self._playing -= 1

    def parse_progress(self):
        self._buffer += self.socket.recv(1024)
        msgs = self._buffer.split(b'\n')
        self._buffer = msgs.pop()
        for msg in msgs:
            data = json.loads(msg.decode('utf-8', errors='replace'))
            self.handle_ipc(data)

    def get_progress(self):
        if self.length == 0:
            return 0
        return self.position / self.length

    def get_title(self):
        title = relpath(self.path)
        if self.metadata and 'icy-title' in self.metadata:
            title = '{} [{}]'.format(title, self.metadata['icy-title'])
        return title

    def set_volume(self, vol):
        self._ipc('set', 'volume', str(vol))

    def stop(self):
        self.is_playing = False
        self._ipc('stop')

    def _play(self):
        if not self.path:
            self.is_playing = False
            return
        self.is_playing = True
        self._playing += 1
        if get_mpv_version() >= (0, 38, 0):
            self._ipc('loadfile', self.path, 'replace', 0, 'start=%i' % self.position)
        else:
            self._ipc('loadfile', self.path, 'replace', 'start=%i' % self.position)

    def play(self, path):
        if path and (m := re.match(r'^(http.*)#t=([0-9]+)$', path)):
            self.path = m[1]
            self.position = float(m[2])
        else:
            self.path = path
            self.position = 0
        self.length = 0
        self._seek_step = 0
        self._play()

    def toggle(self):
        if self.is_playing:
            self.stop()
        elif self.path:
            self._play()

    def seek(self, direction):
        d = direction * self.length * 0.002
        if self._seek_step * d > 0:  # same direction
            self._seek_step += d
        else:
            self._seek_step = d

        self.position += self._seek_step
        self.position = min(self.length, max(0, self.position))
        self._seek_timeout = time.time() + 0.5

    def finish_seek(self):
        if self._seek_timeout and time.time() >= self._seek_timeout:
            self._seek_timeout = None
            self._seek_step = 0
            if self.is_playing:
                self._play()

    @property
    def is_finished(self):
        return self.is_playing and self._playing == 0

    def cleanup(self):
        self._proc.terminate()
        os.remove(self.socket_path)


class Input:
    def __init__(self):
        self.active = False
        self.str = ''

    def start(self, prompt, on_input=None, on_submit=None, initial=''):
        self.str = initial
        self.prompt = prompt
        self.on_input = on_input
        self.on_submit = on_submit
        self.active = True
        if self.on_input:
            self.on_input(self.str)

    def process_key(self, key):
        if not self.active:
            return False
        if key == chr(27):
            self.str = ''
            self.active = False
        elif key == '\n':
            self.active = False
            if self.on_submit:
                self.on_submit(self.str)
        elif key == curses.KEY_BACKSPACE:
            self.str = self.str[:-1]
        elif isinstance(key, str):
            self.str += key
        else:
            self.active = False
            return False
        if self.on_input:
            self.on_input(self.str)
        return True


class List:
    def __init__(self):
        self.items = []
        self.position = 0
        self.cursor = 0
        self.active = -1
        self.search_str = ''

    @property
    def rows(self):
        return app.rows - 4

    def get_title(self):
        raise NotImplementedError

    def set_cursor(self, cursor):
        self.cursor = clamp(cursor, 0, len(self.items) - 1)
        self.position = clamp(
            self.position, self.cursor - self.rows + 1, self.cursor
        )

    def move_cursor(self, diff):
        self.set_cursor(self.cursor + diff)

    def search(self, q, diff=1, offset=0):
        self.search_str = q
        for i in range(len(self.items)):
            pos = (self.cursor + (i + offset) * diff) % len(self.items)
            if str_match(q, self.format_item(self.items[pos])):
                self.set_cursor(pos)
                return True
        return False

    def format_item(self, item):
        return relpath(item)

    def render(self):
        items = self.items[self.position:self.position + self.rows]
        for i, item in enumerate(items):
            attr = 0
            if self.position + i == self.cursor:
                attr |= curses.A_REVERSE
            if self.position + i == self.active:
                attr |= curses.A_BOLD
            s_item = self.format_item(item)
            s_item = space_between(f'  {s_item}', '', app.cols)
            yield (s_item, attr)
        for _i in range(max(0, self.rows - len(items))):
            yield ''

    def process_key(self, key):  # noqa: C901
        if key in [curses.KEY_DOWN, 'j']:
            self.move_cursor(1)
        elif key in [curses.KEY_UP, 'k']:
            self.move_cursor(-1)
        elif key in [curses.KEY_NPAGE, 'J']:
            self.move_cursor(self.rows - 2)
        elif key in [curses.KEY_PPAGE, 'K']:
            self.move_cursor(-(self.rows - 2))
        elif key in [curses.KEY_END, 'G']:
            self.set_cursor(len(self.items))
        elif key in [curses.KEY_HOME, 'g']:
            self.set_cursor(0)
        elif key == '/':
            app.input.start('/', on_input=self.search)
        elif key == ']':
            if self.search_str:
                self.search(self.search_str, 1, 1)
        elif key == '[':
            if self.search_str:
                self.search(self.search_str, -1, 1)
        else:
            return False
        return True


class HelpList(List):
    def __init__(self):
        super().__init__()
        self.items = HELP.split('\n')

    def get_title(self):
        return 'Help'

    def format_item(self, item):
        return item

    def process_key(self, key):
        if key in ['q', 'h']:
            app.help = False
        else:
            return super().process_key(key)
        return True


class Filelist(List):
    def __init__(self):
        super().__init__()
        self.path = None
        self.rsearch_str = ''
        self.set_path(os.getcwd())

    def get_title(self):
        title = f'Filelist: {self.path.rstrip("/")}/'
        if self.rsearch_str:
            title += f'search "{self.rsearch_str}"/'
        return title

    def format_item(self, item):
        s = super().format_item(item)
        ext = get_ext(item)
        if not (ext in AUDIO_EXTENSIONS or ext == 'm3u'):
            s += '/'
        return s

    def set_path(self, path, *, prev=None, refresh=False):
        if path != self.path:
            self.path = path
            os.chdir(path)
            relpath.cache_clear()
            self.search_cache = []
        elif refresh:
            self.search_cache = []
        self.all_items = []
        self.rsearch_str = ''

        for p, ext, is_dir in listdir(path):
            if is_dir or ext == 'm3u' or ext in AUDIO_EXTENSIONS:
                self.all_items.append(p)

        self.items = self.all_items

        if prev and prev in self.items:
            self.set_cursor(self.items.index(prev))
        else:
            self.position = 0
            self.cursor = 0

    def build_search_cache(self, root):
        results = []
        for path, ext, is_dir in listdir(root):
            if is_dir:
                children = self.build_search_cache(path)
                if children:
                    results.append(path)
                    results += children
            elif ext in AUDIO_EXTENSIONS or ext == 'm3u':
                results.append(path)
        return results

    def filter(self, query):
        if not self.search_cache:
            self.search_cache = self.build_search_cache(self.path)

        if query:
            if self.rsearch_str and query.startswith(self.rsearch_str):
                base = self.items
            else:
                base = self.search_cache

            self.items = []
            for path in base:
                if str_match(query, self.format_item(path)):
                    self.items.append(path)
        else:
            self.items = self.all_items

        self.rsearch_str = query
        self.set_cursor(self.cursor)

    def activate(self, item):
        ext = item.rsplit('.', 1)[-1]
        if os.path.isdir(item):
            self.set_path(item)
        elif ext in AUDIO_EXTENSIONS:
            playlist.active = -1
            player.play(item)
        elif ext == 'm3u':
            playlist.load(item)
            app.toggle_tabs()

    def process_key(self, key):
        if key == 'a':
            if self.items and playlist.add(self.items[self.cursor]):
                self.move_cursor(1)
        elif key == 's':
            app.input.start('search: ', on_input=self.filter)
            self.filter(self.rsearch_str)
        elif key == '\n':
            if self.items:
                self.activate(self.items[self.cursor])
        elif key == 'r':
            self.set_path(self.path, refresh=True)
        elif key == curses.KEY_BACKSPACE:
            if self.rsearch_str:
                self.set_path(self.path)
            else:
                self.set_path(os.path.dirname(self.path), prev=self.path)
        else:
            return super().process_key(key)
        return True


class Playlist(List):
    def __init__(self):
        super().__init__()
        self.repeat = False
        self.random = False
        self._played = set()
        self.path = None
        self.items_written = []

    def get_title(self):
        title = 'Playlist'
        if self.path:
            title += f' {os.path.basename(self.path)}'
            if self.items != self.items_written:
                title += '*'
        if self.repeat:
            title += ' [repeat all]'
        if self.random:
            title += ' [random]'
        return title

    def clear(self):
        self.items = []
        self.position = 0
        self.cursor = 0
        self.active = -1
        self._played = set()

    def reorder(self, fn):
        if not self.items:
            return
        cursor_item = self.items[self.cursor]
        try:
            active_item = self.items[self.active]
        except IndexError:
            active_item = None
        fn()
        self.set_cursor(self.items.index(cursor_item))
        if active_item:
            self.active = self.items.index(active_item)

    def shuffle(self):
        self.reorder(lambda: random.shuffle(self.items))

    def sort(self):
        self.reorder(lambda: self.items.sort())

    def remove_item(self):
        self.items.pop(self.cursor)

        if self.active == self.cursor:
            self.active = -1
        elif self.active > self.cursor:
            self.active -= 1

    def move_item(self, direction):
        new_cursor = clamp(self.cursor + direction, 0, len(self.items) - 1)

        if self.active == self.cursor:
            self.active = new_cursor
        elif direction < 0:
            if self.active >= new_cursor and self.active < self.cursor:
                self.active += 1
        else:
            if self.active <= new_cursor and self.active > self.cursor:
                self.active -= 1

        item = self.items.pop(self.cursor)
        self.items.insert(new_cursor, item)
        self.set_cursor(new_cursor)

    def next(self):
        if not self.items:
            return

        if self.random:
            self._played.add(self.active)
            left = set(range(len(self.items))).difference(self._played)
            if left:
                self.active = random.choice(list(left))
            else:
                self._played = set()
                if self.repeat:
                    self.active = random.randrange(len(self.items))
                else:
                    self.active = -1
                    return
        else:
            self.active += 1
            if self.active >= len(self.items) and self.repeat:
                self.active = 0
        try:
            return self.items[self.active]
        except IndexError:
            self.active = -1

    def add_dir(self, path):
        count = 0
        for p, _ext, _is_dir in listdir(path):
            count += self.add(p, recursive=True)
        return count

    def add_playlist(self, path):
        count = 0
        dirname = os.path.dirname(path)
        with open(path, errors='replace') as fh:
            for _line in fh:
                line = _line.strip()
                if not line or line[0] == '#':
                    continue
                if not re.match(r'^(/|https?://)', line):
                    line = os.path.join(dirname, line)
                self.items.append(line)
                count += 1
        return count

    def add(self, path, *, recursive=False):
        ext = path.rsplit('.', 1)[-1]
        if os.path.isdir(path):
            return self.add_dir(path)
        elif ext == 'm3u' and not recursive:
            return self.add_playlist(path)
        elif ext in AUDIO_EXTENSIONS:
            self.items.append(path)
            return 1
        else:
            return 0

    def load(self, path):
        self.clear()
        self.add_playlist(path)
        self.path = path
        self.items_written = self.items.copy()

    def write(self, path):
        try:
            with open(path, 'w') as fh:
                for item in self.items:
                    fh.write(f'{item}\n')
                self.path = path
                self.items_written = self.items.copy()
        except OSError:
            pass

    def process_key(self, key):  # noqa: C901
        if key == 'm':
            self.move_item(1)
        elif key == 'M':
            self.move_item(-1)
        elif key == 'd':
            self.remove_item()
        elif key == 'D':
            self.clear()
        elif key == 'C':
            self.clear()
            self.path = None
            self.items_written = []
        elif key == '\n':
            if not self.items:
                return True
            self.active = self.cursor
            player.play(self.items[self.active])
        elif key == '@':
            self.set_cursor(self.active)
        elif key == 's':
            self.shuffle()
        elif key == 'S':
            self.sort()
        elif key == 'r':
            self.repeat = not self.repeat
        elif key == 'R':
            self.random = not self.random
        elif key == 'w':
            app.input.start(
                'write playlist to path: ',
                on_submit=self.write,
                initial=self.path or filelist.path,
            )
        else:
            return super().process_key(key)
        return True


class Application:
    def __init__(self):
        self.tabs = [filelist, playlist]
        self.help = False
        self.input = Input()
        self.old_lines = []

        # self-pipe to avoid concurrency issues with signal
        self.resize_in, self.resize_out = os.pipe2(os.O_NONBLOCK)

    def refresh_dimensions(self):
        self.rows, self.cols = self.screen.getmaxyx()

    def on_resize(self):
        curses.endwin()
        self.screen.refresh()
        self.refresh_dimensions()
        self.tab.set_cursor(app.tab.cursor)

    @property
    def tab(self):
        if self.help:
            return helplist
        else:
            return self.tabs[0]

    def toggle_tabs(self):
        self.tabs.append(self.tabs.pop(0))

    def format_progress(self):
        progress = min(int(self.cols * player.get_progress()), self.cols - 1)
        return '=' * (progress - 1) + '|' + '-' * (self.cols - progress)

    def _render(self):
        yield (self.tab.get_title(), curses.A_BOLD)
        yield '-' * self.cols

        yield from self.tab.render()
        yield self.format_progress()

        if self.input.active:
            status = f'{self.input.prompt}{self.input.str}█'
        elif self.tab == helplist:
            status = f'cplay-ng {__version__}'
        elif player.is_playing:
            status = f'Playing {player.get_title()}'
        else:
            status = ''

        counter = ' / '.join([
            format_time(player.position),
            format_time(player.length),
        ])
        yield space_between(status, counter, self.cols)

    def render(self, *, force=False):
        lines = list(self._render())
        try:
            for i, line in enumerate(lines):
                if (
                    not force
                    and len(self.old_lines) > i
                    and line == self.old_lines[i]
                ):
                    continue
                self.screen.move(i, 0)
                self.screen.clrtoeol()
                if isinstance(line, str):
                    self.screen.insstr(i, 0, line, 0)
                else:
                    self.screen.insstr(i, 0, *line)
            # make sure cursor is in a meaningful position for a11y
            self.screen.move(self.tab.cursor - self.tab.position + 2, 0)
            self.screen.refresh()
        except curses.error:
            pass
        self.old_lines = lines

    def process_key(self, key):  # noqa: C901
        if self.input.process_key(key):
            pass
        elif self.tab.process_key(key):
            pass
        elif key in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
            player.set_volume(int(key, 10) * 11)
        elif key == curses.KEY_RIGHT:
            player.seek(1)
        elif key == curses.KEY_LEFT:
            player.seek(-1)
        elif key in ['x', ' ']:
            player.toggle()
        elif key == 'n':
            player.play(playlist.next())
        elif key == 'h':
            self.help = True
        elif key in ['q', 'Q']:
            sys.exit(0)
        elif key == '\t':
            app.toggle_tabs()
        else:
            return False
        return True

    def run(self):
        self.refresh_dimensions()
        self.render()

        with selectors.DefaultSelector() as sel:
            sel.register(sys.stdin, selectors.EVENT_READ)
            sel.register(self.resize_in, selectors.EVENT_READ)
            sel.register(player.socket, selectors.EVENT_READ)
            prev = time.time()

            while True:
                player.finish_seek()

                timeout = 0.5 if player.is_playing else None
                for key, _mask in sel.select(timeout):
                    # if we have skipped multiple seconds, it is probably
                    # because the system was suspended. This heuristic is much
                    # simpler than detecting suspend via dbus.
                    if player.is_playing and time.time() - prev > 5:
                        player.stop()
                    prev = time.time()

                    if key.fileobj is self.resize_in:
                        os.read(self.resize_in, 8)
                        self.on_resize()
                        self.render(force=True)
                    elif key.fileobj is sys.stdin:
                        self.process_key(self.screen.get_wch())
                    elif key.fileobj is player.socket:
                        player.parse_progress()

                if player.is_finished:
                    player.play(playlist.next())

                self.render()


player = Player()
playlist = Playlist()
filelist = Filelist()
helplist = HelpList()
app = Application()


def main():
    app.screen = curses.initscr()
    app.screen.keypad(True)  # noqa: FBT003
    curses.cbreak()
    curses.noecho()
    curses.meta(True)  # noqa: FBT003
    curses.curs_set(0)

    signal.signal(signal.SIGWINCH, resize)

    try:
        with enable_ctrl_keys():
            app.run()
    finally:
        player.cleanup()
        curses.endwin()


if __name__ == '__main__':
    main()

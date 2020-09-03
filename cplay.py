import curses
import os
import random
import re
import select
import signal
import subprocess
import sys
import termios
import time
from contextlib import contextmanager

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
C-s, C-r     : next/previous search match
Esc          : cancel
0..9         : volume control
l            : list mode
h            : help
q, Q         : quit

Filelist
--------
a            : add to playlist
BS           : go to parent dir

Playlist
--------
d, D         : delete item/all
m, M         : move item down/up
r, R         : toggle repeat/random
s, S         : shuffle/sort playlist
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


def set_volume(vol):
    subprocess.check_call([
        'pactl', '--', 'set-sink-volume', '0', '%i%%' % int(vol * 100)
    ])


def resize(*args):
    curses.endwin()
    screen.refresh()
    app.refresh_dimensions()
    app.tab.set_cursor(app.tab.cursor)
    app.render()


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


def listdir(path):
    with os.scandir(path) as it:
        for entry in sorted(it, key=lambda e: e.name):
            if entry.name[0] != '.':
                yield (
                    entry.path,
                    entry.is_dir(follow_symlinks=False),
                )


class Player:
    re_progress = re.compile(br'AV?: (\d+):(\d+):(\d+) / (\d+):(\d+):(\d+)')

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    def __init__(self):
        self._proc = None
        self.path = None
        self.position = 0
        self.length = 0
        self._seek_step = 0
        self._seek_timeout = None

    def parse_progress(self, fd):
        match = self.re_progress.search(os.read(fd, 512))
        if match and not self._seek_step:
            ph, pm, ps, lh, lm, ls = map(int, match.groups())
            self.position = ph * 3600 + pm * 60 + ps
            self.length = lh * 3600 + lm * 60 + ls

    def get_progress(self):
        if self.length == 0:
            return 0
        return self.position / self.length

    def _play(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None

        if not self.path:
            return

        self._proc = subprocess.Popen(
            [
                'mpv',
                '--audio-display=no',
                '--start=%i' % self.position,
                '--volume=100',
                '--replaygain=track',
                self.path,
            ],
            stdout=self.stdout_w,
            stderr=self.stderr_w,
            stdin=self.stdin_r,
        )

    def play(self, path):
        self.path = path
        self.position = 0
        self.length = 0
        self._seek_step = 0
        self._play()

    def toggle(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None
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
            if self._proc:
                self._play()

    def is_finished(self):
        return self._proc is not None and self._proc.poll() is not None


class Input:
    def __init__(self):
        self.active = False
        self.str = ''

    def process_key(self, key):
        if not self.active:
            return False
        if key == chr(27):
            self.str = ''
            self.active = False
        elif key == '\n':
            self.active = False
        elif key == curses.KEY_BACKSPACE:
            self.str = self.str[:-1]
        elif isinstance(key, str):
            self.str += key
        else:
            self.active = False
            return False
        return True


class List:
    def __init__(self):
        self.items = []
        self.position = 0
        self.cursor = 0
        self.active = -1

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
        for i in range(len(self.items)):
            pos = (self.cursor + (i + offset) * diff) % len(self.items)
            if q.lower() in self.format_item(self.items[pos]).lower():
                self.set_cursor(pos)
                return True
        return False

    def format_item(self, item):
        if app.verbose:
            name = item
        else:
            name = os.path.basename(item)
        if os.path.isdir(item):
            name += '/'
        return name

    def render(self):
        items = self.items[self.position:self.position + self.rows]
        for i, item in enumerate(items):
            attr = 0
            if self.position + i == self.cursor:
                attr |= curses.A_REVERSE
            if self.position + i == self.active:
                attr |= curses.A_BOLD
            item = self.format_item(item)
            item = space_between('  ' + item, '', app.cols)
            yield (item, attr)
        for i in range(max(0, self.rows - len(items))):
            yield ''

    def process_key(self, key):
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
        self.set_path(os.getcwd())

    def get_title(self):
        return 'Filelist: %s/' % self.path.rstrip('/')

    def set_path(self, path, prev=None):
        self.path = path
        self.items = []

        for p, is_dir in listdir(path):
            ext = p.rsplit('.', 1)[-1]
            if is_dir or ext == 'm3u' or ext in AUDIO_EXTENSIONS:
                self.items.append(p)

        if prev:
            self.set_cursor(self.items.index(prev))
        else:
            self.position = 0
            self.cursor = 0

    def process_key(self, key):
        if key == 'a':
            if not self.items:
                return True
            if playlist.add(self.items[self.cursor]):
                self.move_cursor(1)
        elif key == '\n':
            if not self.items:
                return True
            item = self.items[self.cursor]
            ext = item.rsplit('.', 1)[-1]
            if os.path.isdir(item):
                self.set_path(item)
            elif ext in AUDIO_EXTENSIONS:
                playlist.active = -1
                player.play(item)
        elif key == curses.KEY_BACKSPACE:
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

    def get_title(self):
        title = 'Playlist'
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
        for p, is_dir in listdir(path):
            count += self.add(p, recursive=True)
        return count

    def add_playlist(self, path):
        count = 0
        dirname = os.path.dirname(path)
        with open(path, errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line[0] == '#':
                    continue
                if not re.match(r'^(/|https?://)', line):
                    line = os.path.join(dirname, line)
                self.items.append(line)
                count += 1
        return count

    def add(self, path, recursive=False):
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

    def process_key(self, key):
        if key == 'm':
            self.move_item(1)
        elif key == 'M':
            self.move_item(-1)
        elif key == 'd':
            self.remove_item()
        elif key == 'D':
            self.clear()
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
        else:
            return super().process_key(key)
        return True


class Application:
    def __init__(self):
        self.tabs = [filelist, playlist]
        self.verbose = False
        self.help = False
        self.input = Input()
        self.old_lines = []

    def refresh_dimensions(self):
        self.rows, self.cols = screen.getmaxyx()

    @property
    def tab(self):
        if self.help:
            return helplist
        else:
            return self.tabs[0]

    def toggle_tabs(self):
        self.tabs.append(self.tabs.pop(0))

    def apply(self, lines):
        for i, line in enumerate(lines):
            if len(self.old_lines) > i and line == self.old_lines[i]:
                continue
            screen.move(i, 0)
            screen.clrtoeol()
            if isinstance(line, str):
                line = (line, 0)
            screen.insstr(i, 0, *line)
        screen.refresh()
        self.old_lines = lines

    def format_progress(self):
        progress = min(int(self.cols * player.get_progress()), self.cols - 1)
        return '=' * (progress - 1) + '|' + '-' * (self.cols - progress)

    def _render(self):
        yield (self.tab.get_title(), curses.A_BOLD)
        yield '-' * self.cols

        yield from self.tab.render()
        yield self.format_progress()

        if self.input.active:
            status = '/%s' % self.input.str
        elif player.path and player._proc:
            status = 'Playing %s' % player.path
        else:
            status = 'Stopped'

        counter = '%s / %s' % (
            format_time(player.position),
            format_time(player.length),
        )
        yield space_between(status, counter, self.cols)

    def render(self):
        self.apply(list(self._render()))

    def process_key(self, key):
        if self.input.process_key(key):
            self.tab.search(self.input.str)
        elif self.tab.process_key(key):
            pass
        elif key in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
            set_volume(int(key, 10) / 9.0)
        elif key == curses.KEY_RIGHT:
            player.seek(1)
        elif key == curses.KEY_LEFT:
            player.seek(-1)
        elif key in ['x', ' ']:
            player.toggle()
        elif key == 'n':
            player.play(playlist.next())
        elif key == '/':
            self.input.str = ''
            self.input.active = True
        elif key == chr(19):
            if self.input.str:
                self.tab.search(self.input.str, 1, 1)
        elif key == chr(18):
            if self.input.str:
                self.tab.search(self.input.str, -1, 1)
        elif key == 'h':
            self.help = True
        elif key in ['q', 'Q']:
            sys.exit(0)
        elif key == '\t':
            app.toggle_tabs()
        elif key == 'l':
            self.verbose = not self.verbose
        else:
            return False
        return True

    def run(self):
        self.refresh_dimensions()
        self.render()

        while True:
            player.finish_seek()

            try:
                r, _w, _e = select.select([
                    sys.stdin,
                    player.stderr_r,
                ], [], [], 0.5)
            except select.error:
                continue

            if sys.stdin in r:
                self.process_key(screen.get_wch())
            if player.stderr_r in r:
                player.parse_progress(player.stderr_r)

            if player.is_finished():
                player.play(playlist.next())

            self.render()


player = Player()
playlist = Playlist()
filelist = Filelist()
helplist = HelpList()
app = Application()

screen = curses.initscr()


def main():
    screen.keypad(True)
    curses.cbreak()
    curses.noecho()
    curses.meta(True)
    curses.curs_set(0)

    signal.signal(signal.SIGWINCH, resize)

    try:
        with enable_ctrl_keys():
            app.run()
    finally:
        curses.endwin()


if __name__ == '__main__':
    main()

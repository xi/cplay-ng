#!/usr/bin/env python
# -*- python -*-

"""cplay - A curses front-end for various audio players.

Copyright (C) 1998-2005 Ulf Betlehem <flu@iki.fi>
              2005-2019 see AUTHORS

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
"""

__version__ = 'cplay-ng 3.0.0'

import os
import re
import sys
import time
import glob
import string
import random
import curses
import signal
import select
import locale
import pickle
import gettext
import logging
import argparse
import traceback
import subprocess
from pkg_resources import resource_filename

try:
    import tty
except ImportError:
    tty = None

locale.setlocale(locale.LC_ALL, '')
CODE = locale.getpreferredencoding()

try:
    t = gettext.translation('cplay', resource_filename(__name__, 'i18n'))
    _ = t.gettext
except IOError:
    def _(x):
        return x

XTERM = re.search('rxvt|xterm', os.environ.get('TERM', ''))
SEARCH = {}
APP = None

UNINITIALIZED = 1
STOPPED = 2
FINISHED = 3
PAUSED = 4
PLAYING = 5

METADATA = ('artist', 'album', 'tracknumber', 'title')


class Application:
    def __init__(self):
        self.tcattr = None
        self.restricted = False
        self.quit_after_playlist = False
        self.fifo = ('%s/cplay-control-%s' % (
            os.environ.get('TMPDIR', '/tmp'),
            os.environ['USER']))

    def setup(self):
        if tty is not None:
            self.tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr[0] = tcattr[0] & ~(tty.IXON)
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSANOW, tcattr)
        self.screen = curses.initscr()
        curses.cbreak()
        curses.noecho()
        try:
            curses.meta(1)
        except:
            pass
        self.cursor(0)

        signal.signal(signal.SIGHUP, self.handler_quit)
        signal.signal(signal.SIGINT, self.handler_quit)
        signal.signal(signal.SIGTERM, self.handler_quit)
        signal.signal(signal.SIGWINCH, self.handler_resize)

        # register services
        self.keymapstack = KeymapStack()
        self.window = RootWindow()
        self.player = Player()
        self.timeout = Timeout()
        self.input = Input()
        self.status = self.window.win_status
        self.progress = self.window.win_progress
        self.counter = self.window.win_counter
        self.playlist = self.window.win_tab.win_playlist
        self.filelist = self.window.win_tab.win_filelist

        self.window.setup_keymap()
        self.window.update()
        self.filelist.listdir()
        self.control = FIFOControl()

    def cleanup(self):
        try:
            curses.endwin()
        except curses.error:
            return
        if XTERM:
            sys.stderr.write('\033]0;%s\a' % 'xterm')
        if tty is not None:
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, self.tcattr)
        self.player.backend.stop(quiet=True)
        # remove temporary files
        self.control.cleanup()

    def run(self):
        while True:
            now = time.time()
            timeout = self.timeout.check(now)
            self.filelist.listdir_maybe(now)
            if self.player.backend.get_state() is not STOPPED:
                timeout = 0.5
                if self.player.backend.get_state() is FINISHED:
                    # end of playlist hack
                    entry = self.playlist.change_active_entry(1)
                    if entry is not None:
                        self.player.play(entry)
                    elif self.quit_after_playlist:
                        self.quit()
                    else:
                        self.player.stop()
            streams = [
                sys.stdin,
                self.player.backend.stdout_r,
                self.player.backend.stderr_r,
            ]
            if self.control.fd:
                streams.append(self.control.fd)
            try:
                r, _w, _e = select.select(streams, [], [], timeout)
            except select.error:
                continue
            # user
            if sys.stdin in r:
                c = self.window.getch()
                APP.keymapstack.process(c)
            # backend
            if self.player.backend.stderr_r in r:
                self.player.backend.parse_progress(
                    self.player.backend.stderr_r)
            # backend
            if self.player.backend.stdout_r in r:
                self.player.backend.parse_progress(
                    self.player.backend.stdout_r)
            # remote
            if self.control.fd in r:
                self.control.handle_command()

    def cursor(self, visibility):
        try:
            curses.curs_set(visibility)
        except:
            pass

    def write_recovery(self):
        data = {
            'repeat': self.playlist.repeat,
            'random': self.playlist.random,
            'buffer': self.playlist.buffer,
        }

        backend = self.player.backend
        if backend is not None:
            data['entry'] = backend.entry
            data['offset'] = backend.offset
            data['length'] = backend.length

        with open(self.recover, 'wb') as fh:
            pickle.dump(data, fh)

    def quit(self, status=0):
        if self.recover:
            self.write_recovery()
        self.player.backend.stop(quiet=True)
        sys.exit(status)

    def handler_resize(self, _sig, _frame):
        # curses trickery
        while True:
            try:
                curses.endwin()
                break
            except:
                time.sleep(1)
        self.screen.refresh()
        self.window.resize()
        self.window.update()

    def handler_quit(self, sig, frame):
        self.quit(1)


class Player:
    def __init__(self):
        self.backend = BACKENDS[0]
        self.play_tid = None
        self._mixer = None
        for mixer in MIXERS:
            try:
                self._mixer = mixer()
                logging.debug('Chose mixer %s', mixer.__name__)
                break
            except Exception as e:
                logging.debug('Mixer %s not available: %s', mixer.__name__, e)
                pass

    def pick_backend(self, entry):
        if entry is None:
            return False
        logging.debug('Setting up backend for %s' % str(entry))
        self.backend.stop(quiet=True)
        for backend in BACKENDS:
            if backend.re_files.search(entry.pathname):
                if backend.installed:
                    self.backend = backend
                    return True
        # FIXME: Needs to report suitable backends
        logging.debug('Backend not found')
        APP.status.status(_('Backend not found!'), 1)
        return False

    def play(self, entry, offset=0):
        # Play executed, remove from queue
        self.play_tid = None
        if entry is None or offset is None:
            return
        logging.debug('Starting to play %s' % str(entry))
        if self.pick_backend(entry):
            self.backend.play(entry, offset or entry.offset)
        else:
            APP.timeout.add(1, self.next_prev_song, (1, ))

    def delayed_play(self, entry, offset):
        if self.play_tid:
            APP.timeout.remove(self.play_tid)
        self.play_tid = APP.timeout.add(0.5, self.play, (entry, offset))

    def stop(self):
        self.backend.stop()

    def next_prev_song(self, direction):
        new_entry = APP.playlist.change_active_entry(direction)
        self.play(new_entry, 0)

    def seek(self, offset):
        if self.backend.get_state() is UNINITIALIZED:
            return
        self.backend.seek(offset)
        self.delayed_play(self.backend.entry, self.backend.offset)

    def jump(self, offset):
        self.backend.jump(offset)

    def toggle_pause(self):
        self.backend.toggle_pause()

    def toggle_stop(self):
        self.backend.toggle_stop()

    def key_volume(self, ch):
        self.mixer('set', [int((ch & 0x0f) * 100 / 9.0)])

    def mixer(self, cmd=None, args=()):
        if self._mixer is None:
            APP.status.status(_('No mixer.'), 1)
        else:
            getattr(self._mixer, cmd)(*args)
            APP.status.status(str(self._mixer), 1)


class Input:
    def __init__(self):
        self.active = False
        self.string = ''
        self.prompt = ''
        # optionally patch these
        self.do_hook = None
        self.stop_hook = None
        self.complete_hook = None
        self.keymap = Keymap()
        self.keymap.bind(list(Window.chars), self.do)
        self.keymap.bind([127, curses.KEY_BACKSPACE], self.do, (8, ))
        self.keymap.bind([21, 23], self.do)
        self.keymap.bind(['\a', 27], self.cancel, ())
        self.keymap.bind(['\n', curses.KEY_ENTER], self.stop, ())

    def show(self):
        n = len(self.prompt) + 1
        s = cut(self.string, APP.status.length() - n, left=True)
        APP.status.status('%s: %s ' % (self.prompt, s))

    def start(self, prompt='', initial=''):
        self.active = True
        self.prompt = prompt
        self.string = initial
        APP.cursor(1)
        APP.keymapstack.push(self.keymap)
        self.show()

    def do(self, ch):
        if ch in [8, 127]:  # backspace
            self.string = self.string[:-1]
        elif ch == 9 and self.complete_hook:
            self.string = self.complete_hook(self.string)
        elif ch == 21:  # C-u
            self.string = ''
        elif ch == 23:  # C-w
            self.string = re.sub(r'((.* )?)\w.*', r'\1', self.string)
        elif ch:
            self.string = '%s%c' % (self.string, ch)
        self.show()
        if self.do_hook:
            self.do_hook(self.string)

    def stop(self):
        self.active = False
        APP.cursor(0)
        APP.keymapstack.pop()
        if not self.string:
            APP.status.status(_('cancel'), 1)
        elif self.stop_hook:
            self.stop_hook(self.string)
        self.do_hook = None
        self.stop_hook = None
        self.complete_hook = None

    def cancel(self):
        self.string = ''
        self.stop()


class Window:
    chars = (string.ascii_letters + string.digits + string.punctuation +
             string.whitespace)

    def __init__(self, parent):
        self.parent = parent
        self.children = []
        self.name = None
        self.keymap = None
        self.visible = True
        self.resize()
        if parent:
            parent.children.append(self)

    def insstr(self, s):
        if not s:
            return
        self.w.addstr(s[:-1])
        self.w.hline(ord(s[-1]), 1)  # insch() work-around

    def __getattr__(self, name):
        return getattr(self.w, name)

    def newwin(self):
        return curses.newwin(
            curses.tigetnum('lines'), curses.tigetnum('cols'), 0, 0)

    def resize(self):
        self.w = self.newwin()
        self.ypos, self.xpos = self.getbegyx()
        self.rows, self.cols = self.getmaxyx()
        self.keypad(1)
        self.leaveok(0)
        self.scrollok(0)
        for child in self.children:
            child.resize()

    def update(self):
        self.clear()
        self.refresh()
        for child in self.children:
            child.update()


class ProgressWindow(Window):
    def __init__(self, parent):
        super().__init__(parent)
        self.value = 0

    def newwin(self):
        return curses.newwin(1, self.parent.cols, self.parent.rows - 2, 0)

    def update(self):
        self.move(0, 0)
        self.hline(ord('-'), self.cols)
        if self.value > 0:
            self.move(0, 0)
            x = int(self.value * self.cols)  # 0 to cols - 1
            if x:
                self.hline(ord('='), x)
            self.move(0, x)
            self.insstr('|')
        self.touchwin()
        self.refresh()

    def progress(self, value):
        self.value = min(value, 0.99)
        self.update()


class StatusWindow(Window):
    def __init__(self, parent):
        super().__init__(parent)
        self.default_message = ''
        self.current_message = ''
        self.tid = None

    def newwin(self):
        return curses.newwin(1, self.parent.cols - 12, self.parent.rows - 1, 0)

    def update(self):
        msg = self.current_message
        self.move(0, 0)
        self.clrtoeol()
        self.insstr(cut(msg, self.cols))
        self.touchwin()
        self.refresh()

    def status(self, message, duration=0):
        self.current_message = str(message)
        if self.tid:
            APP.timeout.remove(self.tid)
        if duration:
            self.tid = APP.timeout.add(duration, self.timeout)
        else:
            self.tid = None
        self.update()

    def timeout(self):
        self.tid = None
        self.restore_default_status()

    def set_default_status(self, message):
        if self.current_message == self.default_message:
            self.status(message)
        self.default_message = message
        if XTERM:
            sys.stderr.write('\033]0;%s\a' % (message or 'cplay'))

    def restore_default_status(self):
        self.status(self.default_message)

    def length(self):
        return self.cols


class CounterWindow(Window):
    ELAPSED = 0
    REMAINING = 1

    def __init__(self, parent):
        super().__init__(parent)
        # [seconds elapsed, seconds remaining of current track]
        self.values = [0, 0]
        self.mode = self.REMAINING

    def newwin(self):
        return curses.newwin(
            1, 11, self.parent.rows - 1, self.parent.cols - 11)

    def update(self):
        h, s = divmod(self.values[self.mode], 3600)
        m, s = divmod(s, 60)
        self.move(0, 0)
        self.attron(curses.A_BOLD)
        self.insstr('%02dh %02dm %02ds' % (h, m, s))
        self.attroff(curses.A_BOLD)
        self.touchwin()
        self.refresh()

    def counter(self, elapsed, remaining):
        if elapsed < 0 or remaining < 0:
            logging.debug(
                'Backend reported negative value for (remaining) playing '
                'time.')
        else:
            self.values[0] = elapsed
            self.values[1] = remaining
            self.update()

    def toggle_mode(self):
        if self.mode == self.ELAPSED:
            self.mode = self.REMAINING
            tmp = _('remaining')
        else:
            self.mode = self.ELAPSED
            tmp = _('elapsed')
        APP.status.status(_('Counting %s time') % tmp, 1)
        self.update()


class RootWindow(Window):
    def __init__(self):
        super().__init__(None)
        self.keymap = Keymap()
        APP.keymapstack.push(self.keymap)
        self.win_progress = ProgressWindow(self)
        self.win_status = StatusWindow(self)
        self.win_counter = CounterWindow(self)
        self.win_tab = TabWindow(self)

    def setup_keymap(self):
        self.keymap.bind(12, self.update, ())  # C-l
        self.keymap.bind([curses.KEY_LEFT, 2], APP.player.seek, (-1,))  # C-b
        self.keymap.bind([curses.KEY_RIGHT, 6], APP.player.seek, (1,))  # C-f
        self.keymap.bind([1, '^'], APP.player.jump, (0,))  # C-a
        self.keymap.bind([5, '$'], APP.player.jump, (-1,))  # C-e
        # 0123456789
        self.keymap.bind(list(range(48, 58)), APP.player.key_volume)
        self.keymap.bind(['+'], APP.player.mixer, ('cue', [1]))
        self.keymap.bind('-', APP.player.mixer, ('cue', [-1]))
        self.keymap.bind('n', APP.player.next_prev_song, (+1, ))
        self.keymap.bind('p', APP.player.next_prev_song, (-1, ))
        self.keymap.bind('z', APP.player.toggle_pause, ())
        self.keymap.bind('x', APP.player.toggle_stop, ())
        self.keymap.bind('c', APP.counter.toggle_mode, ())
        self.keymap.bind('Q', APP.quit, ())
        self.keymap.bind('q', self.command_quit, ())
        self.keymap.bind('v', APP.player.mixer, ('toggle', ))

    def command_quit(self):
        APP.input.do_hook = self.do_quit
        APP.input.start(_('Quit? (y/N)'))

    def do_quit(self, string):
        if string == 'y':
            APP.quit()
        else:
            APP.input.cancel()


class TabWindow(Window):
    def __init__(self, parent):
        super().__init__(parent)
        self.active_child = 0

        self.win_filelist = self.add(FilelistWindow)
        self.win_playlist = self.add(PlaylistWindow)
        self.win_help = self.add(HelpWindow)

        keymap = Keymap()
        keymap.bind('\t', self.change_window, ())  # tab
        keymap.bind('h', self.help, ())
        APP.keymapstack.push(keymap)
        APP.keymapstack.push(self.children[self.active_child].keymap)

    def newwin(self):
        return curses.newwin(self.parent.rows - 2, self.parent.cols, 0, 0)

    def update(self):
        self.update_title()
        self.move(1, 0)
        self.hline(ord('-'), self.cols)
        self.move(2, 0)
        self.clrtobot()
        self.refresh()
        child = self.children[self.active_child]
        child.visible = True
        child.update()

    def update_title(self, refresh=True):
        child = self.children[self.active_child]
        self.move(0, 0)
        self.clrtoeol()
        self.attron(curses.A_BOLD)
        self.insstr(child.get_title())
        self.attroff(curses.A_BOLD)
        if refresh:
            self.refresh()

    def add(self, cls):
        win = cls(self)
        win.visible = False
        return win

    def change_window(self, window=None):
        APP.keymapstack.pop()
        self.children[self.active_child].visible = False
        if window:
            self.active_child = self.children.index(window)
        else:
            # toggle windows 0 and 1
            self.active_child = not self.active_child
        APP.keymapstack.push(self.children[self.active_child].keymap)
        self.update()

    def help(self):
        if self.children[self.active_child] == self.win_help:
            self.change_window(self.win_last)
            APP.status.restore_default_status()
        else:
            self.win_last = self.children[self.active_child]
            self.change_window(self.win_help)
            APP.status.status(__version__)


class ListWindow(Window):
    def __init__(self, parent):
        super().__init__(parent)
        self.buffer = []
        self.bufptr = 0
        self.scrptr = 0
        self.search_direction = 0
        self.last_search = ''
        self.hoffset = 0
        self.keymap = Keymap()
        self.keymap.bind(['k', curses.KEY_UP, 16], self.cursor_move, (-1, ))
        self.keymap.bind(['j', curses.KEY_DOWN, 14], self.cursor_move, (1, ))
        self.keymap.bind(['K', curses.KEY_PPAGE], self.cursor_ppage, ())
        self.keymap.bind(['J', curses.KEY_NPAGE], self.cursor_npage, ())
        self.keymap.bind(['g', curses.KEY_HOME], self.cursor_home, ())
        self.keymap.bind(['G', curses.KEY_END], self.cursor_end, ())
        self.keymap.bind(
            ['?', 18], self.start_search, (_('backward-isearch'), -1))
        self.keymap.bind(
            ['/', 19], self.start_search, (_('forward-isearch'), 1))
        self.keymap.bind(['>'], self.hscroll, (8, ))
        self.keymap.bind(['<'], self.hscroll, (-8, ))

    def newwin(self):
        return curses.newwin(
            self.parent.rows - 2, self.parent.cols,
            self.parent.ypos + 2, self.parent.xpos)

    def update(self, force=True):
        self.bufptr = max(0, min(self.bufptr, len(self.buffer) - 1))
        first = self.scrptr
        last = self.scrptr + self.rows - 1
        if self.bufptr < first:
            first = self.bufptr
        if self.bufptr > last:
            first = self.bufptr - self.rows + 1
        if force or self.scrptr != first:
            self.scrptr = first
            self.move(0, 0)
            self.clrtobot()
            i = 0
            for entry in self.buffer[first:first + self.rows]:
                self.move(i, 0)
                i += 1
                self.putstr(entry)
            if self.visible:
                self.refresh()
                self.parent.update_title()
        self.update_line(curses.A_REVERSE)

    def update_line(self, attr=None, refresh=True):
        if not self.buffer:
            return
        ypos = self.bufptr - self.scrptr
        if attr:
            self.attron(attr)
        self.move(ypos, 0)
        self.hline(ord(' '), self.cols)
        self.putstr(self.current())
        if attr:
            self.attroff(attr)
        if self.visible and refresh:
            self.refresh()

    def get_title(self, data=''):
        pos = '%s-%s/%s' % (
            self.scrptr + min(1, len(self.buffer)),
            min(self.scrptr + self.rows, len(self.buffer)),
            len(self.buffer)
        )
        width = self.cols - len(pos) - 2
        data = cut(data, width - len(self.name), left=True)
        return '%-*s  %s' % (width, cut(self.name + data, width), pos)

    def putstr(self, entry, *pos):
        s = str(entry)
        if pos:
            self.move(*pos)
        if self.hoffset:
            s = '<%s' % s[self.hoffset + 1:]
        self.insstr(cut(s, self.cols))

    def current(self):
        if not self.buffer:
            return None
        if self.bufptr >= len(self.buffer):
            self.bufptr = len(self.buffer) - 1
        return self.buffer[self.bufptr]

    def cursor_set(self, bufptr):
        if APP.input.active:
            APP.input.cancel()
        if not self.buffer:
            return
        self.update_line(refresh=False)
        self.bufptr = max(0, min(len(self.buffer) - 1, bufptr))
        self.update(force=False)

    def cursor_move(self, ydiff):
        self.cursor_set(self.bufptr + ydiff)

    def cursor_ppage(self):
        self.cursor_set(self.bufptr - (self.rows - 2))

    def cursor_npage(self):
        self.cursor_set(self.bufptr + (self.rows - 2))

    def cursor_home(self):
        self.cursor_set(0)

    def cursor_end(self):
        self.cursor_set(len(self.buffer))

    def start_search(self, prompt, direction):
        self.search_direction = direction
        if APP.input.active:
            APP.input.prompt = prompt
            self.do_search(APP.input.string, advance=direction)
        else:
            APP.input.do_hook = self.do_search
            APP.input.stop_hook = self.stop_search
            APP.input.start(prompt)

    def stop_search(self, string):
        self.last_search = string
        APP.status.status(_('ok'), 1)

    def do_search(self, string, advance=0):
        index = (self.bufptr + advance) % len(self.buffer)
        origin = index
        while True:
            line = str(self.buffer[index]).lower()
            if line.find(string.lower()) != -1:
                self.update_line(refresh=False)
                self.bufptr = index
                self.update(force=False)
                break
            index = (index + self.search_direction) % len(self.buffer)
            if index == origin:
                APP.status.status(_('Not found: %s ') % string)
                break

    def hscroll(self, value):
        self.hoffset = max(0, self.hoffset + value)
        self.update()


class HelpWindow(ListWindow):
    def __init__(self, parent):
        super().__init__(parent)
        self.name = _('Help: ')
        self.keymap.bind('q', self.parent.help, ())
        self.buffer = _("""\
 Global
 ------
 Up, k, C-p   : move to previous item
 Down, j, C-n : move to next item
 PageUp, K    : move to previous page
 PageDown, J  : move to next page
 Home, g      : move to top
 End, G       : move to bottom
 Enter        : chdir or play
 Tab          : switch between filelist/playlist
 n, p         : next/prev track
 z, x         : toggle pause/stop

 Left, C-f    : seek backward
 Right, C-b   : seek forward
 C-a, C-e     : restart/end track
 C-s, /       : forward isearch
 C-r, ?       : backword isearch
 C-g, Esc     : cancel
 1..9, +, -   : volume control
 c, v         : counter/volume mode
 <, >         : horizontal scrolling
 C-l          : refresh
 l            : list mode
 h            : help
 q, Q         : quit?, Quit!

 t, T         : tag current/regex
 u, U         : untag current/regex
 Sp, i        : invert current/all

 Filelist
 --------
 a            : add (tagged) to playlist
 s            : recursive search
 BS, o        : go to parent/specified dir

 Playlist
 --------
 d, D         : delete (tagged) items/all
 m, M         : move (tagged) tracks after/before
 r, R         : toggle repeat/Random mode
 s, S         : shuffle/Sort playlist
 w            : write playlist to file
 @            : jump to current track
""").splitlines()


class ListEntry:
    def __init__(self, pathname, directory=False):
        self.filename = os.path.basename(pathname)
        self.pathname = pathname
        self.slash = '/' if directory else ''
        self.tagged = False
        self.offset = 0
        self.maxoffset = None

    def __str__(self):
        mark = '#' if self.tagged else ' '
        return '%s %s%s' % (mark, self.vp(), self.slash)

    def vp(self):
        return self.vps[0][1](self)

    def vp_filename(self):
        return self.filename or self.pathname

    def vp_pathname(self):
        return self.pathname

    vps = [[_('filename'), vp_filename],
           [_('pathname'), vp_pathname]]


class PlaylistEntry(ListEntry):
    def __init__(self, pathname, displayname=None, offset=0, maxoffset=None):
        super().__init__(pathname)
        self.metadata = None
        self.active = False
        if displayname is not None:
            self.filename = displayname
        self.offset = offset
        self.maxoffset = maxoffset

    def vp_metadata(self):
        if self.metadata is None:
            data = get_metadata(self.pathname)
            self.metadata = ' - '.join(data[k] for k in METADATA if k in data)
            logging.debug(self.metadata)
        return self.metadata

    vps = ListEntry.vps[:] + [[_('metadata'), vp_metadata]]


class TagListWindow(ListWindow):
    def __init__(self, parent):
        super().__init__(parent)
        self.tag_value = None
        self.keymap.bind(' ', self.command_tag_untag, ())
        self.keymap.bind('i', self.command_invert_tags, ())
        self.keymap.bind('t', self.command_tag, (True, ))
        self.keymap.bind('u', self.command_tag, (False, ))
        self.keymap.bind('T', self.command_tag_regexp, (True, ))
        self.keymap.bind('U', self.command_tag_regexp, (False, ))
        self.keymap.bind('l', self.command_change_viewpoint, ())

    def complete_generic(self, line, quote=False):
        if quote:
            s = re.sub(r'.*[^\\][ \'"()\[\]{}$`]', '', line)
            s, part = re.sub(r'\\', '', s), line[:len(line) - len(s)]
        else:
            s, part = line, ''
        results = glob.glob(os.path.expanduser(s) + '*')
        if not results:
            return line
        elif len(results) == 1:
            lm = results[0]
            if os.path.isdir(lm):
                lm += '/'
        else:
            lm = results[0]
            for result in results:
                for i in range(min(len(result), len(lm))):
                    if result[i] != lm[i]:
                        lm = lm[:i]
                        break
        if quote:
            lm = re.sub(r'([ \'"()\[\]{}$`])', r'\\\1', lm)
        return part + lm

    def command_change_viewpoint(self, cls=ListEntry):
        cls.vps.append(cls.vps.pop(0))
        APP.status.status(_('Listing %s') % cls.vps[0][0], 1)
        APP.player.backend.update_status()
        self.update()

    def command_invert_tags(self):
        for i in self.buffer:
            i.tagged = not i.tagged
        self.update()

    def command_tag_untag(self):
        if not self.buffer:
            return
        tmp = self.buffer[self.bufptr]
        tmp.tagged = not tmp.tagged
        self.cursor_move(1)

    def command_tag(self, value):
        if not self.buffer:
            return
        self.buffer[self.bufptr].tagged = value
        self.cursor_move(1)

    def command_tag_regexp(self, value):
        self.tag_value = value
        APP.input.stop_hook = self.stop_tag_regexp
        APP.input.start(_('Tag regexp') if value else _('Untag regexp'))

    def stop_tag_regexp(self, string):
        try:
            r = re.compile(string, re.IGNORECASE)
            for entry in self.buffer:
                if r.search(str(entry)):
                    entry.tagged = self.tag_value
            self.update()
            APP.status.status(_('ok'), 1)
        except re.error as err:
            APP.status.status(err, 2)

    def get_tagged(self):
        return [x for x in self.buffer if x.tagged]

    def not_tagged(self, l):
        return [x for x in l if not x.tagged]


class FilelistWindow(TagListWindow):
    DIR = 0
    SEARCH = 1

    def __init__(self, parent):
        super().__init__(parent)
        self.oldposition = {}
        self.cwd = None
        try:
            self.chdir(os.getcwd())
        except OSError:
            self.chdir(os.environ['HOME'])
        self.startdir = self.cwd
        self.mtime_when = 0
        self.mtime = None
        self.mode = self.DIR
        self.keymap.bind(
            ['\n', curses.KEY_ENTER], self.command_chdir_or_play, ())
        self.keymap.bind(
            ['.', 127, curses.KEY_BACKSPACE], self.command_chparentdir, ())
        self.keymap.bind('a', self.command_add_recursively, ())
        self.keymap.bind('o', self.command_goto, ())
        self.keymap.bind('s', self.command_search_recursively, ())

    def command_search_recursively(self):
        APP.input.stop_hook = self.stop_search_recursively
        APP.input.start(_('search'))

    def stop_search_recursively(self, query):
        APP.status.status(_('Searching...'))

        try:
            m = re.match('!([a-z0-9]+) +(.+)', query)
            if m:
                key, query = m.groups()
                fn = SEARCH[key]
            else:
                fn = self.fs_search
            results = list(fn(query))
        except Exception as e:
            APP.status.restore_default_status()
            APP.status.status(e, 2)
            return

        if self.mode != self.SEARCH:
            self.chdir(os.path.join(self.cwd, _('search results')))
            self.mode = self.SEARCH
        self.buffer = []
        for pathname, filename, isdir in results:
            entry = ListEntry(pathname, isdir)
            if filename is not None:
                entry.filename = filename
            self.buffer.append(entry)
        self.bufptr = 0
        self.parent.update_title()
        self.update()
        APP.status.restore_default_status()

    def fs_search(self, query):
        re_tmp = re.compile(query, re.IGNORECASE)
        results = []
        for entry in self.buffer:
            if entry.filename == '..':
                continue
            if re_tmp.search(entry.filename):
                results.append((entry.pathname, entry.filename, False))
            elif os.path.isdir(entry.pathname):
                self.search_recursively(re_tmp, entry.pathname, results)
        return results

    def search_recursively(self, re_tmp, directory, results):
        for filename in os.listdir(directory):
            pathname = os.path.join(directory, filename)
            if re_tmp.search(filename):
                if os.path.isdir(pathname):
                    results.append((pathname, None, True))
                elif valid_playlist(pathname) or valid_song(pathname):
                    results.append((pathname, None, False))
            elif os.path.isdir(pathname):
                self.search_recursively(re_tmp, pathname, results)

    def get_title(self):
        self.name = _('Filelist: ')
        return super().get_title(re.sub('/?$', '/', self.cwd))

    def listdir_maybe(self, now=0):
        if now < self.mtime_when + 2:
            return
        self.mtime_when = now
        self.oldposition[self.cwd] = self.bufptr
        try:
            if self.mtime != int(os.stat(self.cwd).st_mtime):
                self.listdir(quiet=True)
        except OSError:
            pass

    def listdir(self, quiet=False, prevdir=None):
        if not quiet:
            APP.status.status(_('Reading directory...'))
        self.mode = self.DIR
        dirs = []
        files = []
        try:
            self.mtime = int(os.stat(self.cwd).st_mtime)
            self.mtime_when = time.time()
            filenames = os.listdir(self.cwd)
            filenames.sort()
            for filename in filenames:
                if filename[0] == '.':
                    continue
                pathname = os.path.join(self.cwd, filename)
                if os.path.isdir(pathname):
                    dirs.append(pathname)
                elif valid_song(pathname):
                    files.append(pathname)
                elif valid_playlist(pathname):
                    files.append(pathname)
        except OSError:
            pass
        dots = ListEntry(os.path.join(self.cwd, '..'), 1)
        self.buffer = [[dots], []][self.cwd == '/']
        for i in dirs:
            self.buffer.append(ListEntry(i, True))
        for i in files:
            self.buffer.append(ListEntry(i))
        if prevdir:
            for bufptr in range(len(self.buffer)):
                if self.buffer[bufptr].filename == prevdir:
                    self.bufptr = bufptr
                    break
            else:
                self.bufptr = 0
        elif self.cwd in self.oldposition:
            self.bufptr = self.oldposition[self.cwd]
        else:
            self.bufptr = 0
        self.parent.update_title()
        self.update()
        if not quiet:
            APP.status.restore_default_status()

    def chdir(self, directory):
        if self.cwd is not None:
            self.oldposition[self.cwd] = self.bufptr
        self.cwd = os.path.normpath(directory)
        try:
            os.chdir(self.cwd)
        except OSError:
            pass

    def command_chdir_or_play(self):
        if not self.buffer:
            return
        if self.current().filename == '..':
            self.command_chparentdir()
        elif os.path.isdir(self.current().pathname):
            self.chdir(self.current().pathname)
            self.listdir()
        elif valid_song(self.current().pathname):
            APP.player.play(self.current())

    def command_chparentdir(self):
        if APP.restricted and self.cwd == self.startdir:
            return
        directory = os.path.basename(self.cwd)
        self.chdir(os.path.dirname(self.cwd))
        self.listdir(prevdir=directory)

    def command_goto(self):
        if APP.restricted:
            return
        APP.input.stop_hook = self.stop_goto
        APP.input.complete_hook = self.complete_generic
        APP.input.start(_('goto'))

    def stop_goto(self, string):
        directory = os.path.expanduser(string)
        if directory[0] != '/':
            directory = os.path.join(self.cwd, directory)
        if not os.path.isdir(directory):
            APP.status.status(_('Not a directory!'), 1)
            return
        self.chdir(directory)
        self.listdir()

    def command_add_recursively(self):
        entries = self.get_tagged()
        if not entries:
            c = self.current()
            APP.playlist.add(c.pathname, filename=c.filename)
            self.cursor_move(1)
            return
        APP.status.status(_('Adding tagged files'), 1)
        for entry in entries:
            APP.playlist.add(
                entry.pathname, filename=entry.filename, quiet=True)
            entry.tagged = False
        self.update()


class PlaylistWindow(TagListWindow):
    def __init__(self, parent):
        super().__init__(parent)
        self.pathname = None
        self.repeat = False
        self.random = False
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.keymap.bind(['\n', curses.KEY_ENTER], self.command_play, ())
        self.keymap.bind('d', self.command_delete, ())
        self.keymap.bind('D', self.command_delete_all, ())
        self.keymap.bind('m', self.command_move, (True, ))
        self.keymap.bind('M', self.command_move, (False, ))
        self.keymap.bind('s', self.command_shuffle, ())
        self.keymap.bind('S', self.command_sort, ())
        self.keymap.bind('r', self.command_toggle_repeat, ())
        self.keymap.bind('R', self.command_toggle_random, ())
        self.keymap.bind('w', self.command_save_playlist, ())
        self.keymap.bind('@', self.command_jump_to_active, ())

    def command_change_viewpoint(self, cls=PlaylistEntry):
        super().command_change_viewpoint(cls)

    def get_title(self):
        def space_out(value, s):
            return s if value else ' ' * len(s)
        self.name = _('Playlist %s %s') % (
            space_out(self.repeat, _('[repeat all]')),
            space_out(self.random, _('[random]')))
        return super().get_title()

    def append(self, item):
        self.buffer.append(item)
        if self.random:
            self.random_left.append(item)

    def add_dir(self, directory):
        # heuristic: if there are any playlists,
        # do not add individual files to avoid duplicates
        foundplaylist = False
        songs = []
        subdirs = []
        for filename in sorted(os.listdir(directory)):
            pathname = os.path.join(directory, filename)
            if valid_playlist(filename) and not foundplaylist:
                self.add_playlist(pathname)
                foundplaylist = True
            elif valid_song(filename):
                songs.append(pathname)
            elif os.path.isdir(pathname):
                subdirs.append(pathname)
        if not foundplaylist:
            for pathname in songs:
                self._add(pathname, quiet=True)
        for pathname in subdirs:
            self.add_dir(pathname)

    def add_m3u(self, line):
        if re.match(r'^(#.*)?$', line):
            return
        if re.match(r'^(/|https?://)', line):
            self.append(PlaylistEntry(line))
        else:
            dirname = os.path.dirname(self.pathname)
            self.append(PlaylistEntry(os.path.join(dirname, line)))

    def add_pls(self, line):
        # FIXME: support title & length
        m = re.match(r'File(\d+)=(.*)', line)
        if m:
            self.append(PlaylistEntry(m.group(2)))

    def add_cue(self, pathname):
        tracks = []
        artist = ''
        album = ''
        dirname = os.path.dirname(pathname)
        with open(pathname) as inp:
            for line in inp:
                keyword, args = line.strip().split(' ', 1)
                if keyword == 'FILE':
                    filename = args.rsplit(' ', 1)[0].strip('"\'')
                elif keyword == 'TRACK':
                    trackno = args.split(' ', 1)[0]
                    tracks.append({'filename': filename, 'no': trackno})
                elif keyword == 'TITLE':
                    if tracks:
                        tracks[-1]['title'] = args.strip('"\'')
                    else:
                        album = args.strip('"\'')
                elif keyword == 'PERFORMER':
                    if tracks:
                        tracks[-1]['artist'] = args.strip('"\'')
                    else:
                        artist = args.strip('"\'')
                elif line.strip().startswith('INDEX 01') and tracks:
                    mins, secs, frames = args.split(' ')[1].split(':')
                    tracks[-1]['start'] = (
                        int(mins) * 60 + int(secs) + float(frames) / 75)
                    if len(tracks) > 1 and tracks[-2].get('end') is None:
                        tracks[-2]['end'] = tracks[-1]['start']
        for n, track in enumerate(tracks):
            trackno = track.get('no', '%2d' % (n + 1))
            title = track.get('title', track['filename'])
            pathname = os.path.join(dirname, track['filename'])
            if os.path.exists(pathname):
                entry = PlaylistEntry(
                    pathname,
                    displayname=title,
                    offset=track.get('start', 0),
                    maxoffset=track.get('end', None))
                artist = track.get('artist', artist)
                entry.metadata = ' - '.join(
                    a for a in (trackno, artist, title, album) if a)
                self.append(entry)

    def add_playlist(self, pathname):
        self.pathname = pathname
        if re.search(r'\.cue$', pathname, re.IGNORECASE):
            self.add_cue(pathname)
            return
        elif re.search(r'\.m3u$', pathname, re.IGNORECASE):
            f = self.add_m3u
        elif re.search(r'\.pls$', pathname, re.IGNORECASE):
            f = self.add_pls
        with open(pathname) as inp:
            for line in inp:
                f(line.strip())

    def _add(self, pathname, filename=None, quiet=False):
        if os.path.isdir(pathname):
            if not quiet:
                APP.status.status(_('Working...'))
            self.add_dir(pathname)
        elif valid_playlist(pathname):
            self.add_playlist(pathname)
        elif valid_song(pathname):
            entry = PlaylistEntry(pathname, displayname=filename)
            self.append(entry)
        else:
            return
        if not quiet:
            self.update()
            APP.status.status(_('Added: %s') % (filename or pathname), 1)

    def add(self, pathname, filename=None, quiet=False):
        try:
            self._add(pathname, filename=filename, quiet=quiet)
        except Exception as e:
            APP.status.status(e, 2)

    def putstr(self, entry, *pos):
        if entry.active:
            self.attron(curses.A_BOLD)
        super().putstr(entry, *pos)
        if entry.active:
            self.attroff(curses.A_BOLD)

    def change_active_entry(self, direction):
        if not self.buffer:
            return
        old = self.get_active_entry()
        new = None
        if self.random:
            if direction > 0:
                if self.random_next:
                    new = self.random_next.pop()
                elif self.random_left:
                    pass
                elif self.repeat:
                    self.random_left = self.buffer[:]
                else:
                    return
                if new is None:
                    new = random.choice(self.random_left)
                    self.random_left.remove(new)
                try:
                    self.random_prev.remove(new)
                except ValueError:
                    pass
                self.random_prev.append(new)
            else:
                if len(self.random_prev) > 1:
                    self.random_next.append(self.random_prev.pop())
                    new = self.random_prev[-1]
                else:
                    return
            if old:
                old.active = False
        elif old:
            index = self.buffer.index(old) + direction
            if not (0 <= index < len(self.buffer) or self.repeat):
                return
            old.active = False
            new = self.buffer[index % len(self.buffer)]
        else:
            new = self.buffer[0]
        new.active = True
        self.update()
        return new

    def get_active_entry(self):
        for entry in self.buffer:
            if entry.active:
                return entry

    def command_jump_to_active(self):
        entry = self.get_active_entry()
        if entry is not None:
            self.bufptr = self.buffer.index(entry)
            self.update()

    def command_play(self):
        if not self.buffer:
            return
        entry = self.get_active_entry()
        if entry is not None:
            entry.active = False
        entry = self.current()
        entry.active = True
        self.update()
        APP.player.play(entry)

    def command_delete(self):
        if not self.buffer:
            return
        current_entry = self.current()
        n = len(self.buffer)
        self.buffer = self.not_tagged(self.buffer)
        if n > len(self.buffer):
            try:
                self.bufptr = self.buffer.index(current_entry)
            except ValueError:
                pass
        else:
            current_entry.tagged = True
            del self.buffer[self.bufptr]
        if self.random:
            self.random_prev = self.not_tagged(self.random_prev)
            self.random_next = self.not_tagged(self.random_next)
            self.random_left = self.not_tagged(self.random_left)
        self.update()

    def command_delete_all(self):
        self.buffer = []
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.active_entry = None
        APP.status.status(_('Playlist cleared'), 1)
        self.update()

    def command_move(self, after=False):
        if not self.buffer:
            return
        current_entry = self.current()
        entries = self.get_tagged()
        if entries:
            if current_entry.tagged:
                return
            self.buffer = self.not_tagged(self.buffer)
            self.bufptr = self.buffer.index(current_entry)
            if after:
                self.bufptr += 1
        else:
            entries = [current_entry]
            self.buffer.pop(self.bufptr)
            self.bufptr += 1 if after else -1
            if self.bufptr < 0:
                self.bufptr = 0
        self.buffer[self.bufptr:self.bufptr] = entries
        self.update()

    def command_shuffle(self):
        random.shuffle(self.buffer)
        self.bufptr = 0
        self.update()
        APP.status.status(_('Shuffled playlist... Oops?'), 1)

    def command_sort(self):
        APP.status.status(_('Working...'))
        self.buffer.sort(key=lambda x: x.vp())
        self.bufptr = 0
        self.update()
        APP.status.status(_('Sorted playlist'), 1)

    def command_toggle_repeat(self):
        self.toggle('repeat', _('Repeat: %s'))

    def command_toggle_random(self):
        self.toggle('random', _('Random: %s'))
        self.random_prev = []
        self.random_next = []
        self.random_left = self.buffer[:]

    def toggle(self, attr, msg):
        setattr(self, attr, not getattr(self, attr))
        APP.status.status(msg % (
            _('on') if getattr(self, attr) else _('off')), 1)
        self.parent.update_title()

    def command_save_playlist(self):
        if APP.restricted:
            return
        default = self.pathname or '%s/' % APP.filelist.cwd
        APP.input.stop_hook = self.stop_save_playlist
        APP.input.start(_('Save playlist'), default)

    def stop_save_playlist(self, pathname):
        if pathname[0] != '/':
            pathname = os.path.join(APP.filelist.cwd, pathname)
        if not re.search(r'\.m3u$', pathname, re.IGNORECASE):
            pathname = '%s%s' % (pathname, '.m3u')
        try:
            file = open(pathname, 'w')
            for entry in self.buffer:
                file.write('%s\n' % entry.pathname)
            file.close()
            self.pathname = pathname
            APP.status.status(_('ok'), 1)
        except IOError as e:
            APP.status.status(e, 2)


class Backend:

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    def __init__(self, commandline, files, fps=1):
        self.commandline = commandline
        self.installed = bool(which(commandline.split()[0]))
        self.re_files = re.compile(files, re.IGNORECASE)
        self.fps = fps
        self.entry = None
        self.paused = False
        self.offset = 0
        self.length = 0
        self.step = 0
        self._proc = None

    def play(self, entry, offset):
        self.stop()

        argv = self.commandline.split()
        argv[0] = which(argv[0])
        for i in range(len(argv)):
            if argv[i] == '{file}':
                argv[i] = entry.pathname
            if argv[i] == '{offset}':
                argv[i] = str(offset * self.fps)

        if entry != self.entry:
            self.entry = entry
            self.length = 0
        self.offset = offset

        logging.debug('Executing %s at offset %d', ' '.join(argv), self.offset)

        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=self.stdout_w,
                stderr=self.stderr_w,
                stdin=self.stdin_r)
        except OSError as err:
            logging.error('play() %s', err)
            return False

        self.paused = False
        self.step = 0
        self.update_status()
        return True

    def stop(self, quiet=False):
        if self.get_state() is PAUSED:
            self.toggle_pause()
        if self.get_state() is PLAYING:
            try:
                self._proc.terminate()
            except OSError as err:
                logging.error('stop() %s', err)
        self._proc = None
        if not quiet:
            self.update_status()

    def toggle_pause(self, quiet=False):
        if self.get_state() is PAUSED:
            self._proc.send_signal(signal.SIGCONT)
            self.paused = False
        elif self.get_state() is PLAYING:
            self._proc.send_signal(signal.SIGSTOP)
            self.paused = True
        if not quiet:
            self.update_status()

    def toggle_stop(self, quiet=False):
        if self.get_state() is STOPPED:
            self.play(self.entry, self.offset)
        else:
            self.stop()
        if not quiet:
            self.update_status()

    def parse_progress(self, fd):
        if self.step:
            return

        r = self.parse_buf(os.read(fd, 512))
        if r is not None:
            self.offset, self.length = r
        if self.length is None:
            self.length = self.offset * 2
        self.show_position()

        if self.entry.maxoffset and self.offset >= self.entry.maxoffset:
            self._proc.terminate()

    def parse_buf(self, buf):
        raise NotImplementedError

    def get_state(self):
        if self.entry is None:
            return UNINITIALIZED
        if self._proc is None:
            return STOPPED
        elif self._proc.poll() is not None:
            return FINISHED
        elif self.paused:
            return PAUSED
        else:
            return PLAYING

    def seek(self, offset):
        d = offset * self.length * 0.002
        self.step = self.step + d if self.step * d > 0 else d
        self.offset = min(self.length, max(0, self.offset + self.step))
        self.show_position()

    def jump(self, offset):
        if offset < 0:
            offset += self.length
        self.play(self.entry, offset)

    def show_position(self):
        APP.counter.counter(self.offset, self.length - self.offset)
        APP.progress.progress(
            (float(self.offset) / self.length) if self.length else 0)

    def update_status(self):
        if self.get_state() is STOPPED:
            APP.status.set_default_status(_('Stopped: %s') % self.entry.vp())
        elif self.get_state() is PAUSED:
            APP.status.set_default_status(_('Paused: %s') % self.entry.vp())
        elif self.get_state() is PLAYING:
            APP.status.set_default_status(_('Playing: %s') % self.entry.vp())
        else:
            APP.status.set_default_status('')


class FrameOffsetBackend(Backend):
    re_progress = re.compile(br'Time.*\s((\d+:)+\d+).*\[((\d+:)+\d+)')

    def parse_buf(self, buf):
        def parse_time(s):
            parts = reversed(s.split(b':'))
            return sum([int(x) * 60 ** i for i, x in enumerate(parts)])

        match = self.re_progress.search(buf)
        if match:
            head = parse_time(match.group(1))
            tail = parse_time(match.group(3))
            return head, head + tail


class FrameOffsetBackendMpp(Backend):
    re_progress = re.compile(br'.*\s(\d+):(\d+).*\s(\d+):(\d+)')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            m1, s1, m2, s2 = map(int, match.groups())
            offset = m1 * 60 + s1
            length = m2 * 60 + s2
            return offset, length


class TimeOffsetBackend(Backend):
    re_progress = re.compile(br'(\d+):(\d+):(\d+)')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            h, m, s = map(int, match.groups())
            tail = h * 3600 + m * 60 + s
            length = max(self.length, tail)
            offset = length - tail
            return offset, length


class SoxBackend(Backend):
    re_progress = re.compile(
        br'(\d+):(\d+):(\d+)\.\d+ \[(\d+):(\d+):(\d+)\.\d+\]')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            h, m, s, h2, m2, s2 = map(int, match.groups())
            head = h * 3600 + m * 60 + s
            tail = h2 * 3600 + m2 * 60 + s2
            return head, head + tail


class GSTBackend(Backend):
    re_progress = re.compile(
        br'Time: (\d+):(\d+):(\d+).(\d+) of (\d+):(\d+):(\d+).(\d+)')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            ph, pm, ps, us, lh, lm, ls, lus = map(int, match.groups())
            offset = ph * 3600 + pm * 60 + ps
            length = lh * 3600 + lm * 60 + ls
            return offset, length


class FFPlay(Backend):
    re_progress = re.compile(br' *(\d+)\.')

    def parse_buf(self, buf):
        match = self.re_progress.match(buf)
        if match:
            offset = int(match.groups()[0])
            return offset, None


class NoOffsetBackend(Backend):

    def parse_buf(self, buf):
        pass

    def seek(self, *dummy):
        pass


class NoBufferBackend(Backend):
    def __init__(self, *args):
        super().__init__(*args)
        self._starttime = 0

    def play(self, entry, offset):
        self._starttime = time.time()
        super().play(entry, offset)

    def parse_buf(self, buf):
        offset = time.time() - self._starttime + self.offset
        return offset, None


class MPlayer(Backend):
    re_progress = re.compile(br'^A:.*?(\d+)\.\d \([^)]+\) of (\d+)\.\d')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            return map(int, match.groups())


class MPV(Backend):
    re_progress = re.compile(br'AV?: (\d+):(\d+):(\d+) / (\d+):(\d+):(\d+)')

    def parse_buf(self, buf):
        match = self.re_progress.search(buf)
        if match:
            ph, pm, ps, lh, lm, ls = map(int, match.groups())
            offset = ph * 3600 + pm * 60 + ps
            length = lh * 3600 + lm * 60 + ls
            return offset, length


class Timeout:
    def __init__(self):
        self._next = 0
        self._dict = {}

    def add(self, timeout, func, args=()):
        self._next += 1
        tid = self._next
        self._dict[tid] = (func, args, time.time() + timeout)
        return tid

    def remove(self, tid):
        del self._dict[tid]

    def check(self, now):
        for tid, (func, args, timeout) in list(self._dict.items()):
            if now >= timeout:
                self.remove(tid)
                func(*args)
        return 0.2 if self._dict else None


class FIFOControl:
    def __init__(self):
        self.commands = {
            'pause': [APP.player.toggle_pause, []],
            'next': [APP.player.next_prev_song, [+1]],
            'prev': [APP.player.next_prev_song, [-1]],
            'forward': [APP.player.seek, [1]],
            'backward': [APP.player.seek, [-1]],
            'play': [APP.player.toggle_stop, []],
            'stop': [APP.player.toggle_stop, []],
            'volume': [self.volume, None],
            'add': [APP.playlist.add, None],
            'empty': [APP.playlist.command_delete_all, []],
            'quit': [APP.quit, []]
        }
        self.fd = None
        if not os.path.exists(APP.fifo):
            os.mkfifo(APP.fifo, 0o600)
            self.fd = open(APP.fifo, 'rb+', 0)

    def cleanup(self):
        if self.fd is not None:
            self.fd.close()
            os.unlink(APP.fifo)

    def handle_command(self):
        argv = self.fd.readline().decode(CODE).strip().split(' ', 1)
        if argv[0] in self.commands:
            f, a = self.commands[argv[0]]
            if a is None:
                a = argv[1:]
            f(*a)

    def volume(self, s):
        argv = s.split()
        try:
            APP.player.mixer(argv[0], [int(argv[1])])
        except:
            pass


class Mixer:
    def __init__(self):
        self._channels = []

    @property
    def channel(self):
        return self._channels[0][1]

    def get(self):
        raise NotImplementedError

    def set(self, level):
        raise NotImplementedError

    def cue(self, increment):
        self.set(self.get() + increment)

    def toggle(self):
        self._channels.append(self._channels.pop(0))

    def __str__(self):
        return _('%s volume %s%%') % (self._channels[0][0], self.get())

    def close(self):
        pass


class OssMixer(Mixer):
    def __init__(self):
        super().__init__()
        import ossaudiodev as oss
        self._mixer = oss.openmixer()
        self._channels = [
            ('PCM', oss.SOUND_MIXER_PCM),
            ('MASTER', oss.SOUND_MIXER_VOLUME),
        ]

    def get(self):
        return self._mixer.get(self.channel)[0]

    def set(self, level):
        self._mixer.set(self.channel, (level, level))

    def close(self):
        self._mixer.close()


class AlsaMixer(Mixer):
    def __init__(self):
        super().__init__()
        import alsaaudio
        self._channels = []
        # HACK: guess valid card indexes (0 could be disabled)
        for cardindex in range(3):
            try:
                for name in alsaaudio.mixers(cardindex):
                    mixer = alsaaudio.Mixer(name, cardindex=cardindex)
                    volumecap = mixer.volumecap()
                    if volumecap and 'Capture Volume' not in volumecap:
                        full_name = '%i %s' % (cardindex, name)
                        self._channels.append((full_name, mixer))
            except alsaaudio.ALSAAudioError:
                pass
        if not self._channels:
            raise ValueError

    def get(self):
        return self._channels[0][1].getvolume()[0]

    def set(self, level):
        try:
            self._channels[0][1].setvolume(level)
        except:
            pass

    def close(self):
        for ch in self._channels:
            ch[1].close()


class PulseMixer(Mixer):
    def __init__(self):
        super().__init__()
        self._channels = [('Master', sink) for sink in self._list_sinks()]
        if not self._channels:
            raise ValueError
        self.set(self.get())

    def _list_sinks(self):
        result = subprocess.Popen(
            ['pactl', 'list', 'sinks'],
            shell=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE).communicate()[0].split(b'\n\n')
        return dict(
            (re.match(b'Sink #([0-9]+)', a).group(1), a) for a in result)

    def get(self):
        sink = self._list_sinks()[self.channel]
        return int(re.search(
            br'^\s+Volume: .* ([0-9]+)%', sink, flags=re.MULTILINE
        ).group(1))

    def set(self, vol):
        subprocess.check_call([
            'pactl', '--', 'set-sink-volume', self.channel, '%s%%' % vol
        ])

    def cue(self, inc):
        self.set('%+d' % inc)


class KeymapStack(list):
    def push(self, item):
        self.append(item)

    def process(self, code):
        for keymap in reversed(self):
            if keymap and keymap.process(code):
                break


class Keymap:
    def __init__(self):
        self.methods = dict()

    def bind(self, key, method, args=None):
        if isinstance(key, (tuple, list)):
            for i in key:
                self.bind(i, method, args)
            return
        elif isinstance(key, str):
            key = ord(key)
        self.methods[key] = (method, args)

    def process(self, key):
        if key not in self.methods:
            return False
        method, args = self.methods[key]
        if args is None:
            args = (key, )
        method(*args)
        return True


# FIXME: Metadata gathering seems a bit slow now. Perhaps it could be
# done in background so it wouldn't slow down responsiveness
def get_metadata(pathname):
    metadata = {
        'title': os.path.basename(pathname) or pathname,
    }

    is_url = re.compile(r'^https?://').match(pathname)
    if is_url or not os.path.exists(pathname):
        return metadata

    try:
        import mutagen
    except ImportError:
        logging.debug('No mutagen available')
        APP.status.status(
            _('Can\'t read metadata, module mutagen not available'), 2)
        return metadata

    try:
        data = mutagen.File(pathname, easy=True)
        for key in METADATA:
            if data and key in data:
                metadata[key] = ' '.join(data[key]).strip()
    except:
        logging.debug('Error reading metadata')
        logging.debug(traceback.format_exc())
        APP.status.status('Error reading metadata', 1)
    return metadata


def valid_song(name):
    return any(backend.re_files.search(name) for backend in BACKENDS)


def valid_playlist(name):
    return re.search(r'\.(m3u|pls|cue)$', name, re.IGNORECASE)


def which(program):
    for path in os.environ.get('PATH', os.defpath).split(':'):
        if path and os.path.exists(os.path.join(path, program)):
            return os.path.join(path, program)


def cut(s, n, left=False):
    if len(s) <= n:
        return s
    elif left:
        return '<%s' % s[-n + 1:]
    else:
        return '%s>' % s[:n - 1]


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__.split('\n')[0],
        epilog=_('When in doubt, press \'h\' for a friendly help page.'))
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument(
        '-d',
        '--debug',
        metavar=_('filename'),
        help=_('Enable debugging output to <filename>.'))
    parser.add_argument('-n', '--restricted', action='store_true',
                        help=_('Start in restricted mode: No shell commands, '
                               'changing directory, goto, or saving '
                               'playlists.'))
    parser.add_argument('-r', '--repeat', action='store_true',
                        help=_('Start in repeat mode.'))
    parser.add_argument('-R', '--random', action='store_true',
                        help=_('Start in random mode.'))
    parser.add_argument('-q', '--autoexit', action='store_true',
                        help=_('quit at the end of the playlist'))
    parser.add_argument('-m', '--toggle-mixer', action='store_true',
                        help=_('Switch mixer channels.'))
    parser.add_argument('-s', '--save',
                        nargs='?', default=False, metavar='FILE',
                        help=_('Save state on close and restore on open.'))
    parser.add_argument('--fifo', help=_('FIFO socket used by cnq'))
    parser.add_argument('files', metavar=_('file'), nargs='*',
                        help=_('file, dir or playlist'))

    args = parser.parse_args()

    if not args.save and args.save is not False:
        args.save = '.cplay.rec'

    if args.save and os.path.exists(args.save):
        with open(args.save, 'rb') as fh:
            recovery = pickle.load(fh)
    else:
        recovery = {}

    args.repeat = args.repeat or recovery.get('repeat', False)
    args.random = args.random or recovery.get('random', False)
    args.buffer = recovery.get('buffer', [])
    args.entry = recovery.get('entry')
    args.offset = recovery.get('offset', 0)
    args.length = recovery.get('length', 0)

    return args


def main():
    args = parse_args()

    if args.debug is not None:
        logging.basicConfig(filename=args.debug, level=logging.DEBUG)

    global APP
    APP = Application()

    if args.fifo is not None:
        APP.fifo = args.fifo

    playlist = []
    if not sys.stdin.isatty():
        playlist = [x.strip() for x in sys.stdin]
        os.close(0)
        os.open('/dev/tty', 0)
    try:
        APP.setup()
        APP.restricted = args.restricted
        APP.recover = args.save

        if args.repeat:
            APP.playlist.command_toggle_repeat()
        if args.random:
            APP.playlist.command_toggle_random()
        if args.autoexit:
            APP.quit_after_playlist = True
        if args.toggle_mixer:
            APP.player.mixer('toggle')
        logging.debug('Preferred encoding is %s' % str(CODE))

        if args.files or playlist:
            for i in args.files or playlist:
                i = os.path.abspath(i) if os.path.exists(i) else i
                APP.playlist.add(i)
            APP.window.win_tab.change_window()
            APP.player.next_prev_song(1)
        elif args.buffer:
            APP.playlist.buffer = args.buffer
            APP.window.win_tab.change_window()

        if args.entry is not None:
            APP.player.setup_backend(args.entry)
            APP.player.backend.offset = args.offset
            APP.player.backend.length = args.length
            APP.player.backend.update_status()

        APP.run()
    except SystemExit:
        APP.cleanup()
    except:
        APP.cleanup()
        traceback.print_exc()


MIXERS = [PulseMixer, AlsaMixer, OssMixer]
BACKENDS = [
    FrameOffsetBackend('ogg123 -q -v -k {offset} {file}', r'\.ogg$'),
    FrameOffsetBackend(
        'splay -f -k {offset} {file}', r'(^https?://|\.mp[123]$)', 38.28),
    FrameOffsetBackend(
        'mpg123 -q -v -k {offset} {file}', r'(^https?://|\.mp[123]$)', 38.28),
    FrameOffsetBackend(
        'mpg321 -q -v -k {offset} {file}', r'(^https?://|\.mp[123]$)', 38.28),
    FrameOffsetBackendMpp(
        'mppdec --gain 2 --start {offset} {file}', r'\.mp[cp+]$'),
    TimeOffsetBackend(
        'madplay -v --display-time=remaining -s {offset} {file}',
        r'\.mp[123]$'),
    MPlayer(
        'mplayer -ss {offset} {file}',
        r'^https?://|\.(mp[1234]|ogg|oga|opus|flac|spx|mp[cp+]|mod|xm|fm|s3m|'
        r'med|col|669|it|mtm|stm|aiff|au|cdr|wav|wma|m4a|m4b|webm)$'),
    MPV('mpv --audio-display=no --start {offset} {file}',
        r'^https?://|\.(mp[1234]|ogg|oga|opus|flac|spx|mp[cp+]|mod|xm|fm|s3m|'
        r'med|col|669|it|mtm|stm|aiff|au|cdr|wav|wma|m4a|m4b|webm)$'),
    GSTBackend(
        'gst123 -k {offset} {file}',
        r'^https?://|\.(mp[1234]|ogg|oga|opus|flac|wav|m4a|m4b|aiff|webm)$'),
    SoxBackend('play {file} trim {offset}', r'\.(aiff|au|cdr|mp3|ogg|wav)$'),
    FFPlay(
        'ffplay -nodisp -autoexit -ss {offset} {file}',
        r'^https?://|\.(mp[1234]|ogg|oga|opus|flac|wav|m4a|m4b|aiff|webm)$'),
    FFPlay(
        'avplay -nodisp -autoexit -ss {offset} {file}',
        r'^https?://|\.(mp[1234]|ogg|oga|opus|flac|wav|m4a|m4b|aiff)$'),
    NoOffsetBackend(
        'mikmod -q -p0 {file}', r'\.(mod|xm|fm|s3m|med|col|669|it|mtm)$'),
    NoOffsetBackend(
        'xmp -q {file}', r'\.(mod|xm|fm|s3m|med|col|669|it|mtm|stm)$'),
    NoOffsetBackend('speexdec {file}', r'\.spx$'),
    NoOffsetBackend(
        'timidity {file}', r'\.(mid|rmi|rcp|r36|g18|g36|mfi|kar|mod|wrd)$'),
]


if __name__ == '__main__':
    main()

# vim: ts=4 sts=4 sw=4 et

#!/usr/bin/env python
# -*- python -*-

__version__ = "cplay-ng 2.0.1"

"""
cplay - A curses front-end for various audio players
Copyright (C) 1998-2005 Ulf Betlehem <flu@iki.fi>
              2005-2010 see AUTHORS

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

import os
import sys
import glob
import time
import getopt
import random
import re
import signal
import string
import select
import subprocess
import traceback
import locale
import logging
from pkg_resources import resource_filename

try:
    from ncurses import curses
except ImportError:
    import curses

locale.setlocale(locale.LC_ALL, "")
code = locale.getpreferredencoding()

try:
    import gettext
    gettext.install('cplay', resource_filename(__name__, 'i18n'))
except:
    _ = lambda s: s

try:
    import tty
except ImportError:
    tty = None

try:
    import magic
except ImportError:
    magic = None

app = None

XTERM = re.search("rxvt|xterm", os.environ["TERM"])
CONTROL_FIFO = ("%s/cplay-control-%s" %
                (os.environ.get("TMPDIR", "/tmp"), os.environ["USER"]))

# Ten band graphical equalizers for mplayer, see man (1) mplayer
# Default: first entry
EQUALIZERS = [
    ("0:0:0:0:0:0:0:0:0:0", "flat"),
    ("3:3:3:2:0:-1:-1:0:0:1", "rock"),
]
SPEED_OFFSET = 0.005

EQUALIZERS = [
    ("0:0:0:0:0:0:0:0:0:0", "flat"),
    ("3:3:3:2:0:-1:-1:0:0:1", "rock"),
]
# Ten band graphical equalizers for mplayer, see man (1) mplayer
# Default: first entry

SPEED_OFFSET = 0.005


def which(program):
    for path in os.environ["PATH"].split(":"):
        if os.path.exists(os.path.join(path, program)):
            return os.path.join(path, program)


def cut(s, n, left=False):
    if left:
        return "<%s" % s[-n + 1:] if len(s) > n else s
    else:
        return "%s>" % s[:n - 1] if len(s) > n else s


class KeymapStack(list):
    def push(self, item):
        self.append(item)

    def process(self, code):
        for keymap in reversed(self):
            if keymap and keymap.process(code):
                break


class Keymap:
    def __init__(self):
        self.methods = [None] * curses.KEY_MAX

    def bind(self, key, method, args=None):
        if isinstance(key, (tuple, list)):
            for i in key:
                self.bind(i, method, args)
            return
        elif isinstance(key, str):
            key = ord(key)
        self.methods[key] = (method, args)

    def process(self, key):
        try:
            if self.methods[key] is None:
                return False
        except IndexError:
            return False
        method, args = self.methods[key]
        if args is None:
            args = (key,)
        method(*args)
        return True


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

    def getmaxyx(self):
        y, x = self.w.getmaxyx()
        try:
            curses.version  # tested with 1.2 and 1.6
        except AttributeError:
            # pyncurses - emulate traditional (silly) behavior
            y, x = y + 1, x + 1
        return y, x

    def touchwin(self):
        try:
            self.w.touchwin()
        except AttributeError:
            self.touchln(0, self.getmaxyx()[0])

    def attron(self, attr):
        try:
            self.w.attron(attr)
        except AttributeError:
            self.w.attr_on(attr)

    def attroff(self, attr):
        try:
            self.w.attroff(attr)
        except AttributeError:
            self.w.attr_off(attr)

    def newwin(self):
        return curses.newwin(curses.tigetnum('lines'),
                             curses.tigetnum('cols'), 0, 0)

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
        Window.__init__(self, parent)
        self.value = 0

    def newwin(self):
        return curses.newwin(1, self.parent.cols, self.parent.rows - 2, 0)

    def update(self):
        self.move(0, 0)
        self.hline(ord('-'), self.cols)
        if self.value > 0:
            self.move(0, 0)
            x = int(self.value * self.cols)  # 0 to cols-1
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
        Window.__init__(self, parent)
        self.default_message = ''
        self.current_message = ''
        self.tid = None

    def newwin(self):
        return curses.newwin(1, self.parent.cols - 12, self.parent.rows - 1, 0)

    def update(self):
        self.move(0, 0)
        self.clrtoeol()
        self.insstr(cut(self.current_message, self.cols))
        self.touchwin()
        self.refresh()

    def status(self, message, duration=0):
        self.current_message = str(message)
        if self.tid:
            app.timeout.remove(self.tid)
        if duration:
            self.tid = app.timeout.add(duration, self.timeout)
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
            sys.stderr.write("\033]0;%s\a" % (message or "cplay"))

    def restore_default_status(self):
        self.status(self.default_message)

    def length(self):
        return self.cols


class CounterWindow(Window):
    ELAPSED = 0
    REMAINING = 1

    def __init__(self, parent):
        Window.__init__(self, parent)
        # [seconds elapsed, seconds remaining of current track]
        self.values = [0, 0]
        self.mode = self.REMAINING

    def newwin(self):
        return curses.newwin(1, 11, self.parent.rows - 1,
                             self.parent.cols - 11)

    def update(self):
        h, s = divmod(self.values[self.mode], 3600)
        m, s = divmod(s, 60)
        self.move(0, 0)
        self.attron(curses.A_BOLD)
        self.insstr("%02dh %02dm %02ds" % (h, m, s))
        self.attroff(curses.A_BOLD)
        self.touchwin()
        self.refresh()

    def counter(self, values):
        """Update the counter with [elapsed, remaining] seconds"""
        if (values[0] < 0 or values[1] < 0):
            logging.debug("Backend reported negative value "
                          "for (remaining) playing time.")
        else:
            self.values = values
            self.update()

    def toggle_mode(self):
        if self.mode == self.ELAPSED:
            self.mode = self.REMAINING
            tmp = _("remaining")
        else:
            self.mode = self.ELAPSED
            tmp = _("elapsed")
        app.status.status(_("Counting %s time") % tmp, 1)
        self.update()


class RootWindow(Window):
    def __init__(self):
        Window.__init__(self, None)
        self.keymap = Keymap()
        app.keymapstack.push(self.keymap)
        self.win_progress = ProgressWindow(self)
        self.win_status = StatusWindow(self)
        self.win_counter = CounterWindow(self)
        self.win_tab = TabWindow(self)

    def setup_keymap(self):
        self.keymap.bind(12, self.update, ())  # C-l
        self.keymap.bind([curses.KEY_LEFT, 2], app.player.seek,
                         (-1, 1))  # C-b
        self.keymap.bind([curses.KEY_RIGHT, 6], app.player.seek,
                         (1, 1))  # C-f
        self.keymap.bind([1, '^'], app.player.seek, (0, 0))  # C-a
        self.keymap.bind([5, '$'], app.player.seek, (-1, 0))  # C-e
        # 0123456789
        self.keymap.bind(list(range(48, 58)), app.player.key_volume)
        self.keymap.bind(['+'], app.player.mixer, ("cue", [1]))
        self.keymap.bind('-', app.player.mixer, ("cue", [-1]))
        self.keymap.bind('n', app.player.next_prev_song, (+1,))
        self.keymap.bind('p', app.player.next_prev_song, (-1,))
        self.keymap.bind('z', app.player.toggle_pause, ())
        self.keymap.bind('x', app.player.toggle_stop, ())
        self.keymap.bind('c', app.counter.toggle_mode, ())
        self.keymap.bind('Q', app.quit, ())
        self.keymap.bind('q', self.command_quit, ())
        self.keymap.bind('v', app.player.mixer, ("toggle",))
        self.keymap.bind(',', app.macro.command_macro, ())
        # FIXME Document this
        self.keymap.bind('[', app.player.incr_reset_decr_speed, (-1,))
        self.keymap.bind(']', app.player.incr_reset_decr_speed, (+1,))
        self.keymap.bind('\\', app.player.incr_reset_decr_speed, (0,))
        self.keymap.bind('e', app.player.next_prev_eq, (+1,))
        self.keymap.bind('E', app.player.next_prev_eq, (-1,))

    def command_quit(self):
        app.input.do_hook = self.do_quit
        app.input.start(_("Quit? (y/N)"))

    def do_quit(self, ch):
        if chr(ch) == 'y':
            app.quit()
        app.input.stop()


class TabWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.active_child = 0

        self.win_filelist = self.add(FilelistWindow)
        self.win_playlist = self.add(PlaylistWindow)
        self.win_help = self.add(HelpWindow)

        keymap = Keymap()
        keymap.bind('\t', self.change_window, ())  # tab
        keymap.bind('h', self.help, ())
        app.keymapstack.push(keymap)
        app.keymapstack.push(self.children[self.active_child].keymap)

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

    def add(self, Class):
        win = Class(self)
        win.visible = False
        return win

    def change_window(self, window=None):
        app.keymapstack.pop()
        self.children[self.active_child].visible = False
        if window:
            self.active_child = self.children.index(window)
        else:
            # toggle windows 0 and 1
            self.active_child = not self.active_child
        app.keymapstack.push(self.children[self.active_child].keymap)
        self.update()

    def help(self):
        if self.children[self.active_child] == self.win_help:
            self.change_window(self.win_last)
        else:
            self.win_last = self.children[self.active_child]
            self.change_window(self.win_help)
            app.status.status(__version__, 2)


class ListWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.buffer = []
        self.bufptr = self.scrptr = 0
        self.search_direction = 0
        self.last_search = ""
        self.hoffset = 0
        self.keymap = Keymap()
        self.keymap.bind(['k', curses.KEY_UP, 16], self.cursor_move, (-1,))
        self.keymap.bind(['j', curses.KEY_DOWN, 14], self.cursor_move, (1,))
        self.keymap.bind(['K', curses.KEY_PPAGE], self.cursor_ppage, ())
        self.keymap.bind(['J', curses.KEY_NPAGE], self.cursor_npage, ())
        self.keymap.bind(['g', curses.KEY_HOME], self.cursor_home, ())
        self.keymap.bind(['G', curses.KEY_END], self.cursor_end, ())
        self.keymap.bind(['?', 18], self.start_search,
                         (_("backward-isearch"), -1))
        self.keymap.bind(['/', 19], self.start_search,
                         (_("forward-isearch"), 1))
        self.keymap.bind(['>'], self.hscroll, (8,))
        self.keymap.bind(['<'], self.hscroll, (-8,))

    def newwin(self):
        return curses.newwin(self.parent.rows - 2, self.parent.cols,
                             self.parent.ypos + 2, self.parent.xpos)

    def update(self, force=True):
        self.bufptr = max(0, min(self.bufptr, len(self.buffer) - 1))
        first, last = self.scrptr, self.scrptr + self.rows - 1
        if (self.bufptr < first):
            first = self.bufptr
        if (self.bufptr > last):
            first = self.bufptr - self.rows + 1
        if force or self.scrptr != first:
            self.scrptr = first
            self.move(0, 0)
            self.clrtobot()
            i = 0
            for entry in self.buffer[first:first + self.rows]:
                self.move(i, 0)
                i = i + 1
                self.putstr(entry)
            if self.visible:
                self.refresh()
                self.parent.update_title()
        self.update_line(curses.A_REVERSE)

    def update_line(self, attr=None, refresh=True):
        if len(self.buffer) == 0:
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

    def get_title(self, data=""):
        pos = "%s-%s/%s" % (self.scrptr + min(1, len(self.buffer)),
                            min(self.scrptr + self.rows, len(self.buffer)),
                            len(self.buffer))
        width = self.cols - len(pos) - 2
        data = cut(data, width - len(self.name), left=True)
        return "%-*s  %s" % (width, cut(self.name + data, width), pos)

    def putstr(self, entry, *pos):
        s = str(entry)
        if pos:
            self.move(*pos)
        if self.hoffset:
            s = "<%s" % s[self.hoffset + 1:]
        self.insstr(cut(s, self.cols))

    def current(self):
        if len(self.buffer) == 0:
            return None
        if self.bufptr >= len(self.buffer):
            self.bufptr = len(self.buffer) - 1
        return self.buffer[self.bufptr]

    def cursor_move(self, ydiff):
        if app.input.active:
            app.input.cancel()
        if len(self.buffer) == 0:
            return
        self.update_line(refresh=False)
        self.bufptr = (self.bufptr + ydiff) % len(self.buffer)
        self.update(force=False)

    def cursor_ppage(self):
        self.bufptr = self.scrptr - 1
        if self.bufptr < 0:
            self.bufptr = len(self.buffer) - 1
        self.scrptr = max(0, self.bufptr - self.rows)
        self.update()

    def cursor_npage(self):
        self.bufptr = self.scrptr + self.rows
        if self.bufptr > len(self.buffer) - 1:
            self.bufptr = 0
        self.scrptr = self.bufptr
        self.update()

    def cursor_home(self):
        self.cursor_move(-self.bufptr)

    def cursor_end(self):
        self.cursor_move(-self.bufptr - 1)

    def start_search(self, prompt_text, direction):
        self.search_direction = direction
        self.not_found = False
        if app.input.active:
            app.input.prompt = "%s: " % prompt_text
            self.do_search(advance=direction)
        else:
            app.input.do_hook = self.do_search
            app.input.stop_hook = self.stop_search
            app.input.start(prompt_text)

    def stop_search(self):
        self.last_search = app.input.string
        app.status.status(_("ok"), 1)

    def do_search(self, ch=None, advance=0):
        old_string = app.input.string
        if ch in [8, 127]:
            new_string = old_string[:-1]
        elif ch:
            new_string = "%s%c" % (old_string, ch)
        elif not old_string:
            new_string = self.last_search
        else:
            new_string = old_string
        app.input.string = new_string
        index = self.bufptr + advance
        while True:
            if not 0 <= index < len(self.buffer):
                app.status.status(_("Not found: %s ") % new_string)
                self.not_found = True
                break
            line = str(self.buffer[index]).lower()
            if line.find(new_string.lower()) != -1:
                app.input.show()
                self.update_line(refresh=False)
                self.bufptr = index
                self.update(force=False)
                self.not_found = False
                break
            if self.not_found:
                app.status.status(_("Not found: %s ") % new_string)
                break
            index = index + self.search_direction

    def hscroll(self, value):
        self.hoffset = max(0, self.hoffset + value)
        self.update()


class HelpWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.name = _("Help")
        self.keymap.bind('q', self.parent.help, ())
        self.buffer = _("""\
  Global                               t, T  : tag current/regex
  ------                               u, U  : untag current/regex
  Up, Down, k, j, C-p, C-n,            Sp, i : invert current/all
  PgUp, PgDn, K, J,                    !, ,  : shell, macro
  Home, End, g, G : movement
  Enter           : chdir or play      Filelist
  Tab             : filelist/playlist  --------
  n, p            : next/prev track    a     : add (tagged) to playlist
  z, x            : toggle pause/stop  s     : recursive search
                                       BS, o : goto parent/specified dir
  Left, Right,                         m, '  : set/get bookmark
  C-f, C-b    : seek forward/backward
  C-a, C-e    : restart/end track      Playlist
  C-s, C-r, / : isearch                --------
  C-g, Esc    : cancel                 d, D  : delete (tagged) tracks/playlist
  1..9, +, -  : volume control         m, M  : move tagged tracks after/before
  c, v        : counter/volume mode    r, R  : toggle repeat/Random mode
  <, >        : horizontal scrolling   s, S  : shuffle/Sort playlist
  C-l, l      : refresh, list mode     w, @  : write playlist, jump to active
  h, q, Q     : help, quit?, Quit!     X     : stop playlist after each track
""").split("\n")


class ListEntry:
    def __init__(self, pathname, dir=False):
        self.filename = os.path.basename(pathname)
        self.pathname = pathname
        self.slash = "/" if dir else ""
        self.tagged = False

    def set_tagged(self, value):
        self.tagged = value

    def is_tagged(self):
        return self.tagged

    def __str__(self):
        mark = "*" if self.is_tagged() else " "
        return "%s %s%s" % (mark, self.vp(), self.slash)

    def vp(self):
        return self.vps[0][1](self)

    def vp_filename(self):
        if self.filename:
            return self.filename
        else:
            return self.pathname

    def vp_pathname(self):
        return self.pathname

    vps = [[_("filename"), vp_filename],
           [_("pathname"), vp_pathname]]


class PlaylistEntry(ListEntry):
    def __init__(self, pathname):
        ListEntry.__init__(self, pathname)
        self.metadata = None
        self.active = False

    def set_active(self, value):
        self.active = value

    def is_active(self):
        return self.active

    def vp_metadata(self):
        if self.metadata is None:
            self.metadata = get_tag(self.pathname)
            logging.debug(self.metadata)
        return self.metadata

    vps = ListEntry.vps[:] + [[_("metadata"), vp_metadata]]


class TagListWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.tag_value = None
        self.keymap.bind(' ', self.command_tag_untag, ())
        self.keymap.bind('i', self.command_invert_tags, ())
        self.keymap.bind('t', self.command_tag, (True,))
        self.keymap.bind('u', self.command_tag, (False,))
        self.keymap.bind('T', self.command_tag_regexp, (True,))
        self.keymap.bind('U', self.command_tag_regexp, (False,))
        self.keymap.bind('l', self.command_change_viewpoint, ())
        self.keymap.bind('!', self.command_shell, ())

    def command_shell(self):
        if app.restricted:
            return
        app.input.stop_hook = self.stop_shell
        app.input.complete_hook = self.complete_shell
        app.input.start(_("shell$ "), colon=False)

    def stop_shell(self):
        s = app.input.string
        curses.endwin()
        sys.stderr.write("\n")
        argv = [x.pathname for x in self.get_tagged()]
        if not argv and self.current():
            argv.append(self.current().pathname)
        ret_value = subprocess.call([s, '--'] + argv, shell=True)
        if ret_value != 0:
            sys.stderr.write("\nshell returned %s, press return!\n" %
                             ret_value)
            sys.stdin.readline()
            app.window.update()
            app.status.restore_default_status()
        else:
            app.status.status(_("Command successfully executed.\n"), 2)
            app.window.update()
        app.cursor(0)

    def complete_shell(self, line):
        return self.complete_generic(line, quote=True)

    def complete_generic(self, line, quote=False):
        if quote:
            s = re.sub('.*[^\\\\][ \'"()\[\]{}$`]', '', line)
            s, part = re.sub('\\\\', '', s), line[:len(line) - len(s)]
        else:
            s, part = line, ""
        results = glob.glob(os.path.expanduser(s) + "*")
        if len(results) == 0:
            return line
        if len(results) == 1:
            lm = results[0]
            if os.path.isdir(lm):
                lm += "/"
        else:
            lm = results[0]
            for result in results:
                for i in range(min(len(result), len(lm))):
                    if result[i] != lm[i]:
                        lm = lm[:i]
                        break
        if quote:
            lm = re.sub('([ \'"()\[\]{}$`])', '\\\\\\1', lm)
        return part + lm

    def command_change_viewpoint(self, klass=ListEntry):
        klass.vps.append(klass.vps.pop(0))
        app.status.status(_("Listing %s") % klass.vps[0][0], 1)
        app.player.backend.update_status()
        self.update()

    def command_invert_tags(self):
        for i in self.buffer:
            i.set_tagged(not i.is_tagged())
        self.update()

    def command_tag_untag(self):
        if len(self.buffer) == 0:
            return
        tmp = self.buffer[self.bufptr]
        tmp.set_tagged(not tmp.is_tagged())
        self.cursor_move(1)

    def command_tag(self, value):
        if len(self.buffer) == 0:
            return
        self.buffer[self.bufptr].set_tagged(value)
        self.cursor_move(1)

    def command_tag_regexp(self, value):
        self.tag_value = value
        app.input.stop_hook = self.stop_tag_regexp
        app.input.start(_("Tag regexp") if value else _("Untag regexp"))

    def stop_tag_regexp(self):
        try:
            r = re.compile(app.input.string, re.I)
            for entry in self.buffer:
                if r.search(str(entry)):
                    entry.set_tagged(self.tag_value)
            self.update()
            app.status.status(_("ok"), 1)
        except re.error as e:
            app.status.status(e, 2)

    def get_tagged(self):
        return [x for x in self.buffer if x.is_tagged()]

    def not_tagged(self, l):
        return [x for x in l if not x.is_tagged()]


class FilelistWindow(TagListWindow):
    DIR = 0
    SEARCH = 1

    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.oldposition = {}
        try:
            self.chdir(os.getcwd())
        except OSError:
            self.chdir(os.environ['HOME'])
        self.startdir = self.cwd
        self.mtime_when = 0
        self.mtime = None
        self.mode = self.DIR
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_chdir_or_play, ())
        self.keymap.bind(['.', 127, curses.KEY_BACKSPACE],
                         self.command_chparentdir, ())
        self.keymap.bind('a', self.command_add_recursively, ())
        self.keymap.bind('o', self.command_goto, ())
        self.keymap.bind('s', self.command_search_recursively, ())
        self.keymap.bind('m', self.command_set_bookmark, ())
        self.keymap.bind("'", self.command_get_bookmark, ())
        self.bookmarks = {39: [self.cwd, 0]}

    def command_get_bookmark(self):
        app.input.do_hook = self.do_get_bookmark
        app.input.start(_("bookmark"))

    def do_get_bookmark(self, ch):
        app.input.string = ch
        bookmark = self.bookmarks.get(ch)
        if bookmark:
            self.bookmarks[39] = [self.cwd, self.bufptr]
            dir, pos = bookmark
            self.chdir(dir)
            self.listdir()
            self.bufptr = pos
            self.update()
            app.status.status(_("ok"), 1)
        else:
            app.status.status(_("Not found!"), 1)
        app.input.stop()

    def command_set_bookmark(self):
        app.input.do_hook = self.do_set_bookmark
        app.input.start(_("set bookmark"))

    def do_set_bookmark(self, ch):
        app.input.string = ch
        self.bookmarks[ch] = [self.cwd, self.bufptr]
        if ch:
            app.status.status(_("ok"), 1)
        else:
            app.input.stop()

    def command_search_recursively(self):
        app.input.stop_hook = self.stop_search_recursively
        app.input.start(_("search"))

    def stop_search_recursively(self):
        try:
            re_tmp = re.compile(app.input.string, re.I)
        except re.error as e:
            app.status.status(e, 2)
            return
        app.status.status(_("Searching..."))
        results = []
        for entry in self.buffer:
            if entry.filename == "..":
                continue
            if re_tmp.search(entry.filename):
                results.append(entry)
            elif os.path.isdir(entry.pathname):
                try:
                    self.search_recursively(re_tmp, entry.pathname, results)
                except:
                    pass
        if self.mode != self.SEARCH:
            self.chdir(os.path.join(self.cwd, _('search results')))
            self.mode = self.SEARCH
        self.buffer = results
        self.bufptr = 0
        self.parent.update_title()
        self.update()
        app.status.restore_default_status()

    def search_recursively(self, re_tmp, dir, results):
        for filename in os.listdir(dir):
            pathname = os.path.join(dir, filename)
            if re_tmp.search(filename):
                if os.path.isdir(pathname):
                    results.append(ListEntry(pathname, 1))
                elif VALID_PLAYLIST(filename) or VALID_SONG(filename):
                    results.append(ListEntry(pathname))
            elif os.path.isdir(pathname):
                self.search_recursively(re_tmp, pathname, results)

    def get_title(self):
        self.name = _("Filelist: ")
        return ListWindow.get_title(self, re.sub("/?$", "/", self.cwd))

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
            app.status.status(_("Reading directory..."))
        self.mode = self.DIR
        dirs = []
        files = []
        try:
            self.mtime = int(os.stat(self.cwd).st_mtime)
            self.mtime_when = time.time()
            filenames = os.listdir(self.cwd)
            filenames.sort()
            for filename in filenames:
                if filename[0] == ".":
                    continue
                pathname = os.path.join(self.cwd, filename)
                if os.path.isdir(pathname):
                    dirs.append(pathname)
                elif VALID_SONG(filename):
                    files.append(pathname)
                elif VALID_PLAYLIST(filename):
                    files.append(pathname)
        except os.error:
            pass
        dots = ListEntry(os.path.join(self.cwd, ".."), 1)
        self.buffer = [[dots], []][self.cwd == "/"]
        for i in dirs:
            self.buffer.append(ListEntry(i, True))
        for i in files:
            self.buffer.append(ListEntry(i))
        if prevdir:
            for self.bufptr in range(len(self.buffer)):
                if self.buffer[self.bufptr].filename == prevdir:
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
            app.status.restore_default_status()

    def chdir(self, dir):
        if hasattr(self, "cwd"):
            self.oldposition[self.cwd] = self.bufptr
        self.cwd = os.path.normpath(dir)
        try:
            os.chdir(self.cwd)
        except:
            pass

    def command_chdir_or_play(self):
        if len(self.buffer) == 0:
            return
        if self.current().filename == "..":
            self.command_chparentdir()
        elif os.path.isdir(self.current().pathname):
            self.chdir(self.current().pathname)
            self.listdir()
        elif VALID_SONG(self.current().filename):
            app.player.play(self.current())

    def command_chparentdir(self):
        if app.restricted and self.cwd == self.startdir:
            return
        dir = os.path.basename(self.cwd)
        self.chdir(os.path.dirname(self.cwd))
        self.listdir(prevdir=dir)

    def command_goto(self):
        if app.restricted:
            return
        app.input.stop_hook = self.stop_goto
        app.input.complete_hook = self.complete_generic
        app.input.start(_("goto"))

    def stop_goto(self):
        dir = os.path.expanduser(app.input.string)
        if dir[0] != '/':
            dir = os.path.join(self.cwd, dir)
        if not os.path.isdir(dir):
            app.status.status(_("Not a directory!"), 1)
            return
        self.chdir(dir)
        self.listdir()

    def command_add_recursively(self):
        l = self.get_tagged()
        if len(l) == 0:
            app.playlist.add(self.current().pathname)
            self.cursor_move(1)
            return
        app.status.status(_("Adding tagged files"), 1)
        for entry in l:
            app.playlist.add(entry.pathname, quiet=True)
            entry.set_tagged(False)
        self.update()


class Playlist:
    def __init__(self):
        self.buffer = []
        self.bufptr = 0
        self.pathname = None
        self.repeat = False
        self.random = False
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.stop = False

    def update(self):
        pass

    def append(self, item):
        self.buffer.append(item)
        if self.random:
            self.random_left.append(item)

    def add_dir(self, dir):
        filenames = os.listdir(dir)
        for filename in sorted(filenames):
            self._add(os.path.join(dir, filename), quiet=True)

    def add_m3u(self, line):
        if re.match("^(#.*)?$", line):
            return
        if re.match("^(/|http://)", line):
            self.append(PlaylistEntry(self.fix_url(line)))
        else:
            dirname = os.path.dirname(self.pathname)
            self.append(PlaylistEntry(os.path.join(dirname, line)))

    def add_pls(self, line):
        # todo - support title & length
        m = re.match("File(\d+)=(.*)", line)
        if m:
            self.append(PlaylistEntry(self.fix_url(m.group(2))))

    def add_playlist(self, pathname):
        self.pathname = pathname
        if re.search("\.m3u$", pathname, re.I):
            f = self.add_m3u
        if re.search("\.pls$", pathname, re.I):
            f = self.add_pls
        file = open(pathname)
        for line in file.readlines():
            f(line.strip())
        file.close()

    def _add(self, pathname, quiet=False):
        if os.path.isdir(pathname):
            if not quiet:
                app.status.status(_("Working..."))
            self.add_dir(pathname)
        elif VALID_PLAYLIST(pathname):
            self.add_playlist(pathname)
        elif VALID_SONG(pathname):
            self.append(PlaylistEntry(pathname))
        else:
            return
        # todo - refactor
        filename = os.path.basename(pathname) or pathname
        if not quiet:
            self.update()
            app.status.status(_("Added: %s") % filename, 1)

    def add(self, pathname, quiet=False):
        try:
            self._add(pathname)
        except Exception as e:
            app.status.status(e, 2)

    def fix_url(self, url):
        return re.sub("(http://[^/]+)/?(.*)", "\\1/\\2", url)

    def change_active_entry(self, direction):
        if len(self.buffer) == 0:
            return
        old = self.get_active_entry()
        new = None
        if self.random:
            if direction > 0:
                if len(self.random_next) > 0:
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
                old.set_active(False)
        elif old:
            index = self.buffer.index(old) + direction
            if not (0 <= index < len(self.buffer) or self.repeat):
                return
            old.set_active(False)
            new = self.buffer[index % len(self.buffer)]
        else:
            new = self.buffer[0]
        new.set_active(True)
        self.update()
        return new

    def get_active_entry(self):
        for entry in self.buffer:
            if entry.is_active():
                return entry

    def command_jump_to_active(self):
        entry = self.get_active_entry()
        if entry is not None:
            self.bufptr = self.buffer.index(entry)
            self.update()

    def command_play(self):
        if len(self.buffer) == 0:
            return
        entry = self.get_active_entry()
        if entry is not None:
            entry.set_active(False)
        entry = self.current()
        entry.set_active(True)
        self.update()
        app.player.play(entry)

    def command_delete(self):
        if len(self.buffer) == 0:
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
            current_entry.set_tagged(True)
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
        app.status.status(_("Deleted playlist"), 1)
        self.update()

    def command_move(self, after=False):
        if len(self.buffer) == 0:
            return
        current_entry = self.current()
        l = self.get_tagged()
        if len(l) == 0 or current_entry.is_tagged():
            return
        self.buffer = self.not_tagged(self.buffer)
        self.bufptr = self.buffer.index(current_entry)
        if after:
            self.bufptr += 1
        self.buffer[self.bufptr:self.bufptr] = l
        self.update()

    def command_shuffle(self):
        random.shuffle(self.buffer)
        self.bufptr = 0
        self.update()
        app.status.status(_("Shuffled playlist... Oops?"), 1)

    def command_sort(self):
        app.status.status(_("Working..."))
        self.buffer.sort(key=lambda x: x.vp())
        self.bufptr = 0
        self.update()
        app.status.status(_("Sorted playlist"), 1)

    def command_toggle_repeat(self):
        self.toggle("repeat", _("Repeat: %s"))

    def command_toggle_random(self):
        self.toggle("random", _("Random: %s"))
        self.random_prev = []
        self.random_next = []
        self.random_left = self.buffer[:]

    def command_toggle_stop(self):
        self.toggle("stop", _("Stop playlist: %s"))

    def toggle(self, attr, format):
        setattr(self, attr, not getattr(self, attr))
        app.status.status(format % (_("on") if getattr(self, attr)
                                    else _("off")), 1)

    def command_save_playlist(self):
        if app.restricted:
            return
        default = self.pathname or "%s/" % app.filelist.cwd
        app.input.stop_hook = self.stop_save_playlist
        app.input.start(_("Save playlist"), default)

    def stop_save_playlist(self):
        pathname = app.input.string
        if pathname[0] != '/':
            pathname = os.path.join(app.filelist.cwd, pathname)
        if not re.search("\.m3u$", pathname, re.I):
            pathname = "%s%s" % (pathname, ".m3u")
        try:
            file = open(pathname, "w")
            for entry in self.buffer:
                file.write("%s\n" % entry.pathname)
            file.close()
            self.pathname = pathname
            app.status.status(_("ok"), 1)
        except IOError as e:
            app.status.status(e, 2)


class PlaylistWindow(TagListWindow, Playlist):
    def __init__(self, parent):
        Playlist.__init__(self)
        TagListWindow.__init__(self, parent)
        self.keymap.bind(['\n', curses.KEY_ENTER], self.command_play, ())
        self.keymap.bind('d', self.command_delete, ())
        self.keymap.bind('D', self.command_delete_all, ())
        self.keymap.bind('m', self.command_move, (True,))
        self.keymap.bind('M', self.command_move, (False,))
        self.keymap.bind('s', self.command_shuffle, ())
        self.keymap.bind('S', self.command_sort, ())
        self.keymap.bind('r', self.command_toggle_repeat, ())
        self.keymap.bind('R', self.command_toggle_random, ())
        self.keymap.bind('X', self.command_toggle_stop, ())
        self.keymap.bind('w', self.command_save_playlist, ())
        self.keymap.bind('@', self.command_jump_to_active, ())

    def command_change_viewpoint(self, klass=PlaylistEntry):
        TagListWindow.command_change_viewpoint(self, klass)

    def get_title(self):
        space_out = lambda value, s: s if value else " " * len(s)
        self.name = _("Playlist %s %s %s") % (
            space_out(self.repeat, _("[repeat]")),
            space_out(self.random, _("[random]")),
            space_out(self.stop, _("[stop]")))
        return ListWindow.get_title(self)

    def putstr(self, entry, *pos):
        if entry.is_active():
            self.attron(curses.A_BOLD)
        ListWindow.putstr(self, entry, *pos)
        if entry.is_active():
            self.attroff(curses.A_BOLD)

    def toggle(self, attr, format):
        Playlist.toggle(self, attr, format)
        self.parent.update_title()


def get_type(pathname):
    if magic is not None:
        mg_string = magic.from_file(pathname)
        logging.debug("Magic type:" + mg_string)
        if re.match("^Ogg data, Vorbis audio.*", mg_string):
            ftype = 'oggvorbis'
        elif re.match("^Ogg data, FLAC audio.*", mg_string):
            ftype = 'oggflac'
        elif re.match("FLAC audio bitstream.*", mg_string):
            ftype = 'flac'
        # For some reason not all ID3 tagged files return an ID3 identifier,
        # so we just need to look for mp3 files and hope they are also ID3d.
        elif re.match(".*MPEG ADTS, layer III.*", mg_string):
            ftype = 'id3'
        else:
            ftype = "unknown"
        logging.debug("Magic category: " + ftype)
        return ftype
    if re.match(".*\.ogg$", pathname, re.I):
        return 'oggvorbis'
    elif re.match(".*\.oga$", pathname, re.I):
        return 'oggflac'
    elif re.match(".*\.flac$", pathname, re.I):
        return 'flac'
    elif re.match(".*\.mp3$", pathname, re.I):
        return 'id3'
    return "unknown"


# FIXME: Metadata gathering seems a bit slow now. Perhaps it could be done
#        in background so it wouldn't slow down responsiveness
def get_tag(pathname):
    if re.compile("^http://").match(pathname) or not os.path.exists(pathname):
        return pathname
    try:
        import mutagen
    except ImportError:
        logging.debug("No mutagen available")
        app.status.status(_("Can't read metadata, module mutagen not "
                            "available"), 2)
        return pathname

    ftype = get_type(pathname)
    try:
        if ftype == 'oggvorbis':
            import mutagen.oggvorbis
            metaopen = mutagen.oggvorbis.Open
        elif ftype == 'id3':
            import mutagen.easyid3
            metaopen = mutagen.easyid3.Open
        elif ftype == 'flac':
            import mutagen.flac
            metaopen = mutagen.flac.Open
        elif ftype == 'oggflac':
            import mutagen.oggflac
            metaopen = mutagen.oggflac.Open
        else:
            app.status.status(_("Can't read metadata, I don't know "
                                "this file"), 1)
            return os.path.basename(pathname)
        f = metaopen(pathname)
    except:
        logging.debug("Error reading metadata")
        logging.debug(traceback.format_exc())
        app.status.status("Error reading metadata", 1)
        return os.path.basename(pathname)

    # FIXME: Allow user to configure metadata view
    try:
        return (" ".join(f.get('artist', ('?',))) + " - " +
                " ".join(f.get('album', ('?',))) + " - " +
                " ".join(f.get('tracknumber', ('?',))) + " " +
                " ".join(f.get('title', ('?',)))).encode(code, 'replace')
    except:
        logging.debug(traceback.format_exc())
        return os.path.basename(pathname)


class Backend:

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    def __init__(self, commandline, files, fps=1):
        self.commandline = commandline
        self.argv = None
        self.re_files = re.compile(files, re.I)
        self.fps = fps
        self.entry = None
        # True only if stopped manually or playlist ran out
        self.stopped = False
        self.paused = False
        self.offset = 0
        self.length = 0
        self.time_setup = None
        self.buf = ''
        self.tid = None
        self._p = None

    def setup(self, entry, offset):
        """Ready the backend with given ListEntry and seek offset"""

        self.argv = self.commandline.split()
        self.argv[0] = which(self.argv[0])
        for i in range(len(self.argv)):
            if self.argv[i] == "{file}":
                self.argv[i] = entry.pathname
            if self.argv[i] == "{offset}":
                self.argv[i] = str(offset * self.fps)
        self.entry = entry
        self.offset = offset
        if offset == 0:
            app.progress.progress(0)
            self.offset = 0
            self.length = 0
        self.time_setup = time.time()
        return self.argv[0]

    def play(self):
        logging.debug("Executing " + " ".join(self.argv))
        logging.debug("My offset is %d" % self.offset)

        self._p = subprocess.Popen(self.argv,
                                   stdout=self.stdout_w,
                                   stderr=self.stderr_w,
                                   stdin=self.stdin_r)

        self.stopped = False
        self.paused = False
        self.step = 0
        self.update_status()

    def stop(self, quiet=False):
        if self._p is None:
            return
        if self.paused:
            self.toggle_pause(quiet)
        try:
            self._p.terminate()
        except OSError:
            pass
        self.stopped = True
        if not quiet:
            self.update_status()

    def toggle_pause(self, quiet=False):
        if self._p is None:
            return
        self._p.send_signal(signal.SIGCONT if self.paused else signal.SIGSTOP)
        self.paused = not self.paused
        if not quiet:
            self.update_status()

    def parse_progress(self):
        if self.stopped or self.step:
            self.tid = None
        else:
            self.parse_buf()
            self.tid = app.timeout.add(1.0, self.parse_progress)

    def read_fd(self, fd):
        self.buf = os.read(fd, 512)
        if self.tid is None:
            self.parse_progress()

    def poll(self):
        if self.stopped or self._p is None:
            return 0
        elif self._p.poll() is not None:
            self._p = None
            app.status.set_default_status("")
            app.counter.counter([0, 0])
            app.progress.progress(0)
            return True

    def seek(self, offset, relative):
        if relative:
            d = offset * self.length * 0.002
            self.step = self.step + d if self.step * d > 0 else d
            self.offset = min(self.length, max(0, self.offset + self.step))
        else:
            self.step = 1
            self.offset = self.length + offset if offset < 0 else offset
        self.show_position()

    def set_position(self, offset, length):
        """Update the visible playback position.

        offset is elapsed time, length the total length of track in seconds

        """
        self.offset = offset
        self.length = length
        self.show_position()

    def show_position(self):
        app.counter.counter((self.offset, self.length - self.offset))
        app.progress.progress((float(self.offset) / self.length)
                              if self.length else 0)

    def update_status(self):
        if self.entry is None:
            app.status.set_default_status("")
        elif self.stopped:
            app.status.set_default_status(_("Stopped: %s") % self.entry.vp())
        elif self.paused:
            app.status.set_default_status(_("Paused: %s") % self.entry.vp())
        else:
            logging.debug(self.entry.vp())
            app.status.set_default_status(_("Playing: %s") % self.entry.vp())


class FrameOffsetBackend(Backend):
    re_progress = re.compile(b"Time.*\s(\d+):(\d+).*\[(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(int, match.groups())
            head, tail = m1*60 + s1, m2*60 + s2
            self.set_position(head, head + tail)


class FrameOffsetBackendMpp(Backend):
    re_progress = re.compile(b".*\s(\d+):(\d+).*\s(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(int, match.groups())
            head = m1*60 + s1
            tail = (m2*60 + s2) - head
            self.set_position(head, head + tail)


class TimeOffsetBackend(Backend):
    re_progress = re.compile(b"(\d+):(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            h, m, s = map(int, match.groups())
            tail = h*3600 + m*60 + s
            head = max(self.length, tail) - tail
            self.set_position(head, head + tail)


class GSTBackend(Backend):
    re_progress = re.compile(b"Time: (\d+):(\d+):(\d+).(\d+)"
                             b" of (\d+):(\d+):(\d+).(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            ph, pm, ps, us, lh, lm, ls, lus = map(int, match.groups())
            position = ph*3600 + pm*60 + ps
            length = lh*3600 + lm*60 + ls
            self.set_position(position, length)


class NoOffsetBackend(Backend):

    def parse_buf(self):
        head = self.offset + 1
        self.set_position(head, head * 2)

    def seek(self, *dummy):
        pass


class MPlayer(Backend):
    re_progress = re.compile(b"^A:.*?(\d+)\.\d \([^)]+\) of (\d+)\.\d")
    speed = 1.0
    eq_cur = 0
    equalizer = EQUALIZERS[eq_cur][0]

    def play(self):
        Backend.play(self)
        self.mplayer_send("speed_set %f" % self.speed)
        self.mplayer_send("af equalizer=" + self.equalizer)
        self.mplayer_send("seek %d\n" % self.offset)

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            curS, totS = map(int, match.groups())
            position, length = curS, totS
            self.set_position(position, length)
        else:
            logging.debug("Cannot parse mplayer output")

    def mplayer_send(self, arg):
        logging.debug("Sending command " + arg)
        try:
            os.write(self.stdin_w, arg + "\n")
        except IOError:
            logging.debug("Can't write to stdin_w.")
            app.status.status(_("ERROR: Cannot send commands to mplayer!"), 3)

    def speed_chg(self, set):
        self.speed = set
        self.mplayer_send("speed_set %f" % self.speed)
        app.status.status(_("Speed: %s%%") % (self.speed * 100), 1)

    def eq_chg(self, offset):
        if len(EQUALIZERS) == 0:
            app.status.status(_("No equalizers configured"), 2)
            return
        self.eq_cur = (self.eq_cur + offset) % (len(EQUALIZERS))
        self.equalizer = EQUALIZERS[self.eq_cur][0]
        app.status.status(_("Equalizer: %s(%s)") % (EQUALIZERS[self.eq_cur][1],
                                                    self.equalizer), 1)
        self.mplayer_send("af equalizer=" + self.equalizer)


class Timeout:
    def __init__(self):
        self.next = 0
        self.dict = {}

    def add(self, timeout, func, args=()):
        tid = self.next = self.next + 1
        self.dict[tid] = (func, args, time.time() + timeout)
        return tid

    def remove(self, tid):
        del self.dict[tid]

    def check(self, now):
        for tid, (func, args, timeout) in list(self.dict.items()):
            if now >= timeout:
                self.remove(tid)
                func(*args)
        return 0.2 if len(self.dict) else None


class FIFOControl:
    def __init__(self):
        self.commands = {
            "pause": [app.player.toggle_pause, []],
            "next": [app.player.next_prev_song, [+1]],
            "prev": [app.player.next_prev_song, [-1]],
            "forward": [app.player.seek, [1, 1]],
            "backward": [app.player.seek, [-1, 1]],
            "play": [app.player.toggle_stop, []],
            "stop": [app.player.toggle_stop, []],
            "volume": [self.volume, None],
            "macro": [app.macro.run_macro, None],
            "add": [app.playlist.add, None],
            "empty": [app.playlist.command_delete_all, []],
            "quit": [app.quit, []]
        }
        self.fd = None
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
            os.mkfifo(CONTROL_FIFO, 0o600)
            self.fd = open(CONTROL_FIFO, "rb+", 0)
        except IOError:
            # warn that we're disabling the fifo because someone raced us?
            return

    def handle_command(self):
        argv = self.fd.readline().strip().split(" ", 1)
        if argv[0] in self.commands.keys():
            f, a = self.commands[argv[0]]
            if a is None:
                a = argv[1:]
            f(*a)

    def volume(self, s):
        argv = s.split()
        try:
            app.player.mixer(argv[0], [int(argv[1])])
        except:
            pass


class Player:
    def __init__(self):
        self.backend = BACKENDS[0]
        self.channels = []
        self.play_tid = None
        self._mixer = None
        for mixer in MIXERS:
            try:
                self._mixer = mixer()
                break
            except Exception:
                pass

    def setup_backend(self, entry, offset=0):
        if entry is None or offset is None:
            return False
        logging.debug("Setting up backend for " + str(entry))
        self.backend.stop(quiet=True)
        for self.backend in BACKENDS:
            if self.backend.re_files.search(entry.pathname):
                if self.backend.setup(entry, offset):
                    return True
        else:
            # FIXME: Needs to report suitable backends
            logging.debug("Backend not found")
            app.status.status(_("Backend not found!"), 1)
            self.backend.stopped = False  # keep going
            return False

    def play(self, entry, offset=0):
        # Play executed, remove from queue
        self.play_tid = None
        if entry is None or offset is None:
            return
        logging.debug("Starting to play " + str(entry))
        if self.setup_backend(entry, offset):
            self.backend.play()
        else:
            app.timeout.add(1, self.next_prev_song, (1,))

    def delayed_play(self, entry, offset):
        if self.play_tid:
            app.timeout.remove(self.play_tid)
        self.play_tid = app.timeout.add(0.5, self.play, (entry, offset))

    def next_prev_song(self, direction):
        new_entry = app.playlist.change_active_entry(direction)
        self.setup_backend(new_entry, 0)  # Fixes DB#287871 and DB#303282.
        # The backend has to be set-up right away when changing songs.
        # Otherwise the user can manipulate the old offset value while waiting
        # for the delayed play to trigger, which causes the next song to play
        # from a wrong offset instead of its beginning.
        self.delayed_play(new_entry, 0)

    def seek(self, offset, relative):
        if self.backend.entry is None:
            return
        self.backend.seek(offset, relative)
        self.delayed_play(self.backend.entry, self.backend.offset)

    def toggle_pause(self):
        if self.backend.entry is None:
            return
        if not self.backend.stopped:
            self.backend.toggle_pause()

    def toggle_stop(self):
        if self.backend.entry is None:
            return
        if not self.backend.stopped:
            self.backend.stop()
        else:
            self.play(self.backend.entry, self.backend.offset)

    def key_volume(self, ch):
        self.mixer("set", [int((ch & 0x0f) * 100 / 9.0)])

    def mixer(self, cmd=None, args=[]):
        if self._mixer is None:
            app.status.status(_("No mixer."), 1)
        else:
            getattr(self._mixer, cmd)(*args)
            app.status.status(str(self._mixer), 1)

    def incr_reset_decr_speed(self, signum):
        if (isinstance(self.backend, MPlayer)):
            if (signum == 0):
                self.backend.speed_chg(1.0)
            else:
                self.backend.speed_chg(self.backend.speed +
                                       signum*SPEED_OFFSET)
        else:
            app.status.status(_("Speed control requires MPlayer"), 1)

    def next_prev_eq(self, direction):
        if(isinstance(self.backend, MPlayer)):
            self.backend.eq_chg(direction)
        else:
            app.status.status(_("Equalizer support requires MPlayer"), 1)


class Input:
    def __init__(self):
        self.active = False
        self.string = ""
        # optionally patch these
        self.do_hook = None
        self.stop_hook = None
        self.complete_hook = None

    def show(self):
        pass

    def start(self, prompt="", data="", colon=True):
        self.active = True
        self.string = data

    def do(self, *args):
        if self.do_hook:
            return self.do_hook(*args)
        ch = args[0] if args else None
        if ch in [8, 127]:  # backspace
            self.string = self.string[:-1]
        elif ch == 9 and self.complete_hook:
            self.string = self.complete_hook(self.string)
        elif ch == 21:  # C-u
            self.string = ""
        elif ch == 23:  # C-w
            self.string = re.sub("((.* )?)\w.*", "\\1", self.string)
        elif ch:
            self.string = "%s%c" % (self.string, ch)
        self.show()

    def stop(self, *args):
        self.active = False
        if self.string and self.stop_hook:
            self.stop_hook(*args)
        self.do_hook = None
        self.stop_hook = None
        self.complete_hook = None

    def cancel(self):
        self.string = ""
        self.stop()


class UIInput(Input):
    def __init__(self):
        Input.__init__(self)
        self.prompt = ""
        self.keymap = Keymap()
        self.keymap.bind(list(Window.chars), self.do)
        self.keymap.bind([127, curses.KEY_BACKSPACE], self.do, (8,))
        self.keymap.bind([21, 23], self.do)
        self.keymap.bind(['\a', 27], self.cancel, ())
        self.keymap.bind(['\n', curses.KEY_ENTER], self.stop, ())

    def show(self):
        n = len(self.prompt) + 1
        s = cut(self.string, app.status.length() - n, left=True)
        app.status.status("%s%s " % (self.prompt, s))

    def start(self, prompt="", data="", colon=True):
        Input.start(self, prompt=prompt, data=data, colon=colon)
        app.cursor(1)
        app.keymapstack.push(self.keymap)
        self.prompt = prompt + (": " if colon else "")
        self.show()

    def stop(self, *args):
        Input.stop(self, *args)
        app.cursor(0)
        app.keymapstack.pop()
        if not self.string:
            app.status.status(_("cancel"), 1)


class MacroController:
    def command_macro(self):
        app.input.do_hook = self.do_macro
        app.input.start(_("macro"))

    def do_macro(self, ch):
        app.input.stop()
        self.run_macro(chr(ch))

    def run_macro(self, c):
        for i in MACRO.get(c, ""):
            app.keymapstack.process(ord(i))


class Application:
    def __init__(self):
        self.tcattr = None
        self.restricted = False

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
        self.input = UIInput()
        self.macro = MacroController()
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
            sys.stderr.write("\033]0;%s\a" % "xterm")
        if tty is not None:
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, self.tcattr)
        # remove temporary files
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
        except IOError:
            pass

    def run(self):
        while True:
            now = time.time()
            timeout = self.timeout.check(now)
            self.filelist.listdir_maybe(now)
            if not self.player.backend.stopped:
                timeout = 0.5
                if self.player.backend.poll():
                    # end of playlist hack
                    self.player.backend.stopped = True
                    if not self.playlist.stop:
                        entry = self.playlist.change_active_entry(1)
                        if entry is None:
                            self.player.backend.stopped = True
                        else:
                            self.player.play(entry)
            R = [sys.stdin, self.player.backend.stdout_r,
                 self.player.backend.stderr_r]
            if self.control.fd:
                R.append(self.control.fd)
            try:
                r, w, e = select.select(R, [], [], timeout)
            except select.error:
                continue
            # user
            if sys.stdin in r:
                c = self.window.getch()
                app.keymapstack.process(c)
            # backend
            if self.player.backend.stderr_r in r:
                self.player.backend.read_fd(self.player.backend.stderr_r)
            # backend
            if self.player.backend.stdout_r in r:
                self.player.backend.read_fd(self.player.backend.stdout_r)
            # remote
            if self.control.fd in r:
                self.control.handle_command()

    def cursor(self, visibility):
        try:
            curses.curs_set(visibility)
        except:
            pass

    def quit(self, status=0):
        self.player.backend.stop(quiet=True)
        sys.exit(status)

    def handler_resize(self, sig, frame):
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


class Mixer(object):
    def __init__(self):
        self.channels = []

    def get(self):
        raise NotImplementedError

    def set(self, level):
        raise NotImplementedError

    def cue(self, increment):
        self.set(self.get() + increment)

    def toggle(self):
        self.channels.append(self.channels.pop(0))

    def __str__(self):
        return _("%s volume %s%%") % (self.channels[0][0], self.get())

    def close(self):
        pass


class OssMixer(Mixer):
    def __init__(self):
        try:
            import ossaudiodev as oss
            self._ossaudiodev = True
        except ImportError:
            import oss
            self._ossaudiodev = False
        self._mixer = oss.openmixer()
        self._channels = [
            ('PCM', oss.SOUND_MIXER_PCM),
            ('MASTER', oss.SOUND_MIXER_VOLUME),
        ]

    def get(self):
        if self._ossaudiodev:
            return self._mixer.get(self._channels[0][1])[0]
        else:
            return self._mixer.read_channel(self._channels[0][1])[0]

    def set(self, level):
        if self._ossaudiodev:
            self._mixer.set(self._channels[0][1], (level, level))
        else:
            self._mixer.write_channel(self._channels[0][1], (level, level))

    def close(self):
        self._mixer.close()


class AlsaMixer(Mixer):
    def __init__(self):
        import alsaaudio
        self.channels = [
            ('Master', alsaaudio.Mixer('Master')),
            ('PCM', alsaaudio.Mixer('PCM')),
        ]

    def get(self):
        return self.channels[0][1].getvolume()[0]

    def set(self, level):
        self.channels[0][1].setvolume(level)

    def close(self):
        for ch in self.channels:
            ch[1].close()


class PulseMixer(Mixer):
    def __init__(self):
        self.channels = [
            ('Master', 'Master')
        ]
        self.set(self.get())

    def get(self):
        out, err = subprocess.Popen(['pacmd', 'dump-volumes'], shell=False,
                                    stdout=subprocess.PIPE).communicate()
        return re.search(r'Sink 0.*current_hw.* ([0-9]+)%', out).group(1)

    def set(self, arg):
        subprocess.check_call(['pactl', 'set-sink-volume', '0', '--',
                              '%s%%' % arg])

    def cue(self, arg):
        self.set('%+d' % arg)


def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "mnrRd:")
    except:
        usage = _("Usage: %s [-d <filename>] [-mnrR] "
                  "[ file | dir | playlist ] ...\n")
        sys.stderr.write(usage % sys.argv[0])
        sys.exit(1)

    # FIXME option checking in two places
    for opt, optarg in opts:
        if opt == "-d":
            logging.basicConfig(filename=optarg, level=logging.DEBUG)

    global app
    app = Application()

    playlist = []
    if not sys.stdin.isatty():
        playlist = [l.strip() for l in sys.stdin.readlines()]
        os.close(0)
        os.open("/dev/tty", 0)
    try:
        app.setup()
        for opt, optarg in opts:
            if opt == "-n":
                app.restricted = True
            if opt == "-r":
                app.playlist.command_toggle_repeat()
            if opt == "-R":
                app.playlist.command_toggle_random()
            if opt == "-m":
                app.player.mixer("toggle")
        logging.debug("Preferred locale is " + str(code))
        if args or playlist:
            for i in args or playlist:
                i = os.path.abspath(i) if os.path.exists(i) else i
                app.playlist.add(i)
            app.window.change_window()
        app.run()
    except SystemExit:
        app.cleanup()
    except Exception:
        app.cleanup()
        traceback.print_exc()


MIXERS = [OssMixer, AlsaMixer, PulseMixer]
BACKENDS = [
    FrameOffsetBackend("ogg123 -q -v -k {offset} {file}", "\.(ogg|flac|spx)$"),
    FrameOffsetBackend("splay -f -k {offset} {file}", "(^http://|\.mp[123]$)",
                       38.28),
    FrameOffsetBackend("mpg123 -q -v -k {offset} {file}",
                       "(^http://|\.mp[123]$)", 38.28),
    FrameOffsetBackend("mpg321 -q -v -k {offset} {file}",
                       "(^http://|\.mp[123]$)", 38.28),
    FrameOffsetBackendMpp("mppdec --gain 2 --start {offset} {file}",
                          "\.mp[cp+]$"),
    TimeOffsetBackend("madplay -v --display-time=remaining -s {offset} {file}",
                      "\.mp[123]$"),
    MPlayer("mplayer -slave -vc null -vo null {file}",
            "^http://|\.(mp[123]|ogg|oga|flac|spx|mp[cp+]|mod|xm|fm|s3m|"
            "med|col|669|it|mtm|stm|aiff|au|cdr|wav|wma|m4a|m4b)$"),
    GSTBackend("gst123 -k {offset} {file}",
               "\.(mp[123]|ogg|opus|oga|flac|wav|m4a|m4b|aiff)$"),
    NoOffsetBackend("mikmod -q -p0 {file}",
                    "\.(mod|xm|fm|s3m|med|col|669|it|mtm)$"),
    NoOffsetBackend("xmp -q {file}",
                    "\.(mod|xm|fm|s3m|med|col|669|it|mtm|stm)$"),
    NoOffsetBackend("play {file}", "\.(aiff|au|cdr|mp3|ogg|wav)$"),
    NoOffsetBackend("speexdec {file}", "\.spx$"),
    NoOffsetBackend("timidity {file}",
                    "\.(mid|rmi|rcp|r36|g18|g36|mfi|kar|mod|wrd)$"),
]

MACRO = {}


def VALID_SONG(name):
    for backend in BACKENDS:
        if backend.re_files.search(name):
            return True
    return False


def VALID_PLAYLIST(name):
    if re.search("\.(m3u|pls)$", name, re.I):
        return True
    return False


for rc in [os.path.expanduser("~/.cplayrc"), "/etc/cplayrc"]:
    try:
        exec(compile(open(rc).read(), rc, 'exec'))
        break
    except IOError:
        pass


if __name__ == "__main__":
    main()

# vim: ts=4 sts=4 sw=4 et

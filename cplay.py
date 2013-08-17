#!/usr/bin/env python
# -*- python -*-

__version__ = "cplay 1.50"

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
import time
import getopt
import re
import signal
import string
import select
from subprocess import call, Popen
import traceback

global mg
try:
    import magic
    mg = magic.open(magic.MAGIC_NONE)
    mg.load()
except ImportError:
    mg = None

try: from ncurses import curses
except ImportError: import curses

try: import tty
except ImportError: tty = None

try:
    import locale
    locale.setlocale(locale.LC_ALL, "")
    code = locale.getpreferredencoding()
except:
    pass

import logging

# ------------------------------------------
_locale_domain = "cplay"
_locale_dir = "/usr/local/share/locale"

try:
    import gettext  # python 2.0
    gettext.install(_locale_domain, _locale_dir)
except ImportError:
    try:
        import fintl
        fintl.bindtextdomain(_locale_domain, _locale_dir)
        fintl.textdomain(_locale_domain)
        _ = fintl.gettext
    except ImportError:
        def _(s): return s
except:
    def _(s): return s

# ------------------------------------------
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

# ------------------------------------------
def which(program):
    for path in string.split(os.environ["PATH"], ":"):
        if os.path.exists(os.path.join(path, program)):
            return os.path.join(path, program)

# ------------------------------------------
def cut(s, n, left=0):
    if left: return len(s) > n and "<%s" % s[-n+1:] or s
    else: return len(s) > n and "%s>" % s[:n-1] or s

# ------------------------------------------
class Stack:
    def __init__(self):
        self.items = ()

    def push(self, item):
        self.items = (item,) + self.items

    def pop(self):
        self.items, item = self.items[1:], self.items[0]
        return item

# ------------------------------------------
class KeymapStack(Stack):
    def process(self, code):
        for keymap in self.items:
            if keymap and keymap.process(code):
                break

# ------------------------------------------
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
            if self.methods[key] is None: return 0
        except IndexError:
            return 0
        method, args = self.methods[key]
        if args is None: args = (key,)
        method(*args)
        return 1

# ------------------------------------------
class Window:
    chars = string.letters+string.digits+string.punctuation+string.whitespace
    def __init__(self, parent):
        self.parent = parent
        self.children = []
        self.name = None
        self.keymap = None
        self.visible = 1
        self.resize()
        if parent: parent.children.append(self)

    def insstr(self, s):
        if not s: return
        self.w.addstr(s[:-1])
        self.w.hline(ord(s[-1]), 1)  # insch() work-around

    def __getattr__(self, name):
        return getattr(self.w, name)

    def getmaxyx(self):
        y, x = self.w.getmaxyx()
        try: curses.version  # tested with 1.2 and 1.6
        except AttributeError:
            # pyncurses - emulate traditional (silly) behavior
            y, x = y+1, x+1
        return y, x

    def touchwin(self):
        try: self.w.touchwin()
        except AttributeError: self.touchln(0, self.getmaxyx()[0])

    def attron(self, attr):
        try: self.w.attron(attr)
        except AttributeError: self.w.attr_on(attr)

    def attroff(self, attr):
        try: self.w.attroff(attr)
        except AttributeError: self.w.attr_off(attr)

    def newwin(self):
        return curses.newwin(curses.tigetnum('lines'), curses.tigetnum('cols'), 0, 0)

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

# ------------------------------------------
class ProgressWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.value = 0

    def newwin(self):
        return curses.newwin(1, self.parent.cols, self.parent.rows-2, 0)

    def update(self):
        self.move(0, 0)
        self.hline(ord('-'), self.cols)
        if self.value > 0:
            self.move(0, 0)
            x = int(self.value * self.cols)  # 0 to cols-1
            x and self.hline(ord('='), x)
            self.move(0, x)
            self.insstr('|')
        self.touchwin()
        self.refresh()

    def progress(self, value):
        self.value = min(value, 0.99)
        self.update()

# ------------------------------------------
class StatusWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.default_message = ''
        self.current_message = ''
        self.tid = None

    def newwin(self):
        return curses.newwin(1, self.parent.cols-12, self.parent.rows-1, 0)

    def update(self):
        self.move(0, 0)
        self.clrtoeol()
        self.insstr(cut(self.current_message, self.cols))
        self.touchwin()
        self.refresh()

    def status(self, message, duration = 0):
        self.current_message = str(message)
        if self.tid: app.timeout.remove(self.tid)
        if duration: self.tid = app.timeout.add(duration, self.timeout)
        else: self.tid = None
        self.update()

    def timeout(self):
        self.tid = None
        self.restore_default_status()

    def set_default_status(self, message):
        if self.current_message == self.default_message: self.status(message)
        self.default_message = message
        XTERM and sys.stderr.write("\033]0;%s\a" % (message or "cplay"))

    def restore_default_status(self):
        self.status(self.default_message)

# ------------------------------------------
class CounterWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.values = [0, 0] # [seconds elapsed, seconds remaining
                             #  of current track]
        self.mode = 1

    def newwin(self):
        return curses.newwin(1, 11, self.parent.rows-1, self.parent.cols-11)

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
            logging.debug("Player reported negative value for (remaining) playing time.")
        else:
            self.values = values
            self.update()

    def toggle_mode(self):
        self.mode = not self.mode
        tmp = [_("elapsed"), _("remaining")][self.mode]
        app.status(_("Counting %s time") % tmp, 1)
        self.update()

# ------------------------------------------
class RootWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        keymap = Keymap()
        app.keymapstack.push(keymap)
        self.win_progress = ProgressWindow(self)
        self.win_status = StatusWindow(self)
        self.win_counter = CounterWindow(self)
        self.win_tab = TabWindow(self)
        keymap.bind(12, self.update, ()) # C-l
        keymap.bind([curses.KEY_LEFT, 2], app.seek, (-1, 1)) # C-b
        keymap.bind([curses.KEY_RIGHT, 6], app.seek, (1, 1)) # C-f
        keymap.bind([1, '^'], app.seek, (0, 0)) # C-a
        keymap.bind([5, '$'], app.seek, (-1, 0)) # C-e
        keymap.bind(range(48,58), app.key_volume) # 0123456789
        keymap.bind(['+'], app.mixer, ("cue", 1))
        keymap.bind('-', app.mixer, ("cue", -1))
        keymap.bind('n', app.next_prev_song, (+1,))
        keymap.bind('p', app.next_prev_song, (-1,))
        keymap.bind('z', app.toggle_pause, ())
        keymap.bind('x', app.toggle_stop, ())
        keymap.bind('c', self.win_counter.toggle_mode, ())
        keymap.bind('Q', app.quit, ())
        keymap.bind('q', self.command_quit, ())
        keymap.bind('v', app.mixer, ("toggle",))
        keymap.bind(',', app.command_macro, ())
        keymap.bind('[', app.incr_reset_decr_speed, (-1,))  # FIXME Document this
        keymap.bind(']', app.incr_reset_decr_speed, (+1,))  # FIXME Document this
        keymap.bind('\\', app.incr_reset_decr_speed, (0,))   # FIXME Document this
        keymap.bind('e', app.next_prev_eq, (+1,)) # FIXME Document this
        keymap.bind('E', app.next_prev_eq, (-1,)) # FIXME Document this

    def command_quit(self):
        app.do_input_hook = self.do_quit
        app.start_input(_("Quit? (y/N)"))
        
    def do_quit(self, ch):
        if chr(ch) == 'y': app.quit()
        app.stop_input()

# ------------------------------------------
class TabWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.active_child = 0

        self.win_filelist = self.add(FilelistWindow)
        self.win_playlist = self.add(PlaylistWindow)
        self.win_help     = self.add(HelpWindow)

        keymap = Keymap()
        keymap.bind('\t', self.change_window, ()) # tab
        keymap.bind('h', self.help, ())
        app.keymapstack.push(keymap)
        app.keymapstack.push(self.children[self.active_child].keymap)

    def newwin(self):
        return curses.newwin(self.parent.rows-2, self.parent.cols, 0, 0)

    def update(self):
        self.update_title()
        self.move(1, 0)
        self.hline(ord('-'), self.cols)
        self.move(2, 0)
        self.clrtobot()
        self.refresh()
        child = self.children[self.active_child]
        child.visible = 1
        child.update()

    def update_title(self, refresh = 1):
        child = self.children[self.active_child]
        self.move(0, 0)
        self.clrtoeol()
        self.attron(curses.A_BOLD)
        self.insstr(child.get_title())
        self.attroff(curses.A_BOLD)
        if refresh: self.refresh()

    def add(self, Class):
        win = Class(self)
        win.visible = 0
        return win

    def change_window(self, window = None):
        app.keymapstack.pop()
        self.children[self.active_child].visible = 0
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
            app.status(__version__, 2)

# ------------------------------------------
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
        return curses.newwin(self.parent.rows-2, self.parent.cols,
                             self.parent.ypos+2, self.parent.xpos)

    def update(self, force = 1):
        self.bufptr = max(0, min(self.bufptr, len(self.buffer) - 1))
        first, last = self.scrptr, self.scrptr + self.rows - 1
        if (self.bufptr < first): first = self.bufptr
        if (self.bufptr > last): first = self.bufptr - self.rows + 1
        if force or self.scrptr != first:
            self.scrptr = first
            self.move(0, 0)
            self.clrtobot()
            i = 0
            for entry in self.buffer[first:first+self.rows]:
                self.move(i, 0)
                i = i + 1
                self.putstr(entry)
            if self.visible:
                self.refresh()
                self.parent.update_title()
        self.update_line(curses.A_REVERSE)

    def update_line(self, attr = None, refresh = 1):
        if not self.buffer: return
        ypos = self.bufptr - self.scrptr
        if attr: self.attron(attr)
        self.move(ypos, 0)
        self.hline(ord(' '), self.cols)
        self.putstr(self.current())
        if attr: self.attroff(attr)
        if self.visible and refresh: self.refresh()

    def get_title(self, data=""):
        pos = "%s-%s/%s" % (self.scrptr+min(1, len(self.buffer)),
                            min(self.scrptr+self.rows, len(self.buffer)),
                            len(self.buffer))
        width = self.cols-len(pos)-2
        data = cut(data, width-len(self.name), 1)
        return "%-*s  %s" % (width, cut(self.name+data, width), pos)

    def putstr(self, entry, *pos):
        s = str(entry)
        pos and self.move(*pos)
        if self.hoffset: s = "<%s" % s[self.hoffset+1:]
        self.insstr(cut(s, self.cols))

    def current(self):
        if len(self.buffer) == 0: return None
        if self.bufptr >= len(self.buffer): self.bufptr = len(self.buffer) - 1
        return self.buffer[self.bufptr]

    def cursor_move(self, ydiff):
        if app.input_mode: app.cancel_input()
        if not self.buffer: return
        self.update_line(refresh = 0)
        self.bufptr = (self.bufptr + ydiff) % len(self.buffer)
        self.update(force = 0)

    def cursor_ppage(self):
        self.bufptr = self.scrptr - 1
        if self.bufptr < 0: self.bufptr = len(self.buffer) - 1
        self.scrptr = max(0, self.bufptr - self.rows)
        self.update()

    def cursor_npage(self):
        self.bufptr = self.scrptr + self.rows
        if self.bufptr > len(self.buffer) - 1: self.bufptr = 0
        self.scrptr = self.bufptr
        self.update()

    def cursor_home(self): self.cursor_move(-self.bufptr)

    def cursor_end(self): self.cursor_move(-self.bufptr - 1)

    def start_search(self, prompt_text, direction):
        self.search_direction = direction
        self.not_found = 0
        if app.input_mode:
            app.input_prompt = "%s: " % prompt_text
            self.do_search(advance = direction)
        else:
            app.do_input_hook = self.do_search
            app.stop_input_hook = self.stop_search
            app.start_input(prompt_text)

    def stop_search(self):
        self.last_search = app.input_string
        app.status(_("ok"), 1)

    def do_search(self, ch = None, advance = 0):
        if ch in [8, 127]: app.input_string = app.input_string[:-1]
        elif ch: app.input_string = "%s%c" % (app.input_string, ch)
        else: app.input_string = app.input_string or self.last_search
        index = self.bufptr + advance
        while 1:
            if not 0 <= index < len(self.buffer):
                app.status(_("Not found: %s ") % app.input_string)
                self.not_found = 1
                break
            line = string.lower(str(self.buffer[index]))
            if string.find(line, string.lower(app.input_string)) != -1:
                app.show_input()
                self.update_line(refresh = 0)
                self.bufptr = index
                self.update(force = 0)
                self.not_found = 0
                break
            if self.not_found:
                app.status(_("Not found: %s ") % app.input_string)
                break
            index = index + self.search_direction

    def hscroll(self, value):
        self.hoffset = max(0, self.hoffset + value)
        self.update()

# ------------------------------------------
class HelpWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.name = _("Help")
        self.keymap.bind('q', self.parent.help, ())
        self.buffer = string.split(_("""\
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
"""), "\n")

# ------------------------------------------
class ListEntry:
    def __init__(self, pathname, dir=0):
        self.filename = os.path.basename(pathname)
        self.pathname = pathname
        self.slash = dir and "/" or ""
        self.tagged = 0

    def set_tagged(self, value):
        self.tagged = value

    def is_tagged(self):
        return self.tagged == 1

    def __str__(self):
        mark = self.is_tagged() and "*" or " "
        return "%s %s%s" % (mark, self.vp(), self.slash)

    def vp(self):
        return self.vps[0][1](self)

    def vp_filename(self):
        return self.filename or self.pathname

    def vp_pathname(self):
        return self.pathname

    vps = [[_("filename"), vp_filename],
           [_("pathname"), vp_pathname]]

# ------------------------------------------
class PlaylistEntry(ListEntry):
    def __init__(self, pathname):
        ListEntry.__init__(self, pathname)
        self.metadata = None
        self.active = 0

    def set_active(self, value):
        self.active = value

    def is_active(self):
        return self.active == 1

    def vp_metadata(self):
        return self.metadata or self.read_metadata()

    def read_metadata(self):
        self.metadata = get_tag(self.pathname)
        logging.debug(self.metadata)
        return self.metadata

    vps = ListEntry.vps[:] + [[_("metadata"), vp_metadata]]

# ------------------------------------------
class TagListWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.keymap.bind(' ', self.command_tag_untag, ())
        self.keymap.bind('i', self.command_invert_tags, ())
        self.keymap.bind('t', self.command_tag, (1,))
        self.keymap.bind('u', self.command_tag, (0,))
        self.keymap.bind('T', self.command_tag_regexp, (1,))
        self.keymap.bind('U', self.command_tag_regexp, (0,))
        self.keymap.bind('l', self.command_change_viewpoint, ())
        self.keymap.bind('!', self.command_shell, ())

    def command_shell(self):
        if app.restricted: return
        app.stop_input_hook = self.stop_shell
        app.complete_input_hook = self.complete_shell
        app.start_input(_("shell$ "), colon=0)

    def stop_shell(self):
        s = app.input_string
        curses.endwin()
        sys.stderr.write("\n")
        argv = map(lambda x: x.pathname, self.get_tagged())
        argv or self.current() and argv.append(self.current().pathname)
        ret_value = call([s, '--'] + argv, shell=True)
        if ret_value != 0:
               sys.stderr.write("\nshell returned %s, press return!\n" % ret_value)
               sys.stdin.readline()
               app.win_root.update()
               app.restore_default_status()
        else:
               app.status(_("Command successfully executed.\n"), 2)
               app.win_root.update()
        app.cursor(0)

    def complete_shell(self, line):
        return self.complete_generic(line, quote=1)

    def complete_generic(self, line, quote=0):
        import glob
        if quote:
            s = re.sub('.*[^\\\\][ \'"()\[\]{}$`]', '', line)
            s, part = re.sub('\\\\', '', s), line[:len(line)-len(s)]
        else:
            s, part = line, ""
        results = glob.glob(os.path.expanduser(s)+"*")
        if len(results) == 0:
            return line
        if len(results) == 1:
            lm = results[0]
            lm = lm + (os.path.isdir(lm) and "/" or "")
        else:
            lm = results[0]
            for result in results:
                for i in range(min(len(result), len(lm))):
                   if result[i] != lm[i]:
                        lm = lm[:i]
                        break
        if quote: lm = re.sub('([ \'"()\[\]{}$`])', '\\\\\\1', lm)
        return part + lm

    def command_change_viewpoint(self, klass=ListEntry):
        klass.vps.append(klass.vps.pop(0))
        app.status(_("Listing %s") % klass.vps[0][0], 1)
        app.player.update_status()
        self.update()

    def command_invert_tags(self):
        for i in self.buffer:
            i.set_tagged(not i.is_tagged())
        self.update()

    def command_tag_untag(self):
        if not self.buffer: return
        tmp = self.buffer[self.bufptr]
        tmp.set_tagged(not tmp.is_tagged())
        self.cursor_move(1)

    def command_tag(self, value):
        if not self.buffer: return
        self.buffer[self.bufptr].set_tagged(value)
        self.cursor_move(1)

    def command_tag_regexp(self, value):
        self.tag_value = value
        app.stop_input_hook = self.stop_tag_regexp
        app.start_input(value and _("Tag regexp") or _("Untag regexp"))

    def stop_tag_regexp(self):
        try:
            r = re.compile(app.input_string, re.I)
            for entry in self.buffer:
                if r.search(str(entry)):
                    entry.set_tagged(self.tag_value)
            self.update()
            app.status(_("ok"), 1)
        except re.error, e:
            app.status(e, 2)

    def get_tagged(self):
        return filter(lambda x: x.is_tagged(), self.buffer)

    def not_tagged(self, l):
        return filter(lambda x: not x.is_tagged(), l)

# ------------------------------------------
class FilelistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.oldposition = {}
        try: self.chdir(os.getcwd())
        except OSError: self.chdir(os.environ['HOME'])
        self.startdir = self.cwd
        self.mtime_when = 0
        self.mtime = None
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_chdir_or_play, ())
        self.keymap.bind(['.', 127, curses.KEY_BACKSPACE],
                         self.command_chparentdir, ())
        self.keymap.bind('a', self.command_add_recursively, ())
        self.keymap.bind('o', self.command_goto, ())
        self.keymap.bind('s', self.command_search_recursively, ())
        self.keymap.bind('m', self.command_set_bookmark, ())
        self.keymap.bind("'", self.command_get_bookmark, ())
        self.bookmarks = { 39: [self.cwd, 0] }

    def command_get_bookmark(self):
        app.do_input_hook = self.do_get_bookmark
        app.start_input(_("bookmark"))

    def do_get_bookmark(self, ch):
        app.input_string = ch
        bookmark = self.bookmarks.get(ch)
        if bookmark:
            self.bookmarks[39] = [self.cwd, self.bufptr]
            dir, pos = bookmark
            self.chdir(dir)
            self.listdir()
            self.bufptr = pos
            self.update()
            app.status(_("ok"), 1)
        else:
            app.status(_("Not found!"), 1)
        app.stop_input()

    def command_set_bookmark(self):
        app.do_input_hook = self.do_set_bookmark
        app.start_input(_("set bookmark"))
        
    def do_set_bookmark(self, ch):
        app.input_string = ch
        self.bookmarks[ch] = [self.cwd, self.bufptr]
        ch and app.status(_("ok"), 1) or app.stop_input()

    def command_search_recursively(self):
        app.stop_input_hook = self.stop_search_recursively
        app.start_input(_("search"))

    def stop_search_recursively(self):
        try: re_tmp = re.compile(app.input_string, re.I)
        except re.error, e:
            app.status(e, 2)
            return
        app.status(_("Searching..."))
        results = []
        for entry in self.buffer:
            if entry.filename == "..":
                continue
            if re_tmp.search(entry.filename):
                results.append(entry)
            elif os.path.isdir(entry.pathname):
                try: self.search_recursively(re_tmp, entry.pathname, results)
                except: pass
        if not self.search_mode:
            self.chdir(os.path.join(self.cwd,_("search results")))
            self.search_mode = 1
        self.buffer = results
        self.bufptr = 0
        self.parent.update_title()
        self.update()
        app.restore_default_status()

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
        if now < self.mtime_when+2: return
        self.mtime_when = now
        self.oldposition[self.cwd] = self.bufptr
        try: self.mtime == os.stat(self.cwd)[8] or self.listdir(quiet=1)
        except os.error: pass

    def listdir(self, quiet=0, prevdir=None):
        quiet or app.status(_("Reading directory..."))
        self.search_mode = 0
        dirs = []
        files = []
        try:
            self.mtime = os.stat(self.cwd)[8]
            self.mtime_when = time.time()
            filenames = os.listdir(self.cwd)
            filenames.sort()
            for filename in filenames:
                if filename[0] == ".": continue
                pathname = os.path.join(self.cwd, filename)
                if os.path.isdir(pathname): dirs.append(pathname)
                elif VALID_SONG(filename): files.append(pathname)
                elif VALID_PLAYLIST(filename): files.append(pathname)
        except os.error: pass
        dots = ListEntry(os.path.join(self.cwd, ".."), 1)
        self.buffer = [[dots], []][self.cwd == "/"]
        for i in dirs: self.buffer.append(ListEntry(i, 1))
        for i in files: self.buffer.append(ListEntry(i))
        if prevdir:
            for self.bufptr in range(len(self.buffer)):
                if self.buffer[self.bufptr].filename == prevdir: break
            else: self.bufptr = 0
        elif self.oldposition.has_key(self.cwd):
            self.bufptr = self.oldposition[self.cwd]
        else: self.bufptr = 0
        self.parent.update_title()
        self.update()
        quiet or app.restore_default_status()

    def chdir(self, dir):
        if hasattr(self, "cwd"): self.oldposition[self.cwd] = self.bufptr
        self.cwd = os.path.normpath(dir)
        try: os.chdir(self.cwd)
        except: pass

    def command_chdir_or_play(self):
        if not self.buffer: return
        if self.current().filename == "..":
            self.command_chparentdir()
        elif os.path.isdir(self.current().pathname):
            self.chdir(self.current().pathname)
            self.listdir()
        elif VALID_SONG(self.current().filename):
            app.play(self.current())

    def command_chparentdir(self):
        if app.restricted and self.cwd == self.startdir: return
        dir = os.path.basename(self.cwd)
        self.chdir(os.path.dirname(self.cwd))
        self.listdir(prevdir=dir)

    def command_goto(self):
        if app.restricted: return
        app.stop_input_hook = self.stop_goto
        app.complete_input_hook = self.complete_generic
        app.start_input(_("goto"))

    def stop_goto(self):
        dir = os.path.expanduser(app.input_string)
        if dir[0] != '/': dir = os.path.join(self.cwd, dir)
        if not os.path.isdir(dir):
            app.status(_("Not a directory!"), 1)
            return
        self.chdir(dir)
        self.listdir()

    def command_add_recursively(self):
        l = self.get_tagged()
        if not l:
            app.win_playlist.add(self.current().pathname)
            self.cursor_move(1)
            return
        app.status(_("Adding tagged files"), 1)
        for entry in l:
            app.win_playlist.add(entry.pathname, quiet=1)
            entry.set_tagged(0)
        self.update()

# ------------------------------------------
class PlaylistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.pathname = None
        self.repeat = 0
        self.random = 0
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.stop = 0
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_play, ())
        self.keymap.bind('d', self.command_delete, ())
        self.keymap.bind('D', self.command_delete_all, ())
        self.keymap.bind('m', self.command_move, (1,))
        self.keymap.bind('M', self.command_move, (0,))
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
        space_out = lambda value, s: value and s or " "*len(s)
        self.name = _("Playlist %s %s %s") % (
            space_out(self.repeat, _("[repeat]")),
            space_out(self.random, _("[random]")),
            space_out(self.stop, _("[stop]")))
        return ListWindow.get_title(self)

    def append(self, item):
        self.buffer.append(item)
        if self.random: self.random_left.append(item)

    def add_dir(self, dir):
        try:
            filenames = os.listdir(dir)
            filenames.sort()
            subdirs = []
            for filename in filenames:
                pathname = os.path.join(dir, filename)
                if VALID_SONG(filename):
                    self.append(PlaylistEntry(pathname))
                elif VALID_PLAYLIST(filename):
                    self.add_playlist(pathname)
                if os.path.isdir(pathname):
                    subdirs.append(pathname)
            map(self.add_dir, subdirs)
        except Exception, e:
            app.status(e, 2)

    def add_m3u(self, line):
        if re.match("^(#.*)?$", line): return
        if re.match("^(/|http://)", line):
            self.append(PlaylistEntry(self.fix_url(line)))
        else:
            dirname = os.path.dirname(self.pathname)
            self.append(PlaylistEntry(os.path.join(dirname, line)))

    def add_pls(self, line):
        # todo - support title & length
        m = re.match("File(\d+)=(.*)", line)
        if m: self.append(PlaylistEntry(self.fix_url(m.group(2))))

    def add_playlist(self, pathname):
        self.pathname = pathname
        if re.search("\.m3u$", pathname, re.I): f = self.add_m3u
        if re.search("\.pls$", pathname, re.I): f = self.add_pls
        file = open(pathname)
        map(f, map(string.strip, file.readlines()))
        file.close()
    
    def add(self, pathname, quiet=0):
        try:
            if os.path.isdir(pathname):
                quiet or app.status(_("Working..."))
                self.add_dir(pathname)
            elif VALID_PLAYLIST(pathname):
                self.add_playlist(pathname)
            else:
                pathname = self.fix_url(pathname)
                self.append(PlaylistEntry(pathname))
            # todo - refactor
            filename = os.path.basename(pathname) or pathname
            quiet or self.update()
            quiet or app.status(_("Added: %s") % filename, 1)
        except Exception, e:
            app.status(e, 2)

    def fix_url(self, url):
        return re.sub("(http://[^/]+)/?(.*)", "\\1/\\2", url)

    def putstr(self, entry, *pos):
        if entry.is_active(): self.attron(curses.A_BOLD)
        ListWindow.putstr(self, entry, *pos)
        if entry.is_active(): self.attroff(curses.A_BOLD)

    def change_active_entry(self, direction):
        if not self.buffer: return
        old = self.get_active_entry()
        new = None
        if self.random:
            if direction > 0:
                if self.random_next: new = self.random_next.pop()
                elif self.random_left: pass
                elif self.repeat: self.random_left = self.buffer[:]
                else: return
                if not new:
                    import random
                    new = random.choice(self.random_left)
                    self.random_left.remove(new)
                try: self.random_prev.remove(new)
                except ValueError: pass
                self.random_prev.append(new)
            else:
                if len(self.random_prev) > 1:
                    self.random_next.append(self.random_prev.pop())
                    new = self.random_prev[-1]
                else: return
            old and old.set_active(0)
        elif old:
            index = self.buffer.index(old)+direction
            if not (0 <= index < len(self.buffer) or self.repeat): return
            old.set_active(0)
            new = self.buffer[index % len(self.buffer)]
        else:
            new = self.buffer[0]
        new.set_active(1)
        self.update()
        return new

    def get_active_entry(self):
        for entry in self.buffer:
            if entry.is_active(): return entry

    def command_jump_to_active(self):
        entry = self.get_active_entry()
        if not entry: return
        self.bufptr = self.buffer.index(entry)
        self.update()

    def command_play(self):
        if not self.buffer: return
        entry = self.get_active_entry()
        entry and entry.set_active(0)
        entry = self.current()
        entry.set_active(1)
        self.update()
        app.play(entry)

    def command_delete(self):
        if not self.buffer: return
        current_entry, n = self.current(), len(self.buffer)
        self.buffer = self.not_tagged(self.buffer)
        if n > len(self.buffer):
            try: self.bufptr = self.buffer.index(current_entry)
            except ValueError: pass
        else:
            current_entry.set_tagged(1)
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
        app.status(_("Deleted playlist"), 1)
        self.update()

    def command_move(self, after):
        if not self.buffer: return
        current_entry, l = self.current(), self.get_tagged()
        if not l or current_entry.is_tagged(): return
        self.buffer = self.not_tagged(self.buffer)
        self.bufptr = self.buffer.index(current_entry)+after
        self.buffer[self.bufptr:self.bufptr] = l
        self.update()

    def command_shuffle(self):
        import random
        l = []
        n = len(self.buffer)
        while n > 0:
            n = n-1
            r = random.randint(0, n)
            l.append(self.buffer[r])
            del self.buffer[r]
        self.buffer = l
        self.bufptr = 0
        self.update()
        app.status(_("Shuffled playlist... Oops?"), 1)

    def command_sort(self):
        app.status(_("Working..."))
        self.buffer.sort(lambda x, y: x.vp() > y.vp() or -1)
        self.bufptr = 0
        self.update()
        app.status(_("Sorted playlist"), 1)

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
        app.status(format % (getattr(self, attr) and _("on") or _("off")), 1)
        self.parent.update_title()

    def command_save_playlist(self):
        if app.restricted: return
        default = self.pathname or "%s/" % app.win_filelist.cwd
        app.stop_input_hook = self.stop_save_playlist
        app.start_input(_("Save playlist"), default)

    def stop_save_playlist(self):
        pathname = app.input_string
        if pathname[0] != '/':
            pathname = os.path.join(app.win_filelist.cwd, pathname)
        if not re.search("\.m3u$", pathname, re.I):
            pathname = "%s%s" % (pathname, ".m3u")
        try:
            file = open(pathname, "w")
            for entry in self.buffer:
                file.write("%s\n" % entry.pathname)
            file.close()
            self.pathname = pathname
            app.status(_("ok"), 1)
        except IOError, e:
            app.status(e, 2)

# ------------------------------------------
def get_type(pathname):
    if mg:
        mg_string = mg.file(pathname)
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
        app.status(_("Can't read metadata, module mutagen not available"), 2)
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
            app.status(_("Can't read metadata, I don't know this file"), 1)
            return os.path.basename(pathname)
        f = metaopen(pathname)
    except:
        logging.debug("Error reading metadata")
        logging.debug(traceback.format_exc())
        app.status("Error reading metadata", 1)
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

# ------------------------------------------
class Player:

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    def __init__(self, commandline, files, fps=1):
        self.commandline = commandline
        self.re_files = re.compile(files, re.I)
        self.fps = fps
        self.entry = None
        self.stopped = 0 # 1 only if stopped manually or playlist ran out
        self.paused = 0
        self.time_setup = None
        self.buf = ''
        self.tid = None
        self._p = None

    def setup(self, entry, offset):
        """Ready the player with given ListEntry and seek offset"""

        self.argv = string.split(self.commandline)
        self.argv[0] = which(self.argv[0])
        for i in range(len(self.argv)):
            if self.argv[i] == "{file}":
                self.argv[i] = entry.pathname
            if self.argv[i] == "{offset}":
                self.argv[i] = str(offset*self.fps)
        self.entry = entry
        self.offset = offset
        if offset == 0:
            app.progress(0)
            self.offset = 0
            self.length = 0
        self.time_setup = time.time()
        return self.argv[0]

    def play(self):
        logging.debug(" ".join(["Executing"] + self.argv))
        logging.debug("My offset is %d" % self.offset)

        self._p = Popen(self.argv, stdout=self.stdout_w, stderr=self.stderr_w,
            stdin=self.stdin_r)

        self.stopped = 0
        self.paused = 0
        self.step = 0
        self.update_status()

    def stop(self, quiet=False):
        if self._p is None:
            return
        self.paused and self.toggle_pause(quiet)
        try:
            self._p.terminate()
        except OSError:
            pass
        self.stopped = 1
        quiet or self.update_status()

    def toggle_pause(self, quiet=False):
        if self._p is None:
            return
        self._p.send_signal(signal.SIGCONT if self.paused else signal.SIGSTOP)
        self.paused = not self.paused
        quiet or self.update_status()

    def parse_progress(self):
        if self.stopped or self.step: self.tid = None
        else:
            self.parse_buf()
            self.tid = app.timeout.add(1.0, self.parse_progress)

    def read_fd(self, fd):
        self.buf = os.read(fd, 512)
        self.tid or self.parse_progress()

    def poll(self):
        if self.stopped or self._p is None:
            return 0
        elif self._p.poll() is not None:
            self._p = None
            app.set_default_status("")
            app.counter([0,0])
            app.progress(0)
            return 1

    def seek(self, offset, relative):
        if relative:
            d = offset * self.length * 0.002
            self.step = self.step * (self.step * d > 0) + d
            self.offset = min(self.length, max(0, self.offset+self.step))
        else:
            self.step = 1
            self.offset = (offset < 0) and self.length+offset or offset
        self.show_position()

    def set_position(self, offset, length):
        """Update the visible playback position.

        offset is elapsed time, length the total length of track in seconds

        """
        self.offset = offset
        self.length = length
        self.show_position()

    def show_position(self):
        app.counter((self.offset, self.length-self.offset))
        app.progress(self.length and (float(self.offset) / self.length))

    def update_status(self):
        if not self.entry:
            app.set_default_status("")
        elif self.stopped:
            app.set_default_status(_("Stopped: %s") % self.entry.vp())
        elif self.paused:
            app.set_default_status(_("Paused: %s") % self.entry.vp())
        else:
            logging.debug(self.entry.vp())
            app.set_default_status(_("Playing: %s") % self.entry.vp())

# ------------------------------------------
class FrameOffsetPlayer(Player):
    re_progress = re.compile("Time.*\s(\d+):(\d+).*\[(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(string.atoi, match.groups())
            head, tail = m1*60+s1, m2*60+s2
            self.set_position(head, head+tail)

# ------------------------------------------
class FrameOffsetPlayerMpp(Player):
    re_progress = re.compile(".*\s(\d+):(\d+).*\s(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(string.atoi, match.groups())
            head = m1*60+s1
            tail = (m2*60+s2) - head
            self.set_position(head, head+tail)

# ------------------------------------------
class TimeOffsetPlayer(Player):
    re_progress = re.compile("(\d+):(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            h, m, s = map(string.atoi, match.groups())
            tail = h*3600+m*60+s
            head = max(self.length, tail) - tail
            self.set_position(head, head+tail)

# ------------------------------------------
class GSTPlayer(Player):
    re_progress = re.compile("Time: (\d+):(\d+):(\d+).(\d+) of (\d+):(\d+):(\d+).(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            ph, pm, ps, us, lh, lm, ls, lus = map(string.atoi, match.groups())
            position = ph*3600+pm*60+ps
            length = lh*3600+lm*60+ls
            self.set_position(position, length)

# ------------------------------------------
class NoOffsetPlayer(Player):

    def parse_buf(self):
        head = self.offset+1
        self.set_position(head, head*2)

    def seek(self, *dummy):
        return 1

# ------------------------------------------
class MPlayer(Player):
    re_progress = re.compile("^A:.*?(\d+)\.\d \([^)]+\) of (\d+)\.\d")
    speed = 1.0
    eq_cur = 0
    equalizer = EQUALIZERS[eq_cur][0]

    def play(self):
        Player.play(self)
        self.mplayer_send("speed_set %f" % self.speed)
        self.mplayer_send("af equalizer=" + self.equalizer)
        self.mplayer_send("seek %d\n" % self.offset)

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            curS, totS = map(string.atoi, match.groups())
            position, length = curS, totS
            self.set_position(position, length)
        else:
            logging.debug("Cannot parse mplayer output")

    def mplayer_send(self, arg):
        logging.debug("Sending command " + arg);
        try:
            os.write(self.stdin_w, arg + "\n")
        except IOError:
            logging.debug("Can't write to stdin_w.")
            app.status(_("ERROR: Cannot send commands to mplayer!"), 3)

    def speed_chg(self, set):
        self.speed = set
        self.mplayer_send("speed_set %f" % self.speed)
        app.status(_("Speed: %s%%") % (self.speed * 100), 1)

    def eq_chg(self, offset):
        if len(EQUALIZERS) == 0:
            app.status(_("No equalizers configured"), 2)
            return
        self.eq_cur = (self.eq_cur + offset) % (len(EQUALIZERS))
        self.equalizer = EQUALIZERS[self.eq_cur][0]
        app.status(_("Equalizer: %s(%s)") % (EQUALIZERS[self.eq_cur][1], self.equalizer), 1)
        self.mplayer_send("af equalizer=" + self.equalizer);

# ------------------------------------------
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
        for tid, (func, args, timeout) in self.dict.items():
            if now >= timeout:
                self.remove(tid)
                func(*args)
        return len(self.dict) and 0.2 or None

# ------------------------------------------
class FIFOControl:
    def __init__(self):
        self.commands = {
            "pause" : [app.toggle_pause, []],
            "next" : [app.next_prev_song, [+1,]],
            "prev" : [app.next_prev_song, [-1,]],
            "forward" : [app.seek, [1, 1]],
            "backward" : [app.seek, [-1, 1]],
            "play" : [app.toggle_stop, []],
            "stop" : [app.toggle_stop, []],
            "volume" : [self.volume, None],
            "macro" : [app.run_macro, None],
            "add" : [app.win_playlist.add, None],
            "empty" : [app.win_playlist.command_delete_all, []],
            "quit" : [app.quit, []]
        }
        self.fd = None
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
            os.mkfifo(CONTROL_FIFO, 0600)
            self.fd = open(CONTROL_FIFO, "rb+", 0)
        except IOError:
            # warn that we're disabling the fifo because someone raced us?
            return


    def handle_command(self):
        argv = self.fd.readline().strip().split(" ", 1)
        if argv[0] in self.commands.keys():
            f, a = self.commands[argv[0]]
            if a is None: a = argv[1:]
            f(*a)

    def volume(self, s):
        argv = s.split()
        app.mixer(argv[0], int(argv[1]))

# ------------------------------------------
class Application:
    def __init__(self):
        self.keymapstack = KeymapStack()
        self.input_mode = 0
        self.input_prompt = ""
        self.input_string = ""
        self.do_input_hook = None
        self.stop_input_hook = None
        self.complete_input_hook = None
        self.channels = []
        self.restricted = 0
        self.input_keymap = Keymap()
        self.input_keymap.bind(list(Window.chars), self.do_input)
        self.input_keymap.bind([127, curses.KEY_BACKSPACE], self.do_input, (8,))
        self.input_keymap.bind([21, 23], self.do_input)
        self.input_keymap.bind(['\a', 27], self.cancel_input, ())
        self.input_keymap.bind(['\n', curses.KEY_ENTER], self.stop_input, ())

    def command_macro(self):
        app.do_input_hook = self.do_macro
        app.start_input(_("macro"))

    def do_macro(self, ch):
        app.stop_input()
        self.run_macro(chr(ch))

    def run_macro(self, c):
        for i in MACRO.get(c, ""):
            self.keymapstack.process(ord(i))

    def setup(self):
        if tty:
            self.tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr[0] = tcattr[0] & ~(tty.IXON)
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSANOW, tcattr)
        self.w = curses.initscr()
        curses.cbreak()
        curses.noecho()
        try: curses.meta(1)
        except: pass
        self.cursor(0)
        signal.signal(signal.SIGHUP, self.handler_quit)
        signal.signal(signal.SIGINT, self.handler_quit)
        signal.signal(signal.SIGTERM, self.handler_quit)
        signal.signal(signal.SIGWINCH, self.handler_resize)
        self.win_root = RootWindow(None)
        self.win_root.update()
        self.win_tab = self.win_root.win_tab
        self.win_filelist = self.win_root.win_tab.win_filelist
        self.win_playlist = self.win_root.win_tab.win_playlist
        self.win_status = self.win_root.win_status
        self.status = self.win_status.status
        self.set_default_status = self.win_status.set_default_status
        self.restore_default_status = self.win_status.restore_default_status
        self.counter = self.win_root.win_counter.counter
        self.progress = self.win_root.win_progress.progress
        self.player = PLAYERS[0]
        self.timeout = Timeout()
        self.play_tid = None
        self.win_filelist.listdir()
        self.control = FIFOControl()

    def cleanup(self):
        try: curses.endwin()
        except curses.error: return
        XTERM and sys.stderr.write("\033]0;%s\a" % "xterm")
        tty and tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, self.tcattr)
        print
        # remove temporary files
        try:
            if os.path.exists(CONTROL_FIFO): os.unlink(CONTROL_FIFO)
        except IOError:
            pass

    def run(self):
        while 1:
            now = time.time()
            timeout = self.timeout.check(now)
            self.win_filelist.listdir_maybe(now)
            if not self.player.stopped:
                timeout = 0.5
                if self.player.poll():
                    self.player.stopped = 1  # end of playlist hack
                    if not self.win_playlist.stop:
                        entry = self.win_playlist.change_active_entry(1)
                        if not entry:
                            self.player.stopped = 1
                        else:
                            self.play(entry)
            R = [sys.stdin, self.player.stdout_r, self.player.stderr_r]
            self.control.fd and R.append(self.control.fd)
            try: r, w, e = select.select(R, [], [], timeout)
            except select.error: continue
            self.kludge = 1
            # user
            if sys.stdin in r:
                c = self.win_root.getch()
                self.keymapstack.process(c)
            # player
            if self.player.stderr_r in r:
                self.player.read_fd(self.player.stderr_r)
            # player
            if self.player.stdout_r in r:
                self.player.read_fd(self.player.stdout_r)
            # remote
            if self.control.fd in r:
                self.control.handle_command()

    def setup_player(self, entry, offset = 0):
        if entry is None or offset is None: return
        logging.debug("Setting up player for " + str(entry))
        self.player.stop(quiet=1)
        for self.player in PLAYERS:
            if self.player.re_files.search(entry.pathname):
                if self.player.setup(entry, offset): break
        else:
            # FIXME: Needs to report suitable players
            logging.debug("Player not found")
            app.status(_("Player not found!"), 1)
            self.player.stopped = 0  # keep going
            return

    def play(self, entry, offset = 0):
        self.play_tid = None # Play executed, remove from queue
        if entry is None or offset is None: return
        logging.debug("Starting to play " + str(entry))
        self.setup_player(entry, offset)
        self.player.play()

    def delayed_play(self, entry, offset):
        if self.play_tid: self.timeout.remove(self.play_tid)
        self.play_tid = self.timeout.add(0.5, self.play, (entry, offset))

    def next_prev_song(self, direction): 
        new_entry = self.win_playlist.change_active_entry(direction) 
        self.setup_player(new_entry, 0) # Fixes DB#287871 and DB#303282.
        # The player has to be set-up right away when changing songs.
        # Otherwise the user can manipulate the old offset value while waiting 
        # for the delayed play to trigger, which causes the next song to play
        # from a wrong offset instead of its beginning.
        self.delayed_play(new_entry, 0)

    def seek(self, offset, relative):
        if not self.player.entry: return
        self.player.seek(offset, relative)
        self.delayed_play(self.player.entry, self.player.offset)

    def toggle_pause(self):
        if not self.player.entry: return
        if not self.player.stopped: self.player.toggle_pause()

    def toggle_stop(self):
        if not self.player.entry: return
        if not self.player.stopped: self.player.stop()
        else: self.play(self.player.entry, self.player.offset)

    def key_volume(self, ch):
        self.mixer("set", int((ch & 0x0f) * 100 / 9.0))

    def mixer(self, cmd=None, arg=None):
        try: self._mixer(cmd, arg)
        except Exception, e: app.status(e, 2)

    def _mixer(self, cmd, arg):
        try: # ALSA
            import alsaaudio
            mixer = alsaaudio.Mixer()
            get, set = mixer.getvolume, mixer.setvolume
            name = 'MASTER'
            if cmd is "set": set(arg)
            if cmd is "cue" and arg!=0: # ALSA doesn't do integer increments
                oldvolume = get()[0]
                newvol = get()[0]
                while (get()[0] == oldvolume):
                    newvol = min(100, max(0, newvol + arg))
                    set(newvol)
                    if ((oldvolume==0 and arg<0) or
                        (oldvolume==100 and arg>0)): 
                        break
            app.status(_("%s volume %s%%") % (name, get()[0]), 1)
        except: # OSS
            try:
                import ossaudiodev
                mixer = ossaudiodev.openmixer()
                get, set = mixer.get, mixer.set
                self.channels = self.channels or \
                    [['MASTER', ossaudiodev.SOUND_MIXER_VOLUME],
                     ['PCM', ossaudiodev.SOUND_MIXER_PCM]]
            except ImportError:
                import oss
                mixer = oss.open_mixer()
                get, set = mixer.read_channel, mixer.write_channel
                self.channels = self.channels or \
                    [['MASTER', oss.SOUND_MIXER_VOLUME],
                     ['PCM', oss.SOUND_MIXER_PCM]]
            if cmd == "toggle": self.channels.insert(0, self.channels.pop())
            name, channel = self.channels[0]
            if cmd == "cue": arg = min(100, max(0, get(channel)[0] + arg))
            if cmd in ["set", "cue"]: set(channel, (arg, arg))
            app.status(_("%s volume %s%%") % (name, get(channel)[0]), 1)
            mixer.close()

    def incr_reset_decr_speed(self, signum):
        if(isinstance(self.player, MPlayer)):
            if(signum == 0):
                self.player.speed_chg(1.0)
            else:
                self.player.speed_chg(self.player.speed + signum * SPEED_OFFSET)
        else:
            app.status(_("Speed control requires MPlayer"), 1)

    def next_prev_eq(self, direction):
        if(isinstance(self.player, MPlayer)):
            self.player.eq_chg(direction)
        else:
            app.status(_("Equalizer support requires MPlayer"), 1)

    def show_input(self):
        n = len(self.input_prompt)+1
        s = cut(self.input_string, self.win_status.cols-n, left=1)
        app.status("%s%s " % (self.input_prompt, s))

    def start_input(self, prompt="", data="", colon=1):
        self.input_mode = 1
        self.cursor(1)
        app.keymapstack.push(self.input_keymap)
        self.input_prompt = prompt + (colon and ": " or "")
        self.input_string = data
        self.show_input()

    def do_input(self, *args):
        if self.do_input_hook:
            return self.do_input_hook(*args)
        ch = args and args[0] or None
        if ch in [8, 127]: # backspace
            self.input_string = self.input_string[:-1]
        elif ch == 9 and self.complete_input_hook:
            self.input_string = self.complete_input_hook(self.input_string)
        elif ch == 21: # C-u
            self.input_string = ""
        elif ch == 23: # C-w
            self.input_string = re.sub("((.* )?)\w.*", "\\1", self.input_string)
        elif ch:
            self.input_string = "%s%c" % (self.input_string, ch)
        self.show_input()

    def stop_input(self, *args):
        self.input_mode = 0
        self.cursor(0)
        app.keymapstack.pop()
        if not self.input_string:
            app.status(_("cancel"), 1)
        elif self.stop_input_hook:
            self.stop_input_hook(*args)
        self.do_input_hook = None
        self.stop_input_hook = None
        self.complete_input_hook = None

    def cancel_input(self):
        self.input_string = ""
        self.stop_input()

    def cursor(self, visibility):
        try: curses.curs_set(visibility)
        except: pass

    def quit(self, status=0):
        self.player.stop(quiet=1)
        sys.exit(status)

    def handler_resize(self, sig, frame):
        # curses trickery
        while 1:
            try: curses.endwin(); break
            except: time.sleep(1)
        self.w.refresh()
        self.win_root.resize()
        self.win_root.update()

    def handler_quit(self, sig, frame):
        self.quit(1)

# ------------------------------------------
def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "mnrRd:")
    except:
        usage = _("Usage: %s [-d <filename>] [-mnrR] [ file | dir | playlist ] ...\n")
        sys.stderr.write(usage % sys.argv[0])
        sys.exit(1)

    for opt, optarg in opts: # FIXME option checking in two places
        if opt == "-d":
            logging.basicConfig(filename=optarg, level=logging.DEBUG)
    global app
    app = Application()

    playlist = []
    if not sys.stdin.isatty():
        playlist = map(string.strip, sys.stdin.readlines())
        os.close(0)
        os.open("/dev/tty", 0)
    try:
        app.setup()
        for opt, optarg in opts:
            if opt == "-n": app.restricted = 1
            if opt == "-r": app.win_playlist.command_toggle_repeat()
            if opt == "-R": app.win_playlist.command_toggle_random()
            if opt == "-m": app.mixer("toggle")
        logging.debug("Preferred locale is " + str(code))
        if args or playlist:
            for i in args or playlist:
                i = os.path.exists(i) and os.path.abspath(i) or i
                app.win_playlist.add(i)
            app.win_tab.change_window()
        app.run()
    except SystemExit:
        app.cleanup()
    except Exception:
        app.cleanup()
        traceback.print_exc()

# ------------------------------------------
PLAYERS = [
    FrameOffsetPlayer("ogg123 -q -v -k {offset} {file}", "\.(ogg|flac|spx)$"),
    FrameOffsetPlayer("splay -f -k {offset} {file}", "(^http://|\.mp[123]$)", 38.28),
    FrameOffsetPlayer("mpg123 -q -v -k {offset} {file}", "(^http://|\.mp[123]$)", 38.28),
    FrameOffsetPlayer("mpg321 -q -v -k {offset} {file}", "(^http://|\.mp[123]$)", 38.28),
    FrameOffsetPlayerMpp("mppdec --gain 2 --start {offset} {file}", "\.mp[cp+]$"),
    TimeOffsetPlayer("madplay -v --display-time=remaining -s {offset} {file}", "\.mp[123]$"),
    MPlayer("mplayer -slave -vc null -vo null {file}",
            "^http://|\.(mp[123]|ogg|oga|flac|spx|mp[cp+]|mod|xm|fm|s3m|" +
                "med|col|669|it|mtm|stm|aiff|au|cdr|wav|wma|m4a|m4b)$"),
    GSTPlayer("gst123 -k {offset} {file}", "\.(mp[123]|ogg|opus|oga|flac|wav|m4a|m4b|aiff)$"),
    NoOffsetPlayer("mikmod -q -p0 {file}", "\.(mod|xm|fm|s3m|med|col|669|it|mtm)$"),
    NoOffsetPlayer("xmp -q {file}", "\.(mod|xm|fm|s3m|med|col|669|it|mtm|stm)$"),
    NoOffsetPlayer("play {file}", "\.(aiff|au|cdr|mp3|ogg|wav)$"),
    NoOffsetPlayer("speexdec {file}", "\.spx$"),
    NoOffsetPlayer("timidity {file}", "\.(mid|rmi|rcp|r36|g18|g36|mfi|kar|mod|wrd)$"),
    ]

MACRO = {}

def VALID_SONG(name):
    for player in PLAYERS:
        if player.re_files.search(name):
            return 1

def VALID_PLAYLIST(name):
    if re.search("\.(m3u|pls)$", name, re.I):
        return 1

for rc in [os.path.expanduser("~/.cplayrc"), "/etc/cplayrc"]:
    try: execfile(rc); break
    except IOError: pass

# ------------------------------------------
if __name__ == "__main__": main()

# vim: ts=4 sts=4 sw=4 et

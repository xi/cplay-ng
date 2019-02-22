#!/usr/bin/env python
# -*- python -*-

"""cplay remote control - remote-control a running cplay
Copyright (C) 2009 Tom Adams <tom@holizz.com>
Copyright (C) 2014 Tobias Bengfort <tobias.bengfort@gmx.net>

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
import locale
import argparse

try:
    import argcomplete
except ImportError:
    argcomplete = None

locale.setlocale(locale.LC_ALL, '')
CODE = locale.getpreferredencoding()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--fifo',
        help='FIFO socket to connect to cplay',
        default="%s/cplay-control-%s" % (os.environ.get("TMPDIR", "/tmp"),
                                         os.environ["USER"]))
    subparsers = parser.add_subparsers(title='command')

    pause_parser = subparsers.add_parser('pause', help="toggle pause")
    pause_parser.set_defaults(cmd='pause')

    next_parser = subparsers.add_parser('next', help="skip to next track")
    next_parser.set_defaults(cmd='next')

    prev_parser = subparsers.add_parser('prev', help="skip to previous track")
    prev_parser.set_defaults(cmd='prev')

    forward_parser = subparsers.add_parser('forward', help="seek forward")
    forward_parser.set_defaults(cmd='forward')

    backward_parser = subparsers.add_parser('backward', help="seek backward")
    backward_parser.set_defaults(cmd='backward')

    play_parser = subparsers.add_parser('play', help="toggle play")
    play_parser.set_defaults(cmd='play')

    stop_parser = subparsers.add_parser('stop', help="toggle stop")
    stop_parser.set_defaults(cmd='stop')

    volume_parser = subparsers.add_parser('volume', help="change volume")
    volume_parser.set_defaults(cmd='volume')
    volume_parser.add_argument('action', choices=['set', 'cue'])
    volume_parser.add_argument('value', type=int)

    macro_parser = subparsers.add_parser('macro', help="execute a macro")
    macro_parser.set_defaults(cmd='macro')
    macro_parser.add_argument('macro')

    add_parser = subparsers.add_parser('add',
                                       help="add files, folders or playlists")
    add_parser.set_defaults(cmd='add')
    add_parser.add_argument('paths', metavar='path', nargs='+')

    empty_parser = subparsers.add_parser('empty', help="clear playlist")
    empty_parser.set_defaults(cmd='empty')

    quit_parser = subparsers.add_parser('quit', help="quit cplay")
    quit_parser.set_defaults(cmd='quit')

    if argcomplete is not None:
        argcomplete.autocomplete(parser)

    return parser.parse_args()


def main():
    args = parse_args()

    if os.path.exists(args.fifo):
        with open(args.fifo, "wb", 0) as fd:
            def send_msg(*msg):
                m = ' '.join(str(i) for i in msg) + '\n'
                fd.write(m.encode(CODE))

            if args.cmd == 'volume':
                send_msg(args.cmd, args.action, args.value)
            elif args.cmd == 'macro':
                send_msg(args.cmd, args.macro)
            elif args.cmd == 'add':
                for path in args.paths:
                    if not path.startswith('http'):
                        path = os.path.abspath(path)
                    send_msg(args.cmd, path)
            else:
                send_msg(args.cmd)
    else:
        print('Could not find %s.' % args.fifo)
        sys.exit(2)


if __name__ == '__main__':
    main()

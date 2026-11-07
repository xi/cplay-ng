import curses
import os
import socket
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import cplay


@contextmanager
def make_tree(*relpaths):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in relpaths:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()

        cplay.filelist.set_path(tmp)
        yield root


def make_player():
    player = cplay.Player()
    player._ipc = MagicMock()
    return player


class CplayTestCase(unittest.TestCase):
    def setUp(self):
        cplay.player = make_player()
        cplay.playlist = cplay.Playlist()
        cplay.filelist = cplay.Filelist()
        cplay.helplist = cplay.HelpList()
        cplay.prompt = cplay.Prompt()
        cplay.app = cplay.Application()


class TestUtils(unittest.TestCase):
    def test_space_between(self):
        self.assertEqual(cplay.space_between('a', 'b', 5), 'a   b')
        self.assertEqual(cplay.space_between('abc', 'def', 4), 'adef')

    def test_str_match(self):
        self.assertTrue(cplay.str_match('glenn bach', 'Glenn Gould - Bach'))
        self.assertFalse(cplay.str_match('mozart', 'Bach'))

    def test_clamp(self):
        self.assertEqual(cplay.clamp(11, 0, 10), 10)

    def test_format_time(self):
        self.assertEqual(cplay.format_time(3661), '01:01:01')

    def test_get_ext(self):
        self.assertEqual(cplay.get_ext('/a/b.flac'), 'flac')

    def test_get_mpv_version(self):
        cplay.get_mpv_version.cache_clear()
        mock = MagicMock()
        mock.stdout = 'mpv v0.40.0 Copyright © 2000-2025'.encode()
        with patch('subprocess.run', return_value=mock):
            self.assertEqual(cplay.get_mpv_version(), (0, 40, 0))

    def test_walk_skips_symlink_loop(self):
        with make_tree('a.mp3', 'sub/b.mp3') as root:
            os.symlink(root, root / 'sub' / 'loop')
            self.assertEqual(
                cplay.walk(root),
                [
                    str(root / 'a.mp3'),
                    str(root / 'sub'),
                    str(root / 'sub' / 'b.mp3'),
                ],
            )


class TestPlayer(unittest.TestCase):
    def test_handle_ipc_updates_state(self):
        player = make_player()
        player.play('a.mp3')

        player.handle_ipc({'event': 'property-change', 'id': 1, 'data': 12.5})
        self.assertEqual(player.position, 12.5)

        player.handle_ipc({'event': 'property-change', 'id': 2, 'data': 100.0})
        self.assertEqual(player.length, 100.0)

        player.handle_ipc({'event': 'property-change', 'id': 3, 'data': {'icy-title': 'Song Name'}})
        self.assertEqual(player.metadata, {'icy-title': 'Song Name'})

        player.handle_ipc({'event': 'end-file'})
        self.assertTrue(player.is_finished)

    def test_handle_ipc_ignore_position_while_seeking(self):
        player = make_player()
        player._seek_step = 5
        player.handle_ipc({'event': 'property-change', 'id': 1, 'data': 99.0})
        self.assertEqual(player.position, 0)

    def test_parse_progress_handles_chunked_messages(self):
        player = make_player()
        a, b = socket.socketpair()
        try:
            player.socket = b
            a.sendall(
                b'{"event":"property-change","id":2,"data":42.0}\n{"event":"prop'
            )
            player.parse_progress()
            self.assertEqual(player.length, 42.0)
            a.sendall(b'erty-change","id":1,"data":1.5}\n')
            player.parse_progress()
            self.assertEqual(player._buffer, b'')
            self.assertEqual(player._seek_step, 0)
            self.assertEqual(player.position, 1.5)
        finally:
            a.close()
            b.close()

    def test_play_url_with_timestamp(self):
        player = make_player()
        player.play('https://example.com/a.mp3#t=42')
        self.assertEqual(player.path, 'https://example.com/a.mp3')
        self.assertEqual(player.position, 42.0)

    def test_seek(self):
        player = make_player()
        player.play('/music/track.mp3')
        player.length = 100.0
        self.assertEqual(player.position, 0)

        with patch('time.time', return_value=1000.0):
            player.seek(1)
            self.assertAlmostEqual(player.position, 0.2)

            player.seek(1)
            self.assertAlmostEqual(player.position, 0.6)

            player.seek(-1)
            self.assertAlmostEqual(player.position, 0.4)

        player.position = 0.4
        with patch('time.time', return_value=player._seek_timeout):
            player.finish_seek()
        player._ipc.assert_called_with(
            'loadfile', '/music/track.mp3', 'replace', 0, 'start=0.4'
        )

    def test_get_progress(self):
        player = make_player()
        player.length = 0
        self.assertEqual(player.get_progress(), 0)
        player.length = 100.0
        player.position = 25.0
        self.assertEqual(player.get_progress(), 0.25)


class TestPrompt(unittest.TestCase):
    def test_input(self):
        prompt = cplay.Prompt()
        prompt.start('', initial='ab')
        prompt.process_key('c')
        self.assertEqual(prompt.value, 'abc')
        prompt.process_key(curses.KEY_BACKSPACE)
        self.assertEqual(prompt.value, 'ab')
        prompt.process_key(chr(27))
        self.assertFalse(prompt.active)

    def test_callbacks(self):
        prompt = cplay.Prompt()
        prompt.start('', on_input=MagicMock(), on_submit=MagicMock(), initial='v')
        prompt.on_input.assert_called_with('v')
        prompt.process_key('x')
        prompt.on_input.assert_called_with('vx')
        prompt.process_key('\n')
        prompt.on_submit.assert_called_once_with('vx')
        self.assertFalse(prompt.active)


class TestList(unittest.TestCase):
    def test_scroll(self):
        lst = cplay.List()
        lst.items = [str(i) for i in range(20)]
        lst.position = 0

        # cursor in view -> no scroll
        lst.set_cursor(0)
        list(lst.render(rows=6, cols=80))
        self.assertEqual(lst.position, 0)

        # curser outside view -> scroll
        lst.set_cursor(19)
        list(lst.render(rows=6, cols=80))
        self.assertEqual(lst.position, 14)

        # space available -> scroll
        list(lst.render(rows=11, cols=80))
        self.assertEqual(lst.position, 9)

    def test_search_wraps_and_can_skip_current(self):
        lst = cplay.List()
        lst.items = ['alpha', 'beta', 'alphabet']
        lst.cursor = 0

        # offset = 0 -> stay on current
        self.assertTrue(lst.search('alp', diff=1, offset=0))
        self.assertEqual(lst.cursor, 0)

        # offset = 1 -> move to next
        self.assertTrue(lst.search('alp', diff=1, offset=1))
        self.assertEqual(lst.cursor, 2)

        # wrap
        self.assertTrue(lst.search('alp', diff=1, offset=1))
        self.assertEqual(lst.cursor, 0)

    def test_process_key_paging(self):
        lst = cplay.List()
        lst.items = [str(i) for i in range(20)]
        lst.page_size = 10
        lst.process_key('J')
        self.assertEqual(lst.cursor, 10)
        lst.process_key('J')
        self.assertEqual(lst.cursor, 19)
        lst.process_key('g')
        self.assertEqual(lst.cursor, 0)
        lst.process_key('G')
        self.assertEqual(lst.cursor, 19)
        lst.process_key('K')
        self.assertEqual(lst.cursor, 9)
        lst.process_key('K')
        self.assertEqual(lst.cursor, 0)


class TestFilelist(unittest.TestCase):
    def test_set_path(self):
        with make_tree('a.mp3', 'sub/b.flac') as root:
            lst = cplay.Filelist()
            lst.set_path(str(root))
            self.assertEqual(lst.items, [str(root / 'a.mp3'), str(root / 'sub')])

            lst.set_path(str(root / 'sub'))
            self.assertEqual(lst.items, [str(root / 'sub' / 'b.flac')])

    def test_set_path_ignores_bad_dir(self):
        with make_tree('locked/x.mp3') as root:
            (root / 'locked').chmod(0)

            lst = cplay.Filelist()
            lst.set_path(str(root))
            self.assertEqual(lst.items, [str(root / 'locked')])

            lst.set_path(str(root / 'nope'))
            self.assertEqual(lst.path, str(root))

            lst.set_path(str(root / 'locked'))
            self.assertEqual(lst.path, str(root))

    def test_go_back_restores_cursor(self):
        with make_tree('a.mp3', 'sub/b.flac') as root:
            lst = cplay.Filelist()
            lst.set_path(str(root))
            self.assertEqual(lst.cursor, 0)
            lst.set_path(str(root / 'sub'))
            lst.set_path(str(root), prev=str(root / 'sub'))
            self.assertEqual(lst.cursor, 1)

    def test_filter(self):
        with make_tree('glenn gould - bach.mp3', 'mozart - sonata.mp3') as root:
            lst = cplay.Filelist()
            lst.set_path(str(root))
            lst.filter('glenn bach')
            self.assertEqual(len(lst.items), 1)
            self.assertEqual(
                lst.get_title(), f'Filelist: {root}/search "glenn bach"/'
            )
            lst.process_key(curses.KEY_BACKSPACE)
            self.assertEqual(lst.items, [
                str(root / 'glenn gould - bach.mp3'),
                str(root / 'mozart - sonata.mp3'),
            ])

    def test_process_key(self):
        with make_tree() as root:
            lst = cplay.Filelist()
            lst.set_path(str(root))
            (root / 'new.mp3').touch()
            self.assertEqual(lst.items, [])
            lst.process_key('r')
            self.assertEqual(lst.items, [str(root / 'new.mp3')])


class TestPlaylist(unittest.TestCase):
    def test_get_title(self):
        lst = cplay.Playlist()
        lst.items = ['a']
        lst.items_written = ['a']

        lst.path = '/x/list.m3u'
        self.assertEqual(lst.get_title(), 'Playlist list.m3u')

        lst.items = ['a', 'b']
        self.assertEqual(lst.get_title(), 'Playlist list.m3u*')

        lst.repeat = True
        self.assertEqual(lst.get_title(), 'Playlist list.m3u* [repeat all]')

        lst.random = True
        self.assertEqual(
            lst.get_title(), 'Playlist list.m3u* [repeat all] [random]'
        )

    def test_next(self):
        lst = cplay.Playlist()
        self.assertIsNone(lst.next())

        lst.items = ['a', 'b']
        self.assertEqual(lst.next(), 'a')
        self.assertEqual(lst.next(), 'b')
        self.assertIsNone(lst.next())

    def test_next_random_repeat(self):
        lst = cplay.Playlist()
        lst.items = ['a', 'b']
        lst.random = True
        lst.repeat = True

        with (
            patch('random.choice', side_effect=[1, 0]),
            patch('random.randrange', return_value=0),
        ):
            self.assertEqual(lst.next(), 'b')
            self.assertEqual(lst.next(), 'a')
            self.assertEqual(lst.next(), 'a')

    def test_add(self):
        with make_tree(
            'song.mp3',
            'list.m3u',
            'sub/nested.flac',
            'sub/ignore.txt',
        ) as root:
            (root / 'list.m3u').write_text('song.mp3\n')
            lst = cplay.Playlist()
            self.assertEqual(lst.add(str(root / 'song.mp3')), 1)
            self.assertEqual(lst.add(str(root / 'sub')), 1)
            self.assertEqual(lst.add(str(root / 'list.m3u')), 1)
            self.assertEqual(lst.add(str(root / 'ignore.txt')), 0)
            self.assertEqual(lst.items, [
                str(root / 'song.mp3'),
                str(root / 'sub' / 'nested.flac'),
                str(root / 'song.mp3'),
            ])

    def test_read_write(self):
        with make_tree() as root:
            (root / 'list.m3u').write_text(
                '# comment\n\ntrack.mp3\n/sub/other.flac\nhttp://x/y.mp3\n'
            )

            lst = cplay.Playlist()
            lst.load(str(root / 'list.m3u'))
            self.assertEqual(
                lst.items,
                [str(root / 'track.mp3'), '/sub/other.flac', 'http://x/y.mp3'],
            )

            out = root / 'out.m3u'
            lst.write(str(out))
            self.assertEqual(
                out.read_text(),
                f'{root}/track.mp3\n/sub/other.flac\nhttp://x/y.mp3\n',
            )

    def test_process_key(self):
        lst = cplay.Playlist()
        lst.items = ['a', 'b', 'c']
        lst.cursor = 0

        lst.process_key('m')
        self.assertEqual(lst.items, ['b', 'a', 'c'])
        self.assertEqual(lst.cursor, 1)

        lst.active = 2
        lst.process_key('@')
        self.assertEqual(lst.cursor, 2)

        lst.process_key('M')
        self.assertEqual(lst.items, ['b', 'c', 'a'])
        self.assertEqual(lst.cursor, 1)

        lst.process_key('d')
        self.assertEqual(lst.items, ['b', 'a'])
        self.assertEqual(lst.cursor, 1)

        lst.process_key('D')
        self.assertEqual(lst.items, [])

        lst.process_key('r')
        self.assertTrue(lst.repeat)

        lst.process_key('R')
        self.assertTrue(lst.random)

    def test_sort_and_shuffle_preserve_anchors(self):
        playlist = cplay.Playlist()
        playlist.items = ['b', 'a', 'c']
        playlist.active = 0
        playlist.cursor = 2

        with patch('random.shuffle', side_effect=lambda x: x.reverse()):
            playlist.process_key('s')
        self.assertEqual(playlist.items, ['c', 'a', 'b'])
        self.assertEqual(playlist.active, 2)
        self.assertEqual(playlist.cursor, 0)

        playlist.process_key('S')
        self.assertEqual(playlist.items, ['a', 'b', 'c'])
        self.assertEqual(playlist.active, 1)
        self.assertEqual(playlist.cursor, 2)


class IntegrationTests(CplayTestCase):
    def test_render(self):
        self.maxDiff = None
        with make_tree('foo.mp3', 'bar.opus', 'sub/baz.flac') as root:
            self.assertEqual(list(cplay.app.render(8, 40)), [
                (f'Filelist: {root}/', curses.A_BOLD),
                '----------------------------------------',
                ('  bar.opus                              ', curses.A_REVERSE),
                ('  foo.mp3                               ', 0),
                ('  sub/                                  ', 0),
                '',
                '|---------------------------------------',
                '                     00:00:00 / 00:00:00',
            ])


            cplay.player.play(str(root / 'foo.mp3'))
            cplay.player.position = 665
            cplay.player.length = 3723

            self.assertEqual(list(cplay.app.render(8, 40)), [
                (f'Filelist: {root}/', curses.A_BOLD),
                '----------------------------------------',
                ('  bar.opus                              ', curses.A_REVERSE),
                ('  foo.mp3                               ', 0),
                ('  sub/                                  ', 0),
                '',
                '======|---------------------------------',
                'Playing: foo.mp3     00:11:05 / 01:02:03',
            ])


    def test_recursive_search(self):
        with make_tree('foo.mp3', 'bar.opus', 'sub/baz.flac') as root:
            for key in 'sba':
                cplay.app.process_key(key)
            self.assertEqual(list(cplay.app.render(7, 40)), [
                (f'Filelist: {root}/search "ba"/', curses.A_BOLD),
                '----------------------------------------',
                ('  bar.opus                              ', curses.A_REVERSE),
                ('  sub/baz.flac                          ', 0),
                '',
                '|---------------------------------------',
                'search: ba           00:00:00 / 00:00:00',
            ])

    def test_add_to_playlist_folder(self):
        with make_tree('alpha.mp3', 'sub/beta.flac') as root:
            cplay.app.process_key('j')
            cplay.app.process_key('a')
            self.assertEqual(
                cplay.playlist.items, [str(root / 'sub' / 'beta.flac')]
            )

    def test_load_playlist(self):
        with make_tree('list.m3u') as root:
            (root / 'list.m3u').write_text('song.mp3\nother.flac\n')
            cplay.app.process_key('\n')
            self.assertEqual(cplay.app.tab, cplay.playlist)
            self.assertEqual(cplay.playlist.path, str(root / 'list.m3u'))
            self.assertEqual(cplay.playlist.items, [
                str(root / 'song.mp3'),
                str(root / 'other.flac'),
            ])

    def test_set_volume(self):
        cplay.app.process_key('5')
        cplay.player._ipc.assert_called_with('set', 'volume', '55')

    def test_seek(self):
        with make_tree('a.mp3') as root:
            cplay.player.play(str(root / 'a.mp3'))
            cplay.player.length = 1000.0
            with patch('time.time', return_value=1000.0):
                cplay.app.process_key(curses.KEY_RIGHT)
                cplay.app.process_key(curses.KEY_RIGHT)
            self.assertEqual(cplay.player.position, 6.0)
            with patch('time.time', return_value=1100.0):
                cplay.app.process_key(curses.KEY_LEFT)
            self.assertEqual(cplay.player.position, 4.0)

    def test_unused_key(self):
        self.assertFalse(cplay.app.process_key('z'))

    def test_toggle_play(self):
        with make_tree('a.mp3') as root:
            cplay.player.play(str(root / 'a.mp3'))
            cplay.app.process_key('x')
            self.assertFalse(cplay.player.is_playing)
            cplay.app.process_key(' ')
            self.assertTrue(cplay.player.is_playing)

    def test_next_track(self):
        with make_tree('a.mp3', 'b.mp3') as root:
            cplay.playlist.items = [str(root / 'a.mp3'), str(root / 'b.mp3')]
            cplay.app.process_key('n')
            self.assertEqual(cplay.player.path, str(root / 'a.mp3'))
            cplay.app.process_key('n')
            self.assertEqual(cplay.player.path, str(root / 'b.mp3'))

    def test_help(self):
        cplay.app.process_key('h')
        self.assertTrue(cplay.app.help)
        cplay.app.process_key('j')
        self.assertEqual(cplay.helplist.cursor, 1)
        cplay.app.process_key('q')
        self.assertFalse(cplay.app.help)

    def test_quit(self):
        with patch('sys.exit') as exit_mock:
            cplay.app.process_key('Q')
        exit_mock.assert_called_once_with(0)

    def test_go_up_directory(self):
        with make_tree('a.mp3', 'sub/b.mp3') as root:
            cplay.filelist.set_path(str(root / 'sub'))
            cplay.app.process_key(curses.KEY_BACKSPACE)
            self.assertEqual(cplay.filelist.path, str(root))

    def test_play_from_playlist(self):
        with make_tree('a.mp3', 'b.mp3') as root:
            cplay.playlist.items = [str(root / 'a.mp3'), str(root / 'b.mp3')]
            cplay.playlist.cursor = 1
            cplay.app.process_key('\t')
            self.assertEqual(cplay.app.tab, cplay.playlist)
            cplay.app.process_key('\n')
            self.assertEqual(cplay.playlist.active, 1)
            self.assertEqual(cplay.player.path, str(root / 'b.mp3'))
            cplay.app.process_key('j')
            self.assertEqual(cplay.playlist.cursor, 1)

    def test_enter_on_audio_file(self):
        with make_tree('a.mp3') as root:
            cplay.app.process_key('\n')
            self.assertEqual(cplay.player.path, str(root / 'a.mp3'))
            self.assertTrue(cplay.player.is_playing)

    def test_enter_on_directory(self):
        with make_tree('a.mp3', 'sub/b.mp3') as root:
            cplay.app.process_key('j')
            cplay.app.process_key('\n')
            self.assertEqual(cplay.filelist.path, str(root / 'sub'))

    def test_search(self):
        with make_tree(
            'alpha1.mp3', 'alpha2.mp3', 'beta.flac', 'xalpha.mp3'
        ):
            cplay.app.process_key('/')
            for ch in 'alpha':
                cplay.app.process_key(ch)
            cplay.app.process_key('\n')
            self.assertEqual(cplay.filelist.cursor, 0)

            cplay.app.process_key(']')
            self.assertEqual(cplay.filelist.cursor, 1)
            cplay.app.process_key(']')
            self.assertEqual(cplay.filelist.cursor, 3)
            cplay.app.process_key(']')
            self.assertEqual(cplay.filelist.cursor, 0)  # wraps

            cplay.app.process_key('[')
            self.assertEqual(cplay.filelist.cursor, 3)

    def test_filelist_empty_keys(self):
        with make_tree():
            cplay.app.process_key('a')
            self.assertEqual(cplay.playlist.items, [])
            cplay.app.process_key('\n')
            self.assertEqual(cplay.app.tab, cplay.filelist)

# 5.4.0 (2024-12-28)

- allow to refresh file list
- automatically pause when playback was interrupted (e.g. because the
  device was suspended)
- strip leading v when parsing mpv version

# 5.3.1 (2024-08-03)

- fix compatibility with mpv 0.38.0
- do not mess up the terminal when importing the cplay python module
- make `__version__` comply with pep440

# 5.3.0 (2024-05-20)

- allow to start URLs with offset (see <https://www.w3.org/TR/media-frags/>)
- fix compatibility with mpv 0.38 and above (thanks to @AlkyoneAoide)

# 5.2.0 (2023-03-09)

- use mpv's native volume controls (thanks to @AlkyoneAoide)
- use `XDG_RUNTIME_DIR` for mpv socket
- do not crash on invalid utf-8 from mpv

# 5.1.0 (2022-11-22)

- change keys for previous/next search match to [ and ]
- if a stream contains metadata, display the name of the currently playing
  track
- use `@DEFAULT_SINK@` instead of hardcoded index to set volume

# 5.0.1 (2021-08-30)

- Fix in-app version number

# 5.0.0 (2021-08-30)

- changes to playlists are no longer automatically written back to their
  files. Instead, a * is appended to the playlist title if there are unsaved
  modifications. They can be written to a file with the `w` key.
- cplay now uses a single instance of mpv and its IPC mechanism instead of
  parsing command line output.
- fix: stop playback when cplay crashes
- fix: do not crash on tiny screen size
- fix: mpv 0.33 compatibility

# 4.0.0 (2020-09-07)

This is a complete rewrite which massively simplifies the code
and intentionally breaks a lot of things in the process.

- breaking changes:
  - drop support for all players except mpv
  - drop support for all mixers except pulse
  - drop support for all playlists except m3u
  - drop support for translations
  - drop support for fifo/cnq
  - drop support for metadata (mutagen)
  - rm all command line arguments
  - rm some key bindings, most without replacement
    - z for play/pause (use x or Space instead)
    - p for previous track
    - +/- for volume control (use 0..9 instead)
    - </> for horizontal scrolling
    - l for list mode
    - t/T/u/U/i/Space for tagging
    - o for open path
    - anything involving the ctrl key
    - Q no longer asks for confirmation
- new features:
  - state and presentation have been decoupled, resulting in simpler code
  - support for unicode input
  - interactive recursive search
  - it is now possible to open a playlist file and edit it rather than just
    adding its contents to the internal playlist

# 3.0.0 (2019-09-20)

- breaking changes:
  - drop support for python 2
  - drop support for configuration
  - drop support for macros
  - drop support for executing shell commands
  - drop support for bookmarks
  - drop support for playing videos
  - drop support for vlc backend
  - drop support for deprecated python-oss
  - drop "stop after each track" feature
- new features:
  - add `--autoexit` option to close at the end of the playlist
  - automatically start playing if a file was passed
  - add support for webm
  - allow moving the current track if none is tagged

# 2.4.1 (2018-04-10)

- fix an issue with translation
- fix outdated references in README

# 2.4.0 (2018-04-10)

- do not add individual files when there are playlists
- add support for cue files
- add mpv backend
- a lot of refactoring and small fixes

# 2.3.0 (2017-06-05)

- fix various unicode issues
- fix detection of valid song/playlist on URLs
- fix: terminate backend on crash
- internal restructuring to facilitate search plugins
- new backend: ffplay
- add http support to avplay and gst123 backends
- fix: infinite loop in vlc backend

# 2.2.0 (2017-01-30)

- fix: space not allowed in extra requirement name
- fix unicode issue with mplayer
- improve sox backend
- allow https in URLs
- add avplay backend
- wrap-around find

# 2.1.2 (2016-01-03)

- fix warning when using setup.py without babel installed
- allow http protocol
- various fixes related to mixers

# 2.1.1 (2015-12-28)

- add `--version` option
- fix displaying bytestrings in python3

# 2.1.0 (2015-10-30)

- dropped support for ncurses
- dropped support for mplayer specific features (speed, equalizer)
- removed lircrc
- removed cplay.list
- added support for VLC
- added support for playing videos
- added `-s` flag to allow saving state on close and restoring on open
- log error instead of crash on invalid cplayrc
- fixed some python3 bytestring issues
- translations are now managed on <https://www.transifex.com/projects/p/cplay-ng/>
- metadata detection has been refactored and should now be more reliable
- a lot of internal restructuring to ease collaboration with Andreas van
  Cranenburgh's fork at <https://github.com/andreasvc/cplay>

# 2.0.3 (2015-03-20)

- test are now run with python2.7, python3.4 and pypy
- some fixes to the pulseaudio volumn mixer (Andreas van Cranenburgh)
- fix parsing of mpg123 output (times larger than 1h)

# 2.0.2 (2014-07-13)

- fix regression where cplay crashed when opened with an argument
- replace getopt by argparse which provides a better command line interface
- allow to select a socket for use with cnq

# 2.0.1 (2014-06-18)

This release brings a basic testing environment and some internal
restructuring.  The following bugs have been fixed:

- cplay:
  - fix regular expressions in python3
  - only add valid songs to playlist
  - don't crash on missing backend
  - don't require babel for installation
- cnq:
  - declare missing argparse dependency

# 2.0.0 (2014-06-15)

cplay has been unmaintained for many years now. I tried to contact the
original developers but without luck. So now I am announcing a fork of
cplay: cplay-ng.

My short time goal with this was to be able to install cplay from pypi.
This goals has now been reached. Future plans include new features and a
test suite.

- cplay:
  - renamed to cplay-ng
  - dropped support for python < 2.6
  - python3 compatibility
  - pep8 compatibility
  - setuptools integration
  - pulse mixer support (Andreas van Cranenburgh)
  - add gst123 backend which uses gstreamer and therefore supports many audio
    formats
  - midi support through timidity
  - scale key-volume mapping such that 9 is 100%
- cnq:
  - renamed to cnq-ng
  - complete rework to become a full featured remote control for cplay-ng

# older releases

releases before 2.0.0 are listed in ChangeLog.old

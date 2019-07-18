Description
-----------

``cplay`` is a minimalist music player with a textual user interface
written in Python. It aims to provide a power-user-friendly interface
with simple filelist and playlist control.

Instead of building an elaborate database of your music library,
``cplay`` allows you to quickly browse the filesystem and enqueue
directories. Cue-files and other playlists are supported.

The original cplay is no longer maintained.  This fork aims to maintaining
the original code as well as keeping it up to date with recent
developments (e.g. python3) and adding new features.

.. image:: screenshot.png
   :alt: screenshot of cplay with file browser

Requirements
------------

- `python3 <http://www.python.org/>`_

For playback, install one or more of the following players:

- `mplayer <http://www.mplayerhq.hu/>`_
- `mpv <https://mpv.io/>`_
- `gst123 <http://space.twc.de/~stefan/gst123.php>`_
- `ffplay <https://ffmpeg.org/ffplay.html>`_
- `avplay <https://www.libav.org/avplay.html>`_
- `sox <http://sox.sf.net/>`_
- `mpg321 <http://sourceforge.net/projects/mpg321/>`_
- `ogg123 <http://www.vorbis.com/>`_
- `mpg123 <http://www.mpg123.org/>`_
- `splay <http://splay.sourceforge.net/>`_
- `madplay <http://www.mars.org/home/rob/proj/mpeg/>`_
- `mikmod <http://www.mikmod.org/>`_
- `xmp <http://xmp.sf.net/>`_
- `speex <http://www.speex.org/>`_
- `timidity <http://sourceforge.net/projects/timidity/>`_

Other optional components:

- `pyalsaaudio <http://pyalsaaudio.sourceforge.net/>`_ (optional) For
  Alsa mixer support

- pulseaudio-utils, specifically the ``pactl`` command (optional) For
  PulseAudio mixer support

- `mutagen <http://code.google.com/p/mutagen/>`_ (optional) For
  metadata support (IDv3 etc.)

Installation
------------

::

    $ pip install cplay-ng

In Debian/Ubuntu, the following installs a selection of players and optional components::

    $ sudo apt-get install mpv gst123 mpg321 vorbis-tools pulseaudio-utils

Usage
-----

::

    $ cplay-ng [ file | dir | playlist ] ...

For a full list of command line options, see ``cplay-ng --help``.
When in doubt about runtime controls, press 'h' for a friendly help page.

Apart from cplay-ng, this distribution also includes the program
cnq-ng to remote-control a running cplay-ng.

Configuration
-------------

If you would like to change the default player or the options passed to the
players just edit the ``BACKENDS`` list at the end of the cplay script.

Miscellaneous
-------------

A playlist can contain URLs, but the playlist itself will have to be
local. For mpeg streaming, splay is recommended.

It is also possible to pipe a playlist to cplay-ng, as stdin will be
reopened on startup unless it is attached to a tty.

The shell command gets the full path of either all tagged items or the
current item as positional arguments.

Description
===========

cplay-ng is a curses front-end for various audio players.  It aims to
provide a power-user-friendly interface with simple filelist and playlist
control. cplay-ng is written in Python and can use either pyncurses or the
standard curses module.

The original cplay is no longer maintained.  This fork aims to maintaining
the original code as well as keeping it up to date with recent
developments (e.g. python3) and adding new features.

Requirements
============

-  `python <http://www.python.org/>`_

-  `pyncurses <http://pyncurses.sourceforge.net/>`_

-  `pyalsaaudio <http://pyalsaaudio.sourceforge.net/>`_ (optional) For
   Alsa mixer support

-  `python-oss <http://net.indra.com/~tim/ossmodule/>`_ (optional) For
   OSS mixer support

-  `mutagen <http://code.google.com/p/mutagen/>`_ (optional) For
   metadata support (IDv3 etc.)

-  `python-magic <https://github.com/ahupp/python-magic>`_ (optional) For
   better filetype recognition

For playback either install `mplayer <http://www.mplayerhq.hu/>`_,
`gst123 <http://space.twc.de/~stefan/gst123.php>`_ or one or more of the
following players:

-  `mpg321 <http://sourceforge.net/projects/mpg321/>`_
-  `ogg123 <http://www.vorbis.com/>`_
-  `mpg123 <http://www.mpg123.org/>`_
-  `splay <http://splay.sourceforge.net/>`_
-  `madplay <http://www.mars.org/home/rob/proj/mpeg/>`_
-  `mikmod <http://www.mikmod.org/>`_
-  `xmp <http://xmp.sf.net/>`_
-  `sox <http://sox.sf.net/>`_
-  `speex <http://www.speex.org/>`_
-  `timidity <http://sourceforge.net/projects/timidity/>`_


Installation
============

::

    pip install cplay-ng


Usage
=====

::

    cplay-ng [-d <filename>] [-nrRm] [ file | dir | playlist ] ...

    -d  Enable debugging output to <filename>.

    -n  Start in restricted mode: No shell commands, changing directory,
        goto, or saving playlists.

    -r  Start in repeat mode.

    -R  Start in random mode.

    -m  Switch channels (oss only)

    When in doubt, press 'h' for a friendly help page.

You can also use ``cnq-ng`` to remote-control a running cplay-ng.


Configuration
=============

If you would like to change options passed to the actual players just edit
the ``PLAYERS`` list in the cplay-ng script or put the ``PLAYERS``
definition in either ``~/.cplayrc`` or ``/etc/cplayrc``. If one of these
files is available, it is executed by cplay-ng before initialization.

Macros are defined using the ``MACRO`` dictionary, where the key is a key
and the value is a string of cplay-ng input. For example, the following
would make ``,d`` delete tagged (or current) files::

    MACRO['d'] = '!rm "$@"\n'

Note, there is currently no version control for the rc-file!


Miscellaneous
=============

A playlist can contain URLs, but the playlist itself will have to be
local. For mpeg streaming, splay is recommended.

It is also possible to pipe a playlist to cplay-ng, as stdin will be
reopened on startup unless it is attached to a tty.

Remote control is available via ``/var/tmp/cplay\_control-$USER``.
Create this with mkfifo and see lircrc for examples.

The shell command gets the full path of either all tagged items or the
current item as positional arguments.

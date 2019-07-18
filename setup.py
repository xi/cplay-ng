#!/usr/bin/env python

import os
import re
from setuptools import setup
from distutils.command.build import build
from setuptools.command.install_lib import install_lib

extra_args = {}

try:
    import babel
    extra_args['message_extractors'] = {
        'cplay': [
            ('**.py', 'python', None)
        ],
    }
except:
    babel = None

DIRNAME = os.path.abspath(os.path.dirname(__file__))
rel = lambda *parts: os.path.abspath(os.path.join(DIRNAME, *parts))

README = open(rel('README.rst')).read()
CPLAY = open(rel('cplay', 'cplay.py')).read()
NAME, VERSION = re.search("__version__ = '([^']+)'", CPLAY).group(1).split()


class _build(build):
    sub_commands = ([('compile_catalog', None)] + build.sub_commands
        if babel is not None
        else build.sub_commands)


class _install_lib(install_lib):
    def run(self):
        if babel is not None:
            self.run_command('compile_catalog')
        install_lib.run(self)


setup(
    name=NAME,
    version=VERSION,
    description="A curses front-end for various audio players",
    long_description=README,
    url='https://github.com/xi/cplay-ng',
    author='Ulf Betlehem',
    author_email='flu@iki.fi',
    maintainer='Tobias Bengfort',
    maintainer_email='tobias.bengfort@posteo.de',
    packages=['cplay'],
    include_package_data=True,
    extras_require={
        'metadata': ['mutagen'],
        'alsa_mixer': ['pyalsaaudio'],
    },
    entry_points={'console_scripts': [
        'cplay-ng=cplay.cplay:main',
        'cnq-ng=cplay.remote_control:main',
    ]},
    cmdclass={
        'build': _build,
        'install_lib': _install_lib,
    },
    license='GPLv2+',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console :: Curses',
        'Intended Audience :: End Users/Desktop',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'License :: OSI Approved :: GNU General Public License v2 or later '
            '(GPLv2+)',
        'Topic :: Multimedia :: Sound/Audio :: Players',
    ],
    **extra_args)

#!/usr/bin/env python

from setuptools import setup

setup(
    name='cplay',
    version='1.50',
    description="A curses front-end for various audio players",
    long_description=open('README.rst').read(),
    url='https://github.com/xi/cplay',
    author='Ulf Betlehem',
    author_email='flu@iki.fi',
    maintainer='Tobias Bengfort',
    maintainer_email='tobias.bengfort@gmx.net',
    py_modules=['cplay'],
    extras_require={
        'filetype': ['python-magic'],
        'metadata': ['mutagen'],
        'alsa mixer': ['pyalsaaudio'],
    },
    entry_points={'console_scripts': 'cplay=cplay:main'},
    license='GPLv2+',
    classifiers=[
        'Development Status :: 7 - Inactive',
        'Environment :: Console :: Curses',
        'Intended Audience :: End Users/Desktop',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'License :: OSI Approved :: GNU General Public License v2 or later '
            '(GPLv2+)',
        'Topic :: Multimedia :: Sound/Audio :: Players',
    ])

#!/usr/bin/env python

from setuptools import setup

README = open('README.rst').read()


setup(
    name='cplay-ng',
    version='4.0.0',
    description='A simple curses audio player',
    long_description=README,
    url='https://github.com/xi/cplay-ng',
    author='Ulf Betlehem',
    author_email='flu@iki.fi',
    maintainer='Tobias Bengfort',
    maintainer_email='tobias.bengfort@posteo.de',
    py_modules=['cplay'],
    entry_points={'console_scripts': [
        'cplay-ng=cplay:main',
    ]},
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
)

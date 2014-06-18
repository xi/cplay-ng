#!/usr/bin/env python

from setuptools import setup
from distutils.command.build import build
from setuptools.command.install_lib import install_lib

try:
    import babel
except:
    babel = None

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
    name='cplay-ng',
    version='2.0.1',
    description="A curses front-end for various audio players",
    long_description=open('README.rst').read(),
    url='https://github.com/xi/cplay-ng',
    author='Ulf Betlehem',
    author_email='flu@iki.fi',
    maintainer='Tobias Bengfort',
    maintainer_email='tobias.bengfort@gmx.net',
    packages=['cplay'],
    include_package_data=True,
    install_requires=[
        'argparse',
    ],
    extras_require={
        'filetype': ['python-magic'],
        'metadata': ['mutagen'],
        'alsa mixer': ['pyalsaaudio'],
    },
    message_extractors={
        'cplay': [
            ('**.py', 'python', None)
        ],
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

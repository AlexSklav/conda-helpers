# coding: utf-8
'''
.. versionchanged:: 0.13
    Add support for Python 3.
'''
from __future__ import absolute_import, print_function, unicode_literals

from ._version import get_versions
from .exe_api import *
from .py_api import *

__version__ = get_versions()['version']
del get_versions

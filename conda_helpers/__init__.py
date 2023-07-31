# coding: utf-8
'''
.. versionchanged:: 0.13
    Add support for Python 3.

.. versionchanged:: 0.21
    Add support for ``conda>=4.4``.
'''

from .exe_api import *
from .py_api import *

from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

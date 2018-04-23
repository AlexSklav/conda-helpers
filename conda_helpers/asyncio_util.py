# coding: utf-8
'''
.. versionadded:: 0.21
'''
from __future__ import absolute_import, print_function, unicode_literals
from functools import wraps
import logging
import platform
import sys
import threading

if sys.version_info <= (3, 4):
    import trollius as asyncio

    from ._async_py27 import run_command
else:
    import asyncio

    from ._async_py35 import run_command


__all__ = ['new_file_event_loop', 'ensure_event_loop', 'with_loop', 'asyncio',
           'run_command']

logger = logging.getLogger(__name__)


def new_file_event_loop():
    '''
    .. versionadded:: 0.15


    Returns
    -------
    asyncio.BaseEventLoop
        Event loop capable of monitoring file IO events, including ``stdout``
        and ``stderr`` pipes.  **Note that on Windows, the default event loop
        _does not_ support file or stream events.  Instead, a
        :class:`ProactorEventLoop` must explicitly be used on Windows. **
    '''
    return (asyncio.ProactorEventLoop() if platform.system() == 'Windows'
            else asyncio.new_event_loop())


def ensure_event_loop():
    '''
    .. versionadded:: 0.15


    Get existing event loop or create a new one if necessary.

    Returns
    -------
    asyncio.BaseEventLoop
    '''
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError as e:
        if 'There is no current event loop' in str(e):
            loop = new_file_event_loop()
            asyncio.set_event_loop(loop)
        else:
            raise
    return loop


def with_loop(func):
    '''
    .. versionadded:: 0.15


    Decorator to run function within an asyncio event loop.

    .. notes::
        Uses :class:`asyncio.ProactorEventLoop` on Windows to support file I/O
        events, e.g., serial device events.

        If an event loop is already bound to the thread, but is either a)
        currently running, or b) *not a :class:`asyncio.ProactorEventLoop`
        instance*, execute function in a new thread running a new
        :class:`asyncio.ProactorEventLoop` instance.
    '''
    @wraps(func)
    def wrapped(*args, **kwargs):
        loop = ensure_event_loop()

        thread_required = False
        if loop.is_running():
            logger.debug('Event loop is already running.')
            thread_required = True
        elif all([platform.system() == 'Windows',
                  not isinstance(loop, asyncio.ProactorEventLoop)]):
            logger.debug('`ProactorEventLoop` required, not `%s`'
                         'loop in background thread.', type(loop))
            thread_required = True

        if thread_required:
            logger.debug('Execute new loop in background thread.')
            finished = threading.Event()

            def _run(generator):
                loop = ensure_event_loop()
                try:
                    result = loop.run_until_complete(asyncio
                                                     .ensure_future(generator))
                except Exception as e:
                    finished.result = None
                    finished.error = e
                else:
                    finished.result = result
                    finished.error = None
                finished.set()
            thread = threading.Thread(target=_run,
                                      args=(func(*args, **kwargs), ))
            thread.daemon = True
            thread.start()
            finished.wait()
            if finished.error is not None:
                raise finished.error
            return finished.result

        logger.debug('Execute in exiting event loop in main thread')
        return loop.run_until_complete(func(**kwargs))
    return wrapped

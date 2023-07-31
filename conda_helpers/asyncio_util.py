# coding: utf-8
import logging
import platform
import threading
import asyncio

from functools import wraps
from typing import Any, Awaitable, Callable
from ._async_py35 import run_command

__all__ = ['ensure_event_loop', 'with_loop', 'asyncio', 'run_command']

logger = logging.getLogger(__name__)


def ensure_event_loop() -> asyncio.BaseEventLoop:
    """
    Get existing event loop or create a new one if necessary.

    Returns
    -------
    asyncio.BaseEventLoop
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError as e:
        if 'There is no current event loop' in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        else:
            raise
    return loop


def with_loop(func: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
    """
    Decorator to run function within an asyncio event loop.

    .. notes::
        Uses :class:`asyncio.ProactorEventLoop` on Windows to support file I/O
        events, e.g., serial device events.

        If an event loop is already bound to the thread, but is either a)
        currently running, or b) *not a :class:`asyncio.ProactorEventLoop`
        instance*, execute function in a new thread running a new
        :class:`asyncio.ProactorEventLoop` instance.
    """

    @wraps(func)
    def wrapped(*args, **kwargs) -> Any:
        loop = ensure_event_loop()

        thread_required = False
        if loop.is_running():
            logger.debug('Event loop is already running.')
            thread_required = True
        else:
            pro_loop = False
            if platform.system() == 'Windows':
                try:
                    pro_loop = isinstance(loop, asyncio.ProactorEventLoop)
                except AttributeError:
                    pass

            if pro_loop:
                logger.debug(f'`ProactorEventLoop` required, not `{type(loop)}` loop in background thread.')
                thread_required = True

        if thread_required:
            logger.debug('Execute new loop in background thread.')
            finished = threading.Event()

            async def run(generator: Awaitable[Any]) -> None:
                loop = ensure_event_loop()
                try:
                    result = await generator
                except Exception as e:
                    finished.result = None
                    finished.error = e
                else:
                    finished.result = result
                    finished.error = None
                finished.set()

            thread = threading.Thread(target=run, args=(func(*args, **kwargs), ))
            thread.daemon = True
            thread.start()
            finished.wait()
            if finished.error is not None:
                raise finished.error
            return finished.result

        logger.debug('Execute in exiting event loop in main thread')
        return loop.run_until_complete(func(**kwargs))

    return wrapped

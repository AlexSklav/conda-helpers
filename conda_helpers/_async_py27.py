from __future__ import absolute_import, print_function, unicode_literals
from backports.shutil_get_terminal_size import get_terminal_size
from functools import partial
import io
import itertools as it
import subprocess as sp
import sys

import colorama as co
import trollius as asyncio


@asyncio.coroutine
def _read_stream(stream, callback=None, buffer_size=None):
    while True:
        data = yield asyncio.From(stream.read(buffer_size or 1))
        if data:
            if callback is not None:
                callback(data)
        else:
            break


@asyncio.coroutine
def run_command(cmd, *args, **kwargs):
    '''
    .. versionchanged:: 0.18
        Display wait indicator if ``verbose`` is set to ``None`` (default).
    '''
    shell = kwargs.pop('shell', True)
    verbose = kwargs.pop('verbose', True)
    buffer_size = kwargs.pop('buffer_size', io.DEFAULT_BUFFER_SIZE)

    if isinstance(cmd, list):
        cmd = sp.list2cmdline(cmd)
    _exec_func = (asyncio.subprocess.create_subprocess_shell
                  if shell else asyncio.subprocess.create_subprocess_exec)
    process = yield asyncio.From(_exec_func(cmd, *args,
                                            stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE))
    stdout_ = io.StringIO()
    stderr_ = io.StringIO()

    terminal_size = get_terminal_size()
    message = [co.Fore.MAGENTA + 'Executing:', co.Fore.WHITE + cmd]
    if sum(map(len, message)) + 2 > terminal_size.columns:
        cmd_len = terminal_size.columns - 2 - sum(map(len, ('...',
                                                            message[0])))
        message[1] = co.Fore.WHITE + cmd[:cmd_len] + '...'
    waiting_indicator = it.cycle(r'\|/-')

    cmd_finished = asyncio.Event()

    @asyncio.coroutine
    def display_status():
        '''
        Display status while executing command.
        '''
        # Update no faster than `stderr` flush interval (if set).
        update_interval = 2 * getattr(sys.stderr, 'flush_interval', .2)

        while not cmd_finished.is_set():
            print('\r' + co.Fore.WHITE + next(waiting_indicator), *message,
                  end='', file=sys.stderr)
            yield asyncio.From(asyncio.sleep(update_interval))

        print('\r' + co.Fore.GREEN + 'Finished:', co.Fore.WHITE + cmd,
              file=sys.stderr)

    def dump(output, data):
        text = data.decode('utf8')
        if verbose:
            print(text, end='')
        output.write(text)

    if verbose is None:
        # Display status while executing command.
        status_future = asyncio.ensure_future(display_status())

    yield asyncio.From(asyncio.wait([_read_stream(process.stdout,
                                                  partial(dump, stdout_),
                                                  buffer_size=buffer_size),
                                     _read_stream(process.stderr,
                                                  partial(dump, stderr_),
                                                  buffer_size=buffer_size)]))

    # Notify that command has completed execution.
    cmd_finished.set()
    if verbose is None:
        # Wait for status to display "Finished: ..."
        yield asyncio.From(status_future)
    return_code = yield asyncio.From(process.wait())
    raise asyncio.Return(return_code, stdout_.getvalue(), stderr_.getvalue())

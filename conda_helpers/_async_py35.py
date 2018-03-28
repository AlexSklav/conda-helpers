from __future__ import absolute_import, print_function, unicode_literals
from functools import partial
import asyncio
import io
import itertools as it
import subprocess as sp
import sys

import colorama as _C


async def _read_stream(stream, callback=None):
    while True:
        data = await stream.read(1)
        if data:
            if callback is not None:
                callback(data)
        else:
            break


async def run_command(cmd, *args, **kwargs):
    '''
    .. versionchanged:: X.X.X
        Display wait indicator if ``verbose`` is set to ``None`` (default).
    '''
    shell = kwargs.pop('shell', True)
    verbose = kwargs.pop('verbose', True)
    if isinstance(cmd, list):
        cmd = sp.list2cmdline(cmd)
    _exec_func = (asyncio.subprocess.create_subprocess_shell
                  if shell else asyncio.subprocess.create_subprocess_exec)
    process = await _exec_func(cmd, *args, stdout=asyncio.subprocess.PIPE,
                               stderr=asyncio.subprocess.PIPE)
    stdout_ = io.StringIO()
    stderr_ = io.StringIO()

    message = (_C.Fore.MAGENTA + 'Executing:', _C.Fore.WHITE + cmd)
    waiting_indicator = it.cycle(r'\|/-')

    def dump(output, data):
        text = data.decode('utf8')
        if verbose:
            print(text, end='')
        elif verbose is None:
            print('\r' + next(waiting_indicator), *message, end='',
                  file=sys.stderr)
        output.write(text)

    await asyncio.wait([_read_stream(process.stdout, partial(dump, stdout_)),
                        _read_stream(process.stderr, partial(dump, stderr_))])

    if verbose is None:
        print('\r' + _C.Fore.GREEN + 'Finished:', message[1], file=sys.stderr)

    return_code = await process.wait()
    return return_code, stdout_.getvalue(), stderr_.getvalue()

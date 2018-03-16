from __future__ import absolute_import, print_function, unicode_literals
from functools import partial
import asyncio
import io
import subprocess as sp


async def _read_stream(stream, callback=None):
    while True:
        data = await stream.read(1)
        if data:
            if callback is not None:
                callback(data)
        else:
            break


async def run_command(cmd, *args, **kwargs):
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

    def dump(output, data):
        text = data.decode('utf8')
        if verbose:
            print(text, end='')
        elif verbose is None:
            print('.', end='')
        output.write(text)

    await asyncio.wait([_read_stream(process.stdout, partial(dump, stdout_)),
                        _read_stream(process.stderr, partial(dump, stderr_))])

    return_code = await process.wait()
    return return_code, stdout_.getvalue(), stderr_.getvalue()

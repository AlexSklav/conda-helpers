# coding: utf-8
u'''
Execute Conda commands, reusing cached output if available.
'''
from __future__ import absolute_import, unicode_literals, print_function
from argparse import ArgumentParser
from collections import OrderedDict
from functools import wraps
import datetime as dt
import re
import subprocess as sp
import sys

import colorama as _C
import joblib as jl
import path_helpers as ph
import six

import conda_helpers as ch


BASE_PARSER = ArgumentParser(add_help=False)
BASE_PARSER.add_argument('--cache-dir', type=ph.path, help='Cache directory '
                         '(default=`%(default)s`).',
                         default=ph.path('~/.conda-helpers-cache').expand())
BASE_PARSER.add_argument('-f', '--force', action='store_true', help='Force '
                         'execution of command (do not used cached result).')
BASE_PARSER.add_argument('-v', '--verbose', action='store_true')


def git_src_info(meta_path):
    '''
    Parameters
    ----------
    meta_path : str
        Path to ``meta.yaml`` Conda recipe file.

    Returns
    -------
    tuple(path, git describe, HEAD hash) or None
        Return ``None`` if no ``git_url`` is specified in the ``meta.yaml``
        file.  Otherwise, return ``git`` info for recipe source.
    '''
    meta_path = ph.path(meta_path)
    recipe_path = meta_path.parent

    match = re.search(r'git_url: +(?P<git_url>[\S^#]*).*$', meta_path.text(),
                      flags=re.MULTILINE)

    git_url = ph.path(match.group('git_url'))

    if git_url.isabs():
        git_dir = git_url
    else:
        git_dir = recipe_path.joinpath(git_url).realpath()

    if git_dir.isdir():
        describe = sp.check_output('git describe --tags --dirty',
                                   cwd=git_dir).strip()
        head = sp.check_output('git rev-parse HEAD', cwd=git_dir).strip()
        return git_dir, describe, head


@wraps(ch.conda_exec)
def conda_exec_memoize(*args, **kwargs):
    '''
    Memoizable
    '''
    global conda_exec

    __file_hashes__ = kwargs.pop('__file_hashes__', tuple())
    __ignore_paths__ = kwargs.pop('__ignore_paths__', tuple())
    # Get absolute path for each ignore path.
    __ignore_paths__ = tuple([ph.path(p).realpath() for p in __ignore_paths__])
    __force_exec__ = kwargs.pop('__force_exec__', False)
    verbose = kwargs.pop('verbose', False)

    cmd_args = list(args)

    __git_revisions__ = tuple()

    for i, a in enumerate(args):
        if isinstance(a, six.string_types):
            if i > 0 and args[i - 1] == '--croot':
                # Ignore `croot` directory.
                continue
            a = ph.path(a)
            if a.exists() and a.realpath() not in __ignore_paths__:
                cmd_args[i] = a.realpath()
                if a.isfile():
                    # Argument is a path to a file that exists and is not
                    # explicitly ignored.  Add hash of file contents to
                    # arguments to allow for content-specific memoization.
                    __file_hashes__ += a.realpath(), a.read_hexhash('sha1')
                    if a.name == 'meta.yaml':
                        git_info = git_src_info(a)
                        __git_revisions__ += (git_info, )
                elif a.isdir():
                    # Argument is a path to a directory that exists and is not
                    # explicitly ignored.  Add hashes of directory contents to
                    # arguments to allow for content-specific memoization.
                    files = []
                    for f in a.walkfiles():
                        files.append((f.realpath(), f.read_hexhash('sha1')))
                        if f.name == 'meta.yaml':
                            git_info = git_src_info(f)
                            __git_revisions__ += (git_info, )
                    __file_hashes__ += (a.realpath(), tuple(files))

    kwargs['verbose'] = True

    if __git_revisions__:
        kwargs['__git_revisions__'] = __git_revisions__
        if verbose:
            for git_dir_i, describe_i, head_i in __git_revisions__:
                print(_C.Fore.MAGENTA + '  git source:',
                      (_C.Fore.WHITE + '{}@'.format(git_dir_i.name)) +
                      (_C.Fore.LIGHTGREEN_EX + '{}'.format(describe_i
                                                           .decode('utf8'))),
                      (_C.Fore.LIGHTCYAN_EX + '({})'.format(head_i[:8]
                                                            .decode('utf8'))),
                      file=sys.stderr)
        kwargs['__git_revisions__'] = __git_revisions__

    kwargs['__file_hashes__'] = __file_hashes__
    output_dir, argument_hash = conda_exec._get_output_dir(*cmd_args, **kwargs)

    if ph.path(output_dir).joinpath('output.pkl').isfile():
        # Cache result exists.
        if __force_exec__:
            # Delete cached output file.
            ph.path(output_dir).joinpath('output.pkl').remove()
            if verbose:
                print(_C.Fore.RED + 'Deleted cached result (`--force` was '
                      'specified.)', file=sys.stderr)
            cached = False
        else:
            cached = True

    else:
        cached = False

    if verbose:
        if cached:
            print(_C.Fore.MAGENTA + 'Reusing cached result...',
                  file=sys.stderr)
        else:
            print(_C.Fore.MAGENTA + 'Executing function (no cache found)...',
                  file=sys.stderr)

    if verbose:
        print(_C.Fore.MAGENTA + '\nOutput\n======', file=sys.stderr)

    # **Note: `conda_exec` is created dynamically in `main()` function to
    # use a dynamically-specified memoize cache directory.**
    output = conda_exec(*cmd_args, **kwargs).replace('\r\n', '\n')
    if cached:
        # Result was loaded from cache.  Since function was not actually
        # run, need to print output.
        sys.stdout.write(output)
    return output


def main(args=None):
    global conda_exec

    _C.init(autoreset=True)
    args = sys.argv[1:]

    if args is None:
        args = sys.argv[1:]

    if '--' in args:
        cmd_args = args[args.index('--') + 1:]
        parser_args = args[:args.index('--')]
    else:
        cmd_args = []
        parser_args = args

    parser = ArgumentParser(description='Memoized Conda commands.')

    sub = parser.add_subparsers(dest='command')
    supported_commands = ['render', 'build']

    subparsers = OrderedDict([(subparser_name_i,
                               sub.add_parser(subparser_name_i,
                                              parents=[BASE_PARSER]))
                              for subparser_name_i in supported_commands])

    args = parser.parse_args(parser_args)

    if not args.command:
        parser.error('No command specified.  Must specify one of: `{}`'
                     .format(', '.join(subparsers.keys())))

    if args.verbose:
        if args.cache_dir == '-':
            print(_C.Fore.MAGENTA + 'Cache disabled.', file=sys.stderr)
            args.cache_dir = None
        elif not args.cache_dir.isdir():
            print(_C.Fore.MAGENTA + 'Creating cache dir:',
                  _C.Fore.WHITE + args.cache_dir.realpath(), file=sys.stderr)
            args.cache_dir = args.cache_dir.realpath()
        else:
            print(_C.Fore.MAGENTA + 'Using cache dir:',
                  _C.Fore.WHITE + args.cache_dir.realpath(), file=sys.stderr)
            args.cache_dir = args.cache_dir.realpath()

    memory = jl.Memory(cachedir=args.cache_dir, verbose=0)

    # **Note: `conda_exec` function is created dynamically to use the
    # dynamically-specified memoize cache directory.**
    conda_exec = memory.cache(ch.conda_exec)

    start = dt.datetime.now()
    try:
        conda_exec_memoize(args.command, *cmd_args, verbose=args.verbose,
                           __force_exec__=args.force)
        end = dt.datetime.now()

        if args.verbose:
            exe_time = (end - start)
            print(_C.Fore.MAGENTA + '\nExecution time: %s' % exe_time,
                  file=sys.stderr)
    finally:
        print(_C.Style.RESET_ALL, end='')


if __name__ == '__main__':
    main()

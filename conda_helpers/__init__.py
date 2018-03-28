# -*- coding: utf-8 -*-
'''
.. versionchanged:: 0.13
    Add support for Python 3.
'''
from __future__ import absolute_import, print_function, unicode_literals

from functools import wraps
import io
import itertools as it
import json
import logging
import pkg_resources
import platform
import re
import subprocess as sp
import sys
import tempfile as tmp
import threading

import path_helpers as ph
import six

from ._version import get_versions
from .recipes import recipe_objs, find_requirements
__version__ = get_versions()['version']
del get_versions
if sys.version_info <= (3, 4):
    import trollius as asyncio

    from ._async_py27 import run_command
else:
    import asyncio

    from ._async_py35 import run_command

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


'''
.. versionadded:: 0.12.3

Match progress messages from Conda install output log.

For example:

    {"maxval": 133256, "finished": false, "fetch": "microdrop-laun", "progress": 0}

See `issue #5 <https://github.com/sci-bots/conda-helpers/issues/5>`_.
'''
cre_json_progress = re.compile(r'{"maxval":[^,]+,\s+"finished":[^,]+,'
                               r'\s+"fetch":\s+[^,]+,\s+"progress":[^}]+}')

'''
.. versionadded:: 0.12.3

Match non-JSON messages, e.g., `Conda menuinst log messages <https://github.com/ContinuumIO/menuinst/issues/49>`_.

For example:

    INFO menuinst_win32:__init__(182): Menu: name: 'MicroDrop', prefix: 'dropbot.py', env_name: 'dropbot.py', mode: 'None', used_mode: 'user'

See also
--------
https://groups.google.com/a/continuum.io/forum/#!topic/anaconda/RWs9of4I2KM

https://github.com/sci-bots/conda-helpers/issues/5
'''
cre_non_json = re.compile(r'^\w')


class PackageNotFound(Exception):
    def __init__(self, missing, available=None):
        '''
        Parameters
        ----------
        missing : str or list
            Name(s) of missing Conda packages.
        available : str or list, optional
            List of package information dictionaries of a set of available
            Conda packages.

            Useful, for example, for code to continue processing packages that
            **are** found.
        '''
        if isinstance(missing, six.string_types):
            self.missing = [missing]
        else:
            self.missing = missing
        if isinstance(available, six.string_types):
            self.available = [available]
        elif available is None:
            self.available = []
        else:
            self.available = available

    def __str__(self):
        if len(self.missing) > 1:
            return ('The following package(s) could not be found: {}'
                    .format(', '.join('`{}`'.format(package_i)
                                      for package_i in self.missing)))
        elif self.missing:
            return ('Package `{}` could not be found.'
                    .format(self.missing[0]))
        else:
            return 'Package not found.'


def f_major_version(version):
    '''
    Parameters
    ----------
    version : str
        Version string (e.g., ``'0.1.0'``, ``'1.0'``).

    Returns
    -------
    int
        Number before first dot in version string (i.e., major version number).
    '''
    return int(version.split('.')[0])


def conda_activate_command():
    '''
    .. versionadded:: 0.3.post2

    Returns
    -------
    list
        Command list to activate Conda environment.

        Can be prepended to a command list to run the command in the activated
        Conda environment corresponding to the running Python executable.
    '''
    prefix = conda_prefix()
    return ['call', r'{prefix}\Scripts\activate.bat' .format(prefix=prefix),
            prefix]


def conda_root():
    '''
    .. versionadded:: 0.3.post2

    Returns
    -------
    path_helpers.path
        Path to Conda **root** environment.
    '''
    return ph.path(sp.check_output(conda_activate_command() +
                                   ['&', 'conda', 'info', '--root'],
                                   shell=True).strip())


def conda_prefix():
    '''
    Returns
    -------
    path_helpers.path
        Path to Conda environment prefix corresponding to running Python
        executable.

        Return ``None`` if not running in a Conda environment.

    .. versionchanged:: 0.12.4
        Use :attr:`sys.prefix` to look up Conda environment prefix.

    .. versionchanged:: 0.13
        Cast :attr:`sys.prefix` as a :class:`path_helpers.path` instance.
    '''
    return ph.path(sys.prefix)


def conda_executable():
    '''
    .. versionadded:: 0.2.post5

    Returns
    -------
    path_helpers.path
        Path to Conda executable.
    '''
    for conda_filename_i in ('conda.exe', 'conda.bat'):
        conda_exe = conda_prefix().joinpath('Scripts', conda_filename_i)
        if conda_exe.isfile():
            return conda_exe
    else:
        raise IOError('Could not locate `conda` executable.')


def conda_upgrade(package_name, match_major_version=False, channels=None):
    '''
    Upgrade Conda package.

    Parameters
    ----------
    package_name : str
        Package name.
    match_major_version : bool, optional
        Only upgrade to versions within the same major version.
    channels : list, optional
        Anaconda channels to add to install command.

    Returns
    -------
    dict
        Dictionary containing:
         - :data:`original_version`: Package version before upgrade.
         - :data:`new_version`: Package version after upgrade (`None` if
           package was not upgraded).
         - :data:`installed_dependencies`: List of dependencies installed
           during package upgrade.  Each dependency is represented as a
           dictionary of the form ``{'package': ..., 'version': ...}``.

    Raises
    ------
    pkg_resources.DistributionNotFound
        If package not installed.
    IOError
        If Conda executable not found in Conda environment.
    subprocess.CalledProcessError
        If `conda search` command fails (in Conda environment).

        This happens, for example, if no internet connection is available.

    See also
    --------
    :func:`pip_helpers.upgrade`


    .. versionchanged:: 0.15
        Use asynchronous :func:`run_command` coroutine to better stream
        ``stdout`` and ``stderr``.
    '''
    result = {'package': package_name,
              'original_version': None,
              'new_version': None,
              'installed_dependencies': []}

    try:
        version_info = conda_version_info(package_name)
    except IOError:
        # Could not locate `conda` executable.
        return result

    result = {'package': package_name,
              'original_version': version_info['installed'],
              'new_version': None,
              'installed_dependencies': []}

    if result['original_version'] is None:
        # Package is not installed.
        raise pkg_resources.DistributionNotFound(package_name, [])

    if match_major_version:
        installed_major_version = f_major_version(version_info['installed'])
        latest_version = [v for v in version_info['versions']
                          if f_major_version(v) == installed_major_version][-1]
    else:
        latest_version = version_info['versions'][-1]

    if result['original_version'] == latest_version:
        # Latest version already installed.
        return result

    if channels is None:
        channels_args = []
    else:
        channels_args = list(it.chain(*[['-c', c] for c in channels]))
    # Running in a Conda environment.
    command = (conda_activate_command() + ['&', 'conda', 'install'] +
               channels_args + ['-y', '{}=={}'.format(package_name,
                                                      latest_version)])
    returncode, stdout, stderr = with_loop(run_command)(command, shell=True,
                                                        verbose=True)
    if returncode != 0:
        message = ('Error executing: `{}`.\nstdout\n------\n\n{}\n\n'
                   'stderr\n------\n\n{}'.format(sp.list2cmdline(command),
                                                 stdout, stderr))
        logger.error(message)
        raise RuntimeError(message)

    if '# All requested packages already installed.' in stdout:
        pass
    elif 'The following NEW packages will be INSTALLED' in stdout:
        match = re.search(r'The following NEW packages will be INSTALLED:\s+'
                          r'(?P<packages>.*)\s+Linking packages', stdout,
                          re.MULTILINE | re.DOTALL)
        cre_package = re.compile(r'\s*(?P<package>\S+):\s+'
                                 r'(?P<version>\S+)-[^-]+\s+')
        packages_str = match.group('packages')
        packages = [match_i.groupdict()
                    for match_i in cre_package.finditer(packages_str)]
        for package_i in packages:
            if package_i['package'] == package_name:
                result['new_version'] = package_i['version']
        installed_dependencies = [p for p in packages
                                  if p['package'] != package_name]
        result['installed_dependencies'] = installed_dependencies
    return result


def conda_version_info(package_name, channels=None):
    '''
    Parameters
    ----------
    package_name : str
        Conda package name.
    channels : list, optional
        Anaconda channels to add to install command.

    Returns
    -------
    dict
        Version information:

         - ``latest``: Latest available version.
         - ``installed``: Installed version (`None` if not installed).

    Raises
    ------
    IOError
        If Conda executable not found.
    subprocess.CalledProcessError
        If `conda search` command fails.

        This happens, for example, if no internet connection is available.
    '''
    if channels is None:
        channels_args = []
    else:
        channels_args = list(it.chain(*[['-c', c] for c in channels]))
    # Use `-f` flag to search for package, but *no other packages that have
    # `<package_name>` in the name*.
    output = sp.check_output(conda_activate_command() +
                             ['&', 'conda', 'search'] + channels_args +
                             ['-f', package_name], shell=True)

    output_lines = output.strip().splitlines()

    line_tokens = [re.split(r'\s+', v) for v in output_lines[1:]]
    versions = [tokens_i[2] if tokens_i[1] in ('*', '.') else tokens_i[1]
                for tokens_i in line_tokens]

    installed_indexes = [i for i, tokens_i in enumerate(line_tokens)
                         if tokens_i[1] == '*']
    installed_version = (None if not installed_indexes
                         else versions[installed_indexes[0]])
    return {'installed': installed_version, 'versions': versions}


def conda_exec(*args, **kwargs):
    r'''
    Execute command using ``conda`` executable in active Conda environment.

    .. versionchanged:: 0.7.3
        Do not escape ``<``, ``>`` characters in ``conda_exec``, since these
        characters are required for less than or greater than version
        specifiers.

        For example, ``"foo >2.0"``, ``"foobar <3.0"``.

    .. versionchanged:: 0.10
        Log executed command as a string, rather than a list of arguments.
        This should make it easier, for example, to copy and paste a command to
        run manually.

    .. versionchanged:: 0.12.2
        Escape ``&``, ``\``, ``|``, ``^``, ``<``, and ``<`` characters, but
        **only** if there is not a space in an argument.  The reason is that if
        there is a space in the argument, the argument will automatically be
        quoted so character escaping is not necessary.

    .. versionchanged:: 0.12.3
        By default, strip non-json lines from output when ``--json`` arg is
        specified.

        See `issue #5 <https://github.com/sci-bots/conda-helpers/issues/5>`_.

    Parameters
    ----------
    *args : list(str)
        Command line arguments to pass to ``conda`` executable.

    Returns
    -------
    str
        Output from command (both ``stdout`` and ``stderr``).


    .. versionchanged:: 0.15
        Use asynchronous :func:`run_command` coroutine to better stream
        ``stdout`` and ``stderr``.
    '''
    verbose = kwargs.get('verbose')

    # By default, strip non-json lines from output when `--json` arg is
    # specified.
    # See https://github.com/sci-bots/microdrop/issues/249.
    json_fix = kwargs.get('json_fix', True)

    # Only escape characters for arguments that do not include a space.  See
    # docstring for details.
    escape_char = '^' if platform.system() == 'Windows' else '\\'
    args = [arg_i if ' ' in arg_i else
            re.sub(r'([&\\^\|<>])', r'{}\1'.format(escape_char), arg_i)
            for arg_i in args]

    # Running in a Conda environment.
    command = conda_activate_command() + ['&', 'conda'] + list(args)
    logger.debug('Executing command: `%s`', sp.list2cmdline(command))
    returncode, stdout, stderr = with_loop(run_command)(command, shell=True,
                                                        verbose=verbose)
    if returncode != 0:
        message = ('Error executing: `{}`.\nstdout\n------\n\n{}\n\n'
                   'stderr\n------\n\n{}'.format(sp.list2cmdline(command),
                                                 stdout, stderr))
        logger.error(message)
        raise RuntimeError(message)

    # Strip non-json lines from output when `--json` arg is specified.
    if '--json' in args and json_fix:
        stdout = ''.join(line_i for line_i in stdout.splitlines()
                         if not any(cre_j.search(line_i)
                                    for cre_j in (cre_json_progress,
                                                  cre_non_json)))
    return stdout


def package_version(name, *args, **kwargs):
    '''
    .. versionchanged:: 0.8
        Accept extra :data:`args` and :data`kwargs`.

    .. versionchanged:: 0.12
        Raise :class:`PackageNotFound` error if one or more specified packages
        could not be found.

        Note that the ``available`` attribute of the raised
        :class:`PackageNotFound` object contains a list of package information
        dictionaries of the set of specified packages that **are** available
        Conda packages.

        This is useful, for example, for code to continue processing packages
        that **are** found.

    Parameters
    ----------
    name : str or list
        Name(s) of installed Conda package.
    *args
        Additional args to pass to :func:`conda_exec`.
    *kwargs
        Additional keyword args to pass to :func:`conda_exec`.

    Returns
    -------
    dict or list
        Dictionary (or dictionaries) containing ``'name'``, ``'version'``,
        ``'features'``, ``'features'``, and ``'build'``.

        If multiple package names were specified in :data:`name` argument, the
        order of the list of version dictionaries is the same as the order of
        the package names in the :data:`name` argument.

    Raises
    ------
    PackageNotFound
        If one or more specified packages could not be found.
    '''
    singleton = isinstance(name, six.string_types)
    if singleton:
        name = [name]

    # Use `conda_exec` since
    versions_js = conda_exec('list', '--json',
                             # XXX Add `' ?'` to force Windows to quote
                             # argument due to a space.
                             #
                             # The argument **MUST** be quoted since it may
                             # contain a pipe character (i.e., `|`).
                             '^({}) ?$'.format('|'.join(name)), *args,
                             **kwargs)
    version_dicts = json.loads(versions_js)
    if not version_dicts:
        raise NameError('Package `{}` not installed.'.format(name))

    if singleton:
        return version_dicts[0]
    else:
        # Return list of version dictionaries in same order as names where
        # specified in `name` argument.
        versions_dict = dict([(version_i['name'], version_i)
                              for version_i in version_dicts])
        missing = [name_i for name_i in name if name_i not in versions_dict]
        available = [versions_dict[name_i] for name_i in name
                     if name_i not in missing]
        if missing:
            raise PackageNotFound(missing, available=available)
        else:
            return available


def development_setup(recipe_dir, *args, **kwargs):
    '''
    Install build and run-time dependencies for specified Conda build recipe.

    Parameters
    ----------
    recipe_dir : str
        Path to Conda build recipe.
    *args
        Additional arguments to pass to ``conda install`` command.
    verbose : bool, optional
        If ``True``, display output of ``conda install`` command.

        If ``False``, do not display output of ``conda install`` command.

        If ``None``, display ``.`` characters to indicate progress during
        ``conda install`` command.


    .. versionchanged:: 0.13.1
        Strip build string (where necessary) from rendered recipe package
        specifiers.  Fixes `issue #4 <https://github.com/sci-bots/conda-helpers/issues/4>`_

    .. versionchanged:: 0.18
        Add support for recipes with multiple outputs.

        See also
        --------
        https://conda.io/docs/user-guide/tasks/build-packages/define-metadata.html#outputs-section
    '''
    verbose = kwargs.pop('verbose', True)
    recipe_dir = ph.path(recipe_dir).realpath()

    # Extract list of build and run dependencies from Conda build recipe.
    logger.info('Extract build dependencies from Conda recipe: %s', recipe_dir)
    # Render recipe for the Python version of the active Conda environment.
    # Note that `conda render` is part of the `conda-build` package, which is
    # installed in the `root` Conda environment, which may have a different
    # version of Python installed.
    PY = '{0.major}.{0.minor}'.format(sys.version_info)

    command = (conda_activate_command() +
               ['&', 'python', '-m', 'conda_helpers', 'render', '-v', '--',
                recipe_dir, '--python=' + PY])
    returncode, stdout, stderr = with_loop(run_command)(command, shell=True,
                                                        verbose=False)
    recipe = stdout

    # Decode one or more outputs from the recipe yaml.
    recipe_objs_ = recipe_objs(recipe)
    # Find all `build` and `run` requirements across all outputs.
    requirements = list(it.chain(*map(find_requirements, recipe_objs_)))
    # Extract package name and version (if specified) from each requirement.
    # XXX Do not include dependencies with wildcard version specifiers, since
    # they are not supported by `conda install`.
    required_packages = [dict(zip(('package', 'version'), r[1].split(' ')[:2]))
                         for r in requirements]

    # XXX Do not include dependencies with wildcard version specifiers, since
    # they are not supported by `conda install`.
    required_packages = [v for v in required_packages
                         if '*' not in v.get('version', '')]

    # Prepend explicit version numbers with '=='.
    for req_i in required_packages:
        if 'version' in req_i and re.search('^\d', req_i['version']):
            req_i['version'] = '==' + req_i['version']

    # Dump sorted list of required packages.
    required_strs = sorted('  {}{}'.format(r['package'],
                                           ' {}'.format(r['version']
                                                        if 'version' in r
                                                        else ''))
                           for r in required_packages)
    logger.info('Install build and run-time dependencies:\n%s',
                '\n'.join(required_strs))

    # Dump list of Conda requirements to a file and install dependencies using
    # `conda install ...`.
    required_packages_file = tmp.TemporaryFile(mode='w', prefix='%s-dev-req-' %
                                               recipe_dir.name, delete=False)
    required_packages_lines = ['{} {}'.format(req_i['package'],
                                              req_i.get('version', '')).strip()
                               for req_i in required_packages]
    try:
        # Create string containing one package descriptor per line.
        required_packages_str = '\n'.join(required_packages_lines)
        required_packages_file.file.write(required_packages_str)
        required_packages_file.file.close()
        conda_exec('install', '-y', '--file', required_packages_file.name,
                   *args, verbose=verbose)
    finally:
        # Remove temporary file containing list of Conda requirements.
        ph.path(required_packages_file.name).remove()


def install_info(install_response, split_version=False):
    '''
    Normalize ``conda install ...`` output, whether run in dry mode or not, to
    return a list of unlinked packages and a list of linked packages.

    .. versionadded:: 0.7

    .. versionchanged:: 0.7.3
        Handle install log actions as :class:`dict` or :class:`list`.

    .. versionchanged:: 0.11
        Optionally split package specifier string into package name and
        version.

    Parameters
    ----------
    install_response : dict
        JSON decoded response from ``conda install ...`` command.
    split_version : bool, optional
        Split package specifier string into package name and version.

        Default to ``False`` to maintain backwards compatibility with versions
        ``< 0.11``.

    Returns
    -------
    unlinked_packages, linked_packages : list, list
        If no packages were installed or removed:
         - :data:`unlinked_packages` is set to ``None``.
         - :data:`linked_packages` is set to ``None``.

        If any packages are installed or removed:
         - :data:`unlinked_packages` is a list of tuples corresponding to the
           packages that were uninstalled/replaced.
         - :data:`linked_packages` is a list of ``(<package name and version>,
           <channel>)`` tuples corresponding to the packages that were
           installed/upgraded.

        If :data:`split_version` is ``True``, each package tuple in
        :data:`unlinked_packages`` and :data:`link_packages` is of the form
        ``(<package name>, <version>, <channel>)``

        If :data:`split_version` is ``False`` (default), each package tuple in
        :data:`unlinked_packages`` and :data:`link_packages` is of the form
        ``(<package name and version>, <channel>)``.

    Raises
    ------
    RuntimeError
        If install response does not include item with key ``'success'``.
    '''
    def f_format_version(v):
        return '{}=={}'.format(v['name'], v['version'])

    if not install_response.get('success'):
        raise RuntimeError('Install operation failed.')
    if 'actions' not in install_response:
        return None, None
    # Read list of actions from response.
    actions = install_response['actions']
    if isinstance(actions, list):
        actions = actions[0]
    if isinstance(install_response['actions'], list):
        # Response was from a dry run.  It has a different format.
        unlink_packages = [[f_format_version(v), v['channel']]
                           for v in actions.get('UNLINK', [])]
        link_packages = [[f_format_version(v), v['channel']]
                         for v in actions.get('LINK', [])]
    else:
        unlink_packages = [v.split('::')[::-1]
                           for v in actions.get('UNLINK', [])]
        link_packages = [v.split('::')[::-1]
                         for v in actions.get('LINK', [])]

    # Sort list of packages to make output deterministic.
    sorted_unlinked = sorted(unlink_packages)
    sorted_linked = sorted(link_packages)

    def _split_version(package_tuples):
        '''
        Parameters
        ----------
        package_tuples : list
            List of package tuples of the form ``(<package name and version>,
            <channel>)``.

        Returns
        -------
        list
            List of package tuples of the form ``(<package name>, <version>,
            <channel>)``, i.e., the :data:`package_tuples` with the package
            name and version number split apart.
        '''
        return [(package_i.split('==') if '==' in package_i
                 else ['-'.join(package_i.split('-')[:-2]),
                       package_i.split('-')[-2]]) + [channel_i]
                for package_i, channel_i in package_tuples]

    if split_version:
        return list(map(_split_version, (sorted_unlinked, sorted_linked)))
    else:
        return sorted_unlinked, sorted_linked


def format_install_info(unlinked, linked):
    '''
    Format output of :func:`install_info` into human-readable form.

    For example:

        Uninstalled:
         - `foo==3.2` (from `conda-forge`)

        Installed:
         - `foobar==1.7` (from `sci-bots`)
         - `bar==1.7` (from `conda-forge`)

    .. versionadded:: 0.9

    .. versionchanged:: 0.12.1
        Implement handling :func:`install_info` output where
        :data:`split_version` set to ``True``.

    Parameters
    ----------
    unlinked : list or None
        If no packages were installed or removed:
         - :data:`unlinked_packages` is set to ``None``.
         - :data:`linked_packages` is set to ``None``.
    linked : list or None
        List of package information tuple either of the form ``(<package name>,
        <version>, <channel>)`` or ``(<package name and version>, <channel>)``.

    Returns
    -------
    str
        Formatted output of :func:`install_info`.
    '''
    output = io.BytesIO()

    def _format_package_tuple(package_tuple):
        '''
        Parameters
        ----------
        package_tuple : tuple
            Conda package information tuple either of the form
            ``(<package name>, <version>, <channel>)`` or of the form
            ``(<package name and version>, <channel>)``.

        See also
        --------
        :func:`install_info`
        '''
        if len(package_tuple) == 2:
            package_i, channel_i = package_tuple
            return ' - `{}` (from `{}`)'.format(package_i, channel_i)
        elif len(package_tuple) == 3:
            package_i, version_i, channel_i = package_tuple
            return ' - `{}=={}` (from `{}`)'.format(package_i, version_i,
                                                    channel_i)
    if unlinked:
        print('Uninstalled:', file=output)
        for package_tuple_i in linked:
            print(_format_package_tuple(package_tuple_i), file=output)
    if unlinked and linked:
        print('', file=output)
    if linked:
        print('Installed:', file=output)
        for package_tuple_i in linked:
            print(_format_package_tuple(package_tuple_i), file=output)
    return output.getvalue()

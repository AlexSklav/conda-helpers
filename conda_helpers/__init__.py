import io
import itertools as it
import json
import logging
import os
import pkg_resources
import platform
import re
import subprocess as sp
import sys
import tempfile as tmp
import types

import path_helpers as ph
import yaml

logger = logging.getLogger(__name__)


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
        if isinstance(missing, types.StringTypes):
            self.missing = [missing]
        else:
            self.missing = missing
        if isinstance(available, types.StringTypes):
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
    '''
    if any(['continuum analytics, inc.' in sys.version.lower(),
            'conda' in sys.version.lower()]):
        # Assume running under Conda.
        if 'CONDA_PREFIX' in os.environ:
            conda_prefix = ph.path(os.environ['CONDA_PREFIX'])
        else:
            # Infer Conda prefix as parent directory of Python executable.
            conda_prefix = ph.path(sys.executable).parent.realpath()
    else:
        # Assume running under Conda.
        conda_prefix = None
    return conda_prefix


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
        latest_version = filter(lambda v: f_major_version(v) ==
                                installed_major_version,
                                version_info['versions'])[-1]
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
    process = sp.Popen(conda_activate_command() +
                       ['&', 'conda', 'install'] + channels_args +
                       ['-y', '{}=={}'.format(package_name, latest_version)],
                       shell=True, stdout=sp.PIPE, stderr=sp.STDOUT)
    lines = []
    ostream = sys.stdout

    # Iterate until end of `stdout` stream (i.e., `b''`).
    for stdout_i in iter(process.stdout.readline, b''):
        ostream.write('.')
        lines.append(stdout_i)
    process.wait()
    print >> ostream, ''
    output = ''.join(lines)
    if process.returncode != 0:
        raise RuntimeError(output)

    if '# All requested packages already installed.' in output:
        pass
    elif 'The following NEW packages will be INSTALLED' in output:
        match = re.search(r'The following NEW packages will be INSTALLED:\s+'
                          r'(?P<packages>.*)\s+Linking packages', output,
                          re.MULTILINE | re.DOTALL)
        cre_package = re.compile(r'\s*(?P<package>\S+):\s+'
                                 r'(?P<version>\S+)-[^-]+\s+')
        packages_str = match.group('packages')
        packages = [match_i.groupdict()
                    for match_i in cre_package.finditer(packages_str)]
        for package_i in packages:
            if package_i['package'] == package_name:
                result['new_version'] = package_i['version']
        installed_dependencies = filter(lambda p: p['package'] != package_name,
                                        packages)
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
    '''
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

    .. versionchanged:: 0.11.1
        Do not escape ``|``, ``^`` characters in ``conda_exec`` since these
        characters are valid characters, for example, in package specifier
        regular expressions.

    Parameters
    ----------
    *args : list(str)
        Command line arguments to pass to ``conda`` executable.

    Returns
    -------
    str
        Output from command (both ``stdout`` and ``stderr``).
    '''
    verbose = kwargs.get('verbose')

    escape_char = '^' if platform.system() == 'Windows' else '\\'
    args = [re.sub(r'([&\\])', r'{}\1'.format(escape_char), arg_i)
            for arg_i in args]

    # Running in a Conda environment.
    command = conda_activate_command() + ['&', 'conda'] + list(args)
    logger.debug('Executing command: `%s`', sp.list2cmdline(command))
    process = sp.Popen(command, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT)
    lines = []
    ostream = sys.stdout

    # Iterate until end of `stdout` stream (i.e., `b''`).
    for stdout_i in iter(process.stdout.readline, b''):
        if verbose is None:
            ostream.write('.')
        elif verbose:
            ostream.write(stdout_i)
        lines.append(stdout_i)
    process.wait()
    print >> ostream, ''
    output = ''.join(lines)
    if process.returncode != 0:
        logger.error('Error executing command: `%s`', sp.list2cmdline(command))
        raise RuntimeError(output)
    return output


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
    singleton = isinstance(name, types.StringTypes)
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
    '''
    verbose = kwargs.pop('verbose', True)
    recipe_dir = ph.path(recipe_dir).realpath()

    # Extract list of build and run dependencies from Conda build recipe.
    logger.info('Extract build dependencies from Conda recipe: %s', recipe_dir)
    rendered_recipe = conda_exec('render', recipe_dir, verbose=False)
    recipe = yaml.load(rendered_recipe)
    requirements = recipe.get('requirements', {})
    build_requirements = set(requirements.get('build', []))
    run_requirements = set(requirements.get('run', []))
    development_reqs = sorted(build_requirements.union(run_requirements))

    # XXX Do not include dependencies with wildcard version specifiers, since
    # they are not supported by `conda install`.
    development_reqs = filter(lambda v: '*' not in v, development_reqs)

    # Dump list of Conda requirements to a file and install dependencies using
    # `conda install ...`.
    logger.info('Install build and run-time dependencies:\n%s',
                '\n'.join(' {}'.format(r) for r in development_reqs))
    development_reqs_file = tmp.TemporaryFile(mode='w', prefix='%s-dev-req-' %
                                              recipe_dir.name, delete=False)
    try:
        # Create string containing one package descriptor per line.
        development_reqs_str = '\n'.join(development_reqs)
        development_reqs_file.file.write(development_reqs_str)
        development_reqs_file.file.close()
        conda_exec('install', '-y', '--file', development_reqs_file.name,
                   *args, verbose=verbose)
    finally:
        # Remove temporary file containing list of Conda requirements.
        ph.path(development_reqs_file.name).remove()


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
         - :data:`unlinked_packages` is a list of tuples corresponding to the packages that
           were uninstalled/replaced.
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
    f_format_version = lambda v: '{}=={}'.format(v['name'], v['version'])

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
                                package_i.split('-')[-2]])
                 + [channel_i] for package_i, channel_i in package_tuples]

    if split_version:
        return map(_split_version, (sorted_unlinked, sorted_linked))
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

    Returns
    -------
    str
        Formatted output of :func:`install_info`.
    '''
    output = io.BytesIO()
    if unlinked:
        print >> output, 'Uninstalled:'
        for package_i, channel_i in unlinked:
            print >> output, ' - `{}` (from `{}`)'.format(package_i, channel_i)
    if unlinked and linked:
        print >> output, ''
    if linked:
        print >> output, 'Installed:'
        for package_i, channel_i in linked:
            print >> output, ' - `{}` (from `{}`)'.format(package_i, channel_i)
    return output.getvalue()

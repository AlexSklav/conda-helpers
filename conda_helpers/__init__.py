import contextlib
import itertools as it
import logging
import os
import pkg_resources
import re
import subprocess as sp
import sys

import path_helpers as ph


@contextlib.contextmanager
def logging_restore():
    '''
    Save logging state upon entering context and restore upon leaving.
    '''
    handlers = logging.root.handlers[:]
    level = logging.root.getEffectiveLevel()
    yield
    handlers_to_remove = logging.root.handlers[:]
    [logging.root.removeHandler(h) for h in handlers_to_remove]
    [logging.root.addHandler(h) for h in handlers]
    logging.root.setLevel(level)


# Import within `logging_restore` context to prevent Conda from clobbering
# active `logging` settings.
with logging_restore():
    import conda.cli.main_list


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

    # Running in a Conda environment.
    process = sp.Popen(conda_activate_command() + ['&', 'conda'] + list(args),
                       shell=True, stdout=sp.PIPE, stderr=sp.STDOUT)
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
        raise RuntimeError(output)
    return output


def package_version(name):
    '''
    Parameters
    ----------
    name : str
        Name of installed Conda package.

    Returns
    -------
    dict
        Dictionary containing ``'name'``, ``'version'``, and ``'build'``.
    '''
    prefix = conda_prefix()
    installed_pkgs = conda.cli.main_list.linked(prefix)
    exitcode, output = (conda.cli.main_list
                        .list_packages(prefix, installed_pkgs,
                                       regex=r'^%s$' % name,
                                       show_channel_urls=False,
                                       format='human'))
    if not output:
        raise NameError('Package `{}` not installed.'.format(name))
    return [dict(zip(['name', 'version', 'build'], re.split(r'\s+',
                                                            line_i)[:3]))
            for line_i in output][0]

import json
import logging
import re
import sys

import path_helpers as ph
import six


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

    .. versionchanged:: X.X.X
        Look up installed package info in ``<prefix>/conda-meta`` directory,
        eliminating dependency on ``conda`` executable.

        This is useful, for example, with Conda environments created with
        ``conda>=4.4``, where a link to the root ``conda`` executable is no
        longer created in the ``Scripts`` directory in the new environment.  In
        such cases, it is not possible to locate the root ``conda`` executable
        given only the child environment.


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
        Dictionary (or dictionaries) containing ``'name'``, ``'version'``, and
        ``'build'``.

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

    version_dicts = list(conda_list('|'.join(name), full_name=True).values())

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


def conda_list(regex, full_name=False):
    '''
    Emulate ``conda list`` command.

    .. note::
        This function **does not** require the ``conda`` executable to be
        available on the system path.


    .. versionadded:: X.X.X
        Look up installed package info in ``<prefix>/conda-meta`` directory,
        eliminating dependency on ``conda`` executable.

        This is useful, for example, with Conda environments created with
        ``conda>=4.4``, where a link to the root ``conda`` executable is no
        longer created in the ``Scripts`` directory in the new environment.  In
        such cases, it is not possible to locate the root ``conda`` executable
        given only the child environment.


    Parameters
    ----------
    regex : str
        Regular expression or package name.
    full_name : bool, optional
        If ``True``, only search for full names, i.e., ``^<regex>$``.

    Returns
    -------
    dict
        Dictionary mapping each matched package name to the corresponding
        package version information, including containing ``'name'``,
        ``'version'``, and ``'build'``.
    '''
    # Match package name(s) to filenames in `<prefix>/conda-meta` according to
    # [Conda package naming conventions][conda-pkg-name].
    #
    # [conda-pkg-name]: https://conda.io/docs/user-guide/tasks/build-packages/package-naming-conv.html
    cre_package = re.compile(r'^(?P<package_name>.*)-(?P<version>[^\-]+)'
                             r'-(?P<build_string>[^\-])+$')
    if full_name:
        regex = '^{}$'.format(regex)

    version_dicts = {}

    for json_file_i in conda_prefix().joinpath('conda-meta').files('*.json'):
        file_match_i = cre_package.match(json_file_i.namebase)
        if not file_match_i:
            # Unrecognized file name format.
            continue
        elif not re.match(regex, file_match_i.group('package_name')):
            # Package name does not match specified regular expression.
            continue
        package_info_i = json.loads(json_file_i.text())
        version_dicts[package_info_i['name']] = package_info_i

    return version_dicts

# coding: utf-8
u'''
Helper functions to process Conda recipes.


.. versionadded:: 0.18
'''
from __future__ import absolute_import, unicode_literals, print_function

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
import pydash as _py


def find_requirements(recipe_obj, package_name=None):
    '''
    Find all ``requirements`` sections in the Conda build recipe.
    '''
    if isinstance(package_name, str):
        package_name = [package_name]
    recipe_obj = _py.clone_deep(recipe_obj)
    matches = []
    _py.map_values_deep(recipe_obj, iteratee=lambda value, path:
                        matches.append((value.split(' ')[0], value, path))
                        if (len(path) > 2 and path[-3] == 'requirements'
                            and isinstance(value, str)
                            and (package_name is None or
                                 value.split(' ')[0] in package_name))
                        else None)
    return matches


def recipe_objs(recipe_str):
    '''
    Parameters
    ----------
    recipe_str : str
        Conda recipe text.

    Returns
    -------
    list<collections.OrderedDict>
        List of outputs decoded from recipe.  While most recipes result in a
        single output, Conda recipes can describe multiple outputs (see the
        `outputs section <https://conda.io/docs/user-guide/tasks/build-packages/define-metadata.html#outputs-section>`_
        in the ``conda build`` documentation).
    '''
    try:
        return [YAML().load(recipe_str)]
    except DuplicateKeyError:
        # multiple outputs from recipe
        lines = recipe_str.splitlines()
        package_starts = [i for i, line_i in enumerate(lines)
                          if line_i.startswith('package:')]
        return [YAML().load('\n'.join(lines[start:end]))
                for start, end in zip(package_starts, package_starts[1:] +
                                      [len(lines)])]

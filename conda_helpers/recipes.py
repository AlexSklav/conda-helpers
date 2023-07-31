# coding: utf-8
import ruamel.yaml
import pydash as _py

from ruamel.yaml.constructor import DuplicateKeyError
from typing import List, Optional, Tuple


def find_requirements(recipe_obj: dict, package_name: Optional[str] = None) -> List:
    """
    Find all ``requirements`` sections in the Conda build recipe.

    Parameters
    ----------
    recipe_obj : dict
        Conda recipe dictionary.

    package_name : str or None, optional
        Name of the package to filter the requirements, by default None.

    Returns
    -------
    List[Tuple[str, str, List[str]]]
        List of tuples containing requirement name, full requirement string, and the path to the requirement.
    """
    if isinstance(package_name, str):
        package_name = [package_name]

    matches = []

    def find_requirements_iter(value, path):
        if (len(path) > 2 and path[-3] == 'requirements'
                and isinstance(value, str)
                and (package_name is None or value.split(' ')[0] in package_name)):
            matches.append((value.split(' ')[0], value, path))

    _py.map_values_deep(recipe_obj, iteratee=find_requirements_iter)
    return matches


def recipe_objs(recipe_str: str) -> List[dict]:
    """
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
    """
    yaml = ruamel.yaml.YAML(typ="safe")
    try:
        return [yaml.load(recipe_str)]
    except DuplicateKeyError:
        # multiple outputs from recipe
        lines = recipe_str.splitlines()
        package_starts = [i for i, line_i in enumerate(lines) if line_i.startswith('package:')]
        return [yaml.load('\n'.join(lines[start:end]))
                for start, end in zip(package_starts, package_starts[1:] + [len(lines)])]

import sys

from setuptools import setup

sys.path.insert(0, '.')
import versioneer

setup(name='conda-helpers',
      version=versioneer.get_version(),
      cmdclass=versioneer.get_cmdclass(),
      description='Add description here.',
      keywords='',
      author='Christian Fobel',
      author_email='christian@fobel.net',
      url='https://github.com/sci-bots/conda-helpers',
      license='BSD',
      packages=['conda_helpers'],
      install_requires=['colorama', 'joblib', 'path-helpers', 'six'],
      # Install data listed in `MANIFEST.in`
      include_package_data=True)

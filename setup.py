from setuptools import setup, find_packages
import versioneer


setup(
    name='binstar-build',

    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),

    author='Sean Ross-Ross',
    author_email='srossross@gmail.com',
    url='http://github.com/Binstar/binstar_client',
    packages=find_packages(),
    install_requires=['anaconda-client',
                      'jinja2', 'psutil'],

    include_package_data=True,
    zip_safe=False,

    entry_points={
          'console_scripts': [
              'binstar-build = binstar_build_client.scripts.build:main',
              'binstar-worker = binstar_build_client.scripts.worker:main',
              'binstar-docker_worker = binstar_build_client.scripts.docker_worker:main',
              'conda-clean-build-dir = binstar_build_client.scripts.conda_clean_build_dir:main',
              ],
           'conda_server.subcommand': ['build = binstar_build_client.scripts.build:add_parser',
                                       'worker = binstar_build_client.scripts.worker:add_parser',
                                       'docker_worker = binstar_build_client.scripts.docker_worker:add_parser'],
                 },

)

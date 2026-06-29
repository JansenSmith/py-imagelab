""" Set up imagelab """
from glob import glob
from os.path import basename
from os.path import splitext

from setuptools import setup
from setuptools import find_packages

setup(
    name='py-imagelab',
    version='0.1.0',
    description='Image Lab',
    author='Dave MacGugan',
    author_email='dave@macgugan.net',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    py_modules=[splitext(basename(path))[0] for path in glob('src/*.py')],
    entry_points={
        "console_scripts": [
            "imagemutate=cli_scripts.imagemutate:run",
            "imagereplay=cli_scripts.imagereplay:run",
            "imagemerge=cli_scripts.imagemerge:run",
            "imagephase=cli_scripts.imagephase:run"
        ]
    },
    install_requires=[
        'numpy',
        'pygame',
        'opencv-python',
    ],
)

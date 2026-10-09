"""python -m sxm_anfatec : print the version and where this copy is."""
from pathlib import Path

from ._version import version

print(f'sxm_anfatec {version()}  ({Path(__file__).resolve().parent})')
print('Modules: bridge (read GUI), writer (set GUI), dde (DDE commands), driver (fast channel I/O), monitor (live view)')
print('Run e.g.: python -m sxm_anfatec.writer dnc   |   python -m sxm_anfatec.monitor')

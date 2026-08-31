"""Import the script under test.

`topoli_user.py` is a single-file tool at the repo root, not an installed
package, so the root goes on sys.path rather than the tool being restructured
into one for the tests' benefit.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

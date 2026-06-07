"""Entry point for running as a script and for PyInstaller/AppImage packaging."""
import sys
from launcher.__main__ import main

sys.exit(main())

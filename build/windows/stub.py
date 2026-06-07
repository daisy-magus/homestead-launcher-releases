"""
Thin Windows stub — compiled to Homestead.exe by PyInstaller (hidden_imports only,
no bundled code). Launches main.py via the embedded Python runtime.
This is the *only* PyInstaller artefact; all real code runs from plain .py files.
"""
import os
import subprocess
import sys
from pathlib import Path

app_dir = Path(sys.executable).parent
python  = app_dir / "python" / "python.exe"
script  = app_dir / "main.py"

os.environ["PYTHONPATH"] = str(app_dir)
sys.exit(subprocess.run([str(python), str(script)] + sys.argv[1:]).returncode)

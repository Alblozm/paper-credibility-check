"""CLI compatibility entry point; see README.md for input contracts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credibility_r2.cli import main
raise SystemExit(main(['extract-claims'] + sys.argv[1:]))

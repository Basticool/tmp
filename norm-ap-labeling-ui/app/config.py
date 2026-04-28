import os
from pathlib import Path

_APP_DIR = Path(__file__).parent   # norm-ap-labeling-ui/app/
_APP_ROOT = _APP_DIR.parent        # norm-ap-labeling-ui/

# Data source paths — override with env vars:
#   TRACES_PATH   path to non_compliant_traces.json (JSONL)
#   NORMS_PATH    path to combined_retail_norms.json
#   PROPS_PATH    path to atomic_propositions.json
DEFAULT_TRACES_PATH = os.environ.get(
    "TRACES_PATH",
    str(_APP_ROOT / "resources" / "non_compliant_traces_2.json"),
)
DEFAULT_NORMS_PATH = os.environ.get(
    "NORMS_PATH",
    str(_APP_ROOT / "resources" / "combined_retail_norms.json"),
)
DEFAULT_PROPS_PATH = os.environ.get(
    "PROPS_PATH",
    str(_APP_ROOT / "resources" / "atomic_propositions.json"),
)

# norm_compliance package lives inside the app root
NORM_COMPLIANCE_REPO = str(_APP_ROOT)

# Storage
RESOURCES_DIR = _APP_ROOT / "resources"
LABELS_DIR = RESOURCES_DIR / "labels"
JOBS_DIR = RESOURCES_DIR / "jobs"
USERS_FILE = RESOURCES_DIR / "users.jsonl"

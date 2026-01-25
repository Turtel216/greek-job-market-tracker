"""DAG tests. They need Airflow, so run them in the Airflow image with
`make test-dags`.
"""

import sys
from pathlib import Path

DAGS = Path(__file__).resolve().parents[2] / "dags"

# Airflow puts the dags folder on sys.path, which is how DAGs import common
sys.path.insert(0, str(DAGS))

import os
from pathlib import Path


def secret(name):
    file = os.getenv(name + '_FILE')
    return Path(file).read_text().strip() if file else os.environ[name]


SOURCE_REPO = 'frankhjwx/FruitsAtelier'
QA_MODEL = 'gpt-6-luna'
CODE_MODEL = 'gpt-6.1-sol'
GUILDS = {1479878482438918297, 1554882915945414726}
OWNER = 136638009691996160
JOB_CAP = 0.50
MONTH_CAP = 90.0  # Leave $10 for billing differences and manual diagnostics.
DEVELOPMENT_CAP = 80.0
QA_CAP = 10.0

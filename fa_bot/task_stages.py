"""Public progress vocabulary; runner reports never contain free-form task data."""
EDIT_STAGES = {
    'editing': 'Inspecting code and preparing the change',
    'waiting_validation': 'Waiting for Windows regression checks',
    'publishing': 'Regression checks passed; saving the verified change',
    'packaging': 'Regression checks passed; preparing your test build',
}
VALIDATION_STAGES = {
    'preparing_baseline': 'Preparing the original version for comparison',
    'baseline': 'Checking that the original version reproduces the issue',
    'preparing_fixed': 'Preparing the changed version',
    'fixed': 'Checking the changed version against the same test',
    'finished': 'Reviewing Windows validation results',
}

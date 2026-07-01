import sys
from pathlib import Path

# Ensure the repository root is importable so first-party ``dcc.*`` imports
# resolve regardless of how this script is launched (``python dcc/project_starter.py``
# or ``python -m dcc.project_starter``) and without an editable install or PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse

from dcc.utils.grid_poly_shape import GeoJSONSplitToCell
from dcc.project_setup.gadm_folder_creator import GADMFilesCreator


class ProjectStarter:

    def run(self):
        
        gadm_creator = GADMFilesCreator()
        result = gadm_creator.process()

        if result:
            country_code, version_idx, admin_scope = result
            splitter = GeoJSONSplitToCell(country_code, version_idx, subregion=admin_scope)
            splitter.grid_country()

if __name__ == '__main__':

    processor = ProjectStarter()
    processor.run()
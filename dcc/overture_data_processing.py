import os
import sys
from pathlib import Path

# Ensure the repository root is importable so first-party ``dcc.*`` imports
# resolve regardless of how this script is launched (``python dcc/overture_data_processing.py``
# or ``python -m dcc.overture_data_processing``) and without an editable install or PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
import argparse
import re

from dcc.data.download_clip_overture_v01 import OvertureDataDownloader
from dcc.data.grid_overture_data_v01 import TileAllOverture
from dcc.data.list_ot_raw_classes import AttributeCollector
from dcc.data.filter_overture_class import FilterAllOverture

class OvertureDataProcessor:

    def __init__(self, country_code, index):

        self.country_code = country_code
        self.index = index

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 

    """
    OvertureDataProcessor orchestrates the execution of data downloading and tiling processes
    for the Overture project in a specified order.

    The processing steps include:
    
    1. Generating administrative boundaries using GADM data.
    2. Splitting GeoJSON files into grid cells for processing.
    3. Downloading Overture and OpenStreetMap (OSM) data.
    4. Tiling the downloaded Overture data for efficient use.
    5. Collecting unique attribute combinations from Overture and OSM data.
    6. Filtering and mapping Overture/OSM classifications.
    """

    def run(self):

        # print(f"Archetype Filtering Enabled (classification.enable_overture_class_filtering): {self.config['classification']['enable_overture_class_filtering']}")
        print(f"\nSelected Class (classification.class): {self.config['classification']['class']}")
        print(f"Overture Dataset Version (overture_version): {self.config['overture_version']}")
        subset = self.config.get('subset')
        if subset:
            print(f"ADM scope enabled: {subset.get('name')} ({subset.get('level')})")
        print("Read (dcc/readme.md) to know more about the above mentioned attributes in the configuration.")
        print("\n")

        # Ask for user consent
        consent = input("Do you confirm these settings? (Y/n): ").strip().upper()
            
        if consent == 'Y':
            clipper = OvertureDataDownloader(self.country_code, self.index)
            clipper.processing_overture()

            tiled_data_overture = TileAllOverture(self.country_code, self.index)
            tiled_data_overture.overture_tiler()

            '''
            The below mentioned class calls is to collect all the unique combinations of overture attributes for each data type
            of interest (eg. Places, Buildings, Bases, etc.). This will generate files that is being used to create mapping
            between the overture data and it's classified type.
            '''

            attributeCollector = AttributeCollector(self.country_code, self.index)
            attributeCollector.run()

            ot_filter = FilterAllOverture(self.country_code, self.index)
            ot_filter.overture_filter()
        else:
            print("Please configure these attribute and rerun the file using the same command.")

if __name__ == '__main__':

    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='Process Overture data.')
    parser.add_argument('--config_name', required=True, help="The file name of the country's config.")

    # Parse arguments
    args = parser.parse_args()
    filename = args.config_name

    pattern = re.compile(r"^([a-zA-Z]+)_config_v(\d+)\.yml$")
    match = pattern.match(filename)
    if match:
        country_code = match.group(1)  # Extracts 'rwa'
        index = int(match.group(2))    # Extracts '0' and converts to int
    else:
        print("No file exists with this name.")

    processor = OvertureDataProcessor(country_code, index)
    processor.run()
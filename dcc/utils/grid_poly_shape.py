import os
import sys
from dotenv import load_dotenv, find_dotenv
import geopandas as gpd
import numpy as np
import yaml
import pandas as pd
import logging
import argparse
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

from dcc.utils.metadata_helper import MetadataLogger
from dcc.utils.subset_helper import resolve_subset_context, boundary_label

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class GeoJSONSplitToCell(MetadataLogger):
    def __init__(self, country_code, index, subregion=None):
        cache_root = os.environ.get("PROJECT_CACHE_METADATA")
        if not cache_root:
            raise ValueError("PROJECT_CACHE_METADATA environment variable is not set")
        # Load configuration
        self.country_code = country_code
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 

        self.project_data = os.environ.get("PROJECT_DATA")
        if not self.project_data:
            raise ValueError("PROJECT_DATA environment variable is not set")

        self.subregion = resolve_subset_context(self.config, subregion)
        metadata_folder = os.path.join(cache_root, country_code, self.__class__.__name__)
        if self.subregion:
            metadata_folder = os.path.join(cache_root, country_code, self.subregion['slug'], self.__class__.__name__)

        super().__init__(metadata_folder)

        self.country_cap_code = self.config['country_cap_code']
        self.boundary_name = boundary_label(self.config, self.subregion)

        # Set up file paths
        if self.subregion and self.subregion.get('grid_path'):
            self.input_file_path = self.subregion['grid_path']
        else:
            self.input_file_path = os.path.join(self.project_data, self.config['country'], f'gadm41_{self.country_cap_code}_grid.geojson')
        self.output_dir = os.path.join(self.project_data, f'{self.country_cap_code}_building_tiles', f'{self.boundary_name}_gridded', 'shapes')

    def split_geojson(self, start_index=40000):
            
        # Load the GeoJSON file into a GeoDataFrame
        gdf = gpd.read_file(self.input_file_path)

        # Create the output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)

        # Iterate through each row in the GeoDataFrame
        for idx, row in gdf.iterrows():
            # Construct the output file name using the four-digit ID
            output_file_name = f"{str(start_index)}_shape.geojson"
            output_file_path = os.path.join(self.output_dir, output_file_name)
            if not os.path.exists(output_file_path):
                # Create a new GeoDataFrame for the current feature
                feature_gdf = gpd.GeoDataFrame([row], columns=gdf.columns, crs=gdf.crs)

                # Save the current feature to a new GeoJSON file
                feature_gdf.to_file(output_file_path, driver='GeoJSON')
            start_index += 1

        logging.info(f"{gdf.shape[0]} GeoJSON files have been saved successfully.")

    def process(self):
        self.split_geojson()
        self.outputs[self.output_dir] = "Country's shape gridded"

    def grid_country(self):
        if self.outputs_exist():
            print("All Country's grid exist.")
            return
        self.process()
        self.save_metadata()

# Usage
if __name__ == "__main__":

    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='Grid country shape.')
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

    splitter = GeoJSONSplitToCell(country_code, index)
    splitter.grid_country()

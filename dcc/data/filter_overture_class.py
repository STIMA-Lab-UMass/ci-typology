import os
import sys
import geopandas as gpd
from dotenv import load_dotenv, find_dotenv
import yaml
from multiprocessing import Pool, current_process
import logging
import argparse
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(thread)d - %(levelname)s - %(message)s')

import dcc.utils.utils_vector as utils_vector
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_tiled_root


class FilterAllOverture():

    """
    A class to filter and process Overture data based on specific archetypes.
    
    Attributes:
    -----------
    country_code : str
        The country code for which the data is being processed.
    config : dict
        Configuration settings loaded from a YAML file specific to the country.
    boundary_name : str
        The country name.
    class_type : str
        The classification type for filtering.
    """

    def __init__(self, country_code, index):

        """
        Initializes the FilterAllOverture class with country-specific settings.
        
        Parameters:
        -----------
        country_code : str
            The country code for which the data is being processed.
        """

        self.country_code = country_code

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.boundary_cap_code = self.config['country_cap_code']
        self.subregion = resolve_subset_context(self.config)
        self.boundary_name = boundary_label(self.config, self.subregion)
        self.boundary = self.config['country']
        self.project_data = os.environ.get("PROJECT_DATA")
        self.class_type = self.config['classification']['class']
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        
        # self.enable_overture_class_filtering = self.config['classification']['enable_overture_class_filtering']


    def filter_class(self, class_type, poi_gdf, output_file_path):

        """
        Filters the given GeoDataFrame based on the specified class type and saves it as a Parquet file.

        Parameters:
        -----------
        class_type : str
            The classification type to filter.
        poi_gdf : GeoDataFrame
            The input data containing points of interest.
        output_file_path : str
            The file path to save the filtered data.
        """

        poi_gdf = poi_gdf[(poi_gdf['subtype'].str.contains(class_type, na=False)) | (poi_gdf['class'].str.contains(class_type, na=False))]
        poi_gdf.reset_index(drop=True, inplace=True)
        if not poi_gdf.empty:
            logging.info(f'Filterd File size: {poi_gdf.shape}')
            poi_gdf.to_parquet(output_file_path)       


    def bldg_filter(self, file):
        # for file in files:
        if file.endswith(".parquet"):
            #get the first name 
            output_file = os.path.join(self.tiled_output,'bldgs', self.class_type,file)

            if not os.path.exists(output_file):
                # Read the geojson POI file
                logging.info(f'Filtering file: {file}')
                poi_gdf = gpd.read_parquet(os.path.join(self.bldgs_path, file))

                column = ['subtype', 'class']
                for item in column:
                    if item not in poi_gdf.columns:
                        poi_gdf[item] = None
                if not poi_gdf.empty:
                    self.filter_class(self.class_type, poi_gdf, output_file)


    def bldg_filtering(self):

        files = os.listdir(self.bldgs_path)
        os.makedirs(os.path.join(self.tiled_output,'bldgs',self.class_type), exist_ok=True)
        with Pool(os.cpu_count()) as pool:
            pool.map(self.bldg_filter, files)
        
        logging.info("Finished Filtering for building!")

    def poi_filter(self, file):

        if file.endswith(".parquet"):
            output_tile = os.path.join(self.tiled_output,'places',self.class_type,file)

            if not os.path.exists(output_tile):
                # Read the geojson POI file
                poi_gdf = gpd.read_parquet(os.path.join(self.places_path, file))
                if 'categories_alternate' in poi_gdf.columns:
                    poi_gdf['categories_alternate'] = poi_gdf['categories_alternate'].apply(lambda x: str(x) if x is not None else x)
                    
                poi_gdf = poi_gdf[(poi_gdf['categories_main'].str.contains(self.class_type, na=False)) | (poi_gdf['categories_alternate'].str.contains(self.class_type, na=False))]
                if not poi_gdf.empty:
                    logging.info(f'Filterd File size: {poi_gdf.shape}')
                    poi_gdf.reset_index(drop=True, inplace=True)
                    poi_gdf.to_parquet(output_tile)


    def poi_filtering(self):

        os.makedirs(os.path.join(self.tiled_output,'places',self.class_type), exist_ok=True)
        files = os.listdir(self.places_path)
        with Pool(os.cpu_count()) as pool:
            pool.map(self.poi_filter, files)

        logging.info("Finished Filtering for places!")


    def base_filter(self, file):

        if file.endswith(".parquet"):

            output_file = os.path.join(self.tiled_output,'bases_poly',self.class_type,file)

            if not os.path.exists(output_file):
                # Separate the polygons and points into different GeoDataFrames
                poi_gdf = gpd.read_parquet(os.path.join(self.bases_poly_path, file))

                if not poi_gdf.empty:
                    column = ['subtype', 'class']
                    for item in column:
                        if item not in poi_gdf.columns:
                            poi_gdf[item] = None
                    self.filter_class(self.class_type, poi_gdf, output_file)


    def base_poly_filtering(self):
        os.makedirs(os.path.join(self.tiled_output,'bases_poly',self.class_type), exist_ok=True)
        files = os.listdir(self.bases_poly_path)
        with Pool(os.cpu_count()) as pool:
            pool.map(self.base_filter, files)

        logging.info("Finished Filtering for bases!")
    

    def get_paths(self, data_source):
        self.tiled_output = scoped_tiled_root(self.config, self.version, self.subregion)
        self.bldgs_path = os.path.join(self.tiled_output, 'bldgs', 'clipped')
        self.places_path = os.path.join(self.tiled_output, 'places', 'clipped')
        self.bases_poly_path = os.path.join(self.tiled_output, 'bases_poly', 'clipped')

    def process(self):

        """
        Executes the filtering process for buildings, places, and bases for both Overture data.
        """

        self.get_paths('overture')
        self.bldg_filtering()
        self.poi_filtering()
        self.base_poly_filtering()

    
    def overture_filter(self):

        """
        Initiates the filtering process if archetype filtering is enabled.
        """

        if self.class_type != 'all': 
            logging.info(f'Filtering Overture data for the {self.class_type} archetype.')   
            self.process()
        else:
            logging.info('Filtering on the basis of Overture class is disabled. Hence, no filtered files wil be generated for the archetype.')            
    
if __name__ == '__main__':

    """
    Main execution block to process command-line arguments and initiate filtering.
    """

    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='Filter Overture data according to the archetype.')
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

    ot_filter = FilterAllOverture(country_code, index)
    ot_filter.overture_filter()
import os
import sys
import duckdb
from dotenv import load_dotenv, find_dotenv
import yaml
from multiprocessing import Pool, current_process, Manager, Lock
import logging
import time
import argparse
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(thread)d - %(levelname)s - %(message)s')

from dcc.utils.overture_version import resolve_overture_version
import dcc.utils.utils_vector as utils_vector
from dcc.utils.metadata_helper import MetadataLogger
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_tiled_root

'''
    This function downloads overture data by executing a command and download 
    County's overture data and saves it to specific directory. It first ensures the
    target directory exists, constructs the file paths, and then execute a command to 
    select and save the filtered overture data into a new geojson file.
'''
class OvertureDataDownloader(MetadataLogger):
    def __init__(self, country_code, index):
        super().__init__(os.path.join(os.environ.get("PROJECT_CACHE_METADATA"), country_code, self.__class__.__name__))
        self.project_data = os.environ.get("PROJECT_DATA")
        self.country_code = country_code

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 

        self.country_cap_code = self.config['country_cap_code']
        self.country_shp = self.config['country_shp']
        self.boundary_cap_code = self.config['country_cap_code']
        self.subregion = resolve_subset_context(self.config)
        self.boundary_name = boundary_label(self.config, self.subregion)
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version


        self.gridded_shapes = os.path.join(self.project_data, f'{self.boundary_cap_code}_building_tiles', f'{self.boundary_name}_gridded', 'shapes')
        if self.subregion and self.subregion.get('boundary_path'):
            self.country_shape = self.subregion['boundary_path']
        else:
            self.country_shape = os.path.join(self.project_data, self.config['country'], 'regions', f'gadm41_{self.country_cap_code}', f'gadm41_{self.country_shp}.json')
        self.output_directory = scoped_tiled_root(self.config, self.version, self.subregion)

        os.makedirs(self.output_directory,  exist_ok=True)

    def get_query(self, theme, data_type, output_file, bbox):

        """
        Constructs the SQL query to download Overture data based on the bounding box.
        
        Parameters:
        -----------
        theme : str
            The theme of the data (e.g., 'places', 'buildings').
        data_type : str
            The specific type within the theme (e.g., 'building', 'place').
        output_file : str
            The path where the output data will be saved.
        bbox : tuple
            The bounding box coordinates (lon_left, lat_bottom, lon_right, lat_top).
        
        Returns:
        --------
        str
            The constructed SQL query.
        """

        lon_left, lat_bottom, lon_right, lat_top = bbox

        if theme == 'places':
            
            extra_query_category = """,
                CASE
                    WHEN categories IS NOT NULL THEN categories.primary
                    ELSE NULL
                END AS categories_main,
                CASE
                    WHEN categories IS NOT NULL THEN categories.alternate
                    ELSE NULL
                END AS categories_alternate                
            """
        else:
            extra_query_category = ""

        extra_query_name = """,
            CASE
                WHEN names IS NOT NULL THEN names.primary
                ELSE NULL
            END AS names_primary,
            CASE
                WHEN names IS NOT NULL THEN names.common
                ELSE NULL
            END AS names_common
        """

        sql_query = f"""
        INSTALL spatial;
        INSTALL httpfs;

        LOAD httpfs;
        LOAD spatial;

        SET s3_region='us-west-2';

        COPY (
            SELECT
                *{extra_query_name}{extra_query_category}
            FROM
                read_parquet('s3://overturemaps-us-west-2/release/{self.version}/theme={theme}/type={data_type}/*', filename=true, hive_partitioning=1)
            WHERE
                bbox.xmin > {lon_left}
                AND bbox.xmax < {lon_right}
                AND bbox.ymin > {lat_bottom}
                AND bbox.ymax < {lat_top}
            ) TO '{output_file}'
        """
        return sql_query

    def send_query_to_download(self, sql_query, output_file):

        # logging.info(f'Downloading: {output_file}')
        max_retries = 3
        attempt = 0

        while attempt < max_retries:
            try:
                duckdb.sql(sql_query)
                # logging.info(f"Query executed successfully. Data copied to {output_file}")
                break  # Exit the loop if the query is successful
            except Exception as e:
                attempt += 1
                time.sleep(30)
                logging.error(f"Attempt {attempt} failed. Error: {e}")
                if attempt == max_retries:
                    logging.error("Max retries reached. Query execution failed.")


    def re_download_corrupt_file(self, file, theme, data_type, suffix, file_path):

        filename = os.path.join(self.gridded_shapes, file)
        file_id = file.split("_shape.geojson")[0]
        lat_top, lat_bottom, lon_left, lon_right = utils_vector.get_bbox(filename)
        bbox = (lon_left, lat_bottom, lon_right, lat_top)

        output_file = os.path.join(os.path.join(self.output_directory, file_path , 'bbox'), f'{file_id}_{suffix}.parquet')   

        sql_query = self.get_query(theme, data_type, output_file, bbox)
        self.send_query_to_download(sql_query, output_file)     

    def download_overture_data(self, file, counter, lock):

        """
        Download the all kind of overture data by constructing and executing a command.
        :return: None
        
        """
        filename = os.path.join(self.gridded_shapes, file)
        file_id = file.split("_shape.geojson")[0]
        lat_top, lat_bottom, lon_left, lon_right = utils_vector.get_bbox(filename)
        bbox = (lon_left, lat_bottom, lon_right, lat_top)

        themes = ['buildings', 'places', 'base', 'base', 'base']
        types = ['building', 'place', 'infrastructure', 'land_use', 'water']
        file_suffix = ['bldg', 'poi', 'base_infra', 'base_land_use', 'base_water']
        output_file_path = ['bldgs', 'places', 'bases', 'bases', 'bases']

        for theme, data_type, suffix, file_path in zip(themes, types, file_suffix, output_file_path):   
            output_file = os.path.join(os.path.join(self.output_directory, file_path , 'bbox'), f'{file_id}_{suffix}.parquet')    

            sql_query = self.get_query(theme, data_type, output_file, bbox)
            
            if not os.path.exists(output_file):
                self.send_query_to_download(sql_query, output_file)  

        with lock:
            counter.value += 1   


    def download_overture(self):
        """
        Initiates the process of downloading Overture data in parallel using multiprocessing.
        """
        file_list = os.listdir(self.gridded_shapes)

        output_file_path = ['bldgs', 'places', 'bases']
        for items in output_file_path:
            os.makedirs(os.path.join(self.output_directory, items, 'bbox'),  exist_ok=True)

        num_workers = min(os.cpu_count(), len(file_list))

        with Manager() as manager:
            counter = manager.Value('i', 0)  # Shared integer counter
            lock = manager.Lock() 

            with Pool(num_workers) as pool:
                results = [pool.apply_async(self.download_overture_data, (file, counter, lock)) for file in file_list]

                # Progress tracking loop
                while counter.value < len(file_list):
                    print(f"Progress: {counter.value}/{len(file_list)} files downloaded", end="\r")
                    time.sleep(0.5)  # Adjust update interval

                pool.close()  # Prevent any more tasks from being added
                pool.join() 

        # with Pool(processes = os.cpu_count()) as pool:
        #     results = pool.map(self.download_overture_data, file_list)
            
    def process(self):
        self.download_overture()

        self.outputs[self.output_directory] = "Overture data downloaded"

    def processing_overture(self):
        if self.outputs_exist():
            print("All Overture outputs exist.")
            return
        self.process()
        self.save_metadata()

if __name__ == '__main__':

    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='Download Overture data.')
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

    clipper = OvertureDataDownloader(country_code, index)
    clipper.processing_overture()


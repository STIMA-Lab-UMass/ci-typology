import os
import sys
import pandas as pd
import geopandas as gpd
from dotenv import load_dotenv, find_dotenv
import yaml
from multiprocessing import Pool, Lock, current_process, active_children
import logging
import argparse
import re
from tqdm.contrib.concurrent import process_map

lock = Lock()

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, scoped_output_root, scoped_tiled_root

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(thread)d - %(levelname)s - %(message)s')

class AttributeCollector():
    
    def __init__(self, country_code, index):

        self.country_code = country_code

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 

        self.class_type = self.config['classification']['class']
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        self.subregion = resolve_subset_context(self.config)
        self.tiled_root = scoped_tiled_root(self.config, self.version, self.subregion)
        # self.enable_overture_class_filtering = self.config['classification']['enable_overture_class_filtering']          
        self.output_path = os.path.join(scoped_output_root(self.config, self.version, self.subregion), 'combined_classes')
        os.makedirs(os.path.join(self.output_path), exist_ok=True)

    def write_attributes_to_file(self, file_tuple):
        file, overture_file_path, id_list, combined_path, file_ex, data_type, admin_columns, columns = file_tuple
        file_path = os.path.join(overture_file_path, file)
        file_name = os.path.basename(file_path)
        file_id = file_name.split(file_ex)[0]

        if (int(file_id) not in id_list):
            # logging.info(f'Reading File: {file_name}')
            id_list.append(int(file_id))

            gdf = gpd.read_parquet(file_path)
            if gdf.shape[0] != 0:
                if 'categories_alternate' in gdf.columns:
                    gdf['categories_alternate'] = gdf['categories_alternate'].apply(lambda x: str(x) if x is not None else x)

                missing_columns = [col for col in columns + admin_columns if col not in gdf.columns]
                for col in missing_columns:
                    gdf[col] = None
                selected_data = gdf[columns + admin_columns].copy()
                selected_data.insert(0, 'file_id', file_id)
                unique_selected_data = selected_data.drop_duplicates(subset=columns, keep='first')

                # Acquire lock before writing to the CSV file
                with lock:
                    with_header = not os.path.isfile(combined_path)
                    unique_selected_data.to_csv(combined_path, mode='a', index=False, header=with_header)
            
    def process_overture(self, data_tuple):

        data_type, suffix = data_tuple
        """
        Collect class attributes from geojson files, then combine them into single datasets for further processing.
        This method will go through all geojson files, extract and select classes columns from overture, handle missing data, and
        merge them into a consolidated CSV file. It also extracts unique class combinations to a separate CSV.
        """
        overture_file_path = os.path.join(self.tiled_root, data_type, 'clipped')

        geojson_files = [f for f in os.listdir(overture_file_path) if f.endswith(f'_{suffix}.parquet')] 
        file_ex = f'_{suffix}.parquet'

        combined_path = os.path.join(self.output_path, f'all_{data_type}_classes.csv')
        if os.path.exists(combined_path):
            combined_df = pd.read_csv(combined_path)
            id_list = combined_df['file_id'].values.tolist()
        else:
            id_list = []

        admin_columns = self.config['admin_columns']
        if data_type == 'places':
            columns = ['names_primary','categories_main', 'categories_alternate']
        else:
            columns = ['names_primary', 'subtype', 'class']    
                
        # Prepare tuples for each file
        file_tuples = [(file, overture_file_path, id_list, combined_path, file_ex, data_type, admin_columns, columns) for file in geojson_files]

        try:
            process_map(self.write_attributes_to_file, file_tuples, max_workers=os.cpu_count(), desc="Listing Overture Data", unit="file")
        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Exiting gracefully...")
            for child in active_children():
                child.terminate()
            for child in active_children():
                child.join()
            os._exit(1)

        # Parallelize file processing
        # with Pool(processes=os.cpu_count()) as pool:
        #     pool.map(self.write_attributes_to_file, file_tuples)  
            
        unique_classes_path = os.path.join(self.output_path, f'unique_all_{data_type}_classes.csv')
        all_classes = pd.read_csv(combined_path, low_memory=False)
        
        unique_classes = all_classes.drop_duplicates(subset=columns, keep='first')
        unique_classes = unique_classes.copy()
        unique_classes.drop(columns=['file_id'], inplace=True, axis=1)
        unique_classes.to_csv(unique_classes_path, header = True, index=False)


    def collect_attributes(self):

        data_types = ['bases_poly', 'bldgs', 'places']
        suffix = ['base_poly', 'bldg', 'poi']
        data_zip = zip(data_types, suffix)

        for data_type_tuple in data_zip:
            self.process_overture(data_type_tuple)     

    def filter_overture_classes(self, data_type):

        data = pd.read_csv(os.path.join(self.output_path, f'unique_all_{data_type}_classes.csv'))
        if not os.path.exists(os.path.join(self.output_path, f'unique_{self.class_type}_{data_type}_classes.csv')):
            if data_type == 'places':
                data_filtered = data[(data['categories_main'].str.contains(self.class_type, na=False)) | (data['categories_alternate'].str.contains(self.class_type, na=False))]
            else:
                data_filtered = data[(data['subtype'].str.contains(self.class_type, na=False)) | (data['class'].str.contains(self.class_type, na=False))]
            
            data_filtered.to_csv(os.path.join(self.output_path, f'unique_{self.class_type}_{data_type}_classes.csv'), index=False)


    def filter_attributes(self):

        data_types = ['bases_poly', 'bldgs', 'places']
        num_workers = len(data_types)
        try:
            process_map(self.filter_overture_classes, data_types, max_workers=os.cpu_count(), desc="Filtering Overture Data", unit="Overture data types")
        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Exiting gracefully...")
            for child in active_children():
                child.terminate()
            for child in active_children():
                child.join()
            os._exit(1)

        # with Pool(num_workers) as pool:
        #     pool.map(self.filter_overture_classes, data_types)  

    def run(self):
        self.collect_attributes()

        if self.class_type != 'all':
            self.filter_attributes()

if __name__ == '__main__':
    # Pass the parsed argument to the class
    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='List Overture text data into CSV.')
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

    attributeCollector = AttributeCollector(country_code, index)    
    attributeCollector.run()
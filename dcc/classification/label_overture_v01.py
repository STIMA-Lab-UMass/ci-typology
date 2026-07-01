import os
import sys
import numpy as np
import pandas as pd
import geopandas as gpd
from dotenv import load_dotenv, find_dotenv
import yaml
from multiprocessing import Pool, current_process
import argparse
import re
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_tiled_root, scoped_output_root

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

class LabelOverture():

    """
    Class to label Overture data using NAICS classification.

    Attributes:
        country_code (str): Country code for the data (e.g., "usa").
        config (dict): Configuration settings loaded from YAML file.
        tiled_output (str): Output directory for tiled Overture data.
        six_naics_classification (bool): Flag for 6-digit NAICS classification.
        class_type (str): Type of classification to apply.
    """

    def __init__(self, country_code, index):

        """
        Initializes the labeling class with the country configuration.

        Args:
            country_code (str): The country code for the data.
        """

        self.country_code = country_code

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
        self.boundary_cap_code = self.config['country_cap_code']
        self.subregion = resolve_subset_context(self.config)
        self.boundary_name = boundary_label(self.config, self.subregion)
        self.boundary = self.config['country']
        self.project_data = os.environ.get("PROJECT_DATA")
        self.six_naics_classification = self.config['classification']['enable_6_digit_naics_classification']
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version

        if self.six_naics_classification:
            self.six_naics = '_naics_6'
        else:
            self.six_naics = ''

        self.class_type = self.config['classification']['class']
        self.tiled_output = scoped_tiled_root(self.config, self.version, self.subregion)
        self.output_root = scoped_output_root(self.config, self.version, self.subregion)

        os.makedirs(self.tiled_output, exist_ok=True)


    def overture_labeling(self, file):

        """
        Labels Overture data with NAICS classification.

        Args:
            file (str): The parquet file to process.
        """

        if file.endswith(".parquet"):

            # print('Reading File ', file)
            output_tile = os.path.join(self.tiled_output, self.data_type,f'{self.class_type}_labeled',file)

            if not os.path.exists(output_tile):

                # Read the GeoJSONSeq POI file
                poi_gdf = gpd.read_parquet(os.path.join(self.overeture_input_path, file))
                mapping = pd.read_csv(os.path.join(self.output_root, 'combined_classes', f'{self.data_type}_{self.class_type}{self.six_naics}_classified.csv'), low_memory=False)

                if 'is_residential' not in mapping.columns:
                    mapping['is_residential'] = False

                # if self.two_naics_classification:
                #     overture_class_mapping = pd.read_csv(os.path.join(os.environ.get('PROJECT_OUT'), 'add_'+self.config['country'], 'combined_classes', f'{self.class_type}_overture_naics_2_classified.csv'), low_memory=False)
                #     df_places = overture_class_mapping[(overture_class_mapping['categories_main'] != '') | (overture_class_mapping['categories_alternate'] != '')]
                #     df_not_places = overture_class_mapping[~((overture_class_mapping['categories_main'] != '') | (overture_class_mapping['categories_alternate'] != ''))]
                    
                if self.six_naics_classification:
                    naics_6_columns = ['sub_classification', 'naics_six_code']
                else:
                    naics_6_columns = []

                poi_gdf = poi_gdf[self.config[f"overture_{self.data_type}_columns"]].copy()

                if self.data_type == 'places':
                    poi_gdf['categories_alternate_org'] = poi_gdf['categories_alternate']
                    poi_gdf['categories_alternate'] = poi_gdf['categories_alternate'].apply(lambda x: str(x) if x is not None else x)
                    
                    poi_gdf = poi_gdf.merge(
                        mapping[['names_primary', 'categories_main', 'categories_alternate', 'classification', 'naics_code', 'is_residential'] + naics_6_columns],
                        on=['names_primary', 'categories_main', 'categories_alternate'],  how='left'
                    )
                    # if self.two_naics_classification:       
                    #     df_places['categories_alternate'] = df_places['categories_alternate'].apply(lambda x: str(x) if isinstance(x, (list, np.ndarray)) else x)
                    #     poi_gdf = poi_gdf.merge(df_places, on=['categories_main','categories_alternate'], how='left')
                    #     poi_gdf.drop(columns = ['class', 'subtype'], inplace=True)

                    poi_gdf['categories_alternate'] = poi_gdf['categories_alternate_org']
                    poi_gdf.drop(columns=['categories_alternate_org'], inplace=True)
                else:
                    poi_gdf = poi_gdf.merge(mapping[['names_primary', 'subtype', 'class', 'classification', 'naics_code', 'is_residential'] + naics_6_columns],
                                            on=['names_primary', 'subtype', 'class'], how='left')
                    # if self.two_naics_classification: 
                    #     poi_gdf = poi_gdf.merge(df_not_places, on=['class', 'subtype'], how = 'left')
                    #     poi_gdf.drop(columns = ['categories_main','categories_alternate'], inplace=True)
  
                poi_gdf['naics_code'] = poi_gdf['naics_code'].apply(lambda x: str(int(x)) if not pd.isnull(x) else None)
                poi_gdf.rename(columns={'classification': 'naics_4_classification'}, inplace=True)
                poi_gdf.rename(columns={'naics_code': 'NAICS_4'}, inplace=True)

                # if self.two_naics_classification:

                #     naics_4_mask = ~poi_gdf['NAICS_4'].isna()
                #     # Extract the first 2 digits from 'NAICS_4' for rows where the mask is True
                #     poi_gdf.loc[naics_4_mask, 'NAICS_2'] = poi_gdf.loc[naics_4_mask, 'NAICS_4'].astype(str).str[:2].astype(int)

                #     # Map the NAICS_2 dictionary to create 'naics_2_classification' for the same rows
                #     nacis_2_dic = self.config['classification']['naics_dict_2']
                #     poi_gdf.loc[naics_4_mask, 'naics_2_classification'] = poi_gdf.loc[naics_4_mask, 'NAICS_2'].map(nacis_2_dic)
                #     poi_gdf['NAICS_2'] = poi_gdf['NAICS_2'].apply(lambda x: str(int(x)) if not pd.isnull(x) else None)

                if self.six_naics_classification:
                    poi_gdf['naics_six_code'] = poi_gdf['naics_six_code'].apply(lambda x: str(int(x)) if not pd.isnull(x) else None)
                    poi_gdf.rename(columns={'sub_classification': 'naics_6_classification'}, inplace=True)
                    poi_gdf.rename(columns={'naics_six_code': 'NAICS_6'}, inplace=True)
                poi_gdf.to_parquet(output_tile)


    def overture_labeler(self):

        """
        Orchestrates the labeling of both Overture data using multiprocessing.
        """
        
        data_types = ['bldgs', 'places', 'bases_poly']

        if self.class_type == 'all':
            input_folder = 'clipped'
        else:
            input_folder = self.class_type

        for data_type in data_types:
            print(f"Labeling {data_type} with custom categories.")
            self.overeture_input_path = os.path.join(self.tiled_output, data_type, input_folder)
            files = os.listdir(os.path.join(self.overeture_input_path))
            os.makedirs(os.path.join(self.tiled_output, data_type, f'{self.class_type}_labeled'), exist_ok=True)
            num_workers = os.cpu_count()  # Desired number of workers
            self.data_type = data_type
            with Pool(num_workers) as pool:
                pool.map(self.overture_labeling, files)

            print(f"Finished labeling {self.data_type}!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Label Overture data with the naics code.')
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

    overture_labeler = LabelOverture(country_code, index)
    overture_labeler.overture_labeler()
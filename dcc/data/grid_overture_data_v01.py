import os
import sys
import pandas as pd
import geopandas as gpd
from functools import partial
from dotenv import load_dotenv, find_dotenv
import yaml
from multiprocessing import Pool, current_process
import logging
import argparse
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

import dcc.utils.utils_vector as utils_vector
from dcc.data.download_clip_overture_v01 import OvertureDataDownloader
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_tiled_root

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')

"""
TileAllOverture is a class designed to handle the clipping of the overture data downloaded for each of the country's grids in a bbox.
This class will clip out the extra data that are not within the boundaries of the country. It also simplifies few of the nested attributes
used in the geojson.
"""

class TileAllOverture():

    def __init__(self, country_code, index):

        self.country_code = country_code
        self.index = index

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.boundary_cap_code = self.config['country_cap_code']
        self.subregion = resolve_subset_context(self.config)
        self.boundary_name = boundary_label(self.config, self.subregion)
        self.boundary = self.config['country']
        project_data = os.environ.get("PROJECT_DATA")
        sub_admin_boundary = self.config['sub_admin_boundary']
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version


        self.gridded_shapes = os.path.join(project_data , f'{self.boundary_cap_code}_building_tiles', f'{self.boundary_name}_gridded', 'shapes')
        gadm_dir = os.path.join(project_data , self.boundary, 'regions', f"gadm41_{self.boundary_cap_code}")
        json_path = os.path.join(gadm_dir, f'gadm41_{sub_admin_boundary}.json')
        gpkg_path = os.path.join(gadm_dir, f'gadm41_{sub_admin_boundary}.gpkg')
        if os.path.exists(json_path):
            self.gadm_sub_admin = json_path
        elif os.path.exists(gpkg_path):
            self.gadm_sub_admin = gpkg_path
        else:
            raise FileNotFoundError(
                f"Could not find sub-admin boundary file for {sub_admin_boundary} "
                f"in either JSON or GPKG format under {gadm_dir}."
            )
        self.tiled_output = scoped_tiled_root(self.config, self.version, self.subregion)

        self.buildings_overture = os.path.join(self.tiled_output, 'bldgs', 'bbox')
        self.places_overture = os.path.join(self.tiled_output, 'places', 'bbox')
        self.bases_overture = os.path.join(self.tiled_output, 'bases', 'bbox')

        self.admin_columns = self.config['admin_columns']

        os.makedirs(self.tiled_output, exist_ok=True)
        os.makedirs(os.path.join(self.tiled_output,'bases_poi','clipped'), exist_ok=True)
        os.makedirs(os.path.join(self.tiled_output,'bases_poly','clipped'), exist_ok=True)

    def _needs_reclip(self, parquet_path):
        """
        Determines whether an existing clipped parquet needs to be regenerated.
        We reclip if the file is missing, unreadable, missing admin columns, or
        every admin column is blank, because boundary filtering relies on these values.
        """
        if not self.admin_columns:
            return False

        if not os.path.exists(parquet_path):
            return True

        try:
            gdf = gpd.read_parquet(parquet_path, columns=self.admin_columns)
        except Exception:
            return True

        if any(col not in gdf.columns for col in self.admin_columns):
            return True

        for col in self.admin_columns:
            series = gdf[col]
            if series.notna().any():
                if series.dropna().astype(str).str.strip().ne('').any():
                    return False
        return True

    def clipping_overture(self, country_shape, poi_gdf, output_clipfile):
        """
        Clips the input POI data to the boundary of the country and saves the result.

        Parameters
        ----------
        country_shape : str
            The path to the country boundary shape file.
        poi_gdf : GeoDataFrame
            The GeoDataFrame containing the POI data to be clipped.
        output_clipfile : str
            The path where the clipped GeoDataFrame should be saved.
        """
        boundary_data = gpd.read_file(country_shape)
        boundary_data = boundary_data.reset_index(drop=True)

        # Check if the file is empty
        if len(poi_gdf) == 0:
            logging.debug("The file is empty")
            return

        # Filter POIs that lie inside the boundary
        poi_gdf = gpd.overlay(poi_gdf, boundary_data, how='intersection', keep_geom_type=False)
        if not poi_gdf.empty:
            
            remove_list = self.config['admin_columns']
            poi_gdf.rename(columns={col: f"overture_{col}" for col in remove_list if col in poi_gdf.columns}, inplace=True)

            columns = remove_list + ['geometry']
            if os.path.exists(self.gadm_sub_admin):
                admin_bound = gpd.read_file(self.gadm_sub_admin)[columns]

                poi_gdf = gpd.sjoin(poi_gdf, admin_bound, how = 'left')
                poi_gdf.drop(columns=['index_left', 'index_right'], inplace=True, errors='ignore')

                # The code below drops all duplicate POIs that might have been on the boundary and exist in both tertiary regions.
                poi_gdf = poi_gdf.drop_duplicates(subset=[self.config['overture_id_column'], 'geometry'], keep='first')
                poi_gdf = poi_gdf.reset_index(drop=True)

            poi_gdf.to_parquet(output_clipfile)


    def bldg_and_poi_tile(self, file, theme, data_type, suffix, folder_path, max_tries=3):
        # for file in files:
        if file.endswith(".geojson"):

            #get the first name 
            file_id = file.split("_shape.geojson")[0]
            fl_name = file_id+f'_{suffix}.parquet'
            output_tile = os.path.join(self.tiled_output, folder_path,'clipped',fl_name)

            if self._needs_reclip(output_tile):
                if os.path.exists(output_tile):
                    os.remove(output_tile)
                # Read the GeoJSONSeq POI file
                logging.debug(f'Clipping file: {fl_name}')
                try:
                    input_file = os.path.join(getattr(self, f"{theme}_overture"), fl_name)
                    
                    poi_gdf = gpd.read_parquet(input_file)
                    poi_gdf = poi_gdf.to_crs(epsg=4326)
                    poi_gdf['geometry'] = poi_gdf['geometry'].centroid
                    self.clipping_overture(os.path.join(self.gridded_shapes,file), poi_gdf, output_tile)
                    
                except (FileNotFoundError, IOError, ValueError) as e:
                    print(f"Error opening file with GeoPandas: {e}")
                    print(file_id)
                    if max_tries>0:
                        overture_downloader = OvertureDataDownloader(self.country_code, self.index)
                        overture_downloader.re_download_corrupt_file(file, theme, data_type, suffix, folder_path)
                        self.bldg_and_poi_tile(file, theme, data_type, suffix, folder_path, max_tries-1)


    def bldg_and_poi_tiling(self):
        """
        This method is used to tile the buildings within the boundary of the country_shapes.
        It checks if the file is empty, reads the GeoJSONSeq building file, and filters the buildings
        that lie inside the boundary. The filtered buildings are then written to the output_tilefile.
        """
        data_types = ['building', 'place']
        file_suffix = ['bldg', 'poi']
        themes = ['buildings', 'places']
        folder_paths = ['bldgs', 'places']

        files = os.listdir(self.gridded_shapes)

        for data_type, suffix, theme, folder_path in zip(data_types, file_suffix, themes, folder_paths):

            os.makedirs(os.path.join(self.tiled_output, folder_path,'clipped'), exist_ok=True)
            num_workers = os.cpu_count()  # Desired number of workers

            logging.debug(f"Clipping {theme} files.")
            max_tries = 3

            func = partial(self.bldg_and_poi_tile, theme=theme, data_type=data_type, suffix=suffix, folder_path=folder_path, max_tries=max_tries)
            with Pool(num_workers) as pool:
                pool.map(func, files)

            logging.info(f"Finished tiling and clipping for {theme}!")


    def base_tile(self, file, max_tries=3):
        """
        This method is used to tile the base polygons within the boundary of the country_shapes.
        It creates a directory for the clipped base files if it doesn't exist. It first merge all the different kind of bases
        that are infrastructure, land and water. It also extract polygon and points type geometry from the geojson file.
        If the output file doesn't exist, it clips the bases data and saves it to the output directory.
        """
        if file.endswith(".geojson"):

            #get the first name 
            file_id = file.split("_shape.geojson")[0]

            # Read the GeoJSON files into GeoDataFrames
            flag = 0
            try:
                gdf_infra = gpd.read_parquet(os.path.join(self.bases_overture, f'{file_id}_base_infra.parquet'))
                flag += 1
                gdf_land = gpd.read_parquet(os.path.join(self.bases_overture, f'{file_id}_base_land_use.parquet'))
                flag += 1
                gdf_water = gpd.read_parquet(os.path.join(self.bases_overture, f'{file_id}_base_water.parquet'))
                flag += 1
                # Concatenate the GeoDataFrames
                gdf_combined = gpd.GeoDataFrame(pd.concat([gdf_infra, gdf_land, gdf_water], ignore_index=True))

                # logging.info(f'Concated GDF shape: {gdf_combined.shape} , Infra: {gdf_infra.shape}, Land: {gdf_land.shape}, Water: {gdf_water.shape}')

                output_tile_poi = os.path.join(self.tiled_output,'bases_poi','clipped',file_id+'_base_poi.parquet')
                output_tile_polygon = os.path.join(self.tiled_output,'bases_poly','clipped',file_id+'_base_poly.parquet')

                needs_poi = self._needs_reclip(output_tile_poi)
                needs_poly = self._needs_reclip(output_tile_polygon)

                if needs_poi and os.path.exists(output_tile_poi):
                    os.remove(output_tile_poi)
                if needs_poly and os.path.exists(output_tile_polygon):
                    os.remove(output_tile_polygon)

                if needs_poi:
                    # Separate the polygons and points into different GeoDataFrames
                    poi_gdf = gdf_combined[gdf_combined.geometry.type == 'Point']
                    poi_gdf = poi_gdf.to_crs(epsg=4326)
                    self.clipping_overture(os.path.join(self.gridded_shapes,file), poi_gdf, output_tile_poi)

                if needs_poly:
                    # Separate the polygons and points into different GeoDataFrames
                    poly_gdf = gdf_combined[gdf_combined.geometry.type.isin(['Polygon', 'MultiPolygon'])]
                    poly_gdf = poly_gdf.to_crs(epsg=4326)
                    self.clipping_overture(os.path.join(self.gridded_shapes,file), poly_gdf, output_tile_polygon)

            except (FileNotFoundError, IOError, ValueError) as e:
                print(f"Error opening file with GeoPandas: {e}")
                print(file_id)
                if max_tries>0:
                    overture_downloader = OvertureDataDownloader(self.country_code, self.index)
                    if flag == 0:
                        overture_downloader.re_download_corrupt_file(file, 'base', 'infrastructure', 'base_infra', 'bases')
                    elif flag == 1:
                        overture_downloader.re_download_corrupt_file(file, 'base', 'land_use', 'base_land_use', 'bases')
                    elif flag == 2:
                        overture_downloader.re_download_corrupt_file(file, 'base', 'water', 'base_water', 'bases')
                    else:
                        return
                    self.base_tile(file, max_tries-1)


    def base_tiling(self):

        files = os.listdir(self.gridded_shapes)
        num_workers = os.cpu_count()  # Desired number of workers
        logging.info("Clipping base files.")
        with Pool(num_workers) as pool:
            pool.map(self.base_tile, files)

        logging.info("Finished tiling and clipping for bases!")

    def overture_tiler(self):
        self.bldg_and_poi_tiling()
        self.base_tiling()

if __name__ == '__main__':

    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser(description='Clip Overture data on boundary.')
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

    overture_tiler = TileAllOverture(country_code, index)
    overture_tiler.overture_tiler()
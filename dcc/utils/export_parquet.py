import os
import sys
import geopandas as gpd
from dotenv import load_dotenv, find_dotenv
import yaml
import pandas as pd
import argparse
load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
import dcc.utils.utils_vector as utils_vector


class ParquetProcessor:

    def read_parquet_files(self, input_directory):
        """
        Reads all Parquet files from the input directory and stores them as GeoDataFrames in a list.
        """
        gdfs = []
        for filename in os.listdir(input_directory):
            if filename.endswith('.parquet'):
                print('Reading Filename:', filename)
                filepath = os.path.join(input_directory, filename)
                gdf = gpd.read_parquet(filepath)
                gdfs.append(gdf)
        return gdfs

    def combine_parquet_files(self, gdfs):
        """
        Combines all the GeoDataFrames in the list into a single GeoDataFrame.

        Returns:
            GeoDataFrame: The combined GeoDataFrame.
        """
        combined_gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True))
        return combined_gdf

    def save_to_geopackage(self, combined_gdf, output_file):
        """
        Saves the combined GeoDataFrame to a GeoPackage file.

        Args:
            combined_gdf (GeoDataFrame): The combined GeoDataFrame.
        """
        combined_gdf.to_file(output_file, driver='GPKG')
        print(f"All Parquet files have been successfully converted to {output_file}")

    def process_parquet_files(self, input_directory, output_file):
        """
        Processes the Parquet files by reading, combining, and saving them to a GeoPackage.
        """
        print(f"Exporting parquet to a .gpkg file")
        gdfs = self.read_parquet_files(input_directory)
        combined_gdf = self.combine_parquet_files(gdfs)

        self.save_to_geopackage(combined_gdf, output_file)

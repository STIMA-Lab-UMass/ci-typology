import os
import sys
from pathlib import Path

# Ensure the repository root is importable so first-party ``dcc.*`` imports
# resolve regardless of how this script is launched (``python dcc/classify_overture.py``
# or ``python -m dcc.classify_overture``) and without an editable install or PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from dotenv import load_dotenv, find_dotenv
from multiprocessing import current_process
import argparse
import re
import pandas as pd

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_output_root, scoped_tiled_root
from dcc.classification.label_overture_v01 import LabelOverture
from dcc.classification.openai_naics_4_classification_res_non_res import OpenAIResNonRes4Classifier
from dcc.classification.openai_naics_6_classification import NAICSOpenAIClassifier
from dcc.classification.openai_naics_4_classification import OpenAIClassifier
from dcc.utils.export_parquet import ParquetProcessor
from dcc.classification.model_config import choose_model

class OvertureClassifier:

    """
    A classifier for processing and labeling Overture data
    with NAICS classification codes.
    
    Attributes:
        country_code (str): The country code for which classification is performed.
        config (dict): Configuration settings loaded from a YAML file.
        file_path (str): Path to the directory where classified files are stored.
        class_type (str): Type of classification (e.g., 'all').
        boundary_admin_name (str): Name of the country boundary.
        six_naics_classification (bool): Flag indicating whether 6-digit NAICS classification is enabled.
    """

    def __init__(self, country_code, index):

        """
        Initializes the OvertureClassifier with the specified country code.
        
        Args:
            country_code (str): The country code for which classification is performed.
        """

        self.country_code = country_code
        self.index = index

        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{self.index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        self.subregion = resolve_subset_context(self.config)
        output_root = scoped_output_root(self.config, self.version, self.subregion)
        self.file_path = os.path.join(output_root, 'combined_classes')
        os.makedirs(self.file_path, exist_ok=True)
        self.export_output_dir = output_root
        self.country = self.config['country']
        self.class_type = self.config['classification']['class']
        self.boundary_admin_name = boundary_label(self.config, self.subregion)

        self.six_naics_classification = self.config['classification']['enable_6_digit_naics_classification']
        self.admin_filter = None
        self.tiled_output_root = scoped_tiled_root(self.config, self.version, self.subregion)

    def _local_file_paths(self, data_types, suffix):
        return [
            os.path.join(self.file_path, f"{data_type}{suffix}.csv")
            for data_type in data_types
        ]

    def _local_files_exist(self, paths):
        if not paths:
            return False
        return all(self.validate_csv_file(path) for path in paths)

    def _prompt_stage_mode(self, stage_label, local_paths):
        local_ready = self._local_files_exist(local_paths)

        options = []
        options.append({
            "key": "1",
            "action": "run",
            "text": f"Run from scratch, calling LLM APIs for {stage_label}.",
            "enabled": True
        })
        local_desc = f"Reuse precomputed mapping under {os.path.dirname(local_paths[0]) if local_paths else 'N/A'}"
        options.append({
            "key": "2",
            "action": "local",
            "text": f"{local_desc}" + ("" if local_ready else " (Unavailable: required files missing or invalid)"),
            "enabled": local_ready
        })

        print(f"\nHow do you want to handle {stage_label}?")
        for opt in options:
            print(f"{opt['key']}. {opt['text']}")

        while True:
            choice = input("Enter option number: ").strip()
            selected = next((opt for opt in options if opt['key'] == choice), None)
            if selected and selected["enabled"]:
                return selected["action"]
            print("Invalid choice or option unavailable. Please select an available option number.")


    def validate_csv_file(self, file_path):
        """
        Validates if a CSV file exists and has readable content.
        
        Args:
            file_path (str): Path to the CSV file
            
        Returns:
            bool: True if file is valid, False otherwise
        """
        if not os.path.exists(file_path):
            print(f"File does not exist: {file_path}")
            return False
            
        try:
            # Check if file is empty
            if os.path.getsize(file_path) == 0:
                print(f"File is empty: {file_path}")
                return False
                
            # Try to read the header to verify file structure
            df_test = pd.read_csv(file_path, nrows=0)
            if len(df_test.columns) == 0:
                print(f"File has no columns: {file_path}")
                return False
                
            return True
            
        except Exception as e:
            print(f"Error validating file {file_path}: {str(e)}")
            return False

    def _ensure_subset_outputs_from_all(self, data_types):
        if self.class_type == "all":
            return
        for data_type in data_types:
            output_file = os.path.join(self.file_path, f'{data_type}_{self.class_type}_classified.csv')
            input_file = os.path.join(self.file_path, f'{data_type}_all_classified.csv')
            unique_file = os.path.join(self.file_path, f'unique_{self.class_type}_{data_type}_classes.csv')

            if not os.path.exists(output_file) and os.path.exists(input_file) and os.path.exists(unique_file):
                if not self.validate_csv_file(input_file) or not self.validate_csv_file(unique_file):
                    continue

                try:
                    data = pd.read_csv(unique_file)
                    df = pd.read_csv(input_file)
                    df_selected = df.drop(columns=['is_residential'], errors='ignore')

                    if data_type == 'places':
                        common_columns = ['names_primary', 'categories_main', 'categories_alternate']
                    else:
                        common_columns = ['names_primary', 'subtype', 'class']

                    df_filtered = df_selected.merge(data[common_columns], on=common_columns, how='inner').reset_index(drop=True)
                    df_filtered['naics_code'] = df_filtered['naics_code'].map(lambda x: str(int(x)) if not pd.isna(x) else x)
                    df_filtered.to_csv(output_file, index=False)
                    print(f"Derived {output_file} from all-classified mapping.")

                except Exception as e:
                    print(f"Error deriving {data_type} subset mapping: {str(e)}")

    def process_4_naics_classification(self, data_types):

        """
        Processes data for classification using OpenAI models.
        
        Args:
            data_types (list): List of data types to process.
        """

        if self.class_type == "all":
            openai_classifier = OpenAIResNonRes4Classifier(self.country_code, self.index, data_types)
            openai_classifier.openai_classifier(self.admin_filter)
        else:
            openai_classifier = OpenAIClassifier(self.country_code, self.index, data_types)
            openai_classifier.openai_classifier(self.admin_filter)

    def process_6_naics_classification(self, data_types):
        """
        Processes 6-digit NAICS classification for data types with enhanced error handling.
        
        Args:
            data_types (list): List of data types to process for 6-digit NAICS classification.
        """
        try:
            valid_data_types = []
            for data_type in data_types:
                input_file = os.path.join(self.file_path, f'{data_type}_{self.class_type}_classified.csv')
                
                if self.validate_csv_file(input_file):
                    valid_data_types.append(data_type)
                else:
                    print(f"Warning: Skipping 6-digit NAICS classification for {data_type} - required input file is missing or invalid: {input_file}")
            
            if not valid_data_types:
                print(f"No valid input files found for 6-digit NAICS classification. Process: {current_process().name}")
                return
                
            print(f"Processing 6-digit NAICS classification for: {valid_data_types}")
            openai_classifier = NAICSOpenAIClassifier(self.country_code, self.index, valid_data_types)
            openai_classifier.openai_classifier()  # 6-digit classification works on already filtered 4-digit data
            
        except Exception as e:
            print(f"Error in process_6_naics_classification for data types {data_types}: {str(e)}")

    def export_parquet_gkpg(self):
        """Export the labelled (reclassified) Overture data to a separate
        GeoPackage per theme: buildings, places (POIs), and bases_poly
        (geometries). Each theme is written to its own file so every layer keeps
        a single geometry type (buildings/places are points, bases are polygons)
        and opens cleanly in GIS tools. Files land in ``self.export_output_dir``.

        Returns a list of ``(data_type, output_path, feature_count)`` for the
        GeoPackages that were written.
        """
        import geopandas as gpd

        data_types = ['bldgs', 'places', 'bases_poly']
        processor = ParquetProcessor()
        written = []

        for data_type in data_types:
            input_directory = os.path.join(
                self.tiled_output_root, data_type, f"{self.class_type}_labeled"
            )
            print(f"\nExporting {data_type}:")
            print(f"  Input: {input_directory}")

            if not os.path.exists(input_directory):
                print(f"  Skipping {data_type} - labelled directory doesn't exist")
                continue

            # Read every non-empty labelled parquet tile for this theme.
            gdfs = []
            for filename in sorted(os.listdir(input_directory)):
                if not filename.endswith('.parquet'):
                    continue
                filepath = os.path.join(input_directory, filename)
                if os.path.getsize(filepath) == 0:
                    continue
                try:
                    gdf = gpd.read_parquet(filepath)
                except Exception as exc:
                    print(f"  Warning: could not read {filename}: {exc}")
                    continue
                if len(gdf):
                    gdfs.append(gdf)

            if not gdfs:
                print(f"  Skipping {data_type} - no non-empty labelled parquet files")
                continue

            combined = processor.combine_parquet_files(gdfs)
            if combined.crs is None:
                combined = combined.set_crs(gdfs[0].crs)

            output_file = os.path.join(
                self.export_output_dir,
                f'overture_{self.class_type}_classified_{data_type}_{self.country}_{self.version}.gpkg'
            )
            try:
                processor.save_to_geopackage(combined, output_file)
                geom = combined.geom_type.dropna().iloc[0] if len(combined) else 'NA'
                print(f"  ✓ {len(combined)} {geom} features -> {os.path.basename(output_file)}")
                written.append((data_type, output_file, len(combined)))
            except Exception as exc:
                print(f"  ✗ Failed to export {data_type}: {exc}")

        print("\nGeoPackage export complete.")
        if written:
            print(f"Per-theme GeoPackages written under {self.export_output_dir}:")
            for data_type, path, n in written:
                print(f"  - {data_type}: {os.path.basename(path)} ({n} features)")
        else:
            print("No labelled data found to export.")
        return written
                    
    def save_config(self):
        # Save the config dictionary to a JSON file
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{self.index}.yml"), 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
            
    def run(self):

        """
        Runs the complete classification pipeline including file processing,
        NAICS classification, and labeling.
        """

        print("\n")
        # Print settings and request confirmation
        print("OpenAI API Settings:")
        # print(f"  Batch Size (openai_api.batch_size): {self.config['openai_api']['batch_size']}\n")
        choose_model()

        options = ['100', 'others']
        print('Please enter the Batch Size (openai_api.batch_size):')
        for i, option in enumerate(options, 1):
            print(f"{i}. {option}")

        while True:
            try:
                choice = int(input("Enter your choice: "))
                if 1 <= choice <= len(options):
                    res = options[choice - 1]
                    break
                else:
                    print(f"Invalid choice. Please enter a number between 1-{len(options)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

        if res == 'others':
            res = input("\nPlease enter the batch size:")
            if not res:
                res = 100

        self.config['openai_api']['batch_size'] = int(res)

        print("Archetype Classification Settings:")
        # print(f"  Enable 6-Digit NAICS Classification (classification.enable_6_digit_naics_classification): {self.config['classification']['enable_6_digit_naics_classification']}\n")
        options = [True, False]
        print('Please enable/disable 6-Digit NAICS Classification:')
        for i, option in enumerate(options, 1):
            print(f"{i}. {option}")

        while True:
            try:
                choice = int(input("Enter your choice: "))
                if 1 <= choice <= len(options):
                    res = options[choice - 1]
                    break
                else:
                    print(f"Invalid choice. Please enter a number between 1-{len(options)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

        self.config['classification']['enable_6_digit_naics_classification'] = res
        
        # Save config to a JSON file
        self.save_config()
        self.six_naics_classification = self.config['classification']['enable_6_digit_naics_classification']

        print("Read (dcc/readme.md) to know more about the above mentioned attributes in the configuration.")

        # Ask about administrative filtering
        admin_filter_choice = input("Do you want to filter classification by administrative boundaries? (e.g., specific state/county) (y/N): ").strip().lower()
        if admin_filter_choice == 'y':
            print(f"Available administrative columns: {self.config['admin_columns']}")
            admin_col = input(f"Enter the administrative column name: ").strip()
            if admin_col in self.config['admin_columns']:
                # Show all possible values from the GADM boundary data
                try:
                    # Get the sub_admin_boundary file path
                    sub_admin_boundary = self.config['sub_admin_boundary']
                    project_data = os.environ.get("PROJECT_DATA")
                    boundary_file = os.path.join(project_data, self.config['country'], 'regions',
                                               f"gadm41_{self.config['country_cap_code']}", f'gadm41_{sub_admin_boundary}.gpkg')

                    if os.path.exists(boundary_file):
                        import geopandas as gpd
                        gdf = gpd.read_file(boundary_file)
                        if admin_col in gdf.columns:
                            unique_values = sorted(gdf[admin_col].dropna().unique())
                            print(f"All available {admin_col} values ({len(unique_values)} total):")
                            # Display in a nice format, maybe 5-6 per line
                            for i in range(0, len(unique_values), 6):
                                print(f"  {', '.join(unique_values[i:i+6])}")
                        else:
                            print(f"Warning: Column '{admin_col}' not found in boundary file.")
                    else:
                        print(f"Warning: Boundary file not found at {boundary_file}")
                except Exception as e:
                    print(f"Could not read boundary data: {str(e)}")

                admin_value = input(f"Enter the value to filter by (e.g., 'Maryland' for US states): ").strip()
                self.admin_filter = {admin_col: admin_value}
                print(f"Will filter classification to {admin_col} = {admin_value}")
                print("Note: Only data from this administrative boundary will be sent to LLM classification.")
            else:
                print(f"Invalid column '{admin_col}'. Available columns: {self.config['admin_columns']}")
                self.admin_filter = None

        # Ask for user consent
        consent = input("Do you confirm these settings? (Y/n): ").strip().upper()
            
        if consent == 'Y':

            flat_data_types = ['bldgs', 'places', 'bases_poly']

            print("Starting 4 digit NAICS code classification. This might take several hours.")
            mode_4 = self._prompt_stage_mode(
                "4-digit NAICS classification",
                self._local_file_paths(flat_data_types, f"_{self.class_type}_classified")
            )

            if mode_4 == 'run':
                try:
                    self.process_4_naics_classification(flat_data_types)
                    print("4-digit NAICS classification completed successfully.")
                except Exception as e:
                    print(f"Error during 4-digit NAICS classification: {str(e)}")
                    print("Continuing with available data...")
            else:
                print("Reusing existing 4-digit classification files.")
                self._ensure_subset_outputs_from_all(flat_data_types)

            if self.six_naics_classification:
                print("Starting 6 digit NAICS code classification.")
                mode_6 = self._prompt_stage_mode(
                    "6-digit NAICS classification",
                    self._local_file_paths(flat_data_types, f"_{self.class_type}_naics_6_classified")
                )

                if mode_6 == 'run':
                    try:
                        self.process_6_naics_classification(flat_data_types)
                        print("6-digit NAICS classification completed successfully.")
                    except Exception as e:
                        print(f"Error during 6-digit NAICS classification: {str(e)}")
                        print("Continuing with available data...")
                else:
                    print("Reusing existing 6-digit classification files.")
                             
            print("Labeling NAICS code classification to Overture.")

            try:
                overture_labeler = LabelOverture(self.country_code, self.index)
                overture_labeler.overture_labeler()
            except Exception as e:
                print(f"Error during overture labeling: {str(e)}")

            # Final step: export the reclassified Overture data to per-theme
            # GeoPackages (buildings, POIs/places, geometries/bases).
            print("\nExporting reclassified Overture data to per-theme GeoPackages.")
            try:
                self.export_parquet_gkpg()
            except Exception as e:
                print(f"Error during GeoPackage export: {str(e)}")

        else:
            print("Please configure these attribute and rerun the file using the same command.")


if __name__ == '__main__':

    """
    Main entry point for executing the OvertureClassifier.
    Parses command-line arguments and runs the classification process.
    """

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
    # Parse arguments
    args = parser.parse_args()
    processor = OvertureClassifier(country_code, index)
    processor.run()
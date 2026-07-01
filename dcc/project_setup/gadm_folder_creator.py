import os
import yaml
import sys
import logging
import zipfile
import geopandas as gpd
import glob
import re
import unicodedata

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

from dcc.project_setup.create_country_yml import CountryConfig
from dcc.project_setup.grid_gdam_shape import GeoJSONGridder

GADM_DOWNLOAD_INSTRUCTIONS = """
============================================================
Global GADM boundary file not found.
============================================================
The pipeline needs the global GADM 4.1 boundary geopackage
('gadm_410-levels.gpkg'), from which it slices each country's
admin layers (ADM_0 .. ADM_5). Download it once, manually, from GADM:

  1. Open  https://gadm.org/download_world.html
  2. Under "the entire world", choose the *Geopackage* format,
     "six separate layers" variant. This downloads
     'gadm_410-levels.zip' (~1 GB; contains layers ADM_0 .. ADM_5).
     (Do NOT use the single-layer 'gadm_410.gpkg' -- the code
      expects the '-levels' geopackage.)
  3. Create a 'GADM_global' folder inside your PROJECT_DATA directory
     and move the zip there (the pipeline unzips it automatically):

         mkdir -p "$PROJECT_DATA/GADM_global"
         mv ~/Downloads/gadm_410-levels.zip "$PROJECT_DATA/GADM_global/"

     Expected layout (either the zip or the unzipped gpkg is fine):
         {data}/GADM_global/gadm_410-levels.zip   ->
         {data}/GADM_global/gadm_410-levels.gpkg

  4. Re-run:  python3 dcc/project_starter.py
============================================================
"""



class GADMFilesCreator:
    def __init__(self):
        """
        Initializes the GADMFilesCreator class with the file path.
        :param file_path: Path to the GPKG file.
        """
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',"country_config.yml"), 'r') as file:
            self.config = yaml.safe_load(file)

        project_data = os.environ.get("PROJECT_DATA")
        if not project_data:
            raise ValueError("PROJECT_DATA environment variable is not set")
        self.project_root = os.environ.get("PROJECT_ROOT")
        if not self.project_root:
            raise ValueError("PROJECT_ROOT environment variable is not set")

        self.project_data = project_data
        self.file_path = os.path.join(self.project_data, 'GADM_global', 'gadm_410-levels.zip')
        self.final_file = os.path.join(self.project_data, 'GADM_global', 'gadm_410-levels.gpkg')

    def file_exists(self):
        """
        Checks if the GPKG file exists at the specified path.
        :return: True if the file exists, False otherwise.
        """
        return os.path.exists(self.final_file)

    def _print_gadm_instructions(self):
        """Prints the manual GADM download walkthrough (gadm.org)."""
        print(GADM_DOWNLOAD_INSTRUCTIONS.format(data=self.project_data))

    def ensure_global_gadm(self):
        """
        Ensures the global GADM geopackage is available locally.

        The pipeline expects a user-supplied global GADM 4.1 geopackage at
        ``$PROJECT_DATA/GADM_global/gadm_410-levels.zip`` (or the already
        unzipped ``gadm_410-levels.gpkg``). If only the zip is present it is
        unzipped automatically. If neither file exists, the manual download
        instructions are printed and this returns ``False`` -- it never
        attempts a network/cloud download.

        :return: True if the global geopackage is ready, False otherwise.
        """
        if os.path.exists(self.final_file):
            return True

        if os.path.exists(self.file_path):
            print("Found gadm_410-levels.zip; extracting the global GADM geopackage...")
            try:
                os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
                with zipfile.ZipFile(self.file_path, "r") as zip_ref:
                    zip_ref.extractall(os.path.dirname(self.file_path))
                logging.info("Successfully extracted the global GADM geopackage.")
            except zipfile.BadZipFile as exc:
                logging.error("Could not extract %s: %s", self.file_path, exc)
                self._print_gadm_instructions()
                return False
            return os.path.exists(self.final_file)

        self._print_gadm_instructions()
        return False


    def get_country(self):
        """
        Prompts the user to select a country from the available list.
        
        :return: Selected country code if valid, otherwise None.
        """

        country_list = self.config['country_map']
        # Display the full list of countries
        print("Available countries:")
        for key, value in country_list.items():
            print(f"{key}   - {value}")

        # Prompt the user to choose a country code
        country_code = input("Enter the GID_0 code of the country you want to select: ").strip().upper()

        if not country_code:  # If the user enters a value, update self.distance
             return None
        
        # Check if the country code exists in the data
        if country_code in list(country_list.keys()):
            selected_country = country_list[country_code]
            print(f"Selected country: {selected_country}")
            return country_code
        else:
            print("Invalid country code. Please try again.")
            return

    def find_largest_index(self, country_code):
        # Define the file pattern
        path_pattern = os.path.join(self.project_root, 'envs', f"{country_code.lower()}_config_v*.yml")
        
        # Find all matching files
        files = glob.glob(path_pattern)
        
        # Extract indices using regex
        index_pattern = re.compile(rf"{country_code.lower()}_config_v(\d+)\.yml$")
        indices = []
        
        for file in files:
            match = index_pattern.search(os.path.basename(file))
            if match:
                indices.append(int(match.group(1)))
        
        # Return the largest index if found, else None
        return max(indices)+1 if indices else None
        
    def create_country_yml(self, country_code):
        """
        Creates a YAML configuration file for the selected country.
        
        :param country_code: The country code for which the YAML file should be created.
        """

        self.index = self.find_largest_index(country_code)

        if self.index is None:
            self.index = 0

        print(f"Creating yml file for country {country_code}")
        country_config = CountryConfig(country_code, self.index)
        # Generate the YAML file
        country_config.create_yml()

    def create_country_gdam(self, country_code):
        """
        Extracts country-specific GADM data and saves it as JSON files.
        
        :param country_code: The country code for which GADM data should be processed.
        """
        gadm_level = self.config['gadm_level']
        path = os.path.join(self.project_data, country_code.lower(), "regions", f"gadm41_{country_code.upper()}")
        os.makedirs(path, exist_ok=True)

        for level in gadm_level:
            level_number = level.split('_')[1]
            file_name = f"gadm41_{country_code.upper()}_{level_number}.gpkg"

            if not os.path.exists(os.path.join(path, file_name)):
                level_data = gpd.read_file(self.final_file, layer=level)
                selected_country = level_data[level_data['GID_0'] == country_code]

                if not selected_country.empty:
                    print(f"File for admin level {level_number} is getting created.")
                    selected_country.to_file(os.path.join(path, file_name), driver="GPKG")
                else:
                    print(f"No data for admin level {level_number}")

            else:
                print(f"The file already exsits for admin level {level_number}")


    def create_country_grid(self, country_code):
        """
        Creates a grid-based GeoJSON file for the selected country.
        
        :param country_code: The country code for which the grid should be generated.
        """
        
        path = os.path.join(self.project_data, country_code.lower())
        file_name = f"gadm41_{country_code.upper()}_0.gpkg"
        output_file_name = f"gadm41_{country_code.upper()}_grid.geojson"

        if not os.path.exists(os.path.join(path, output_file_name)):
            gridder = GeoJSONGridder(os.path.join(path, "regions", f"gadm41_{country_code.upper()}", file_name))
            grid = gridder.create_grid()
            gridder.save_grid(os.path.join(path, output_file_name))
    
    def _admin_level_file(self, country_code, level_suffix='1'):
        country_code = country_code.upper()
        return os.path.join(
            self.project_data,
            country_code.lower(),
            "regions",
            f"gadm41_{country_code}",
            f"gadm41_{country_code}_{level_suffix}.gpkg"
        )

    def _slugify(self, value):
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
        value = value.lower()
        value = re.sub(r'[^a-z0-9]+', '-', value)
        value = value.strip('-')
        return value or 'region'

    def list_admin1_regions(self, country_code):
        level_file = self._admin_level_file(country_code, '1')
        if not os.path.exists(level_file):
            return []
        gdf = gpd.read_file(level_file)
        if 'NAME_1' not in gdf.columns or 'GID_1' not in gdf.columns:
            return []
        records = []
        for _, row in gdf.iterrows():
            records.append({
                "name": row['NAME_1'],
                "gid": row['GID_1']
            })
        return sorted(records, key=lambda item: item['name'])

    def prompt_admin1_scope(self, country_code):
        admin_regions = self.list_admin1_regions(country_code)
        if not admin_regions:
            return None

        choice = input("Restrict processing to an ADM_1 region? (y/N): ").strip().lower()
        if choice not in ('y', 'yes'):
            return None

        print("\nAvailable ADM_1 regions:")
        for idx, region in enumerate(admin_regions, start=1):
            print(f"{idx}. {region['name']}")

        while True:
            selection = input("Enter the number of the ADM_1 region (or press Enter to cancel): ").strip()
            if not selection:
                return None
            if not selection.isdigit():
                print("Please enter a valid number.")
                continue
            selection_idx = int(selection)
            if 1 <= selection_idx <= len(admin_regions):
                return admin_regions[selection_idx - 1]
            print("Invalid selection. Please choose a number from the list.")

    def prepare_admin1_scope(self, country_code, admin_region):
        slug = self._slugify(admin_region['name'])
        admin_dir = os.path.join(self.project_data, country_code.lower(), "admin1", slug)
        os.makedirs(admin_dir, exist_ok=True)

        boundary_file = os.path.join(admin_dir, f"{slug}_boundary.geojson")
        grid_file = os.path.join(admin_dir, f"{slug}_grid.geojson")

        level_file = self._admin_level_file(country_code, '1')
        gdf = gpd.read_file(level_file)
        subset = gdf[gdf['GID_1'] == admin_region['gid']]
        if subset.empty:
            raise ValueError(f"Could not locate ADM_1 geometry for {admin_region['name']}")

        if not os.path.exists(boundary_file):
            subset.to_file(boundary_file, driver="GeoJSON")

        if not os.path.exists(grid_file):
            gridder = GeoJSONGridder(boundary_file)
            gridder.save_grid(grid_file)

        return {
            "level": "ADM_1",
            "name": admin_region['name'],
            "slug": slug,
            "gid": admin_region['gid'],
            "boundary_path": boundary_file,
            "grid_path": grid_file
        }

    def _relative_project_path(self, absolute_path):
        if not absolute_path or not self.project_data:
            return absolute_path
        try:
            return os.path.relpath(absolute_path, self.project_data)
        except ValueError:
            return absolute_path

    def update_subset_config(self, country_code, index, subset_context):
        config_path = os.path.join(self.project_root, 'envs', f"{country_code.lower()}_config_v{index}.yml")
        if not os.path.exists(config_path):
            return

        with open(config_path, 'r') as file:
            config_data = yaml.safe_load(file)

        if subset_context:
            config_data['subset'] = {
                "level": subset_context['level'],
                "name": subset_context['name'],
                "slug": subset_context['slug'],
                "gid": subset_context['gid'],
                "paths": {
                    "boundary": self._relative_project_path(subset_context['boundary_path']),
                    "grid": self._relative_project_path(subset_context['grid_path'])
                },
                "tiled_suffix": subset_context['slug']
            }
        else:
            config_data['subset'] = None

        with open(config_path, 'w') as file:
            yaml.safe_dump(config_data, file)

    def process(self):
        """
        Main function that executes the entire process of handling GADM files.
        """
        if self.file_exists():
            print(f"The file {os.path.split(self.final_file)[1]} already exists.")
        elif not self.ensure_global_gadm():
            # Global GADM geopackage is missing; instructions were printed.
            sys.exit(1)

        country_code = self.get_country()

        if not country_code:
            print("Country code was not entered.")
            return
        
        self.create_country_gdam(country_code)
        self.create_country_yml(country_code)

        admin_choice = self.prompt_admin1_scope(country_code)
        admin_scope = None
        if admin_choice:
            admin_scope = self.prepare_admin1_scope(country_code, admin_choice)
            print(f"Limiting project starter to ADM_1 region: {admin_scope['name']}")
        else:
            print("Proceeding with full-country setup.")
            self.create_country_grid(country_code)

        self.update_subset_config(country_code, self.index, admin_scope)
        return country_code.lower(), self.index, admin_scope
        
# Example Usage
if __name__ == "__main__":
    gadm_creator = GADMFilesCreator()
    country_code = gadm_creator.process()
    print("Choosen country is: ", country_code)

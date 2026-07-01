import os
import sys
import glob
import logging
import xml.etree.ElementTree as ET
from urllib.error import URLError
from urllib.request import urlopen
from dotenv import load_dotenv, find_dotenv
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))

class CountryConfig:
    def __init__(self, country_code, index):
        self.country_code = country_code
        self.index = index
        yaml = YAML()
        with open(os.path.join(os.environ.get("PROJECT_ROOT"), 'envs', "country_config.yml"), 'r') as file:
            self.global_config = yaml.load(file)
        self.country_map = self.global_config['country_map']
        self.save_path = os.path.join(os.environ.get("PROJECT_ROOT"), 'envs')
        self.overture_class = self.global_config['overture_class']
        self.overture_version = self.global_config['overture_version']
        self.overture_version_dynamic = self._fetch_available_overture_releases()

        # Initialize configuration as a CommentedMap
        self.config = CommentedMap()
        self._build_config()
        self._add_comments()

    def _fetch_available_overture_releases(self):
        """
        Pulls the latest list of releases directly from the public Overture S3 bucket.
        Returns a list ordered from newest to oldest, prefixed with the sentinel 'latest'
        so users can always opt into the newest release.
        """
        list_url = "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
        try:
            with urlopen(list_url, timeout=15) as response:
                payload = response.read()
        except URLError as exc:
            logging.warning("Unable to reach Overture release listing: %s", exc)
            return None

        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            logging.warning("Unable to parse Overture release listing: %s", exc)
            return None

        namespace = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        releases = []
        for prefix in root.findall('s3:CommonPrefixes', namespace):
            prefix_elem = prefix.find('s3:Prefix', namespace)
            if prefix_elem is None or not prefix_elem.text:
                continue
            trimmed = prefix_elem.text.strip('/')
            parts = trimmed.split('/')
            if len(parts) == 2 and parts[0] == 'release':
                releases.append(parts[1])

        releases = sorted(set(releases))
        if not releases:
            return None

        releases = list(reversed(releases))
        return ['latest'] + releases

    def get_num_of_admin(self, country_code):

        # Construct the directory path
        base_dir = os.environ.get("PROJECT_DATA")
        if not base_dir:
            print("Error: PROJECT_DATA environment variable is not set.")
            return 0

        target_dir = os.path.join(base_dir, country_code.lower(), "regions", f"gadm41_{country_code.upper()}")

        # Check if the directory exists
        if not os.path.isdir(target_dir):
            print(f"Error: Directory does not exist: {target_dir}")
            return 0

        # Construct the file pattern
        file_pattern = f"gadm41_{country_code.upper()}*.json"
        matching_files = glob.glob(os.path.join(target_dir, file_pattern))

        # Return the count of matching files
        return len(matching_files)

    def get_sub_admin_list(self, admin_num):

        res_list = []
        if admin_num == 1:
            res_list.append('NAME_1')
        return res_list


    def _build_config(self):
        """Build the nested CommentedMap structure."""
        self.config["country"] = self.country_code.lower()
        self.config["country_cap_code"] = self.country_code.upper()
        self.config["country_shp"] = f"{self.country_code.upper()}_0"
        self.config["country_name"] = self.country_map[self.country_code].lower()
        admin_num = self.get_num_of_admin(self.country_code)
        self.config["sub_admin_boundary"] = f"{self.country_code.upper()}_{admin_num - 1 if admin_num == 1 else 1}"
        self.config["admin_columns"] = self.get_sub_admin_list(admin_num - 1 if admin_num == 1 else 1)
        self.config["subset"] = None
        
        # Nested structures (CommentedMap for comments)
        openai_api = CommentedMap()
        openai_api["batch_size"] = 100

        print('\nOpenAI API concurrency settings:')
        max_workers_input = input("Max parallel workers for classification (default: 20): ").strip()
        if max_workers_input and max_workers_input.isdigit() and int(max_workers_input) > 0:
            openai_api["max_workers"] = int(max_workers_input)
        else:
            openai_api["max_workers"] = 20

        rpm_input = input("Requests per minute limit (default: 200): ").strip()
        if rpm_input and rpm_input.isdigit() and int(rpm_input) > 0:
            openai_api["requests_per_minute"] = int(rpm_input)
        else:
            openai_api["requests_per_minute"] = 200

        self.config["openai_api"] = openai_api

        classification = CommentedMap()
        options = self.overture_class
        print('Which sector are you interested in:')
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
            res = input("\nWhich sector are you interested in (dcc/classification/overture_class.txt) :")
            if not res:
                res = 'all'
        classification["class"] = res

        # classification["enable_2_digit_naics_classification"] = False
        classification["enable_6_digit_naics_classification"] = False
        classification["naics_dict_2"] = self.global_config['naics_dict_2']
        classification["naics_dict_3"] = self.global_config['naics_dict_3']
        classification["naics_dict_4"] = self.global_config['naics_dict_4']
        classification["naics_dict_6"] = self.global_config['naics_dict_6']
        self.config["classification"] = classification

        options = self.overture_version_dynamic or self.overture_version
        options = list(options)
        if 'others' not in options:
            options.append('others')
        print('Please enter the Overture version:')
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
            res = input("\nPlease enter the Overture version (https://docs.overturemaps.org/release/):")
            if not res:
                res = '2024-12-18.0'
        self.config["overture_version"] = res

        self.config["overture_bldgs_columns"] = ["id", "names_primary", "names_common", "subtype", "class", "geometry"]
        self.config["overture_places_columns"] = ["id", "names_primary", "names_common", "categories_main", "categories_alternate", "geometry"]
        self.config["overture_bases_poly_columns"] = ["id", "names_primary", "names_common", "subtype", "class", "geometry"]
        self.config["overture_id_column"] = "id"

    def _add_comments(self):
        """Add inline comments to specific keys."""
        self.config.yaml_set_comment_before_after_key("country", before="Country code in lowercase (e.g., 'usa').")
        self.config.yaml_set_comment_before_after_key("country_shp", before="Shapefile name for the country which has the country's boundary geojson.")
        self.config.yaml_set_comment_before_after_key("sub_admin_boundary", before="File prefix of GADM geojson file which has the sub admin boundaries")
        self.config.yaml_set_comment_before_after_key("openai_api", before="Configuration for OpenAI API batching.")
        self.config["openai_api"].yaml_set_comment_before_after_key("batch_size", before="Number of records per batch.")
        self.config["openai_api"].yaml_set_comment_before_after_key("max_workers", before="Max parallel threads for classification.")
        self.config["openai_api"].yaml_set_comment_before_after_key("requests_per_minute", before="Rate limit for OpenAI API calls.")

        self.config.yaml_set_comment_before_after_key("classification", before="Configuration for classification of overture data.")
        # self.config["classification"].yaml_set_comment_before_after_key("enable_overture_class_filtering", before="If you want to filter data with specific Overture type (e.g industrial, school, hospitals, etc)")
        # self.config["classification"].yaml_set_comment_before_after_key("class", before="If enable_overture_class_filtering is enabled then mentioned the type of data you want to filter. (default is all)")
        self.config["classification"].yaml_set_comment_before_after_key("code_type", before="Mention your primary classfication choice. Prefered is naics_dict_4.")
        self.config["classification"].yaml_set_comment_before_after_key("enable_6_digit_naics_classification", before="Enable if you also want to classifiy into 6 digit NAICS code")

    def create_yml(self):
        """Generate the YAML file with comments."""
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)  # Formatting
        filename = f"{self.country_code.lower()}_config_v{self.index}.yml"
        filepath = os.path.join(self.save_path, filename)
        with open(filepath, "w") as file:
            yaml.dump(self.config, file)
        print(f"Configuration file '{filename}' created successfully.")
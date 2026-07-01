import os
import sys
import yaml
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import math
import json
from openai import OpenAI
import argparse
import time
import re
import logging

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_output_root
from dcc.utils.rate_limiter import RateLimiter
import dcc.utils.utils_vector as utils_vector
from dcc.classification.model_config import get_model_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(thread)d - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)

class OpenAIClassifier():
    def __init__(self, country_code, index, data_types = None):

        """
        A classifier that uses the OpenAI API to classify data based on NAICS codes and categories.

        Attributes:
            country_config (dict): Configuration for the country, loaded from YAML.
            config (dict): Configuration for the classifier, based on the country.
            client (OpenAI): OpenAI API client for handling requests.
            saving_path (str): Path where classified results will be saved.
            categories_dic (dict): Dictionary of NAICS categories and their corresponding codes.
            categories (list): List of category names from NAICS.
            code_naics (list): List of NAICS codes.
            class_type (str): Type of classification based on the archetype.
            boundary_admin_name (str): Name of the administrative boundary.
            batch_size (int): Batch size for processing data.
            data_types (list): List of data types to classify, defaulting to various building and place types.
        """

        self.country_code = country_code 
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        self.subregion = resolve_subset_context(self.config)
        self.client = OpenAI(api_key=os.environ.get("OPEN_AI_API_KEY"))
        self.saving_path = os.path.join(scoped_output_root(self.config, self.version, self.subregion), 'combined_classes')

        self.categories_dic = self.config['classification']['naics_dict_4']
        self.categories = list(self.categories_dic.values())
        self.code_naics = list(self.categories_dic.keys())

        self.class_type = self.config['classification']['class']
        self.boundary_admin_name = boundary_label(self.config, self.subregion)
        self.admin_columns = self.config['admin_columns']

        self.batch_size = self.config['openai_api']['batch_size']
        self.max_workers = int(self.config['openai_api'].get('max_workers', 20))
        requests_per_minute = self.config['openai_api'].get('requests_per_minute')
        self.rate_limiter = RateLimiter.from_per_minute(requests_per_minute)
        
        if data_types is None:
            self.data_types = ['bldgs', 'places', 'bases_poly']
        else:
            self.data_types = data_types

    def create_manual_res(self, batch_data):

        """
        Creates a manual classification result if the OpenAI API fails.

        Args:
            batch_data (pandas.DataFrame): The batch of data to classify.

        Returns:
            list: A list of dictionaries containing default classification with "Others" category.
        """

        logging.info("Manually creating list of JSON.")
        batch_dic = batch_data.to_dict(orient='records')
        return [
            {"index": item['index'], "naics_code": None, "category": "Others"}
            for item in batch_dic
        ]

    def api_req_prompt(self, prompt, batch_size,  batch_data, retries=3, delay=5):

        """
        Sends a request to the OpenAI API and handles the response.

        Args:
            prompt (str): The prompt to send to the OpenAI API.
            batch_size (int): The expected size of the batch being processed.
            batch_data (pandas.DataFrame): The batch data to be classified.
            retries (int, optional): Number of retries if the API request fails. Defaults to 3.
            delay (int, optional): Delay between retries in seconds. Defaults to 5.

        Returns:
            list: Parsed JSON response containing classification results.
        """

        attempt = 0
        config = get_model_config()
        model_name = config["model"]
        params = config["parameters"]

        while attempt < retries:
            try:
                self.rate_limiter.wait()
                response = self.client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    **params
                )
                content = response.choices[0].message.content

                # Split and extract JSON part
                # logging.info(content)
                parts = content.split('```json')
                if len(parts) > 1:
                    json_part = parts[1].split('```')[0]
                    content = json_part.strip()
                json_str = content.replace('```json', '').replace('```', '').strip()
                
                # Parse JSON
                parsed_json = json.loads(json_str)
                if not len(parsed_json) == batch_size:
                    logging.info(f"Got less number of responses than expected. Retrying attempt {attempt + 1}/{retries}...")
                    attempt += 1
                    time.sleep(delay) 
                else:
                    return parsed_json

            except (json.JSONDecodeError, KeyError) as e:
                logging.info(f"Error in response: {e}. Retrying attempt {attempt + 1}/{retries}...")
                attempt += 1
                time.sleep(delay)
            except Exception as e:
                attempt += 1
                retry_delay = delay * (2 ** (attempt - 1))
                logging.warning(f"API error: {type(e).__name__}: {e}. Retrying attempt {attempt}/{retries} after {retry_delay}s...")
                time.sleep(retry_delay)

        # If all retries fail
        logging.info("Failed to get a valid response after multiple attempts.")
        return self.create_manual_res(batch_data)

    def get_prompt(self, batch_data):

        """
        Generates the prompt to send to the OpenAI API based on the batch data.

        Args:
            batch_data (pandas.DataFrame): The batch of data for which to generate the prompt.

        Returns:
            str: The formatted prompt to send to the OpenAI API.
        """

        batch_dic = batch_data.to_dict(orient='records')
        location = 'NAME_1' if 'NAME_1' in batch_data.columns else {self.boundary_admin_name}
        prompt = f"""
        Classify the following data into one of these NAICS categories and codes:
        {self.categories_dic}.

        1. The data is the buildings and poi data in {self.boundary_admin_name}, Try to build a relation between 'names_primary' with other given attributes with classes in dictionary.
        2. You can also perform a web search on the 'names_primary' in location {location}. Determine the type based on the result.
        3. Assign a 4 digit NAICS code and category STRICTLY from the provided naics dictionary.

        Ensure the response is in JSON format as a list of classified results for each row, in the following structure ("index" should be the value from index column of the data):
        [
            {{"index": batch_dic['index'], "naics_code": <4_digit_code>, "category": "<category-name>"}},
            ...
        ]

        DO NOT RETURN ANY TEXT OTHER THAN JSON.

        Only when the data does not fit any category, return:
        {{"index": batch_dic['index'], "naics_code": null, "category": "Others"}}.

        batch_dic: {batch_dic}
        """
        return prompt

    def check_not_valid(self,code, classification):

        """
        Checks if the classification result is valid by comparing the NAICS code and category.

        Args:
            code (int or None): The NAICS code assigned by the classifier.
            classification (str): The category assigned by the classifier.

        Returns:
            bool: True if the classification is invalid, False otherwise.
        """

        if code is None and classification != 'Others':
            return True
        elif code is None and classification == 'Others':
            return False
        elif ((classification not in self.categories) or (int(code) not in self.code_naics)):
            return True
        else:
            if self.categories_dic[int(code)] == classification:
                return False
            else:
                return True

        return True


    def find_key_by_value(self, val):
        for key, value in self.categories_dic.items():
            if value == val:
                return key
        return None


    def append_csv(self, data, output_file, data_columns, data_type):

        """
        Appends classified data to a CSV file, handling batch processing and retries.

        Args:
            data (pandas.DataFrame): The data to be classified and saved.
            output_file (str): The output file path where the classified data will be appended.
        """
        if os.path.exists(output_file) and os.stat(output_file).st_size == 0:
            # If the file is empty, delete it
            os.remove(output_file)
            
        to_classify = data

        if os.path.exists(output_file):
            df = pd.read_csv(output_file)
            to_classify = data.merge(df[data_columns], how='left', indicator=True).query('_merge == "left_only"').drop(columns=['_merge'])
            to_classify = to_classify.reset_index(drop=True)
            logging.info(f"{to_classify.shape[0]} records are getting classified.")

        batch_size = self.batch_size
        total_batches = math.ceil(to_classify.shape[0] / batch_size) if to_classify.shape[0] else 0
        if total_batches == 0:
            return

        worker_count = min(max(1, self.max_workers), total_batches)
        progress_lock = threading.Lock()
        completed_batches = 0

        header_written = os.path.exists(output_file) and os.path.getsize(output_file) > 0

        with ThreadPoolExecutor(max_workers=worker_count) as executor, open(output_file, mode='a', newline='') as file:
            futures = []
            for batch_start in range(0, to_classify.shape[0], batch_size):
                batch_end = min(batch_start + batch_size, to_classify.shape[0])
                batch_data = to_classify.iloc[batch_start:batch_end].reset_index(drop=False)
                futures.append(executor.submit(self._classify_batch, batch_data))

            for future in as_completed(futures):
                batch_result = future.result()
                if batch_result is not None and not batch_result.empty:
                    batch_result.to_csv(file, header=not header_written, index=False)
                    header_written = True
                with progress_lock:
                    completed_batches += 1
                    logging.info(f"For {data_type}, {completed_batches}/{total_batches} batches processed.")

    def _classify_batch(self, batch_data):
        prompt = self.get_prompt(batch_data)
        res_json = self.api_req_prompt(prompt, batch_data.shape[0], batch_data)
        output_rows = []

        for classification_result in res_json:
            index = int(classification_result["index"])
            classification = classification_result["category"]
            code = classification_result["naics_code"]

            retry_count = 0
            max_retries = 3

            while self.check_not_valid(code, classification) and retry_count < max_retries:
                retry_count += 1
                time.sleep(3)

                if classification in self.categories:
                    code = self.find_key_by_value(classification)
                else:
                    single_row = batch_data[batch_data['index'] == index].reset_index(drop=True)
                    if single_row.empty:
                        break
                    prompt = self.get_prompt(single_row)
                    response = self.api_req_prompt(prompt, 1, single_row)
                    try:
                        index = int(response[0]["index"])
                        classification = response[0]["category"]
                        code = response[0]["naics_code"]
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logging.info(f"Error in response: {e}. Retrying attempt...")

            if self.check_not_valid(code, classification):
                classification = 'Others'
                code = None

            row = batch_data[batch_data['index'] == index]
            if row.empty:
                continue
            row = row.iloc[0].copy()
            row.drop(labels=['index'], inplace=True, errors='ignore')
            row['classification'] = classification
            row['naics_code'] = str(code)
            output_rows.append(row.drop(labels=['index'], errors='ignore'))

        if output_rows:
            return pd.DataFrame(output_rows)
        return None
                    

    def file_classification(self, input_file, output_file, data_type, admin_filter=None):

        """
        Processes and classifies a given file based on the data type.

        Args:
            input_file (str): The input CSV file containing unclassified data.
            output_file (str): The output CSV file where classified data will be stored.
            data_type (str): The type of data being classified (e.g., 'bldgs', 'places').
            admin_filter (dict): Optional filter for administrative boundaries (e.g., {'NAME_1': 'Maryland'}).
        """

        input_path = os.path.join(self.saving_path, input_file)
        data = pd.read_csv(input_path)
        data_columns = []
        if data_type == 'places':
            data_columns = ['names_primary', 'categories_main', 'categories_alternate']
            data = data[data_columns + self.admin_columns]
        else:
            data_columns = ['names_primary', 'subtype', 'class']
            data = data[data_columns + self.admin_columns]

        # Apply administrative filter if provided
        if admin_filter:
            for col, value in admin_filter.items():
                if col in data.columns:
                    data = data[data[col] == value]
                    logging.info(f"Filtered data to {len(data)} records for {col} = {value}")

        output_path = os.path.join(self.saving_path, output_file)

        self.append_csv(data, output_path, data_columns, data_type)

    def openai_classifier(self, admin_filter=None):
        # Parallelize across available CPU cores (network-bound workload benefits from threads)
        worker_count = min(len(self.data_types), os.cpu_count() or 1)
        logging.info(f"Running OpenAI classification with {worker_count} parallel worker(s) over {self.data_types}.")

        def _classify_single(data_type):
            logging.info(f'The OpenAI classification is in progress for {data_type}.')
            self.file_classification(
                f'unique_{self.class_type}_{data_type}_classes.csv',
                f'{data_type}_{self.class_type}_classified.csv',
                data_type,
                admin_filter
            )
            logging.info(f'OpenAI classification is complete for {data_type}.')

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_classify_single, data_type) for data_type in self.data_types]
            for future in as_completed(futures):
                # propagate exceptions immediately
                future.result()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Classify data using OpenAI api")

    # Add an argument to accept a list of strings for --class_type
    parser.add_argument('--data_types', type=str, nargs='+', required=False, default=['bldgs', 'places', 'bases_poly'],
                        help="Mention the datatypes you're interested in (space-separated list)")
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
        logging.info("No file exists with this name.")

    openai_classifier = OpenAIClassifier(country_code, index, args.data_types)
    openai_classifier.openai_classifier()
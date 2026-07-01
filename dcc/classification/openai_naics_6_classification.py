import os
import sys
import yaml
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import math
import logging
import json
import numpy as np
from openai import OpenAI
import argparse
import time
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, boundary_label, scoped_output_root
from dcc.utils.rate_limiter import RateLimiter
import dcc.utils.utils_vector as utils_vector
from dcc.classification.model_config import get_model_config


class NAICSOpenAIClassifier():

    def __init__(self, country_code, index, data_types = None):

        self.country_code = country_code
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        self.subregion = resolve_subset_context(self.config)
        self.client = OpenAI(api_key=os.environ.get("OPEN_AI_API_KEY"))
        self.saving_path = os.path.join(scoped_output_root(self.config, self.version, self.subregion), 'combined_classes')

        self.categories_code = self.config['classification']['naics_dict_4']
        self.categories_6_digit_code = self.config['classification']['naics_dict_6']
        self.admin_columns = self.config['admin_columns']

        self.class_type = self.config['classification']['class']
        self.boundary_admin_name = boundary_label(self.config, self.subregion)
        self.batch_size = self.config['openai_api']['batch_size']     
        self.max_workers = int(self.config['openai_api'].get('max_workers', 20))
        requests_per_minute = self.config['openai_api'].get('requests_per_minute')
        self.rate_limiter = RateLimiter.from_per_minute(requests_per_minute)
        
        if data_types is None:
            self.data_types = ['bldgs', 'places', 'bases_poly']
        else:
            self.data_types = data_types

    def create_manual_res(self, batch_data):
        print("Manually creating list of JSON.")
        batch_dic = batch_data.to_dict()
        return [ 
            {"index": batch_dic['index'], "naics_code_6": None, "category": "Others"}
            for item in batch_dic
        ]


    def api_req_prompt(self, prompt, batch_size, batch_data, retries=3, delay=5):
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
                print(content)
                parts = content.split('```json')
                if len(parts) > 1:
                    json_part = parts[1].split('```')[0]
                    content = json_part.strip()
                json_str = content.replace('```json', '').replace('```', '').strip()
                
                # Parse JSON
                parsed_json = json.loads(json_str)
                if not len(parsed_json) == batch_size:
                    print(f"Got less number of responses than expected. Retrying attempt {attempt + 1}/{retries}...")
                    attempt += 1
                    time.sleep(delay) 
                else:
                    return parsed_json

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error in response: {e}. Retrying attempt {attempt + 1}/{retries}...")
                attempt += 1
                time.sleep(delay)
            except Exception as e:
                attempt += 1
                retry_delay = delay * (2 ** (attempt - 1))
                logging.warning(f"API error: {type(e).__name__}: {e}. Retrying attempt {attempt}/{retries} after {retry_delay}s...")
                time.sleep(retry_delay)

        # If all retries fail
        print("Failed to get a valid response after multiple attempts.")
        return self.create_manual_res(batch_data)
    

    def get_prompt(self, batch_data, naics_code, classification):
        sub_df = batch_data.drop(['classification', 'naics_code'], axis=1)
        if 'NAME_1' in sub_df.columns:
            sub_df = sub_df.rename(columns={'NAME_1': 'location'})

        batch_dic = sub_df.to_dict(orient='records')
        prompt = f"""
        Classify the following data into one of the given 6 digit NAICS categories and codes:
        {self.naics_sub_category}.

        1. The data is the buildings and poi data in {self.boundary_admin_name}, these data are already classified into 4 digit NAICS code {int(naics_code)}: {classification}. 
            Try to find out more about the datapoint and further classify it into given 6 digit NAICS code.
        2. Perform a web search on the 'names_primary'/'description' and other attributes. Determine the type based on the result.

        Ensure the response is in JSON format, in the following structure:
        [
            {{"index": batch_dic['index'], "naics_code_6": <6-digit-code>, "category": "<category-name>"}},
            ...
        ]
        If the data does not fit any category, return:
        {{"index": batch_dic['index'], "naics_code_6": null, "category": "Others"}}.

        DO NOT RETURN ANY TEXT OTHER THAN JSON.

        batch_dic = {batch_dic}
        """
        return prompt

    def check_not_valid(self, naics_4, code, classification, categories=None, codes=None, sub_category=None):
        if code is None and classification != 'Others':
            print('1.:',naics_4, code, classification)
            self.fail_comment = "NAICS Code is missing for the Classification. Reclassify: "
            return True
        elif code is None and classification == 'Others':
            return False
        elif ((classification not in (categories or self.categories_6_naics_list)) or (int(code) not in (codes or self.code_6_naics_list))):
            print('2.:',naics_4, code, classification)
            self.fail_comment = "Either the NAICS code or the Classification is not from the provided dictionary. Reclassify: "
            return True
        else:
            lookup = sub_category or getattr(self, 'naics_sub_category', {})
            if lookup.get(int(code)) == classification:
                return False
            else:
                print('3.:',naics_4, code, classification)
                self.fail_comment = "NAICS code and Classification is not correctly mapped. Reclassify: "
                return True

        return True
                    
    def append_csv(self, data, output_file, naics_4_code):

        if os.path.exists(output_file) and os.stat(output_file).st_size == 0:
            # If the file is empty, delete it
            os.remove(output_file)

        to_classify = data
        if os.path.exists(output_file):
            df = pd.read_csv(output_file, low_memory=False)  
            if not pd.isna(naics_4_code):
                sub_df = df[df['naics_code'] == naics_4_code].copy()
            else:
                sub_df = df[df['classification'] == 'Others'].copy()
            
            print(f"Out of {data.shape[0]} Data for NAICS {naics_4_code}, {sub_df.shape[0]} is 6_NAICS classified")
            data_columns = [item for item in data.columns if not item.startswith('NAME_')]
            to_classify = data.merge(sub_df[data_columns], how='left', indicator=True).query('_merge == "left_only"').drop(columns=['_merge'])
            to_classify = to_classify.reset_index(drop=True)
            print(f"{to_classify.shape[0]} records are getting classified.")

        start_index = 0
            
        # Open the CSV in append mode
        batch_size = self.batch_size
        total_batches = math.ceil(to_classify.shape[0] / batch_size) if to_classify.shape[0] else 0
        if total_batches == 0:
            return

        worker_count = min(max(1, self.max_workers), total_batches)
        header_written = os.path.exists(output_file) and os.path.getsize(output_file) > 0

        progress_lock = threading.Lock()
        completed_batches = 0

        def classify_batch(batch_data):
            if pd.isna(naics_4_code):
                batch_data = batch_data.copy()
                batch_data['sub_classification'] = None
                batch_data['naics_six_code'] = None
                batch_data = batch_data.drop(columns=['index'], errors='ignore')
                return batch_data

            naics_sub_category = self.categories_6_digit_code[int(naics_4_code)]
            self.naics_sub_category = naics_sub_category
            prompt = self.get_prompt(batch_data, naics_4_code, self.categories_code[int(naics_4_code)])
            res_json = self.api_req_prompt(prompt, batch_data.shape[0], batch_data)

            code_6_naics_list = list(naics_sub_category.keys())
            categories_6_naics_list = list(naics_sub_category.values())

            output_rows = []
            for classification_result in res_json:
                index = int(classification_result["index"])
                classification = classification_result["category"]
                code = classification_result["naics_code_6"]

                retry_count = 0
                max_retries = 3

                while self.check_not_valid(naics_4_code, code, classification, categories_6_naics_list, code_6_naics_list, naics_sub_category) and retry_count < max_retries:
                    retry_count += 1
                    time.sleep(3)
                    single_row = batch_data[batch_data['index'] == index].reset_index(drop=False)
                    if single_row.empty:
                        break
                    prompt = self.fail_comment + self.get_prompt(single_row, naics_4_code, self.categories_code[int(naics_4_code)])
                    print("Retrying:", index, "naics code:", naics_4_code, "failed due to :", self.fail_comment)
                    response = self.api_req_prompt(prompt, 1, single_row)
                    try:
                        index = int(response[0]["index"])
                        classification = response[0]["category"]
                        code = response[0]["naics_code_6"]
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        print(f"Error in response: {e}. Retrying attempt...")

                if self.check_not_valid(naics_4_code, code, classification, categories_6_naics_list, code_6_naics_list, naics_sub_category):
                    classification = 'Others'
                    code = None

                row = batch_data[batch_data['index'] == index]
                if row.empty:
                    continue
                row = row.iloc[0].copy()
                row.drop(labels=['index'], inplace=True, errors='ignore')
                row['sub_classification'] = classification
                row['naics_six_code'] = str(code)
                output_rows.append(row)

            if output_rows:
                return pd.DataFrame(output_rows)
            return pd.DataFrame()

        with ThreadPoolExecutor(max_workers=worker_count) as executor, open(output_file, mode='a', newline='') as file:
            futures = []
            for batch_start in range(0, to_classify.shape[0], batch_size):
                batch_end = min(batch_start + batch_size, to_classify.shape[0])
                batch_data = to_classify.iloc[batch_start:batch_end].reset_index(drop=False)
                futures.append(executor.submit(classify_batch, batch_data))

            for future in as_completed(futures):
                batch_result = future.result()
                if batch_result is not None and not batch_result.empty:
                    batch_result.to_csv(file, header=not header_written, index=False)
                    header_written = True
                with progress_lock:
                    completed_batches += 1
                    print(f"For NAICS {naics_4_code}, {completed_batches}/{total_batches} batches processed.")
                    

    def file_classification(self, input_file, output_file, data_type):
        input_path = os.path.join(self.saving_path, input_file)
        data = pd.read_csv(input_path)
        
        output_path = os.path.join(self.saving_path, output_file)
        if data.empty:
            empty_df = data.copy()
            if 'sub_classification' not in empty_df.columns:
                empty_df['sub_classification'] = None
            if 'naics_six_code' not in empty_df.columns:
                empty_df['naics_six_code'] = None
            empty_df.to_csv(output_path, index=False)
            return
            
        data = data.sort_values(by=['naics_code']).reset_index(drop=True)
        naics_list = list(data['naics_code'].unique())
        for naics in naics_list:
            if not pd.isna(naics):
                df = data[(data['naics_code']) == naics].copy()
            else:
                df = data[(data['classification'] == 'Others') | (data['classification'].isna())]

            if df.shape[0] > 0:
                self.append_csv(df, output_path, naics)


    def openai_classifier(self):
        worker_count = min(len(self.data_types), os.cpu_count() or 1)
        print(f'Running 6-digit NAICS classification with {worker_count} parallel worker(s).')

        def _classify_single(data_type):
            print(f'The OpenAI 6-digit classification in progress for {data_type}.')
            self.file_classification(
                f'{data_type}_{self.class_type}_classified.csv',
                f'{data_type}_{self.class_type}_naics_6_classified.csv',
                data_type
            )
            print(f'OpenAI 6-digit classification is complete for {data_type}.')

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_classify_single, data_type) for data_type in self.data_types]
            for future in as_completed(futures):
                future.result()

        print('OpenAI classification is complete for all the datatypes.')

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
        print("No file exists with this name.")

    openai_classifier = NAICSOpenAIClassifier(country_code, index, args.data_types)
    openai_classifier.openai_classifier()
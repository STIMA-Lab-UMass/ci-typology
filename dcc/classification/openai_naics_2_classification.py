import os
import sys
import yaml
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from multiprocessing import Pool, current_process
import logging
import json
from openai import OpenAI
import argparse
import time
import re

load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))
from dcc.utils.overture_version import resolve_overture_version
from dcc.utils.subset_helper import resolve_subset_context, scoped_output_root
from dcc.utils.rate_limiter import RateLimiter
import dcc.utils.utils_vector as utils_vector
from dcc.classification.model_config import get_model_config

class NAICS2OpenAIClassifier():
    def __init__(self, country_code, index, input_path):

        self.country_code = country_code
        with open(os.path.join(os.environ.get("PROJECT_ROOT"),'envs',f"{self.country_code}_config_v{index}.yml"), 'r') as file:
            self.config = yaml.safe_load(file) 
            
        self.version = resolve_overture_version(self.config['overture_version'])
        self.config['overture_version'] = self.version
        self.subregion = resolve_subset_context(self.config)
        self.client = OpenAI(api_key=os.environ.get("OPEN_AI_API_KEY"))
        self.saving_path = os.path.join(scoped_output_root(self.config, self.version, self.subregion), 'combined_classes')
        self.input_path = input_path

        self.class_type = self.config['classification']['class']

        self.categories_dic = self.config['classification']['naics_dict_2']
        self.categories = list(self.categories_dic.values())
        self.code_naics = list(self.categories_dic.keys())
        self.batch_size = self.config['openai_api']['batch_size']
        requests_per_minute = self.config['openai_api'].get('requests_per_minute')
        self.rate_limiter = RateLimiter.from_per_minute(requests_per_minute)

    def create_manual_res(self, batch_data):

        """
        Creates a manual classification result if the OpenAI API fails.

        Args:
            batch_data (pandas.DataFrame): The batch of data to classify.

        Returns:
            list: A list of dictionaries containing default classification with "Others" category.
        """

        print("Manually creating list of JSON.")
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

    def get_prompt(self, batch_data):

        """
        Generates the prompt to send to the OpenAI API based on the batch data.

        Args:
            batch_data (pandas.DataFrame): The batch of data for which to generate the prompt.

        Returns:
            str: The formatted prompt to send to the OpenAI API.
        """

        batch_dic = batch_data.to_dict(orient='records')
        prompt = f"""
        Classify the following data into one of these NAICS categories and codes:
        {self.categories_dic}.

        1. Assign a 2 digit NAICS code and category STRICTLY from the provided naics dictionary.

        Ensure the response is in JSON format as a list of classified results for each row, in the following structure ("index" should be the value from index column of the data):
        [
            {{"index": batch_dic['index'], "naics_code": <2_digit_code>, "category": "<category-name>"}},
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


    def append_csv(self, data, output_file, data_columns):

        """
        Appends classified data to a CSV file, handling batch processing and retries.

        Args:
            data (pandas.DataFrame): The data to be classified and saved.
            output_file (str): The output file path where the classified data will be appended.
        """

        to_classify = data

        if os.path.exists(output_file):
            df = pd.read_csv(output_file)
            to_classify = data.merge(df[data_columns], how='left', indicator=True).query('_merge == "left_only"').drop(columns=['_merge'])
            to_classify = to_classify.reset_index(drop=True)

        start_index = 0

        print("Starting from index :", start_index, 'for file:', output_file)

        with open(output_file, mode='a', newline='') as file:
            batch_size = self.batch_size

            # Process data in batches of 100 rows
            for batch_start in range(start_index, data.shape[0], batch_size):
                batch_end = min(batch_start + batch_size, data.shape[0])
                batch_data = data.loc[batch_start:batch_end-1].reset_index(drop=False)

                prompt = self.get_prompt(batch_data)
                res_json = self.api_req_prompt(prompt, batch_data.shape[0], batch_data)
                print("Got Response for index:", res_json[0]["index"], 'to index:', res_json[len(res_json)-1]["index"])
                
                for classification_result in res_json:
                    index = int(classification_result["index"])
                    classification = classification_result["category"]
                    code = classification_result["naics_code"]

                    retry_count = 0
                    max_retries = 3

                    # Retry logic if the category is not in self.categories
                    while self.check_not_valid(code, classification) and retry_count < max_retries:

                        retry_count += 1
                        time.sleep(3)  # Wait for 2 seconds before retrying
                        print("Retrying:", index)
                        if classification in self.categories:
                            code = self.find_key_by_value(classification)
                            print('Corrected data: ', index, code, classification)
                        else:
                            batch_data = data.loc[[index]].reset_index(drop=False)
                            prompt = self.get_prompt(batch_data)
                            response = self.api_req_prompt(prompt, 1, batch_data)
                            try:
                                index = int(response[0]["index"])
                                classification = response[0]["category"]
                                code = response[0]["naics_code"]
                            except (json.JSONDecodeError, KeyError, ValueError) as e:
                                print(f"Error in response: {e}. Retrying attempt...")

                    # If after max retries the classification is still invalid, move on
                    if self.check_not_valid(code, classification):
                        classification = 'Others'
                        code = None

                    row = data.loc[index].copy()
                    row['naics_2_classification'] = classification
                    row['NAICS_2'] = code
                    row_df = pd.DataFrame([row])
                    row_df.to_csv(file, header=(file.tell() == 0), index=False)
           

    def file_classification(self, output_file):
        data = pd.read_csv(self.input_path)
            
        output_path = os.path.join(self.saving_path, output_file)
        self.append_csv(data, output_path, data.columns)

    def openai_classifier(self):
        # Edit the file names as per requirement
        print(f'The Openai classification in progress for overture classes. This might take several hours.')
        self.file_classification(f'{self.class_type}_overture_naics_2_classified.csv')
        
        print('OpenAi classification is complete for all the datatypes.')
    
    def get_data(self):

        file_path = os.path.join(self.saving_path, f'overture_{self.class_type}_classes.csv')
        final_df = pd.DataFrame()

        if not os.path.exists(file_path):
            data_types = ['bases_poly', 'places', 'bldgs']

            for data_type in data_types:
                file_name = f'unique_{self.class_type}_{data_type}_classes.csv'
                inp_file = os.path.join(self.saving_path, file_name)
                data_df = pd.read_csv(inp_file)
                
                if data_type != 'places':
                    attributes = ['class', 'subtype']
                else:
                    attributes = ['categories_main', 'categories_alternate']

                # Filter the rows
                filtered_df = data_df[attributes].drop_duplicates(subset=attributes)
                final_df = pd.concat([final_df, filtered_df], ignore_index=True)

            # Write the final DataFrame to the CSV file
            final_df.to_csv(file_path, index=False)

                                
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Classify data using OpenAI api")
    parser.add_argument('--config_name', required=True, help="The file name of the country's config.")
    parser.add_argument('-ifp', '--input_path', required=True, help="Input file path to the file which we need to classifiy") 

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

    openai_classifier = NAICS2OpenAIClassifier(country_code, index, args.input_path)
    # openai_classifier.get_data()
    openai_classifier.openai_classifier()
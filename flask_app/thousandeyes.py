#!/usr/bin/env python3
"""
Copyright (c) 2023 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__copyright__ = "Copyright (c) 2023 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import json
import os
import sys
import warnings
from urllib3.exceptions import InsecureRequestWarning
import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# Load in Environment Variables
load_dotenv()
THOUSAND_EYES_TOKEN = os.getenv('THOUSAND_EYES_TOKEN')

# Base URL
BASE_URL = "https://api.thousandeyes.com/v6"


class ThousandEyes:
    def __init__(self, logger):
        """
        Initialize the ThousandEyes class
        and save authentication headers
        """
        self.auth_token = THOUSAND_EYES_TOKEN
        self.logger = logger
        self.test_name_to_id = {}

        with requests.Session() as self.s:
            # Set session headers
            self.headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f"Bearer {self.auth_token}"
            }

            # Get list of tests, build test_name to id mapping
            self.__getTestList()

    def __getTestList(self):
        """
        Get current list of tests configured in TE Dashboard, map test name to id
        """
        url = f"{BASE_URL}/tests"
        params = {}

        resp = self.getData(url, params)

        if resp:
            resp_json = json.loads(resp)

            # If tests found, create dictionary mapping test name to id
            if 'test' in resp_json and len(resp_json['test']) > 0:
                for test in resp_json['test']:
                    if test['savedEvent'] == 0:
                        self.test_name_to_id[test['testName']] = test['testId']

                self.logger.info(f"Successfully authenticated to TE and created ['test_name': 'test_id'] mapping: {self.test_name_to_id}")
                return

        # If no tests found, end the entire program (major data missing)
        self.logger.error(f"Unable to find any test data from TE instance... {resp}")
        sys.exit(-1)

    def getTestDetails(self, test_id):
        """
        Get test details for test_id (agent, name, target, etc.)
        """
        url = f"{BASE_URL}/tests/{test_id}"
        params = {}

        resp = self.getData(url, params)

        if resp:
            resp_json = json.loads(resp)

            # If tests found, return test details
            if 'test' in resp_json:
                return resp_json['test'][0]

        self.logger.error(f"Unable to find any test data for test: {test_id}")
        return None

    def getTestData_NetworkE2E(self, test_id, time_period):
        """
        Get Network metric test data for the specific test id (if applicable), return results
        """
        url = f"{BASE_URL}/net/metrics/{test_id}"
        params = {"window": f"{time_period}s"}

        resp = self.getData(url, params)

        if resp:
            resp_json = json.loads(resp)

            # If data found, return data, if multiple pages, iterate through pages
            if 'net' in resp_json and 'metrics' in resp_json['net']:
                metrics = []
                while True:
                    if 'net' in resp_json and 'metrics' in resp_json['net']:
                        metrics += resp_json['net']['metrics']

                    # Call next url if paging present
                    if 'next' in resp_json['pages']:
                        resp = self.getData(resp_json['pages']['next'], {})
                        resp_json = json.loads(resp)
                    else:
                        break

                return metrics

        self.logger.error(f"Unable to find any test network data for test: {test_id}")
        return None

    def getTestID(self, test_name):
        """
        Return Test ID from mapping if test_name exists in mapping
        """
        if test_name in self.test_name_to_id:
            return self.test_name_to_id[test_name]
        else:
            return None

    def getData(self, get_url, params):
        """
        General function for HTTP GET requests with authentication headers
        """
        # console.print(f"Sending GET to: {get_url}")
        resp = self.s.get(get_url, headers=self.headers, params=params, verify=False)

        if resp.status_code == 200:
            return resp.text
        else:
            self.logger.error(f"Request FAILED (code {str(resp.status_code)}): {resp.text}")
            return None

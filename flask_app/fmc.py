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
import time
import warnings
from urllib3.exceptions import InsecureRequestWarning
import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# Load in Environment Variables
load_dotenv()
FMC_HOSTNAME = os.getenv('FMC_HOSTNAME')
FMC_USERNAME = os.getenv('FMC_USERNAME')
FMC_PASSWORD = os.getenv('FMC_PASSWORD')

# Define base URLS
PLATFORM_URL = "https://" + FMC_HOSTNAME + "/api/fmc_platform/v1"
CONFIG_URL = "https://" + FMC_HOSTNAME + "/api/fmc_config/v1"


class FirePower:
    def __init__(self, logger):
        """
        Initialize the FirePower class, log in to FMC,
        and save authentication headers
        """
        self.headers = None
        self.refresh_token = None
        self.auth_token = None
        self.global_UUID = None
        self.logger = logger

        with requests.Session() as self.s:
            # Authenticate to FMC
            self.logger.info(f"Attempting login to FMC: {FMC_HOSTNAME}...")
            self.authRequest()

    def authRequest(self):
        """
        Authenticate to FMC and retrieve auth token, refresh_token
        """
        auth_url = f"{PLATFORM_URL}/auth/generatetoken"
        resp = self.s.post(auth_url, auth=(FMC_USERNAME, FMC_PASSWORD), verify=False)
        if resp.status_code == 204:
            # API token, Refresh token, default domain, and other info returned within HTTP headers
            self.logger.info("Log in successful!")

            # Save auth token, refresh token, & global domain UUID
            self.auth_token = resp.headers["X-auth-access-token"]
            self.refresh_token = resp.headers["X-auth-refresh-token"]
            self.global_UUID = resp.headers["DOMAIN_UUID"]

            # Set session headers
            self.headers = {
                "Content-Type": "application/json",
                "X-auth-access-token": self.auth_token
            }
        else:
            self.logger.error(f"FMC Authentication Failed: {resp.text}")
            sys.exit(-1)

    def getDeviceRecords(self):
        """
        Get list of device records (FTDs)
        """
        url = f"{CONFIG_URL}/domain/{self.global_UUID}/devices/devicerecords"
        params = {"expanded": True, 'limit': 1000}

        resp = self.getData(url, params)

        if resp:
            resp_json = json.loads(resp)

            # If devices returned, return list of devices, else return None
            if 'items' in resp_json and len(resp_json['items']) > 0:
                return resp_json['items']

        self.logger.error(f"Unable to find any device records...")
        return None

    def getHealthMetrics(self, time_period_seconds, device_uuid, metric, regex_filter):
        """
        Return health monitor metrics for device(s) for a specific metric
        """
        startTime, endTime = calculate_health_time_period_unix(time_period_seconds)

        url = f"{CONFIG_URL}/domain/{self.global_UUID}/health/metrics"
        params = {
            "filter": f"deviceUUIDs:{device_uuid};metric:{metric};startTime:{startTime};endTime:{endTime};step:60;regexFilter:{regex_filter}", "limit": 1000}

        resp = self.getData(url, params)

        if resp:
            resp_json = json.loads(resp)

            # If metrics are returned, return metrics, else return None
            if 'items' in resp_json and len(resp_json['items']) > 0:
                return resp_json['items']

        self.logger.error(f"Unable to find any metrics in the last {time_period_seconds} seconds for the following filter: {params['filter']}")
        return None

    def getData(self, get_url, params):
        """
        General function for HTTP GET requests with authentication headers
        """
        # console.print(f"Sending GET to: {get_url}")
        resp = self.s.get(get_url, headers=self.headers, params=params, verify=False)

        # Access Token Invalid, recreate tokens re-call get
        if resp.status_code == 401:
            self.authRequest()
            resp = self.s.get(get_url, headers=self.headers, params=params, verify=False)

        if resp.status_code == 200:
            return resp.text
        else:
            self.logger.error(f"Request FAILED (code {str(resp.status_code)}): {resp.text}")
            return None


def calculate_health_time_period_unix(time_period):
    """
    Calculate time stamps for time period used in health metrics call in Unix Seconds
    :param time_period: time period (in seconds)
    :param time_period: time period (in seconds)
    :return: (startTime, endTime) Unix Seconds
    """
    # Get the current UTC time
    endTime = time.time()

    # Calculate the start time stamp
    startTime = endTime - time_period

    return startTime, endTime

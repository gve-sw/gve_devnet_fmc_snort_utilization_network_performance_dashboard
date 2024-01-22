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

import datetime
import json
import logging
from logging.handlers import TimedRotatingFileHandler

import requests as requests
from flask import Flask, render_template
from flask_caching import Cache

import config
from fmc import FirePower
from thousandeyes import ThousandEyes

# Flask Config
app = Flask(__name__)

# Configure Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Set up logging
logger = logging.getLogger('my_logger')
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s %(levelname)s: %(filename)s:%(funcName)s:%(lineno)d - %(message)s')

# log to stdout
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

# log to files (last 7 days, rotated at midnight local time each day)
log_file = "./logs/dashboard_logs.log"
file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=7)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# Define Global Class Object (contains all API methods used in dashboard)
fmc = FirePower(logger)
te = ThousandEyes(logger)


# Methods
def getSystemTimeAndLocation():
    """Returns location and time of accessing device"""
    # request user ip
    userIPRequest = requests.get('https://get.geojs.io/v1/ip.json')
    userIP = userIPRequest.json()['ip']

    # request geo information based on ip
    geoRequestURL = 'https://get.geojs.io/v1/ip/geo/' + userIP + '.json'
    geoRequest = requests.get(geoRequestURL)
    geoData = geoRequest.json()

    # create info string
    location = geoData['country']
    timezone = geoData['timezone']
    current_time = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
    timeAndLocation = "System Information: {}, {} (Timezone: {})".format(location, current_time, timezone)

    return timeAndLocation


@cache.memoize(timeout=300)  # Cache the result for 5 minutes
def get_ftd_devices():
    """
    Get a list of FTD devices, used for further calls to SNORT CPU health data and Device Name -> TE Test Mappings.
    :return: a curated list of FTD devices with explicit information (for webpage display) and device name
    """
    # Get device records (FTDs)
    device_records = fmc.getDeviceRecords()

    if device_records:
        # Extract out specific data about each device
        devices = [
            {
                'id': device['id'],
                'name': device["name"] if 'name' in device else None,
                'hostName': device["hostName"] if 'hostName' in device else None,
                'performanceTier': device["performanceTier"] if 'performanceTier' in device else None,
                'sw_version': device["sw_version"] if 'sw_version' in device else None,
                'snortEngine': device["snortEngine"] if 'snortEngine' in device else None,
                'snortVersion': device["metadata"]['snortVersion'] if 'snortVersion' in device['metadata'] else None,
            } for device in device_records
        ]

        # Useful logging
        log_devices = {}
        for device in devices:
            if device['name']:
                log_devices[device['name']] = device['id']

        logger.info(f"Found the following FTD Devices: {log_devices}")
        return devices

    return None


@cache.memoize(
    timeout=int(config.TIME_PERIOD_SECONDS))  # Cache the result for X seconds (1 hour - 3600 seconds by default)
def get_health_metrics(time_period_seconds, device_uuids, metric, regexFilter):
    """
    Return FMC health metrics for a specific metric across a list of device uuids. Refer to the documentation here
    for what the data corresponds to: https://www.cisco.com/c/en/us/support/docs/software-resources/220193-upgrade-fp
    -device-health-monitoring.html#anc6
    :param time_period_seconds: Time period to retrieve data for (in seconds)
    :param device_uuids: A list of FTD device unique ids to query data for
    :param metric: Specific metric to retrieve data for (options listed in API documentation)
    :param regexFilter: An additional filter to further specify the metric data (ex: metric = cpu, regexFilter = snort_avg)
    :return: dictionary mapping FTD device uuid -> raw metric data returned (format [[time_stamp_seconds, data_value], ...])
    """
    # Get health metric data for specific metric for each device_uuid
    health_metrics = []
    for device_uuid in device_uuids:
        health_metric = fmc.getHealthMetrics(int(time_period_seconds), device_uuid, metric, regexFilter)

        if health_metric:
            health_metrics.append(health_metric[0])

    if len(health_metrics) > 0:
        device_uuid_to_metrics = {}
        for health_metric in health_metrics:
            # Extract metric data returned
            try:
                metric_data = json.loads(health_metric['response'])

                # Extract raw data, create mapping of device uuid to raw data
                raw_data = metric_data['data']['result'][0]['values']

                # Create mapping of device uuid to raw data
                device_uuid_to_metrics[health_metric['deviceUUID']] = raw_data
            except Exception as e:
                logger.error(
                    f"Unable to find Health metric data for metric ({metric}), in devices: {health_metric['deviceUUID']}")
                logger.error(f"Error: {str(e)}")

        logger.info(f"Successfully found health metrics for the following devices: {list(device_uuid_to_metrics.keys())} with the "
                    f"following query parameters: Time Period ({time_period_seconds}), Metric ({metric}), "
                    f"Regex Filter ({regexFilter})")
        return device_uuid_to_metrics

    return None


@cache.memoize(
    timeout=int(config.TIME_PERIOD_SECONDS))  # Cache the result for X seconds (1 hour - 3600 seconds by default)
def get_te_test_results(time_period_seconds, devices):
    """
    Get ThousandEyes (TE) network test results for each test associated to each FTD device. Specifically extracts
    network metrics portion of test results (if supported for the test). Return TE Test details (name, etc.) as well
    :param time_period_seconds: Time period to retrieve data for (in seconds)
    :param devices: List of FTD devices to retrieve TE test data for (relies on Device Name -> TE Test Name mapping)
    :return: a tuple of TE test details and TE raw test data each a dictionary with FTD device uuid -> value mapping
    """
    te_results = {}
    te_test_details = {}

    # Get ThousandEyes Test Data, Test Details (leverage mapping of FTD name to TE Test Name in config.py)
    for device in devices:
        if device['name'] and device['name'] in config.TE_TEST_MAPPING:
            # Get test ID
            test_name = config.TE_TEST_MAPPING[device['name']]
            test_id = te.getTestID(test_name)

            if test_id:
                # Not none, means we found a valid test!

                # Get test details
                test_details = te.getTestDetails(test_id)

                if test_details:
                    te_test_details[device['id']] = test_details

                # Get test results
                test_data = te.getTestData_NetworkE2E(test_id, time_period_seconds)

                if test_data:
                    te_results[device['id']] = test_data

            else:
                logger.error(f"No test found for {test_name}, is the test created in ThousandEyes?")
        else:
            logger.error(f"No device to test mapping found for {device['name']}.... skipping")

    logger.info(f"Found TE test results for the following devices: {list(te_results.keys())}")
    logger.info(f"Found TE test details for the following devices: {list(te_test_details.keys())}")

    return te_results, te_test_details


def convert_snort_metric_data(raw_snort_cpu_metrics):
    """
    Convert raw SNORT CPU metric data (from get_health_metrics) to a more compatible format for analysis and graph display
    :param raw_snort_cpu_metrics: Raw data in the format [[time_stamp_unix_seconds, data_value],...]
    :return: return a new dictionary for each FTD device mapping "HH:MM": "data_value" (much easier to work with)
    """
    snort_cpu_metrics = {}

    for device in raw_snort_cpu_metrics:
        snort_cpu_metrics[device] = {}

        for data in raw_snort_cpu_metrics[device]:
            try:
                # Convert Unix Epoch back to UTC HH:MM:SS time for graph display
                converted_time = datetime.datetime.utcfromtimestamp(data[0]).strftime("%H:%M")

                # Build new dictionary of values with key (timestamp), value (cpu usage)
                snort_cpu_metrics[device][converted_time] = data[1]
            except Exception as e:
                # Skip all bogus metric values
                logger.error(f"Error: {str(e)}, skipping...")

    return snort_cpu_metrics


def convert_te_test_data(raw_te_results):
    """
    Convert raw TE network test data to per FTD device dictionary mapping "time_stamp": [average latency, Loss, Jitter].
    Remove necessary data, and format data values fpr easier graph display.
    :param raw_te_results: Raw TE network data (includes all return fields, time stamped list)
    :return: a new dictionary per FTD device mapping time stamp to list of 3 key values [average latency, Loss, Jitter]
    """
    te_results = {}

    for device in raw_te_results:
        te_results[device] = {}

        for data in raw_te_results[device]:
            try:
                # Extract HH:MM from date as key value in dictionary
                converted_time = data['date'].split()[1][:-3]

                # Build new dictionary of values with key (timestamp), values (latency, loss, jitter)
                te_results[device][converted_time] = [data['avgLatency'], data['loss'], data['jitter']]
            except Exception as e:
                # Skip all bogus data values
                logger.error(f"Error: {str(e)}, skipping...")
                continue

    return te_results


def calculate_snort_avg(snort_cpu_metrics):
    """
    Calculate SNORT CPU average value from converted SNORT CPU dataset, round to a single decimal point
    :param snort_cpu_metrics: Converted SNORT CPU metrics ["device_id": [{"HH:MM": cpu_value}, ...]]
    :return: SNORT CPU average value from dataset
    """
    snort_cpu_avgs = {}

    for device in snort_cpu_metrics:
        # Convert string values to floats and calculate the average
        cpu_values = [float(value) for value in snort_cpu_metrics[device].values()]
        average = sum(cpu_values) / len(cpu_values) if len(cpu_values) > 0 else 0

        # Round to one decimal place
        snort_cpu_avgs[device] = round(average, 1)

    logger.info(f'Calculated SNORT AVG per device to be: {snort_cpu_avgs}')
    return snort_cpu_avgs


def calculate_snort_max(snort_cpu_metrics):
    """
    Calculate SNORT CPU max value from converted SNORT CPU dataset, round to a single decimal point (for 24-hour max)
    :param snort_cpu_metrics: Converted SNORT CPU metrics ["device_id": [{"HH:MM": cpu_value}, ...]]
    :return: SNORT CPU max value from dataset
    """
    snort_cpu_max_24h = {}

    for device in snort_cpu_metrics:
        # Convert string values to floats and calculate the average
        cpu_values = [float(value) for value in snort_cpu_metrics[device].values()]
        max_cpu = max(cpu_values) if len(cpu_values) > 0 else 0

        # Round to one decimal place
        snort_cpu_max_24h[device] = round(max_cpu, 1)

    logger.info(f'Calculated SNORT MAX (24-hour period) per device to be: {snort_cpu_max_24h}')
    return snort_cpu_max_24h


def calculate_te_avg(te_results):
    """
    Calculate TE Latency average value from converted dataset, round to single decimal point
    :param te_results: Converted SNORT CPU metrics ["device_id": [{"HH:MM": [Latency, Loss, Jitter]]}, ...]]
    :return: TE Latency average value from dataset
    """
    te_latency_avgs = {}

    for device in te_results:
        # Extract first value (latency), calculate the average
        latency_values = [value[0] for value in te_results[device].values()]
        average = sum(latency_values) / len(latency_values) if len(latency_values) > 0 else 0

        # Round to one decimal place
        te_latency_avgs[device] = round(average, 1)

    logger.info(f'Calculated TE Average Latency per test to be: {te_latency_avgs}')
    return te_latency_avgs


# Flask Routes
@app.route('/')
def index():
    """
    Main landing page, retrieve data, render html page (periodically called via JavaScript to refresh data)
    """
    # Baseline values (in case of errors)
    snort_cpu_metrics = None
    snort_average = None
    snort_max_24h = None

    # Get list of FTD Devices
    devices = get_ftd_devices()

    if devices:
        # Create a list of device UUIDs for health metrics
        device_uuids = [device['id'] for device in devices if device['id']]

        # Get Snort CPU metrics for the last {config.TIME_PERIOD_SECONDS} seconds
        raw_snort_cpu_metrics = get_health_metrics(int(config.TIME_PERIOD_SECONDS), device_uuids, 'cpu', 'snort_avg')

        if raw_snort_cpu_metrics:
            # Modify SNORT CPU Data to make it more presentable
            snort_cpu_metrics = convert_snort_metric_data(raw_snort_cpu_metrics)

            # Calculate SNORT Average usage over data period
            snort_average = calculate_snort_avg(snort_cpu_metrics)

        # Get Snort CPU metrics for the last 24 hours
        raw_snort_cpu_metrics_24h = get_health_metrics(86400, device_uuids, 'cpu', 'snort_avg')

        if raw_snort_cpu_metrics_24h:
            # Modify SNORT CPU Data to make it more presentable
            snort_cpu_metrics_24h = convert_snort_metric_data(raw_snort_cpu_metrics_24h)

            # Calculate max CPU value in 24 hours
            snort_max_24h = calculate_snort_max(snort_cpu_metrics_24h)

        # Get TE Test Data for each associated test with each device (Modify TE Latency Test Data to make it more
        # presentable)
        raw_te_results, raw_te_test_details = get_te_test_results(int(config.TIME_PERIOD_SECONDS), devices)
        te_results = convert_te_test_data(raw_te_results)

        # Calculate ThousandEyes Test Average latency over data period
        te_latency_average = calculate_te_avg(te_results)

        # Append metrics to devices list for display on dashboard
        for device in devices:
            # TE Test Details
            if raw_te_test_details and device['id'] in raw_te_test_details:
                device['te_test_details'] = raw_te_test_details[device['id']]
            else:
                # No data found for some reason
                device['te_test_details'] = None

            # Snort Metrics
            if snort_cpu_metrics and device['id'] in snort_cpu_metrics:
                device['snort_cpu_data'] = snort_cpu_metrics[device['id']]
            else:
                # No data found for some reason
                device['snort_cpu_data'] = None

            # Snort AVG
            if snort_average and device['id'] in snort_average:
                device['snort_cpu_avg'] = snort_average[device['id']]
            else:
                # No data found for some reason
                device['snort_cpu_avg'] = None

            # Snort MAX
            if snort_max_24h and device['id'] in snort_max_24h:
                device['snort_cpu_max'] = snort_max_24h[device['id']]
            else:
                # No data found for some reason
                device['snort_cpu_max'] = None

            # TE Test Results
            if te_results and device['id'] in te_results:
                device['te_test_data'] = te_results[device['id']]
            else:
                # No data found for some reason
                device['te_test_data'] = None

            # TE Latency AVG
            if te_latency_average and device['id'] in te_latency_average:
                device['te_latency_avg'] = te_latency_average[device['id']]
            else:
                # No data found for some reason
                device['te_latency_avg'] = None

        # Sort FTD Devices by Name
        devices = sorted(devices, key=lambda device: device['name'])

    return render_template('index.html', hiddenLinks=False, timeAndLocation=getSystemTimeAndLocation(), devices=devices,
                           max_snort_cpu=int(config.MAX_SNORT_UTILIZATION), max_te_latency=int(config.MAX_TE_LATENCY),
                           test_period=int(config.TIME_PERIOD_SECONDS))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

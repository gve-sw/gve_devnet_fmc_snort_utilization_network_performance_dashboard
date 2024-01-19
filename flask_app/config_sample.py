# FMC Health Metrics, ThousandEyes Test Params
TIME_PERIOD_SECONDS = 3600  # Default of the last hour worth of data

# Thresholds for Equation
MAX_SNORT_UTILIZATION = 5  # Whole numbers only (ex: 5, 10, 85, etc.)
MAX_TE_LATENCY = 20  # Whole numbers only (ex: 5, 10, 85, etc.)

# ThousandEyes Network Test mappings to FTD devices (FTD Device Name -> ThousandEyes Network Test Name)
TE_TEST_MAPPING = {
    '<ftd_device_name>': '<thousand_eyes_test_name>'
}

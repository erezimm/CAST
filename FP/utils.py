import datetime
import clickhouse_connect
import time

import pandas as pd
import numpy as np
from astropy.time import Time

from django.conf import settings

# Logging
import logging
logger = logging.getLogger(__name__)


N_MAX_RESULTS = 200  # Maximum number of results to return from the database


def connect_to_clickhouse():
    host = settings.EUCLID['host']
    port = settings.EUCLID['port']
    username = settings.EUCLID['username']
    password = settings.EUCLID['password']
    client = clickhouse_connect.get_client(host=host, port=port, username=username, password=password)
    return client


def get_unique_request_id():
    epoch_start = datetime.datetime(2025, 1, 1)
    seconds_since_epoch = (datetime.datetime.now() - epoch_start).total_seconds()
    request_id = int(seconds_since_epoch * 1000)  # Convert to milliseconds and ensure it's an integer
    return request_id

def get_last_fp(ra, dec, jd_start, jd_end, max_results=N_MAX_RESULTS, use_existing_ref=True):
    """
    Fetch data from the ClickHouse database based on RA, DEC, and days.
    """
    client = connect_to_clickhouse()
    
    # Write request into requests table
    request_id = get_unique_request_id()
    user_id =  settings.EUCLID['CAST_user_id']

    query = f""" INSERT INTO last.forcedphot_requests 
    (request_id, user_id, ra, dec, jd_start, jd_end, n_epoch_max, useexistingref) 
    VALUES  ( {request_id}, {user_id}, {ra}, {dec}, {jd_start}, {jd_end}, {max_results}, {use_existing_ref} )
    """
    logger.info("Inserting forcedphot request: %s", query)
    client.query(query)

    retries = 6
    retry_delay = 5  # seconds

    for i in range(retries):
        query = f""" select request_id, user_id, status, ra, dec, jd_start, jd_end, fieldid, mountnum, camnum, cropid  
                     from last.forcedphot_requests
                     where request_id = {request_id} """
        logger.info(f"Checking status for request id {request_id} attempt {i+1}/{retries}")
        query_result = client.query(query)
        df_tables = pd.DataFrame(query_result.result_rows, columns=query_result.column_names)
        
        if not df_tables.empty:
            status = df_tables['status'][0]
            if status == 0:  # Pending
                logger.info("Request is pending, waiting for processing...")
            elif status == 1:  # OK
                logger.info("Request processed successfully.")
                break
            elif status == 2:  # Failed
                logger.error("Status 2, no results.")
                return None, None
        
        time.sleep(retry_delay)

    if status != 1:
        raise TimeoutError("Timeout from DB. Try again later")

    # Fetch the results from the forced photometry output table     
    query = f""" select * from last.forcedphotsub_output
    where request_id = {request_id}
                """
    logger.info("Fetching forcedphot results: %s", query)
    query_result = client.query(query)
    fp_results = pd.DataFrame(query_result.result_rows, columns=query_result.column_names)
    if fp_results.empty:
        logger.warning("No results found for request_id: %s", request_id)
        return None, None
    
    detections_mask = np.logical_and(fp_results['sn'] > 3, fp_results['s'] > 3)
    detections = fp_results[detections_mask]
    nondetections = fp_results[~detections_mask]
    
    return detections, nondetections



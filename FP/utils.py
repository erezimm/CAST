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
    db_settings = settings.FORCED_PHOTOMETRY_DB
    host = db_settings['host']
    port = db_settings['port']
    username = db_settings['username']
    password = db_settings['password']
    client = clickhouse_connect.get_client(host=host, port=port, username=username, password=password)
    return client


def get_unique_request_id():
    epoch_start = datetime.datetime(2025, 1, 1)
    seconds_since_epoch = (datetime.datetime.now() - epoch_start).total_seconds()
    request_id = int(seconds_since_epoch * 1000)  # Convert to milliseconds and ensure it's an integer
    return request_id

def get_last_fp(ra, dec, jd_start, jd_end, 
                fieldid="''", cropid=0, mountnum=0, camnum=0,
                max_results=N_MAX_RESULTS, use_existing_ref=True, resub=False,
                timeout=30):
    """
    Fetch data from the ClickHouse database based on RA, DEC, and days.
    """
    client = connect_to_clickhouse()
    
    request_id = get_unique_request_id()
    user_id =  settings.FORCED_PHOTOMETRY_DB['CAST_user_id']

    query = f""" INSERT INTO last.forcedphot_requests 
    (request_id, user_id, ra, dec, jd_start, jd_end, fieldid, cropid, mountnum, camnum, n_epoch_max, useexistingref, resub) 
    VALUES  ( {request_id}, {user_id}, {ra}, {dec}, {jd_start}, {jd_end}, {fieldid}, {cropid}, {mountnum}, {camnum}, {max_results}, {use_existing_ref} , {resub})
    """
    logger.info("Inserting forcedphot request: %s", query)
    client.query(query)

    timeout_time = time.time() + timeout
    retry_delay = 5  # seconds

    while True:
        if time.time() > timeout_time:
            raise TimeoutError(f"Timeout from DB. Try again later. Request ID: {request_id}")
        
        query = f""" select request_id, user_id, status, ra, dec, jd_start, jd_end, fieldid, mountnum, camnum, cropid  
                     from last.forcedphot_requests
                     where request_id = {request_id} """

        query_result = client.query(query)
        df_tables = pd.DataFrame(query_result.result_rows, columns=query_result.column_names)
        
        if not df_tables.empty:
            status = df_tables['status'][0]
            if status == 0:  # Pending
                logger.info(f"Request ID {request_id} is still pending...")
            elif status == 1:  # OK
                logger.info("Request processed successfully.")
                break
            elif status == 2:  # Failed
                logger.error("Status 2, no results.")
                return None, None
        
        time.sleep(retry_delay)

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



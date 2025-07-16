import datetime
import pandas as pd
import clickhouse_connect
import time

from astropy.time import Time

from django.conf import settings


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

def get_last_fp(ra, dec, days_ago):
    """
    Fetch data from the ClickHouse database based on RA, DEC, and days.
    """
    client = connect_to_clickhouse()
    
    # Write request into requests table
    request_id = get_unique_request_id()
    user_id =  settings.EUCLID['CAST_user_id']

    jd_now = Time.now().jd
    jd_start = jd_now - days_ago

    query = f""" INSERT INTO last.forcedphot_requests 
    (request_id, user_id, ra, dec, jd_start, jd_end) 
    VALUES  ( {request_id}, {user_id}, {ra}, {dec}, {jd_start}, {jd_now} )
    """
    client.query(query)

    time.sleep(5)  # Wait for the request to be logged TODO: query requests for status

    # Fetch the results from the forced photometry output table     
    query = f""" select * from last.forcedphotsub_output
    where request_id = {request_id}
                """
    query_result = client.query(query)
    fp_results = pd.DataFrame(query_result.result_rows, columns=query_result.column_names)
    print(fp_results)   
    res_daysago = jd_now - fp_results['jd']
    res_magpsf = fp_results['mag_psf']

    return res_daysago, res_magpsf
    
    try:
        data = client.query_dataframe(query)
        return data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()  # Return an empty DataFrame on error




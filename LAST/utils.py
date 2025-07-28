import clickhouse_connect
import pandas as pd
from astropy.time import Time
from astroplan import Observer
from astropy.coordinates import EarthLocation
import astropy.units as u
from datetime import datetime
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.patches import Polygon
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from astropy.coordinates import SkyCoord, Angle
import argparse
import os
import matplotlib
matplotlib.use('Agg')
from django.conf import settings
import re

#Get the logger
import logging
logger = logging.getLogger(__name__)


# connect to ClickHouse database
def connect_to_clickhouse():
    host = settings.LAST_DB['host']
    port = settings.LAST_DB['port']
    username = settings.LAST_DB['username']
    password = settings.LAST_DB['password']
    client = clickhouse_connect.get_client(host=host, port=port, username=username, password=password)
    return client

def plot_galactic_plane(ax):
    galactic_l = np.linspace(0, 360, 1000) * u.deg  # Galactic longitude
    galactic_b_plane = np.zeros_like(galactic_l)  # Galactic latitude = 0
    galactic_b_upper = np.ones(1000) * 20 * u.deg  # +10 degrees
    galactic_b_lower = -galactic_b_upper # -10 degrees
    
    # Convert Galactic boundaries to RA/Dec
    galactic_plane_coords = SkyCoord(l=galactic_l, b=galactic_b_plane, frame="galactic")
    galactic_upper_coords = SkyCoord(l=galactic_l, b=galactic_b_upper, frame="galactic")
    galactic_lower_coords = SkyCoord(l=galactic_l, b=galactic_b_lower, frame="galactic")
    
    ra_dec_coords = galactic_plane_coords.transform_to("icrs")  # Convert to RA/Dec
    ra_plane = -ra_dec_coords.ra.radian + np.pi  # Shift RA to [-π, π]
    dec_plane = ra_dec_coords.dec.radian
    ra_dec_coords_upper = galactic_upper_coords.transform_to("icrs")  # Convert to RA/Dec
    ra_upper = -ra_dec_coords_upper.ra.radian + np.pi
    dec_upper = ra_dec_coords_upper.dec.radian
    ra_dec_coords_lower = galactic_lower_coords.transform_to("icrs")  # Convert to RA/Dec
    ra_lower = -ra_dec_coords_lower.ra.radian + np.pi
    dec_lower = ra_dec_coords_lower.dec.radian
    
    # Split data to avoid crossing RA boundaries
    mask = np.abs(np.diff(ra_plane)) > np.pi  # Identify large jumps (RA boundary crossings)
    plane_segments = np.split(np.column_stack((ra_plane, dec_plane)), np.where(mask)[0] + 1)
    mask = np.abs(np.diff(ra_upper)) > np.pi  # Identify large jumps (RA boundary crossings)
    upper_segments = np.split(np.column_stack((ra_upper, dec_upper)), np.where(mask)[0] + 1)
    mask = np.abs(np.diff(ra_lower)) > np.pi  # Identify large jumps (RA boundary crossings)
    lower_segments = np.split(np.column_stack((ra_lower, dec_lower)), np.where(mask)[0] + 1)
    
    # Plot each segment separately
    for plane_seg, upper_seg, lower_seg in zip(plane_segments, upper_segments, lower_segments):
        # ax.fill_betweenx(
        #     y=np.concatenate([lower_seg[:, 1], upper_seg[:, 1]]),  # Dec boundaries
        #     x1=np.concatenate([lower_seg[:, 0], upper_seg[:, 0]]),  # RA boundaries
        #     x2=plane_seg[:, 0],  # Plane RA
        #     color="yellow",
        #     alpha=0.5,
        # )
        ax.plot(plane_seg[:, 0], plane_seg[:, 1], color="black", linestyle="--", linewidth=1)
        ax.plot(upper_seg[:, 0], upper_seg[:, 1], color="red", linestyle="--", linewidth=1)
        ax.plot(lower_seg[:, 0], lower_seg[:, 1], color="red", linestyle="--", linewidth=1)

def plot_fields_with_ra_0_to_24(fields, field_counts,colormap=True):
    fig = plt.figure(figsize=(12, 6))
    ax = fig.add_subplot(111, projection="mollweide")
    ax.set_title("", fontsize=16, weight='bold')
    # Plot the galactic plane
    plot_galactic_plane(ax)

    # Normalize the field counts for the color map
    if colormap:
        norm = Normalize(vmin=field_counts['count'].min(), vmax=10)#field_counts['count'].max())
        cmap = plt.cm.nipy_spectral  # Use a vibrant colormap
        sm = ScalarMappable(norm=norm, cmap=cmap)
    
    # field_size = np.sqrt(30) * u.deg  # 30 arcmin = 0.5°, so half-size is 0.25°
    for _, row in fields.iterrows():
        RA_max = row.RA_max
        RA_min = row.RA_min
        Dec_min = row.Dec_min
        Dec_max = row.Dec_max
        
        field = row.ID
        corners = [
            (RA_min, Dec_min),
            (RA_min, Dec_max),
            (RA_max, Dec_max),
            (RA_max, Dec_min),
        ]

        # Convert to radians and Mollweide-safe coordinates
        vertices = []
        for ra_deg, dec_deg in corners:
            ra_rad = np.deg2rad(ra_deg)
            ra_rad = ((-ra_rad + np.pi + np.pi) % (2 * np.pi)) - np.pi  # RA wrapping
            if np.abs(ra_rad) == np.pi:
                if ra_rad < 0:
                    ra_rad += 0.01
                else:
                    ra_rad -= 0.01
            dec_rad = np.deg2rad(dec_deg)
            vertices.append((ra_rad, dec_rad))

        # Check for long RA span and fix it in-place
        ra_vals = [ra for ra, _ in vertices]
        ra_span = np.ptp(ra_vals)
        if np.rad2deg(ra_span) > 180:
            logger.warning(f"Field {field} has large RA span ({np.rad2deg(ra_span):.1f}°): {vertices}")
            # Fix: shift negative RA values by +2π and rewrap to [-π, π]
            vertices = [(((ra + 2 * np.pi) if ra < 0 else ra), dec) for ra, dec in vertices]
            # vertices = [(((ra + np.pi) % (2 * np.pi)) - np.pi, dec) for ra, dec in vertices]

        if colormap:
            count = field_counts[field_counts['field'] == int(field)]['count'].values[0]
            if count > 10:
                color = "lightblue"
            else:
                color = cmap(norm(count))
        else:
            color = "lightblue"

        poly = Polygon(vertices, closed=True, edgecolor="black", facecolor=color, lw=0.5)
        ax.add_patch(poly)

    ax.grid(True, linestyle='--', alpha=0.5)
    ax.set_xlabel("RA (hours)")
    ax.set_ylabel("Dec (deg)")
    
    # Update RA ticks to 0 to 24 hours
    ra_tick_positions = np.linspace(np.pi, -np.pi, 13)  # Tick positions in radians
    ra_tick_labels = [f"{int(tick % 24)}h" for tick in np.linspace(0, 24, 13)]  # Wrap at 24 hours
    ax.set_xticks(ra_tick_positions)
    ax.set_xticklabels(ra_tick_labels)
    
    # Add colorbar
    if colormap:
        cbar = plt.colorbar(sm, ax=ax, orientation="vertical", pad=0.1, aspect=30, shrink=0.9)
        cbar.set_label("Field Observation Count", fontsize=12, weight='bold')
        cbar.ax.tick_params(labelsize=10, width=2, length=6)
        cbar.outline.set_edgecolor("black")
        cbar.outline.set_linewidth(2)
        
def format_time_no_ms(astropy_time):
    """Convert astropy Time to ISOT string without milliseconds."""
    dt = astropy_time.to_datetime()
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def get_sunset_sunrise(date_str):
    loc = EarthLocation(lat=30.052984, lon=35.040677, height=400)
    observer = Observer(location=loc, timezone='UTC')
    date = Time(date_str)
    sunset = observer.sun_set_time(date, which='next')
    sunrise = observer.sun_rise_time(date + 1*u.day, which='next')
    return sunset, sunrise

def get_fields_per_date(date_str,mounts=['01', '02', '03', '05', '06', '07', '08', '09', '10']):
    client = connect_to_clickhouse()
    sunset, sunrise = get_sunset_sunrise(date_str)
    sunset = format_time_no_ms(sunset)
    sunrise = format_time_no_ms(sunrise)
    records = []
    for mount in mounts:
        query = f"""select * FROM observatory_operation.operation_strings  
                    WHERE rediskey LIKE 'unitCS.set.GeneralStatus:{mount}%' 
                    AND value LIKE 'T%%observing%%'
                    AND time > '{sunset}' AND time < '{sunrise}'
                    """
        query_result = client.query(query)
        # query_result = client.query("SHOW TABLES FROM observatory_operation")
        df = pd.DataFrame(query_result.result_rows, columns=query_result.column_names)
        if df.empty:
            continue
        df['mount'] = mount
        df['field']= df['value'].apply(lambda x: x[x.find('"')+1:x.rfind('"')])
        df['target'] = df['field'].apply(lambda x: x.split('.')[1] if '.' in x else pd.NA)
        # df['field'] = df['field'].apply(lambda x: int(x.split('.')[0]) if '.' in x else int(x))
        df['field'] = df['field'].apply(lambda x: int(re.sub(r'\D', '', x.split('.')[0])) if re.search(r'\d', x) else pd.NA)

        df_reduced = df[['mount','time', 'field', 'target']]
        records.append(df_reduced)
    # Combine all into a single DataFrame
    if records == []:
        summary_df = pd.DataFrame(columns=['mount', 'time', 'field', 'target'])
        field_counts = pd.DataFrame(columns=['field', 'count'])
        return summary_df,field_counts
    summary_df = pd.concat(records, ignore_index=True)
    summary_df['count'] = summary_df['field'].value_counts()
    field_counts = summary_df['field'].value_counts().reset_index()
    client.close()
    return summary_df,field_counts

def plot_fields(date_str=datetime.now().strftime('%Y-%m-%d'), colormap=True):
    last_fields_path = os.path.join(settings.MEDIA_ROOT, "LAST")
    last_fields = pd.read_pickle(last_fields_path+"/LAST_sky_fields.pkl")
    last_fields['RA_min_rad'] = np.deg2rad(last_fields.RA_min)
    last_fields['RA_max_rad'] = np.deg2rad(last_fields.RA_max)
    last_fields['Dec_min_rad'] = np.deg2rad(last_fields.Dec_min)
    last_fields['Dec_max_rad'] = np.deg2rad(last_fields.Dec_max)

    summary_df,field_counts = get_fields_per_date(date_str)
    survey_fields = summary_df#[summary_df['target'].isna()]
    last_fields = last_fields[last_fields['ID'].isin(survey_fields['field'])]
    plot_fields_with_ra_0_to_24(last_fields, field_counts)
    
    plt.tight_layout()
    plot_path = os.path.join(settings.STATIC_ROOT, "LAST", "plots")
    plt.savefig(plot_path+f"/{date_str}.png", dpi=300)
    plt.close()
    return plot_path
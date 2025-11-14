#!/usr/bin/env python3

from typing import Tuple
import tkinter as tk
import matplotlib.pyplot as plt
from astropy.visualization import astropy_mpl_style
from astropy.io import fits
from PIL import Image, ImageTk
from PIL.Image import Resampling
import subprocess
import re
import argparse
from pathlib import Path
from itertools import cycle
import logging
import shutil
import os
import time
from datetime import datetime
import pytz
import json
from dataclasses import dataclass, asdict, field
from tqdm import tqdm

RMS_HOST = 'pi@10.10.0.155'
MIN_FITS_THRESHOLD = 5  # Minimum average FITS files across stations for a "good night"
# SSH connection reuse options to prevent connection exhaustion on RMS server
# ControlMaster=auto: reuse existing connections, ControlPersist=300: keep connections alive 5min
SSH_OPTS = '-o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=300 -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=2 -o BatchMode=yes -o StrictHostKeyChecking=no'


def is_server_available(max_retries: int = 3) -> bool:
    """ Check if the RMS server is reachable with retry logic """
    for attempt in range(max_retries):
        try:
            # Simple ping-like check with timeout
            cmd = f"timeout 10 ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no {RMS_HOST} 'echo server_available'"
            result = subprocess.check_output(cmd, shell=True, timeout=15, stderr=subprocess.PIPE).decode("utf-8").strip()
            if result == "server_available":
                if attempt > 0:
                    logging.info(f"Server available after {attempt + 1} attempts")
                return True
        except subprocess.TimeoutExpired as e:
            logging.warning(f"Server check attempt {attempt + 1}/{max_retries}: SSH timeout after {e.timeout}s")
        except subprocess.CalledProcessError as e:
            logging.warning(f"Server check attempt {attempt + 1}/{max_retries}: SSH failed with exit code {e.returncode}: {e.stderr.decode() if e.stderr else 'no stderr'}")
        except Exception as e:
            logging.warning(f"Server check attempt {attempt + 1}/{max_retries}: Unexpected error: {type(e).__name__}: {e}")
        
        if attempt < max_retries - 1:
            sleep_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
            logging.debug(f"Waiting {sleep_time}s before retry...")
            time.sleep(sleep_time)
    
    logging.error(f"Server {RMS_HOST} unavailable after {max_retries} attempts")
    return False

@dataclass
class State:
    last_dirs: dict = field(default_factory=dict) # last dir per station: {'station-1': 'dir1', 'station-2': 'dir2'}
    last_switch: str = field(default='') # last time we switched to a new dir
    last_check: str = field(default='') # last time we checked for new images
    last_server_check: str = field(default='') # last time we attempted server connection (success or failure)
    image_dir: str = field(default='current')
    active_stations: list = field(default_factory=list) # stations that had images in last check

    def save(self, filename: str):
        with open(filename, "w") as f:
            json.dump(asdict(self), f, indent=4)

    @classmethod
    def load(cls, filename: str):
        with open(filename, "r") as f:
            data = json.load(f)

        # Handle backward compatibility: migrate last_dir to last_dirs
        if 'last_dir' in data and 'last_dirs' not in data:
            data['last_dirs'] = {'default': data.pop('last_dir')}

        # Set default values for missing fields
        if 'last_dirs' not in data:
            data['last_dirs'] = {}
        if 'active_stations' not in data:
            data['active_stations'] = []
        if 'last_check' not in data:
            data['last_check'] = ''
        if 'last_server_check' not in data:
            data['last_server_check'] = ''
        
        # Remove deprecated fields that are no longer part of the State class
        if 'last_good_night_check' in data:
            data.pop('last_good_night_check')

        return cls(**data)


class Application():
    images = None

    def __init__(self, state: State, full_screen, monitor_index=None):
        self.window = tk.Tk()
        self.state = state
        self.monitor_index = monitor_index
        # tk.Tk.__init__(self, *args, **kwargs)
        self.window.attributes("-topmost", True)
        self.window.attributes("-topmost", False)

        self.window.title("Cams Slideshow")
        self.window.resizable(width=True, height=True)

        # Configure display positioning before setting fullscreen (only if monitor specified)
        if full_screen and monitor_index is not None:
            self._configure_display_position()
        elif full_screen:
            # Just use default fullscreen without specific monitor positioning
            # Use single monitor dimensions (not combined width of all monitors)
            self.window.update_idletasks()
            self.target_width = 1920
            self.target_height = 1080

        self.window.attributes("-fullscreen", full_screen)
        self.current_slide = tk.Label(bg="black", highlightbackground='black', highlightcolor='black', highlightthickness=1)
        self.duration_ms = 5000
        # Create a label with text, specifying the font size and color
        self.text_label = tk.Label(self.window, text="", font=("Arial", 24), fg="white", bg="black", anchor="nw")
        # Position the label in the top left corner
        self.text_label.place(x=10, y=10)  # Adjust the x and y values as needed

        # Bind the Escape key to the exit_fullscreen method
        self.window.bind("<Escape>", self.exit_fullscreen)
        self.window.bind("<Return>", self.exit_fullscreen)  # Enter key
        self.window.bind("<space>", self.exit_fullscreen)  # Space key
        self.current_slide.pack(fill=tk.BOTH, expand=True)

    def _get_primary_monitor(self, monitor_index=0):
        """Detect primary monitor using DRM on Wayland"""
        # Find connected displays
        drm_path = '/sys/class/drm'
        connected_displays = []

        for entry in sorted(os.listdir(drm_path)):
            if any(interface in entry for interface in ['HDMI-A-', 'DP-', 'eDP-']):
                status_path = f"{drm_path}/{entry}/status"
                modes_path = f"{drm_path}/{entry}/modes"

                if os.path.exists(status_path) and os.path.exists(modes_path):
                    with open(status_path, 'r') as f:
                        if f.read().strip() == 'connected':
                            with open(modes_path, 'r') as f:
                                mode = f.readline().strip()
                                if 'x' in mode:
                                    width, height = mode.split('x')
                                    connected_displays.append({
                                        'name': entry,
                                        'width': int(width),
                                        'height': int(height),
                                        'x': 0,  # Will be calculated based on index
                                        'y': 0
                                    })

        if not connected_displays:
            logging.error("No connected displays found!")
            return {'name': 'fallback', 'width': 1920, 'height': 1080, 'x': 0, 'y': 0}

        # Physical layout: HDMI-A-2 (left) and HDMI-A-1 (right) due to cabling
        # Reverse display order to match physical layout
        connected_displays.reverse()

        # Calculate x offsets based on monitor positions (left to right)
        x_offset = 0
        for i, display in enumerate(connected_displays):
            display['x'] = x_offset
            x_offset += display['width']

        # Select the requested monitor
        if monitor_index >= len(connected_displays):
            logging.warning(f"Monitor index {monitor_index} not available, using monitor 0")
            monitor_index = 0

        target_display = connected_displays[monitor_index]
        logging.info(f"Selected monitor {monitor_index}: {target_display['name']} at position ({target_display['x']}, {target_display['y']}) with size {target_display['width']}x{target_display['height']}")
        return target_display
    
    def _configure_display_position(self):
        """Configure window position for selected monitor"""
        self.window.update_idletasks()

        monitor = self._get_primary_monitor(self.monitor_index)
        geometry = f"{monitor['width']}x{monitor['height']}+{monitor['x']}+{monitor['y']}"
        self.window.geometry(geometry)

        self.target_width = monitor['width']
        self.target_height = monitor['height']

        logging.info(f"Configured for monitor {self.monitor_index}: {geometry}")

    def exit_fullscreen(self, event=None):
        # To toggle fullscreen off
        self.window.attributes("-fullscreen", False)
        # Or to close the application, uncomment the next line
        self.window.destroy()

    def slide_filename_to_date(self, filename):
        # Extract the date and time parts, handling both old and new format
        parts = filename.split('_')
        
        # Handle filenames that start with FF_ prefix
        if parts[0] == 'FF':
            # FF_BE000D_20250802_205157_744_0048896_BE000D format
            date_str, time_str = parts[2:4]
        else:
            # Standard format: 20250802_204215_924_0044544_BE0012 
            date_str, time_str = parts[0:2]

        # Extract station info if present (last part after splitting by '_')
        station_info = ""
        if len(parts) > 6 and (parts[-1].startswith('station-') or parts[-1].startswith('BE')):
            station_info = f" ({parts[-1]})"

        # Parse the date and time parts
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        time_obj = datetime.strptime(time_str, '%H%M%S').time()

        # Combine date and time into a single datetime object
        combined_datetime = datetime.combine(date_obj, time_obj).replace(tzinfo=pytz.UTC)

        # Convert to Europe/Brussels time
        brussels_tz = pytz.timezone('Europe/Brussels')
        combined_datetime = combined_datetime.astimezone(brussels_tz)

        # Map month numbers to Dutch month names
        month_names = {
            1: 'januari', 2: 'februari', 3: 'maart', 4: 'april',
            5: 'mei', 6: 'juni', 7: 'juli', 8: 'augustus',
            9: 'september', 10: 'oktober', 11: 'november', 12: 'december'
        }
        month_name = month_names[combined_datetime.month]

        # Construct the new string with optional station info
        return f"{combined_datetime.day} {month_name} {combined_datetime.year} @ {combined_datetime.strftime('%H:%M')}{station_info}"

    def resize_image(self, img, max_width, max_height):
        """Resizes an image proportionally to fit within the given width and height."""
        width, height = img.size
        aspect_ratio = width / height
        
        # Calculate scaling factors for both dimensions
        scale_width = max_width / width
        scale_height = max_height / height
        
        # Use the smaller scale factor to ensure the image fits within the screen
        scale = min(scale_width, scale_height)
        
        # Calculate new dimensions
        new_width = int(width * scale)
        new_height = int(height * scale)

        resized_img = img.resize((new_width, new_height), Resampling.BICUBIC)
        # Create new image with screen dimensions (not original image dimensions)
        new_img = Image.new("RGB", (max_width, max_height))
        new_img.paste(resized_img, ((max_width - new_width) // 2, (max_height - new_height) // 2))
        return new_img

    def convert_fits(self, fits_file, number, path):
        plt.style.use(astropy_mpl_style)

        image_data = fits.getdata(fits_file, ext=1)

        plt.figure()
        plt.imshow(image_data, cmap='gray')
        plt.axis('off')  # Turn off the axis

        # Preserve original filename, just change extension from .fits to .png
        filename = str(fits_file.name)
        output_filename = f"{path}/{filename.replace('.fits', '.png')}"
        
        logging.debug(f"converting {fits_file} to {output_filename}")
        plt.savefig(output_filename, bbox_inches='tight', pad_inches=0, dpi=300)

        # Close the figure
        plt.close()

    def convert_all_fits(self, path):
        """ Convert all FITS files in path and subdirectories """
        # Check if we have station subdirectories
        station_dirs = [d for d in Path(path).iterdir() if d.is_dir() and (d.name.startswith('station-') or d.name.startswith('BE'))]

        if station_dirs:
            # Multi-station structure: process each station
            slide_number = 0
            total_files = sum(len(list(station_dir.glob("*.fits"))) for station_dir in station_dirs)
            
            with tqdm(total=total_files, desc="Converting FITS to PNG", unit="file") as pbar:
                for station_dir in sorted(station_dirs):
                    fits_files = list(station_dir.glob("*.fits"))
                    fits_files.sort()
                    logging.info(f"Converting {len(fits_files)} FITS files from {station_dir.name}")

                    for fits_file in fits_files:
                        try:
                            self.convert_fits(fits_file, slide_number, path)
                            slide_number += 1
                            pbar.update(1)
                        except Exception as e:
                            logging.error(f"could not convert {fits_file}, {e}")
                            pbar.update(1)
        else:
            # Single directory structure (backward compatibility)
            fits_files = list(Path(path).glob("*.fits"))
            fits_files.sort()
            
            with tqdm(fits_files, desc="Converting FITS to PNG", unit="file") as pbar:
                for number, fits_file in enumerate(pbar):
                    try:
                        self.convert_fits(fits_file, number, path)
                    except Exception as e:
                        logging.error(f"could not convert {fits_file}, {e}")

    # def are_fits_converted(self, path):
    #     fits_files = list(Path(path).glob("*.fits"))
    #     png_files = list(Path(path).glob("slide*.png"))
    #     return len(fits_files) == len(png_files)

    def create_zip(self, image_paths, width, height):
        max = len(image_paths)
        logging.debug(f"Resizing images to {width}x{height}")
        
        # Create progress bar for image processing
        paths_as_strings = [x.name for x in image_paths]
        thezip = []
        
        with tqdm(image_paths, desc="Processing images", unit="image") as pbar:
            for current, image_path in enumerate(pbar):
                try:
                    # Load, resize and convert to PhotoImage
                    img = Image.open(image_path)
                    resized_img = self.resize_image(img, width, height)
                    photoimage = ImageTk.PhotoImage(resized_img)
                    
                    thezip.append((current, max, image_path.name, photoimage))
                except Exception as e:
                    logging.error(f"Error processing image {image_path}: {e}")
                    
        return thezip

    def create_image_cycle(self, path):
        image_paths = list(Path(path).glob("*.png"))
        image_paths.sort()
        
        # Use target dimensions if available (single monitor), otherwise fallback to screen dimensions
        if hasattr(self, 'target_width') and hasattr(self, 'target_height'):
            width = self.target_width
            height = self.target_height
        else:
            width = self.window.winfo_screenwidth()
            height = self.window.winfo_screenheight()
            
        logging.debug(f"Using display dimensions: {width}x{height}")
        thezip = self.create_zip(image_paths, width, height)
        return cycle(thezip)

    def get_correct_images(self, path):
        updated = check_time_and_run(self.state)
        if updated:
            logging.debug("New fits were downloaded, converting them...")
            self.convert_all_fits(self.state.image_dir)
        if updated or self.images is None:
            logging.debug("Creating new image cycle...")
            self.images = self.create_image_cycle(self.state.image_dir)
        return self.images

    def display_next_slide(self):
        try:
            self.images = self.get_correct_images(self.state.image_dir)
        except Exception as e:
            logging.error(f"Could not get images: {e}")
            return
        current, max, name, self.next_image = next(self.images)
        self.text_label.config(text=f"({current+1}/{max}) {self.slide_filename_to_date(name)}")
        self.current_slide.config(image=self.next_image)
        self.current_slide.pack()
        # Keep window title fixed as "Cams Slideshow" for i3 window assignment
        # self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        logging.debug("Starting slideshow")
        self.display_next_slide()


def get_all_stations(state: State = None, server_available: bool = True) -> list:
    """ Dynamically discover all available stations """
    if not server_available:
        # Fallback to last known active stations from state
        if state and state.active_stations:
            logging.info(f"Server unavailable - using last known active stations: {state.active_stations}")
            return state.active_stations
        logging.warning("Server unavailable and no cached stations - using fallback")
        return ['BE000D']  # ultimate fallback
    
    try:
        cmd = f"ssh {SSH_OPTS} {RMS_HOST} 'ls -d RMS_data/BE* 2>/dev/null | xargs -n1 basename 2>/dev/null || true'"
        result = subprocess.check_output(cmd, shell=True, timeout=30).decode("utf-8")
        stations = [s.strip() for s in result.splitlines() if s.strip() and s.startswith('BE')]
        logging.info(f"Found stations: {stations}")
        return stations
    except Exception as e:
        logging.error(f"Could not discover stations: {e}")
        # Fallback to last known active stations from state
        if state and state.active_stations:
            logging.info(f"Using last known active stations: {state.active_stations}")
            return state.active_stations
        return ['BE000D']  # ultimate fallback


def is_good_night(station_fits_counts: dict, date_context: str = "unknown date") -> bool:
    """
    Determines if a night is 'good' based on average FITS count across all stations.
    Args: 
        station_fits_counts: dict of {station: fits_count}
        date_context: string describing which night/date is being evaluated
    Returns: True if average >= MIN_FITS_THRESHOLD
    """
    if not station_fits_counts:
        logging.debug(f"Night evaluation for {date_context}: No stations with data")
        return False
    
    total_fits = sum(station_fits_counts.values())
    num_stations = len(station_fits_counts)
    average_fits = total_fits / num_stations
    result = average_fits >= MIN_FITS_THRESHOLD
    
    status = "GOOD" if result else "POOR"
    logging.info(f"Night evaluation for {date_context}: {status} - {total_fits} total fits across {num_stations} stations, average: {average_fits:.1f} (threshold: {MIN_FITS_THRESHOLD})")
    return result


def get_station_dirs_for_date(date_pattern: str, state: State = None) -> dict:
    """
    Get FITS counts for all stations for a specific date pattern.
    Args: date_pattern: e.g., "20250804" or "*20250804*"
    Returns: dict of {station: {'directory': dir_name, 'fits_count': count}}
    """
    stations = get_all_stations(state)
    station_dirs = {}
    
    for station in stations:
        try:
            # List directories matching the date pattern (format: BE####_YYYYMMDD_HHMMSS_######)
            cmd = f"timeout 30 ssh {SSH_OPTS} {RMS_HOST} 'ls -d RMS_data/{station}/ArchivedFiles/{station}_{date_pattern}_* 2>/dev/null | head -1'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8").strip()
            
            if not result:
                logging.debug(f"No directory found for {station} on {date_pattern}")
                continue
                
            directory = result.split('/')[-1]  # Get just the directory name
            
            # Count FITS files in this directory
            cmd = f"timeout 30 ssh {SSH_OPTS} {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles/{directory}/*.fits 2>/dev/null | wc -l'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8")
            nr_fits = int(result.strip())
            
            if nr_fits >= 0:  # Include all stations, even with 0 files
                station_dirs[station] = {
                    'directory': directory,
                    'fits_count': nr_fits
                }
                logging.debug(f"Station {station}: {directory} has {nr_fits} fits files")
                
        except subprocess.TimeoutExpired as e:
            logging.warning(f"Station {station} check timeout after {e.timeout}s for date {date_pattern}")
            continue
        except subprocess.CalledProcessError as e:
            logging.warning(f"Station {station} SSH failed (exit {e.returncode}) for date {date_pattern}: {e.stderr.decode() if e.stderr else 'no stderr'}")
            continue
        except Exception as e:
            logging.warning(f"Station {station} unexpected error for date {date_pattern}: {type(e).__name__}: {e}")
            continue
    
    return station_dirs


def find_last_good_night(last_checked_date: str = None, state: State = None) -> dict:
    """
    Find the most recent night where the average FITS count across all stations
    meets the MIN_FITS_THRESHOLD. Goes back in time until a good night is found.
    Args:
        last_checked_date: ISO date string (YYYY-MM-DD) of the last time we checked.
                          If provided, start checking from dates after this date.
    Returns: dict of {station: {'directory': dir_name, 'fits_count': count}}
    """
    from datetime import datetime, timedelta
    
    current_date = datetime.now()
    
    # Determine the starting point for checking
    if last_checked_date:
        try:
            last_checked = datetime.fromisoformat(last_checked_date)
            # Start checking from yesterday (to get fresh data) but not older than last check
            yesterday = current_date - timedelta(days=1)
            start_date = max(last_checked.date(), yesterday.date())
            logging.info(f"Last check was {last_checked_date}, starting fresh check from {start_date}")
        except ValueError:
            logging.warning(f"Invalid last_checked_date format: {last_checked_date}, starting from yesterday")
            start_date = (current_date - timedelta(days=1)).date()
    else:
        # First time checking - start from yesterday
        start_date = (current_date - timedelta(days=1)).date()
        logging.info("First time checking - starting from yesterday")
    
    max_days_back = 30  # Don't go back more than 30 days
    
    # Check from start_date backwards to find the most recent good night
    for days_back in range(0, max_days_back):
        check_date = start_date - timedelta(days=days_back)
        date_pattern = check_date.strftime("%Y%m%d")
        
        logging.info(f"Checking night {date_pattern} ({check_date})")
        
        station_dirs = get_station_dirs_for_date(date_pattern, state)
        if not station_dirs:
            logging.debug(f"No data found for {date_pattern}")
            continue
            
        # Check if this is a good night
        fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
        
        if is_good_night(fits_counts, date_pattern):
            total_fits = sum(fits_counts.values())
            avg_fits = total_fits / len(fits_counts)
            logging.info(f"Found good night: {date_pattern} with {total_fits} total fits, average {avg_fits:.1f} per station")
            return station_dirs
        else:
            total_fits = sum(fits_counts.values())
            avg_fits = total_fits / len(fits_counts) if fits_counts else 0
            logging.debug(f"Clouded night: {date_pattern} with {total_fits} total fits, average {avg_fits:.1f} per station")
    
    logging.warning(f"No good night found in the checked date range from {start_date}")
    return {}


def get_latest_dirs_all_stations(state: State = None, server_available: bool = True) -> dict:
    """ Gets the most recent dir from each station with fits files """
    stations = get_all_stations(state, server_available)
    station_dirs = {}

    if not server_available:
        # Return empty dict when server unavailable - caller will handle fallback
        logging.info("Server unavailable - cannot fetch latest directories")
        return station_dirs

    for station in stations:
        try:
            # SSH into the machine and list the directories in the station folder
            cmd = f"timeout 30 ssh {SSH_OPTS} {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles 2>/dev/null || true'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8")

            directories = [d.strip() for d in result.splitlines() if d.strip()]
            if not directories:
                logging.debug(f"No directories found in {station}")
                continue

            # Find the latest directory based on the naming convention for this station
            directories.sort(key=lambda x: (re.search(r'(\d{4})(\d{2})(\d{2})', x).groups() if re.search(r'(\d{4})(\d{2})(\d{2})', x) else (0,0,0)), reverse=True)
            latest_directory = directories[0]

            # Count fits files in this directory
            cmd = f"timeout 30 ssh {SSH_OPTS} {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles/{latest_directory}/*.fits 2>/dev/null | wc -l'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8")
            nr_fits = int(result.strip())

            if nr_fits > 0:  # Only include stations with images
                station_dirs[station] = {
                    'directory': latest_directory,
                    'fits_count': nr_fits
                }
                logging.info(f"Station {station}: {latest_directory} has {nr_fits} fits files")
            else:
                logging.debug(f"Station {station}: {latest_directory} has no fits files")

        except subprocess.TimeoutExpired as e:
            logging.warning(f"Station {station} latest check timeout after {e.timeout}s")
            continue
        except subprocess.CalledProcessError as e:
            logging.warning(f"Station {station} latest check SSH failed (exit {e.returncode}): {e.stderr.decode() if e.stderr else 'no stderr'}")
            continue
        except Exception as e:
            logging.warning(f"Station {station} latest check unexpected error: {type(e).__name__}: {e}")
            continue

    logging.info(f"Found {len(station_dirs)} stations with images: {list(station_dirs.keys())}")
    return station_dirs


def fetch_latest_dirs_all_stations(station_dirs: dict) -> int:
    """ Fetch latest directories from all stations with images """
    os.makedirs('latest', exist_ok=True)

    total_fits = 0
    for station, info in station_dirs.items():
        latest_dir = info['directory']
        fits_count = info['fits_count']

        logging.info(f"Fetching {fits_count} images from {station}/{latest_dir}")

        # Create subdirectory for this station
        station_dir = f'latest/{station}'
        os.makedirs(station_dir, exist_ok=True)

        # Use rsync to fetch the latest directory from this station
        rsync_cmd = f'rsync -r -av --delete -v -e "ssh {SSH_OPTS}" "{RMS_HOST}:/home/pi/RMS_data/{station}/ArchivedFiles/{latest_dir}/*.fits" {station_dir}/'

        try:
            subprocess.run(rsync_cmd, check=True, shell=True)
            logging.debug(f"rsync_cmd: {rsync_cmd} - done")
            total_fits += fits_count
        except subprocess.CalledProcessError as e:
            logging.error(f"rsync failed for {station}: {e}")

    if total_fits > 0:
        switch_latest_dir()

    return total_fits


def fetch_latest_dir(latest_dir: str, station: str) -> str:
    """ Legacy function for single station - kept for compatibility """
    os.makedirs('latest', exist_ok=True)

    # Use rsync to fetch the latest directory
    rsync_cmd = f'rsync -r -av --delete -v -e "ssh {SSH_OPTS}" "{RMS_HOST}:/home/pi/RMS_data/{station}/ArchivedFiles/{latest_dir}/*.fits" ./latest/'

    try:
        subprocess.run(rsync_cmd, check=True, shell=True)
        logging.debug(f"rsync_cmd: {rsync_cmd} - done")
    except subprocess.CalledProcessError as e:
        logging.error(f"rsync failed with error: {e}")

    switch_latest_dir()


def switch_latest_dir():
    # Delete 'current_old' directory if it exists
    shutil.rmtree('current_old', ignore_errors=True)

    # Rename 'current' to 'current_old' if 'current' exists
    if os.path.exists('current'):
        os.rename('current', 'current_old')

    # Rename 'latest' to 'current'
    os.rename('latest', 'current')
    logging.info("Successfully switched to dir with latest images")


# DEPRECATED
def was_modified_today(directory_path: str) -> bool:
    # Get the last modification time in seconds since the epoch
    mod_time_since_epoch = os.path.getmtime(directory_path)

    # Convert to a datetime object
    mod_datetime = datetime.fromtimestamp(mod_time_since_epoch)

    # Get the current time and date
    current_datetime = datetime.now()

    # Compare the date parts
    was_modified = mod_datetime.date() == current_datetime.date()
    #logging.debug(f"{directory_path} was modified today: {was_modified}")
    return was_modified


def is_time_for_updating(last_check: str) -> bool:
    """ Is it time to update the images? """

    if last_check == '':
        return True

    # Get the last modification time in seconds since the epoch
    last_check_date = datetime.fromisoformat(last_check)

    # Get the current time and date
    current_datetime = datetime.now()

    # are we still in the same day?
    is_same_day = last_check_date.date() == current_datetime.date()
    return not is_same_day


def is_time_for_server_check(last_server_check: str) -> bool:
    """ Is it time to attempt a server connection? Wait at least 1 hour between attempts """
    
    if last_server_check == '':
        return True
    
    last_server_check_date = datetime.fromisoformat(last_server_check)
    current_datetime = datetime.now()
    
    # Check if at least 1 hour has passed since last server check
    time_since_last_check = current_datetime - last_server_check_date
    return time_since_last_check.total_seconds() >= 3600  # 1 hour = 3600 seconds


def touch_directory(directory_path: str, offset_sec=0):
    dir_time = time.time() - offset_sec
    os.utime(directory_path, (dir_time, dir_time))


def check_time_and_run(state) -> bool:
    """ Checks if it's time to run the script, returns True if we ran it """
    now = datetime.now()

    if now.hour < 9:
        return False
    elif not is_time_for_updating(state.last_check):
        return False
    
    # Proceed with check if it's time to update images
    if now.hour >= 9 and is_time_for_updating(state.last_check):
        logging.info(f"Time to check for new images (after 9 AM and last check: {state.last_check})")
        
        # Check server availability if enough time has passed since last server check
        server_available = True  # Assume available unless we need to check
        if is_time_for_server_check(state.last_server_check):
            logging.info(f"Checking if RMS server {RMS_HOST} is available...")
            server_available = is_server_available()
            logging.info(f"Server availability check result: {server_available}")
            # Update server check timestamp
            state.last_server_check = now.isoformat()
        else:
            logging.info(f"Skipping server check (last check: {state.last_server_check})")
        
        if not server_available:
            logging.warning(f"RMS server ({RMS_HOST}) is not available - continuing with existing images")
            logging.info("Will retry server connection in 1 hour")
            state.save('latest_state.json')
            return False  # No new images fetched, continue with what we have
        
        try:
            # Server is available, proceed with checking for new images
            last_check_msg = f" (last check: {state.last_check})" if state.last_check else " (first time checking)"
            logging.info(f"Server available! Looking for the last good night across all stations{last_check_msg}")
            station_dirs = find_last_good_night(state.last_check, state)
            
            if not station_dirs:
                logging.warning("No good night found in recent history, falling back to latest directories from each station")
                station_dirs = get_latest_dirs_all_stations(state, server_available)
                if station_dirs:
                    fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
                    total_fits = sum(fits_counts.values())
                    avg_fits = total_fits / len(fits_counts) if fits_counts else 0
                    logging.info(f"Fallback found {len(station_dirs)} stations with {total_fits} total fits (avg {avg_fits:.1f} per station)")
                else:
                    logging.error("No stations with images found in fallback check")
            
            state.last_check = now.isoformat()

            # Check if this represents new images compared to what we have
            has_new_images = False
            new_stations = []
            for station, info in station_dirs.items():
                current_dir = info['directory']
                last_dir = state.last_dirs.get(station, '')
                if current_dir != last_dir:
                    has_new_images = True
                    new_stations.append(f"{station}:{current_dir}")
            
            if has_new_images:
                logging.info(f"New images found in stations: {', '.join(new_stations)}")
            else:
                logging.info("No new images found - all stations have same directories as before")

            # Also check if we have a good night (average threshold met)
            fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
            is_good = is_good_night(fits_counts, "current candidate night")
            total_fits = sum(fits_counts.values()) if fits_counts else 0
            avg_fits = total_fits / len(fits_counts) if fits_counts else 0
            
            if is_good:
                logging.info(f"Good night confirmed: {total_fits} total fits, average {avg_fits:.1f} per station (>= {MIN_FITS_THRESHOLD} threshold)")
            else:
                logging.info(f"Poor night quality: {total_fits} total fits, average {avg_fits:.1f} per station (< {MIN_FITS_THRESHOLD} threshold)")
            
            # Decision logic with clear logging
            should_fetch = (has_new_images and is_good) or not state.active_stations
            
            if should_fetch:
                if not state.active_stations:
                    logging.info("No previous active stations - fetching initial images regardless of quality")
                else:
                    logging.info("Conditions met for fetching new images: new images found AND good night quality")
                
                logging.info(f"Starting fetch from {len(station_dirs)} stations...")
                total_fits = fetch_latest_dirs_all_stations(station_dirs)
                avg_fits = total_fits / len(station_dirs) if station_dirs else 0
                logging.info(f"Successfully fetched {total_fits} total images from {len(station_dirs)} stations (avg {avg_fits:.1f} per station)")
                
                state.last_switch = state.last_check
                state.active_stations = list(station_dirs.keys())
                logging.info(f"Updated active stations: {state.active_stations}")

                # Update last_dirs for all stations
                for station, info in station_dirs.items():
                    state.last_dirs[station] = info['directory']
            else:
                # Explain why we're not fetching
                reasons = []
                if not has_new_images:
                    reasons.append("no new images")
                if not is_good:
                    reasons.append(f"poor quality (avg {avg_fits:.1f} < {MIN_FITS_THRESHOLD})")
                
                logging.info(f"Not fetching new images: {' and '.join(reasons)} - keeping existing images")

            state.save('latest_state.json')
            logging.info(f"Check completed for stations: {list(station_dirs.keys())}. Updated state saved.")
            return has_new_images and is_good
        except Exception as e:
            logging.error(f"Could not check stations: {e}")
            # Continue with existing images rather than crashing
            return False
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some images.')
    parser.add_argument('-i', '--image_directory', type=str, help='The directory of images to process')
    parser.add_argument('-f', '--fetch_latest_images', action='store_true', help='Fetch latest images. TEST ONLY')
    parser.add_argument('--find_good_night', action='store_true', help='Find the last good night and exit')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    # parser.add_argument('-n', '--no-update', action='store_true', help='Do not update the images')
    parser.add_argument('-F', '--full-screen', action='store_true', help='Full screen')
    parser.add_argument('-m', '--monitor', type=int, default=None, help='Monitor index to use (0=first, 1=second, etc.). Default: let window manager decide.')

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Get the logger for the 'PIL' library
    pil_logger = logging.getLogger('PIL')

    # Set the logging level to INFO to suppress DEBUG messages
    pil_logger.setLevel(logging.INFO)

    state = State()
    if os.path.exists('latest_state.json'):
        state = State.load('latest_state.json')

    logging.info(f"Starting with {state=}")

    if args.image_directory and not Path(args.image_directory).is_dir():
        logging.debug(f"Error: {args.image_directory} is not a valid directory.")
        exit(1)

    if args.fetch_latest_images:
        logging.info("Fetching images from all stations")
        station_dirs = get_latest_dirs_all_stations(state)
        if station_dirs:
            total_fits = fetch_latest_dirs_all_stations(station_dirs)
            logging.info(f"Successfully fetched {total_fits} images from {len(station_dirs)} stations")
            state.active_stations = list(station_dirs.keys())
            for station, info in station_dirs.items():
                state.last_dirs[station] = info['directory']
            state.save('latest_state.json')
        else:
            logging.warning("No stations with images found")
    elif args.find_good_night:
        logging.info("Finding the last good night...")
        station_dirs = find_last_good_night(state.last_check, state)
        if station_dirs:
            fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
            total_fits = sum(fits_counts.values())
            avg_fits = total_fits / len(fits_counts)
            logging.info(f"Last good night found with {len(station_dirs)} stations:")
            for station, info in station_dirs.items():
                logging.info(f"  {station}: {info['directory']} ({info['fits_count']} files)")
            logging.info(f"Total: {total_fits} files, Average: {avg_fits:.1f} files per station")
        else:
            logging.warning("No good night found in recent history")
    else:
        # try:
        logging.debug("Slideshow mode")
        state.image_dir = args.image_directory if args.image_directory else state.image_dir
        if not os.path.exists(state.image_dir):
            os.makedirs(state.image_dir, exist_ok=True)
            touch_directory(state.image_dir, offset_sec=60*60*24)  # pretend we ran it yesterday
        
        # Check for new images and convert before starting slideshow
        last_check_msg = f" (last server check: {state.last_check})" if state.last_check else " (never checked before)"
        logging.info(f"Checking for new images before starting slideshow{last_check_msg}")
        updated = check_time_and_run(state)
        
        # If no update happened, check if we need to ensure we have good images
        if not updated:
            png_files = list(Path(state.image_dir).glob("*.png"))
            should_fetch_good_night = False
            
            if not png_files:
                logging.info("No PNG images available - checking if we have FITS to convert or need to fetch")
                # Check if we have FITS files that we can convert to PNG
                fits_files = list(Path(state.image_dir).glob("**/*.fits"))
                if fits_files:
                    logging.info(f"Found {len(fits_files)} existing FITS files in current directory, converting to PNG...")
                    app_temp = Application(full_screen=False, state=state)
                    app_temp.convert_all_fits(state.image_dir)
                    logging.info("FITS to PNG conversion completed")
                    # Re-check for PNG files after conversion
                    png_files = list(Path(state.image_dir).glob("*.png"))
                    if png_files:
                        logging.info(f"Successfully created {len(png_files)} PNG files from FITS")
                        should_fetch_good_night = False
                    else:
                        logging.warning("Failed to create PNG files from FITS, will fetch good night")
                        should_fetch_good_night = True
                else:
                    logging.info("No FITS files available either - will fetch images from last good night")
                    should_fetch_good_night = True
            else:
                # Check if current images are from a good night
                if state.active_stations:
                    # Estimate current night quality based on what we have and extract date
                    current_fits_counts = {}
                    current_date = "unknown"
                    
                    for station in state.active_stations:
                        station_dir = Path(state.image_dir) / station
                        if station_dir.exists():
                            fits_count = len(list(station_dir.glob("*.fits")))
                            current_fits_counts[station] = fits_count
                            # Extract date from the last known directory for this station
                            if station in state.last_dirs:
                                dir_name = state.last_dirs[station]
                                # Extract date from format like BE0012_20250802_201224_068781
                                import re
                                date_match = re.search(r'_(\d{8})_', dir_name)
                                if date_match:
                                    current_date = date_match.group(1)
                                    break
                    
                    if current_fits_counts:
                        date_context = f"existing images from {current_date}" if current_date != "unknown" else "existing images"
                        if not is_good_night(current_fits_counts, date_context):
                            total_fits = sum(current_fits_counts.values())
                            avg_fits = total_fits / len(current_fits_counts)
                            logging.info(f"Current images from {current_date} are from a poor night (avg {avg_fits:.1f} < {MIN_FITS_THRESHOLD}) - will fetch better night")
                            should_fetch_good_night = True
                        else:
                            logging.info(f"Current images from {current_date} are from a good night - keeping them")
            
            if should_fetch_good_night:
                logging.info("Fetching images from last good night...")
                station_dirs = find_last_good_night(state.last_check, state)
                if station_dirs:
                    total_fits = fetch_latest_dirs_all_stations(station_dirs)
                    avg_fits = total_fits / len(station_dirs) if station_dirs else 0
                    logging.info(f"Fetched {total_fits} images from last good night (avg {avg_fits:.1f} per station)")
                    app_temp = Application(full_screen=False, state=state)
                    app_temp.convert_all_fits(state.image_dir)
                    logging.info("Image conversion completed")
                    # Update state
                    state.active_stations = list(station_dirs.keys())
                    for station, info in station_dirs.items():
                        state.last_dirs[station] = info['directory']
                    # last_check is already updated in check_time_and_run
                    state.save('latest_state.json')
                else:
                    logging.error("No good night found and no existing images!")
        elif updated:
            logging.info("New images downloaded, converting to PNG...")
            app_temp = Application(full_screen=False, state=state)
            app_temp.convert_all_fits(state.image_dir)
            logging.info("Image conversion completed")
        
        application = Application(full_screen=args.full_screen, state=state, monitor_index=args.monitor)
        application.start()
        application.window.mainloop()
        logging.debug("Starting application")
        # except:
        #     logging.error("Unexpected error: %s", sys.exc_info()[0])

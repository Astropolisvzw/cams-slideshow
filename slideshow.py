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

RMS_HOST = 'pi@10.10.0.171'
MIN_FITS_THRESHOLD = 5  # Minimum average FITS files across stations for a "good night"


def is_server_available() -> bool:
    """ Check if the RMS server is reachable """
    try:
        # Simple ping-like check with timeout
        cmd = f"timeout 10 ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no {RMS_HOST} 'echo server_available'"
        result = subprocess.check_output(cmd, shell=True, timeout=15, stderr=subprocess.DEVNULL).decode("utf-8").strip()
        return result == "server_available"
    except Exception:
        return False

@dataclass
class State:
    last_dirs: dict = field(default_factory=dict) # last dir per station: {'station-1': 'dir1', 'station-2': 'dir2'}
    last_switch: str = field(default='') # last time we switched to a new dir
    last_check: str = field(default='') # last time we checked for new images
    last_good_night_check: str = field(default='') # last time we checked for good nights
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

        return cls(**data)


class Application():
    images = None

    def __init__(self, state: State, full_screen):
        self.window = tk.Tk()
        self.state = state
        # tk.Tk.__init__(self, *args, **kwargs)
        self.window.attributes("-topmost", True)
        self.window.attributes("-topmost", False)

        self.window.title("Slideshow")
        self.window.resizable(width=True, height=True)
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
        self.current_slide.pack()

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
        new_width = min(max_width, width)
        new_height = int(new_width / aspect_ratio)

        if new_height > max_height:
            new_height = min(max_height, height)
            new_width = int(new_height * aspect_ratio)

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
        width = self.window.winfo_screenwidth()
        height = self.window.winfo_screenheight()
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
        self.window.title(name)
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
        cmd = f"ssh {RMS_HOST} 'ls -d RMS_data/BE* 2>/dev/null | xargs -n1 basename 2>/dev/null || true'"
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


def is_good_night(station_fits_counts: dict) -> bool:
    """
    Determines if a night is 'good' based on average FITS count across all stations.
    Args: station_fits_counts: dict of {station: fits_count}
    Returns: True if average >= MIN_FITS_THRESHOLD
    """
    if not station_fits_counts:
        return False
    
    total_fits = sum(station_fits_counts.values())
    num_stations = len(station_fits_counts)
    average_fits = total_fits / num_stations
    
    logging.debug(f"Night evaluation: {total_fits} total fits across {num_stations} stations, average: {average_fits:.1f}")
    return average_fits >= MIN_FITS_THRESHOLD


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
            cmd = f"timeout 30 ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 {RMS_HOST} 'ls -d RMS_data/{station}/ArchivedFiles/{station}_{date_pattern}_* 2>/dev/null | head -1'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8").strip()
            
            if not result:
                logging.debug(f"No directory found for {station} on {date_pattern}")
                continue
                
            directory = result.split('/')[-1]  # Get just the directory name
            
            # Count FITS files in this directory
            cmd = f"timeout 30 ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles/{directory}/*.fits 2>/dev/null | wc -l'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8")
            nr_fits = int(result.strip())
            
            if nr_fits >= 0:  # Include all stations, even with 0 files
                station_dirs[station] = {
                    'directory': directory,
                    'fits_count': nr_fits
                }
                logging.debug(f"Station {station}: {directory} has {nr_fits} fits files")
                
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            logging.warning(f"Could not check station {station} for date {date_pattern} (timeout/connection failed): {e}")
            continue
        except Exception as e:
            logging.warning(f"Could not check station {station} for date {date_pattern}: {e}")
            continue
    
    return station_dirs


def find_last_good_night(last_checked_date: str = None, state: State = None) -> dict:
    """
    Find the most recent night where the average FITS count across all stations
    meets the MIN_FITS_THRESHOLD. Goes back in time until a good night is found.
    Args:
        last_checked_date: ISO date string (YYYY-MM-DD) of the last time we checked.
                          If provided, only check dates after this date.
    Returns: dict of {station: {'directory': dir_name, 'fits_count': count}}
    """
    from datetime import datetime, timedelta
    
    # Start from today and go back
    current_date = datetime.now()
    
    # If we have a last checked date, start from the day after that
    start_days_back = 0
    if last_checked_date:
        try:
            last_checked = datetime.fromisoformat(last_checked_date)
            days_since_last_check = (current_date - last_checked).days
            # Only check dates newer than the last check (don't re-check the last checked date)
            start_days_back = max(0, days_since_last_check - 1)
            logging.debug(f"Last good night check was {last_checked_date}, starting check from {start_days_back} days back")
        except ValueError:
            logging.warning(f"Invalid last_checked_date format: {last_checked_date}, starting from today")
            start_days_back = 0
    
    max_days_back = 30  # Don't go back more than 30 days
    
    for days_back in range(start_days_back, max_days_back):
        check_date = current_date - timedelta(days=days_back)
        date_pattern = check_date.strftime("%Y%m%d")
        
        logging.info(f"Checking night {date_pattern} ({days_back} days ago)")
        
        station_dirs = get_station_dirs_for_date(date_pattern, state)
        if not station_dirs:
            logging.debug(f"No data found for {date_pattern}")
            continue
            
        # Check if this is a good night
        fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
        
        if is_good_night(fits_counts):
            total_fits = sum(fits_counts.values())
            avg_fits = total_fits / len(fits_counts)
            logging.info(f"Found good night: {date_pattern} with {total_fits} total fits, average {avg_fits:.1f} per station")
            return station_dirs
        else:
            total_fits = sum(fits_counts.values())
            avg_fits = total_fits / len(fits_counts) if fits_counts else 0
            logging.debug(f"Clouded night: {date_pattern} with {total_fits} total fits, average {avg_fits:.1f} per station")
    
    logging.warning(f"No good night found in the checked date range")
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
            cmd = f"timeout 30 ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles 2>/dev/null || true'"
            result = subprocess.check_output(cmd, shell=True, timeout=45).decode("utf-8")

            directories = [d.strip() for d in result.splitlines() if d.strip()]
            if not directories:
                logging.debug(f"No directories found in {station}")
                continue

            # Find the latest directory based on the naming convention for this station
            directories.sort(key=lambda x: (re.search(r'(\d{4})(\d{2})(\d{2})', x).groups() if re.search(r'(\d{4})(\d{2})(\d{2})', x) else (0,0,0)), reverse=True)
            latest_directory = directories[0]

            # Count fits files in this directory
            cmd = f"timeout 30 ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 {RMS_HOST} 'ls RMS_data/{station}/ArchivedFiles/{latest_directory}/*.fits 2>/dev/null | wc -l'"
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

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            logging.warning(f"Could not check station {station} (timeout/connection failed): {e}")
            continue
        except Exception as e:
            logging.warning(f"Could not check station {station}: {e}")
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
        rsync_cmd = f'rsync -r -av --delete -v -e ssh "{RMS_HOST}:/home/pi/RMS_data/{station}/ArchivedFiles/{latest_dir}/*.fits" {station_dir}/'

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
    rsync_cmd = f'rsync -r -av --delete -v -e ssh "{RMS_HOST}:/home/pi/RMS_data/{station}/ArchivedFiles/{latest_dir}/*.fits" ./latest/'

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


def touch_directory(directory_path: str, offset_sec=0):
    dir_time = time.time() - offset_sec
    os.utime(directory_path, (dir_time, dir_time))


def check_time_and_run(state) -> bool:
    """ Checks if it's time to run the script, returns True if we ran it """
    now = datetime.now()

    if now.hour >= 9 and is_time_for_updating(state.last_check):
        # First check if server is available
        server_available = is_server_available()
        
        if not server_available:
            logging.warning("RMS server is not available - continuing with existing images")
            state.last_check = now.isoformat()  # Update check time to avoid repeated attempts
            state.save('latest_state.json')
            return False  # No new images fetched, continue with what we have
        
        try:
            # Server is available, proceed with checking for new images
            logging.info("Looking for the last good night across all stations...")
            station_dirs = find_last_good_night(state.last_good_night_check, state)
            
            if not station_dirs:
                logging.warning("No good night found, falling back to latest directories")
                station_dirs = get_latest_dirs_all_stations(state, server_available)
            
            state.last_check = now.isoformat()
            state.last_good_night_check = now.isoformat()

            # Check if this represents new images compared to what we have
            has_new_images = False
            for station, info in station_dirs.items():
                current_dir = info['directory']
                last_dir = state.last_dirs.get(station, '')
                if current_dir != last_dir:
                    has_new_images = True
                    break

            # Also check if we have a good night (average threshold met)
            fits_counts = {station: info['fits_count'] for station, info in station_dirs.items()}
            is_good = is_good_night(fits_counts)
            
            if (has_new_images and is_good) or not state.active_stations:
                total_fits = fetch_latest_dirs_all_stations(station_dirs)
                avg_fits = total_fits / len(station_dirs) if station_dirs else 0
                logging.info(f"Fetched {total_fits} total images from {len(station_dirs)} stations (avg {avg_fits:.1f} per station)")
                state.last_switch = state.last_check
                state.active_stations = list(station_dirs.keys())

                # Update last_dirs for all stations
                for station, info in station_dirs.items():
                    state.last_dirs[station] = info['directory']
            else:
                if not is_good:
                    avg_fits = sum(fits_counts.values()) / len(fits_counts) if fits_counts else 0
                    logging.info(f"Current night not good enough (avg {avg_fits:.1f} < {MIN_FITS_THRESHOLD}), keeping existing images")
                else:
                    logging.debug(f"No new images found across stations")

            state.save('latest_state.json')
            logging.info(f"Checked stations: {list(station_dirs.keys())}")
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
        station_dirs = find_last_good_night(state.last_good_night_check, state)
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
        logging.info("Checking for new images before starting slideshow...")
        updated = check_time_and_run(state)
        
        # If no update happened, check if we need to ensure we have good images
        if not updated:
            png_files = list(Path(state.image_dir).glob("*.png"))
            should_fetch_good_night = False
            
            if not png_files:
                logging.info("No PNG images available")
                # Check if we have FITS files that we can convert to PNG
                fits_files = list(Path(state.image_dir).glob("**/*.fits"))
                if fits_files:
                    logging.info(f"Found {len(fits_files)} FITS files, converting to PNG...")
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
                    logging.info("No FITS files available either, will fetch good night")
                    should_fetch_good_night = True
            else:
                # Check if current images are from a good night
                if state.active_stations:
                    # Estimate current night quality based on what we have
                    current_fits_counts = {}
                    for station in state.active_stations:
                        station_dir = Path(state.image_dir) / station
                        if station_dir.exists():
                            fits_count = len(list(station_dir.glob("*.fits")))
                            current_fits_counts[station] = fits_count
                    
                    if current_fits_counts and not is_good_night(current_fits_counts):
                        total_fits = sum(current_fits_counts.values())
                        avg_fits = total_fits / len(current_fits_counts)
                        logging.info(f"Current images are from a bad night (avg {avg_fits:.1f} < {MIN_FITS_THRESHOLD})")
                        should_fetch_good_night = True
            
            if should_fetch_good_night:
                logging.info("Fetching images from last good night...")
                station_dirs = find_last_good_night(state.last_good_night_check, state)
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
                    state.last_good_night_check = datetime.now().isoformat()
                    state.save('latest_state.json')
                else:
                    logging.error("No good night found and no existing images!")
        elif updated:
            logging.info("New images downloaded, converting to PNG...")
            app_temp = Application(full_screen=False, state=state)
            app_temp.convert_all_fits(state.image_dir)
            logging.info("Image conversion completed")
        
        application = Application(full_screen=args.full_screen, state=state)
        application.start()
        application.window.mainloop()
        logging.debug("Starting application")
        # except:
        #     logging.error("Unexpected error: %s", sys.exc_info()[0])

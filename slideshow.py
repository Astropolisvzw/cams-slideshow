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
import glob
import time
from datetime import datetime
import json
from dataclasses import dataclass, asdict, field


@dataclass
class State:
    last_dir: str = field(default='')
    last_switch: str = field(default='')
    image_dir: str = field(default='current')

    def save(self, filename: str):
        with open(filename, "w") as f:
            json.dump(asdict(self), f, indent=4)

    @classmethod
    def load(cls, filename: str):
        with open(filename, "r") as f:
            data = json.load(f)
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
        self.current_slide = tk.Label(bg="black")
        self.duration_ms = 5000
        # Create a label with text, specifying the font size and color
        self.text_label = tk.Label(self.window, text="", font=("Arial", 20), fg="white", bg="black", anchor="nw")
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
        """ Converts a filename like 'slide035_20231014_021113_363_0776192.png' to '14 oktober 2023 om 02:11' """

        # Extract the date and time parts
        date_str, time_str = filename.split('_')[1:3]

        # Parse the date and time parts
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        time_obj = datetime.strptime(time_str, '%H%M%S')

        # Map month numbers to Dutch month names
        month_names = {
            1: 'januari', 2: 'februari', 3: 'maart', 4: 'april',
            5: 'mei', 6: 'juni', 7: 'juli', 8: 'augustus',
            9: 'september', 10: 'oktober', 11: 'november', 12: 'december'
        }
        month_name = month_names[date_obj.month]

        # Construct the new string
        return f"{date_obj.day} {month_name} {date_obj.year} om {time_obj.strftime('%H:%M')}"

    def resize_image(self, img, max_width, max_height):
        """Resizes an image proportionally to fit within the given width and height."""
        logging.debug(f"resizing image to {max_width}x{max_height}, {type(img)}")
        width, height = img.size
        aspect_ratio = width / height
        new_width = min(max_width, width)
        new_height = int(new_width / aspect_ratio)
        logging.debug(f"new size: {new_width}x{new_height}")

        if new_height > max_height:
            new_height = min(max_height, height)
            new_width = int(new_height * aspect_ratio)

        resized_img = img.resize((new_width, new_height), Resampling.BICUBIC)
        new_img = Image.new("RGB", (width, height))
        new_img.paste(resized_img, ((width - new_width) // 2, (height - new_height) // 2))
        return new_img

    def convert_fits(self, fits_file, number, path):
        plt.style.use(astropy_mpl_style)
        # f = '/Users/mike/dev/astropolis/cams-slideshow/data/BE000D_20220713_205043_342065_detected/FF_BE000D_20220713_211249_360_0033024.fits'

        image_data = fits.getdata(fits_file, ext=1)

        plt.figure()
        plt.imshow(image_data, cmap='gray')
        plt.axis('off')  # Turn off the axis

        # Extract substring after 'BE000D_' and remove '.fits' extension
        timestring = str(fits_file).split('BE000D_')[1].replace('.fits', '')

        # Save the figure without any surrounding whitespace
        output_filename = f"{path}/slide{number:03d}_{timestring}.png"  # specify the path where you want to save the image
        logging.debug(f"converting {fits_file} to {output_filename}")
        plt.savefig(output_filename, bbox_inches='tight', pad_inches=0, dpi=300)

        # Close the figure
        plt.close()

    def convert_all_fits(self, path):
        fits_files = list(Path(path).glob("*.fits"))
        fits_files.sort()
        for number, fits_file in enumerate(fits_files):
            try:
                self.convert_fits(fits_file, number, path)
            except UnidentifiedImageError:
                logging.error(f"could not convert {fits_file}")

    def are_fits_converted(self, path):
        fits_files = list(Path(path).glob("*.fits"))
        png_files = list(Path(path).glob("slide*.png"))
        return len(fits_files) == len(png_files)

    def create_zip(self, image_paths, width, height):
        max = len(image_paths)
        resized_images = (self.resize_image(Image.open(p), width, height) for p in image_paths)
        logging.debug("resized_images: %s", resized_images)
        photoimages = map(ImageTk.PhotoImage, resized_images)
        paths_as_strings = [x.name for x in image_paths]
        # thezip = zip(current, max, paths_as_strings, photoimages)
        thezip = [(current, max, path_str, photoimage) for current, (path_str, photoimage) in enumerate(zip(paths_as_strings, photoimages))]
        return thezip

    def create_image_cycle(self, path):
        image_paths = list(Path(path).glob("slide*.png"))
        image_paths.sort()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        # logging.debug('the list:', list(image_paths), type(image_paths))
        # logging.debug(list(map(lambda p: p, image_paths)))
        thezip = self.create_zip(image_paths, width, height)
        # thezip = zip(map(lambda p: p.name, image_paths),
        #              map(ImageTk.PhotoImage, map(Image.open,
        #                                          image_paths)))
        # logging.debug('zip:', list(thezip))
        # logging.debug("length of thezip is", len(list(thezip)))
        self.images = cycle(thezip)

    def display_next_slide(self):
        updated = check_time_and_run(self.state)
        if updated:
            self.convert_all_fits(self.state.image_dir)
        if updated or self.images is None:
            self.create_image_cycle(self.state.image_dir)
        current, max, name, self.next_image = next(self.images)
        self.text_label.config(text=f"({current+1}/{max}) {self.slide_filename_to_date(name)}")
        self.current_slide.config(image=self.next_image)
        self.current_slide.pack()
        self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        self.display_next_slide()


def check_latest_dir() -> Tuple[str, int]:
    """ Gets the most recent dir via ssh + number of fits files in it """
    # SSH into the machine and list the directories in the specified folder
    cmd = "ssh pi@10.10.0.113 'ls RMS_data/ArchivedFiles'"
    result = subprocess.check_output(cmd, shell=True).decode("utf-8")

    # Convert the result to a list of directories
    directories = result.splitlines()

    # Find the latest directory based on the naming convention
    directories.sort(key=lambda x: (re.search(r'(\d{4})(\d{2})(\d{2})', x).groups() if re.search(r'(\d{4})(\d{2})(\d{2})', x) else (0,0,0)), reverse=True)
    latest_directory = directories[0]
    logging.info(f"latest_directory found: {latest_directory}")

    # find out if there are any fits files
    cmd = f"ssh pi@10.10.0.113 'ls RMS_data/ArchivedFiles/{latest_directory}/*.fits | wc -l'"
    result = subprocess.check_output(cmd, shell=True).decode("utf-8")
    fits = result.splitlines()
    logging.debug("fits: %s", fits)
    nr_fits = len(fits)
    return latest_directory, nr_fits


def fetch_latest_dir(latest_dir: str) -> str:
    os.makedirs('latest', exist_ok=True)

    # Use rsync to fetch the latest directory
    rsync_cmd = f'rsync -r -av --delete -v -e ssh "pi@10.10.0.113:/home/pi/RMS_data/ArchivedFiles/{latest_dir}/*.fits" ./latest/'

    try:
        subprocess.run(rsync_cmd, check=True, shell=True)
        logging.debug(f"rsync_cmd: {' '.join(rsync_cmd)} - done")
    except subprocess.CalledProcessError as e:
        logging.error(f"rsync failed with error: {e}")


    # subprocess.call(rsync_cmd, shell=True)
    # logging.debug(f"rsync_cmd: {rsync_cmd} - done")
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


def was_modified_today(directory_path: str) -> bool:
    # Get the last modification time in seconds since the epoch
    mod_time_since_epoch = os.path.getmtime(directory_path)

    # Convert to a datetime object
    mod_datetime = datetime.fromtimestamp(mod_time_since_epoch)

    # Get the current time and date
    current_datetime = datetime.now()

    # Compare the date parts
    return mod_datetime.date() == current_datetime.date()


def touch_directory(directory_path: str):
    current_time = time.time()
    os.utime(directory_path, (current_time, current_time))


def check_time_and_run(state) -> bool:
    """ Checks if it's time to run the script, returns True if we ran it """
    now = datetime.now()

    if now.hour >= 9 and not was_modified_today(state.image_dir):
        latest_dir, nr_fits = check_latest_dir()
        if nr_fits > 0:
            fetch_latest_dir(latest_dir)
            state.last_switch = now.isoformat()
        state.last_dir = latest_dir
        touch_directory(state.image_dir)  # don't run again today
        state.save('latest_state.json')
        logging.info(f"Wrote new last_dir: {state=}")
        return True
    return False


def cams_dir_to_date(s) -> datetime:
    try:
        date_str = s.split('_')[1]
        return datetime.strptime(date_str, '%Y%m%d')
    except (IndexError, ValueError):
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some images.')
    parser.add_argument('-i', '--image_directory', type=str, help='The directory of images to process')
    parser.add_argument('-f', '--fetch_latest_images', action='store_true', help='Fetch images before processing')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    # parser.add_argument('-n', '--no-update', action='store_true', help='Do not update the images')
    parser.add_argument('-F', '--full-screen', action='store_true', help='Full screen')

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
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

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if args.fetch_latest_images:
        logging.debug("Fetching images")
        fetch_latest_dir()
    else:
        # try:
        logging.debug("Slideshow mode")
        state.image_dir = args.image_directory if args.image_directory else state.image_dir
        application = Application(full_screen=args.full_screen, state=state)
        application.start()
        application.window.mainloop()
        logging.debug("Starting application")
        # except:
        #     logging.error("Unexpected error: %s", sys.exc_info()[0])

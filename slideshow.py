#!/usr/bin/env python3

import tkinter as tk
import matplotlib.pyplot as plt
from astropy.visualization import astropy_mpl_style
from astropy.io import fits
from astropy.utils.data import get_pkg_data_filename
import tkinter as tk
from PIL import Image, ImageTk
from PIL.Image import Resampling
import subprocess
import re
import argparse
from pathlib import Path
from itertools import cycle
import logging
import sys
import shutil
import os
import glob
import datetime

has_run_today = False


class Application():
    image_dir = 'current'

    def __init__(self, full_screen):
        self.window = tk.Tk()
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
        date_obj = datetime.datetime.strptime(date_str, '%Y%m%d')
        time_obj = datetime.datetime.strptime(time_str, '%H%M%S')

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
        png_files = list(Path(path).glob("slide*.png"))
        if len(fits_files) == len(png_files):
            logging.debug("all fits already converted")
            return
        for number, fits_file in enumerate(fits_files):
            try:
                self.convert_fits(fits_file, number, path)
            except UnidentifiedImageError:
                logging.error(f"could not convert {fits_file}")

    def create_zip(self, image_paths, width, height):
        max = len(image_paths)
        resized_images = (self.resize_image(Image.open(p), width, height) for p in image_paths)
        logging.debug("resized_images: %s", resized_images)
        photoimages = map(ImageTk.PhotoImage, resized_images)
        paths_as_strings = [x.name for x in image_paths]
        # thezip = zip(current, max, paths_as_strings, photoimages)
        thezip = [(current, max, path_str, photoimage) for current, (path_str, photoimage) in enumerate(zip(paths_as_strings, photoimages))]
        return thezip

    def set_image_directory(self, path):
        self.image_dir = path
        logging.debug("converting all fits in %s", path)
        self.convert_all_fits(path)
        logging.debug("converting all fits done")

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
        current, max, name, self.next_image = next(self.images)
        self.text_label.config(text=f"({current+1}/{max}) {self.slide_filename_to_date(name)}")
        self.current_slide.config(image=self.next_image)
        self.current_slide.pack()
        self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        update = check_time_and_run()
        if update:
            self.set_image_directory(self.image_dir)
        self.display_next_slide()


def fetch_latest_dir():
    # Step 1: SSH into the machine and list the directories in the specified folder
    cmd = "ssh pi@10.10.0.113 'ls RMS_data/ArchivedFiles'"
    result = subprocess.check_output(cmd, shell=True).decode("utf-8")

    # Convert the result to a list of directories
    directories = result.splitlines()

    # Step 2: Find the latest directory based on the naming convention
    directories.sort(key=lambda x: (re.search(r'(\d{4})(\d{2})(\d{2})', x).groups() if re.search(r'(\d{4})(\d{2})(\d{2})', x) else (0,0,0)), reverse=True)
    latest_directory = directories[0]
    logging.info(f"latest_directory found: {latest_directory}")

    # Step 3: Prepare directories for rsync_cmd
    # shutil.rmtree('latest', ignore_errors=True)
    os.makedirs('latest', exist_ok=True)

    # Step 4: Use rsync to fetch the latest directory
    rsync_cmd = f'rsync -r -av --delete -v -e ssh "pi@10.10.0.113:/home/pi/RMS_data/ArchivedFiles/{latest_directory}/*.fits" ./latest/'
    subprocess.call(rsync_cmd, shell=True)
    logging.debug(f"rsync_cmd: {rsync_cmd} - done")

    # Step 5: Check the number of *.fits files in the 'latest' directory
    fits_files_count = len(glob.glob('latest/*.fits'))
    logging.debug(f"Number of *.fits files: {fits_files_count}")

    if fits_files_count > 0:
        # If there are more than 0 *.fits files:

        # Step 5: Delete 'current_old' directory if it exists
        shutil.rmtree('current_old', ignore_errors=True)

        # Step 6: Rename 'current' to 'current_old' if 'current' exists
        if os.path.exists('current'):
            os.rename('current', 'current_old')

        # Step 7: Rename 'latest' to 'current'
        os.rename('latest', 'current')
        logging.info("Successfully fetched latest images")
    else:
        logging.info("No *.fits files found in 'latest' directory")


def check_time_and_run():
    global has_run_today
    now = datetime.datetime.now()

    if now.hour >= 9 and not has_run_today:
        fetch_latest_dir()
        has_run_today = True
        return True
    elif now.hour < 9:
        has_run_today = False
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some images.')
    parser.add_argument('-i', '--image_directory', type=str, help='The directory of images to process')
    parser.add_argument('-f', '--fetch_latest_images', action='store_true', help='Fetch images before processing')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    # parser.add_argument('-n', '--no-update', action='store_true', help='Do not update the images')
    parser.add_argument('-F', '--full-screen', action='store_true', help='Full screen')

    args = parser.parse_args()
    # Get the logger for the 'PIL' library
    pil_logger = logging.getLogger('PIL')

    # Set the logging level to INFO to suppress DEBUG messages
    pil_logger.setLevel(logging.INFO)

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
        image_dir = args.image_directory if args.image_directory else 'current'
        application = Application(full_screen=args.full_screen)
        application.start()
        application.window.mainloop()
        application.set_image_directory(image_dir)
        logging.debug("Starting application")
        # except:
        #     logging.error("Unexpected error: %s", sys.exc_info()[0])

#!/usr/bin/env python3

import tkinter as tk
import matplotlib.pyplot as plt
from astropy.visualization import astropy_mpl_style
from astropy.io import fits
from astropy.utils.data import get_pkg_data_filename
import tkinter as tk
from PIL import Image, ImageTk
from PIL.Image import Resampling

class Application():

    def __init__(self, *args, **kwargs):
        self.window = tk.Tk()
        # tk.Tk.__init__(self, *args, **kwargs)
        self.window.attributes("-topmost", True)
        self.window.attributes("-topmost", False)

        self.window.title("Slideshow")
        self.window.resizable(width=True, height=True)
        self.window.attributes("-fullscreen", True)
        self.current_slide = tk.Label(bg="black")
        self.current_slide.pack()
        self.duration_ms = 5000

    def resize_image(self, img, max_width, max_height):
        """Resizes an image proportionally to fit within the given width and height."""
        print(f"resizing image to {max_width}x{max_height}, {type(img)}, {dir(img)}")
        width, height = img.size
        print(f"resizing image to {max_width}x{max_height}")
        aspect_ratio = width / height
        new_width = min(max_width, width)
        new_height = int(new_width / aspect_ratio)
        print(f"new size: {new_width}x{new_height}")

        if new_height > max_height:
            new_height = min(max_height, height)
            new_width = int(new_height * aspect_ratio)

        resized_img = img.resize((new_width, new_height), Resampling.BICUBIC)
        print(resized_img)
        new_img = Image.new("RGB", (width, height))
        print(new_img)
        new_img.paste(resized_img, ((width - new_width) // 2, (height - new_height) // 2))
        print(new_img)
        return new_img


    def convert_fits(self, fits_file, number):
        print(f"converting {fits_file} to slide{number:03d}.png")
        plt.style.use(astropy_mpl_style)
        # f = '/Users/mike/dev/astropolis/cams-slideshow/data/BE000D_20220713_205043_342065_detected/FF_BE000D_20220713_211249_360_0033024.fits'

        image_data = fits.getdata(fits_file, ext=1)

        plt.figure()
        plt.imshow(image_data, cmap='gray')
        plt.axis('off')  # Turn off the axis

        # Save the figure without any surrounding whitespace
        output_filename = f"slide{number:03d}.png"  # specify the path where you want to save the image
        plt.savefig(output_filename, bbox_inches='tight', pad_inches=0, dpi=300)

        # Close the figure
        plt.close()

    def convert_all_fits(self, path):
        from pathlib import Path
        fits_files = Path(path).glob("*.fits")
        for number, fits_file in enumerate(fits_files):
            try:
                self.convert_fits(fits_file, number)
            except UnidentifiedImageError:
                print(f"could not convert {fits_file}")

    def create_zip(self, image_paths, width, height):
        resized_images = (self.resize_image(Image.open(p), width, height) for p in image_paths)
        print("resized_images:", resized_images)
        photoimages = map(ImageTk.PhotoImage, resized_images)
        print(photoimages)
        paths_as_strings = [x.name for x in image_paths]
        print(paths_as_strings)
        thezip = zip(paths_as_strings, photoimages)
        return thezip

    def set_image_directory(self, path):
        from pathlib import Path
        from itertools import cycle
        print("converting all fits")
        # self.convert_all_fits(path)
        print("converting all fits done")

        image_paths = list(Path(path).glob("slide*.png"))
        image_paths.sort()
        print("image_paths:", image_paths)
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        # print('the list:', list(image_paths), type(image_paths))
        # print(list(map(lambda p: p, image_paths)))
        thezip = self.create_zip(image_paths, width, height)
        # thezip = zip(map(lambda p: p.name, image_paths),
        #              map(ImageTk.PhotoImage, map(Image.open,
        #                                          image_paths)))
        # print('zip:', list(thezip))
        # print("length of thezip is", len(list(thezip)))
        self.images = cycle(thezip)

    def display_next_slide(self):
        name, self.next_image = next(self.images)
        self.current_slide.config(image=self.next_image)
        self.current_slide.pack()
        self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        self.display_next_slide()


def main():
    try:
        application = Application()
        application.set_image_directory("/Users/mike/dev/astropolis/cams-slideshow/rsynctest/")
        application.start()
        application.window.mainloop()
    except:
        print("Unexpected error:", sys.exc_info()[0])


if __name__ == "__main__":
    import sys
    sys.exit(main())

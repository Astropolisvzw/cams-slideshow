#!/usr/bin/env python3

import tkinter as tk


class Application():

    def __init__(self, *args, **kwargs):
        self.window = tk.Tk()
        # tk.Tk.__init__(self, *args, **kwargs)
        self.window.attributes("-topmost", True)
        self.window.attributes("-topmost", False)

        self.window.title("Slideshow")
        self.window.resizable(width=True, height=True)
        self.window.attributes("-fullscreen", True)
        self.current_slide = tk.Label()
        self.current_slide.pack()
        self.duration_ms = 1000

    def convert_fits(self, fits_file):
        from astropy.io import fits
        from PIL import Image
        import numpy as np

        fits_file = fits.open(fits_file)
        print(fits_file)
        data = fits_file[0].data
        data = np.array(data, dtype=np.uint8)
        img = Image.fromarray(data)
        img.save(fits_file[0].header['OBJECT'] + '.png')

    def convert_all_fits(self, path):
        from pathlib import Path
        fits_files = Path(path).glob("*.fits")
        for fits_file in fits_files:
            self.convert_fits(fits_file)

    def set_image_directory(self, path):
        from pathlib import Path
        from PIL import Image, ImageTk
        from itertools import cycle
        self.convert_all_fits(path)

        image_paths = list(Path(path).glob("*.fits"))
        print('the list:', list(image_paths), type(image_paths))
        print(list(map(lambda p: p, image_paths)))
        thezip = zip(map(lambda p: p.name, image_paths),
                     map(ImageTk.PhotoImage, map(Image.open,
                                                 image_paths)))
        print('zip:', list(thezip))
        self.images = cycle(thezip)

    def display_next_slide(self):
        name, self.next_image = next(self.images)
        self.current_slide.config(image=self.next_image)
        self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        self.display_next_slide()




def main():
    application = Application()
    application.set_image_directory("/Users/mike/Downloads/BE000D_20221111_164122_247164_detected/")
    application.start()
    application.window.mainloop()


if __name__ == "__main__":
    import sys
    sys.exit(main())

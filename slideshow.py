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

    def set_image_directory(self, path):
        from pathlib import Path
        from PIL import Image, ImageTk
        from itertools import cycle

        image_paths = Path(path).glob("*.png")
        self.images = cycle(zip(map(lambda p: p.name, image_paths),
                                map(ImageTk.PhotoImage, map(Image.open,
                                                            image_paths))))

    def display_next_slide(self):
        name, self.next_image = next(self.images)
        self.current_slide.config(image=self.next_image)
        self.window.title(name)
        self.window.after(self.duration_ms, self.display_next_slide)

    def start(self):
        self.display_next_slide()


def main():
    application = Application()
    application.set_image_directory("./support/")
    application.start()
    application.window.mainloop()


if __name__ == "__main__":
    import sys
    sys.exit(main())

from imgui_bundle import imgui
import OpenGL.GL as gl
import numpy as np


class ImageHelper:
    def __init__(self, image: np.ndarray):
        self.height, self.width = image.shape[:2]
        self.n_planes = 1 if image.ndim==2 else image.shape[2]
        if self.n_planes==1:
            self.data = np.dstack(3*(image,))
            self.n_planes = 3
        else:
            self.data = image

        self.texture_id: np.uint32 = gl.glGenTextures(1)
        self.apply()

    def apply(self):
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture_id)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_BORDER)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_BORDER)
        fmt = gl.GL_RGBA if self.n_planes==4 else gl.GL_RGB
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, fmt, self.width, self.height, 0, fmt, gl.GL_UNSIGNED_BYTE, self.data.data)


    def render(self, width: int, height: int, *args, **kwargs):
        if imgui.is_rect_visible((width, height)):
            imgui.image(self.texture_id, (width, height), *args, **kwargs)
            return True
        else:
            # Skip if outside view
            imgui.dummy((width, height))
            return False
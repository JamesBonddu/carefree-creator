import cv2
import time

import numpy as np

from PIL import Image
from enum import Enum
from typing import Any
from fastapi import Response
from pydantic import Field
from pydantic import BaseModel
from cftool.cv import to_uint8
from cftool.cv import np_to_bytes
from cftool.cv import ImageProcessor
from cftool.geometry import Matrix2D
from cfclient.models import ImageModel

from .common import get_response
from .common import IAlgorithm
from .common import ReturnArraysModel


cv_erode_endpoint = "/cv/erode"
cv_resize_endpoint = "/cv/resize"
cv_affine_endpoint = "/cv/affine"
cv_histogram_match_endpoint = "/cv/hist_match"


def erode(array: np.ndarray, n_iter: int, kernel_size: int) -> np.ndarray:
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    return cv2.erode(array, kernel, iterations=n_iter)


class Resampling(str, Enum):
    NEAREST = "nearest"
    BOX = "box"
    BILINEAR = "bilinear"
    HAMMING = "hamming"
    BICUBIC = "bicubic"
    LANCZOS = "lanczos"


class ResamplingModel(BaseModel):
    resampling: Resampling = Field(Resampling.BILINEAR, description="resampling method")


def resize(image: Image.Image, w: int, h: int, resampling: Resampling) -> Image.Image:
    if resampling == Resampling.NEAREST:
        r = Image.NEAREST
    elif resampling == Resampling.BOX:
        r = Image.BOX
    elif resampling == Resampling.BILINEAR:
        r = Image.BILINEAR
    elif resampling == Resampling.HAMMING:
        r = Image.HAMMING
    elif resampling == Resampling.BICUBIC:
        r = Image.BICUBIC
    else:
        r = Image.LANCZOS
    return image.resize((w, h), r)


def affine(
    image: Image.Image,
    a: float,
    b: float,
    c: float,
    d: float,
    e: float,
    f: float,
    w: int,
    h: int,
    resampling: Resampling,
) -> np.ndarray:
    matrix2d = Matrix2D(a=a, b=b, c=c, d=d, e=e, f=f)
    properties = matrix2d.decompose()
    iw, ih = image.size
    nw = max(round(iw * abs(properties.w)), 1)
    nh = max(round(ih * abs(properties.h)), 1)
    array = np.array(resize(image, nw, nh, resampling))
    properties.w = 1
    properties.h = 1 if properties.h > 0 else -1
    matrix2d = Matrix2D.from_properties(properties)
    return cv2.warpAffine(array, matrix2d.matrix, [w, h])


class ErodeModel(ReturnArraysModel, ImageModel):
    n_iter: int = Field(1, description="number of iterations")
    kernel_size: int = Field(3, description="size of the kernel")
    threshold: int = Field(0, description="threshold of the alpha channel")


@IAlgorithm.auto_register()
class Erode(IAlgorithm):
    model_class = ErodeModel

    endpoint = cv_erode_endpoint

    def initialize(self) -> None:
        pass

    async def run(self, data: ErodeModel, *args: Any, **kwargs: Any) -> Response:
        self.log_endpoint(data)
        t0 = time.time()
        image = await self.get_image_from("url", data, kwargs)
        t1 = time.time()
        array = np.array(image.convert("RGBA"))
        alpha = array[..., -1]
        alpha = cv2.threshold(alpha, data.threshold, 255, cv2.THRESH_BINARY)[1]
        eroded_alpha = erode(alpha, data.n_iter, data.kernel_size)
        array[..., -1] = eroded_alpha
        t2 = time.time()
        res = get_response(data, [array])
        self.log_times(
            {
                "download": t1 - t0,
                "process": t2 - t1,
                "get_response": time.time() - t2,
            }
        )
        return res


class ResizeModel(ReturnArraysModel, ResamplingModel, ImageModel):
    w: int = Field(..., description="width of the output image")
    h: int = Field(..., description="width of the output image")


@IAlgorithm.auto_register()
class Resize(IAlgorithm):
    model_class = ResizeModel

    endpoint = cv_resize_endpoint

    def initialize(self) -> None:
        pass

    async def run(self, data: ResizeModel, *args: Any, **kwargs: Any) -> Response:
        self.log_endpoint(data)
        t0 = time.time()
        image = await self.get_image_from("url", data, kwargs)
        t1 = time.time()
        resized = np.array(resize(image, data.w, data.h, data.resampling))
        t2 = time.time()
        res = get_response(data, [resized])
        self.log_times(
            {
                "download": t1 - t0,
                "process": t2 - t1,
                "get_response": time.time() - t2,
            }
        )
        return res


class BaseAffineModel(BaseModel):
    a: float = Field(..., description="`a` of the affine matrix")
    b: float = Field(..., description="`b` of the affine matrix")
    c: float = Field(..., description="`c` of the affine matrix")
    d: float = Field(..., description="`d` of the affine matrix")
    e: float = Field(..., description="`e` of the affine matrix")
    f: float = Field(..., description="`f` of the affine matrix")


class AffineModel(ResizeModel, BaseAffineModel):
    pass


@IAlgorithm.auto_register()
class Affine(IAlgorithm):
    model_class = AffineModel

    endpoint = cv_affine_endpoint

    def initialize(self) -> None:
        pass

    async def run(self, data: AffineModel, *args: Any, **kwargs: Any) -> Response:
        self.log_endpoint(data)
        t0 = time.time()
        image = await self.get_image_from("url", data, kwargs)
        t1 = time.time()
        output = affine(
            image,
            data.a,
            data.b,
            data.c,
            data.d,
            data.e,
            data.f,
            data.w,
            data.h,
            data.resampling,
        )
        t2 = time.time()
        res = get_response(data, [output])
        self.log_times(
            {
                "download": t1 - t0,
                "process": t2 - t1,
                "get_response": time.time() - t2,
            }
        )
        return res


class HistogramMatchModel(ImageModel):
    bg_url: str = Field(..., description="The `cdn` / `cos` url of the background.")
    use_hsv: bool = Field(False, description="Whether use the HSV space to match.")
    strength: float = Field(1.0, description="Strength of the matching.")


@IAlgorithm.auto_register()
class HistogramMatch(IAlgorithm):
    model_class = HistogramMatchModel

    endpoint = cv_histogram_match_endpoint

    def initialize(self) -> None:
        pass

    async def run(
        self,
        data: HistogramMatchModel,
        *args: Any,
        **kwargs: Any,
    ) -> Response:
        self.log_endpoint(data)
        t0 = time.time()
        image = await self.get_image_from("url", data, kwargs)
        bg = await self.get_image_from("bg_url", data, kwargs)
        t1 = time.time()
        to_normalized = lambda im: np.array(im).astype(np.float32) / 255.0
        rgba, bg_arr = map(to_normalized, [image, bg])
        rgb, alpha = rgba[..., :3], rgba[..., -1:]
        merged = rgb * alpha + bg_arr * (1.0 - alpha)
        merged, bg_arr = map(to_uint8, [merged, bg_arr])
        t2 = time.time()
        if data.use_hsv:
            merged = cv2.cvtColor(merged, cv2.COLOR_RGB2HSV)
            bg_arr = cv2.cvtColor(bg_arr, cv2.COLOR_RGB2HSV)
        adjusted = ImageProcessor.match_histograms(
            merged,
            bg_arr,
            alpha[..., 0] > 0,
            strength=data.strength,
        )
        if data.use_hsv:
            adjusted = cv2.cvtColor(adjusted, cv2.COLOR_HSV2RGB)
        t3 = time.time()

        from PIL import Image

        Image.fromarray(adjusted).save("adjusted.png")

        content = np_to_bytes(np.zeros([64, 64]))
        # content = np_to_bytes(adjusted)
        # content = np_to_bytes(merged)
        self.log_times(
            {
                "download": t1 - t0,
                "preprocess": t2 - t1,
                "calculation": t3 - t2,
                "to_bytes": time.time() - t3,
            }
        )
        return Response(content=content, media_type="image/png")


__all__ = [
    "cv_erode_endpoint",
    "cv_resize_endpoint",
    "cv_affine_endpoint",
    "cv_histogram_match_endpoint",
    "ErodeModel",
    "ResizeModel",
    "BaseAffineModel",
    "AffineModel",
    "HistogramMatchModel",
    "Erode",
    "Resize",
    "Affine",
    "HistogramMatch",
]

"""Blob helper functions."""
import numpy as np
import cv2
from lib.utils.config import cfg

def im_list_to_blob(ims):
    """Convert a list of images into a network input.

    Assumes images are already prepared (means subtracted, BGR order, ...).
    输送入网络输入时候，将多张图像打包成一个矩阵，相当于一个batch,以最大的图像宽度为主
    图像最大shape为1000*6000*3.不够的补0
    """
    max_shape = np.array([im.shape for im in ims]).max(axis=0)
    # print('max_shape',max_shape)
    num_images = len(ims)
    blob = np.zeros((num_images, max_shape[0], max_shape[1], 3),
                    dtype=np.float32)
    for i in range(num_images):
        im = ims[i]
        blob[i, 0:im.shape[0], 0:im.shape[1], :] = im

    return blob

def prep_im_for_blob(im, pixel_means, target_size, max_size):
    """Mean subtract and scale an image for use in a blob."""
    im = im.astype(np.float32, copy=False)
    im -= pixel_means
    im_shape = im.shape
    im_size_min = np.min(im_shape[0:2])
    im_size_max = np.max(im_shape[0:2])
    im_scale = float(target_size) / float(im_size_min)
    # Prevent the biggest axis from being more than MAX_SIZE
    if np.round(im_scale * im_size_max) > max_size:
        im_scale = float(max_size) / float(im_size_max)
    if cfg.TRAIN.RANDOM_DOWNSAMPLE:
        r = 0.6 + np.random.rand() * 0.4
        im_scale *= r
    im = cv2.resize(im, None, None, fx=im_scale, fy=im_scale,
                    interpolation=cv2.INTER_LINEAR)

    return im, im_scale

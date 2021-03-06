# -*- coding:utf-8 -*-
import numpy as np
import time
from lib.rpn_layer.generate_anchors import generate_anchors

from lib.utils.config import cfg
from lib.bbox_utils.bbox_transform import bbox_transform_inv, clip_boxes
from lib.nms.nms_wrapper import nms

"""
Outputs object detection proposals by applying estimated bounding-box
transformations to a set of regular boxes (called "anchors").
"""
def proposal_layer(rpn_cls_prob_reshape, rpn_bbox_pred, im_info, _feat_stride=[cfg["ANCHOR_WIDTH"],]):
    """
    Parameters
    ----------
    rpn_cls_prob_reshape: (1 , H , W , Ax2) outputs of RPN, prob of bg or fg
    rpn_bbox_pred: (1 , H , W , Ax4), rgs boxes output of RPN
    im_info: a list of [image_height, image_width, scale_ratios]
    _feat_stride: the downsampling ratio of feature map to the original input image
    anchor_scales: the scales to the basic_anchor (basic anchor is [16, 16])
    ----------
    Returns
    ----------
    rpn_rois : (1 x H x W x A, 5) e.g. [0, x1, y1, x2, y2]

    # Algorithm:
    #
    # for each (H, W) location i
    #   generate A anchor boxes centered on cell i
    #   apply predicted bbox deltas at cell i to each of the A anchors
    # clip predicted boxes to image
    # remove predicted boxes with either height or width < threshold
    # sort all (proposal, score) pairs by score from highest to lowest
    # take top pre_nms_topN proposals before NMS
    # apply NMS with threshold 0.7 to remaining proposals
    # take after_nms_topN proposals after NMS
    # return the top proposals (-> RoIs top, scores top)
    #layer_params = yaml.load(self.param_str_)

    """
    _anchors = generate_anchors()  # 生成基本的10个anchor
    _num_anchors = _anchors.shape[0]  # 10个anchor

    im_info = im_info[0]  # 原始图像的高宽、缩放尺度

    assert rpn_cls_prob_reshape.shape[0] == 1, \
        'Only single item batches are supported'
    pre_nms_topN  = cfg["TEST"]["RPN_PRE_NMS_TOP_N"]  # 12000,在做nms之前，最多保留的候选box数目
    post_nms_topN = cfg["TEST"]["RPN_POST_NMS_TOP_N"]  # 2000，做完nms之后，最多保留的box的数目
    nms_thresh    = cfg["TEST"]["RPN_NMS_THRESH"]  # nms用参数，阈值是0.7
    min_size      = cfg["TEST"]["RPN_MIN_SIZE"]  # 候选box的最小尺寸，目前是16，高宽均要大于16

    height, width = rpn_cls_prob_reshape.shape[1:3]  # feature-map的高宽
    width = width // 10

    # the first set of _num_anchors channels are bg probs
    # the second set are the fg probs, which we want
    # (1, H, W, A)
    # 获取第一个分类结果
    scores = np.reshape(np.reshape(rpn_cls_prob_reshape, [1, height, width, _num_anchors, 2])[:,:,:,:,1],
                        [1, height, width, _num_anchors])
    # 提取到object的分数，non-object的我们不关心
    # 并reshape到1*H*W*10

    bbox_deltas = rpn_bbox_pred  # 模型输出的pred是相对值，需要进一步处理成真实图像中的坐标

    # Enumerate all shifts
    # 同anchor-target-layer-tf这个文件一样，生成anchor的shift，进一步得到整张图像上的所有anchor
    shift_x = np.arange(0, width) * _feat_stride
    shift_y = np.arange(0, height) * _feat_stride

    # shift_x shape = [height, width]
    # 生成同样维度的两个矩阵
    shift_x, shift_y = np.meshgrid(shift_x, shift_y)
    # print("shift_x", shift_x.shape)
    # print("shift_y", shift_y.shape)
    # shifts shape = [height*width,4]
    shifts = np.vstack((shift_x.ravel(), shift_y.ravel(),
                        shift_x.ravel(), shift_y.ravel())).transpose()

    # Enumerate all shifted anchors:
    #
    # add A anchors (1, A, 4) to
    # cell K shifts (K, 1, 4) to get
    # shift anchors (K, A, 4)
    # reshape to (K*A, 4) shifted anchors
    A = _num_anchors  # 10
    K = shifts.shape[0]  # height*width,[height*width,4]
    anchors = _anchors.reshape((1, A, 4)) + \
              shifts.reshape((1, K, 4)).transpose((1, 0, 2))
    anchors = anchors.reshape((K * A, 4))  # 这里得到的anchor就是整张图像上的所有anchor

    # Transpose and reshape predicted bbox transformations to get them
    # into the same order as the anchors:
    # bbox deltas will be (1, 4 * A, H, W) format
    # transpose to (1, H, W, 4 * A)
    # reshape to (1 * H * W * A, 4) where rows are ordered by (h, w, a)
    # in slowest to fastest order
    bbox_deltas = bbox_deltas.reshape((-1, 4))  # (HxWxA, 4)

    # Same story for the scores:
    scores = scores.reshape((-1, 1))

    # TODO: 回归2个值需要进行修改
    # Convert anchors into proposals via bbox transformations
    proposals = bbox_transform_inv(anchors, bbox_deltas)  # 做逆变换，得到box在图像上的真实坐标
    # TODO: 回归2个值需要进行修改
    # 2. clip predicted boxes to image
    proposals = clip_boxes(proposals, im_info[:2])  # 将所有的proposal修建一下，超出图像范围的将会被修剪掉

    # 3. remove predicted boxes with either height or width < threshold
    # (NOTE: convert min_size to input image scale stored in im_info[2])
    keep = _filter_boxes(proposals, min_size)  # 移除那些proposal小于一定尺寸的proposal
    proposals = proposals[keep, :]  # 保留剩下的proposal
    scores = scores[keep]
    bbox_deltas=bbox_deltas[keep, :]
    print('proposals1', proposals.shape)
    score_filter = np.where(scores > 0.0)[0]
    proposals = proposals[score_filter, :]
    scores = scores[score_filter]
    bbox_deltas = bbox_deltas[score_filter, :]
    print('proposals2', proposals.shape)
    # remove irregular boxes, too fat too tall
    # keep = _filter_irregular_boxes(proposals)
    # proposals = proposals[keep, :]
    # scores = scores[keep]

    # 4. sort all (proposal, score) pairs by score from highest to lowest
    # 5. take top pre_nms_topN (e.g. 6000)
    order = scores.ravel().argsort()[::-1]  # score按得分的高低进行排序
    if pre_nms_topN > 0:                # 保留12000个proposal进去做nms
        order = order[:pre_nms_topN]
    # print('proposals3', proposals.shape)
    proposals = proposals[order, :]
    scores = scores[order]
    bbox_deltas=bbox_deltas[order,:]
    print('proposals3', proposals.shape)
    s = time.time()

    # 6. apply nms (e.g. threshold = 0.7)
    # 7. take after_nms_topN (e.g. 300)
    # 8. return the top proposals (-> RoIs top)

    keep = nms(np.hstack((proposals, scores)), nms_thresh)  # 进行nms操作，保留2000个proposal
    print(time.time()-s)
    if post_nms_topN > 0:
        keep = keep[:post_nms_topN]
    proposals = proposals[keep, :]
    scores = scores[keep]
    bbox_deltas=bbox_deltas[keep, :]

    # Output rois blob
    # Our RPN implementation only supports a single input image, so all
    # batch inds are 0
    blob = np.hstack((scores.astype(np.float32, copy=False), proposals.astype(np.float32, copy=False)))

    return blob, bbox_deltas


def _filter_boxes(boxes, min_size):
    """Remove all boxes with any side smaller than min_size."""
    ws = boxes[:, 2] - boxes[:, 0] + 1
    hs = boxes[:, 3] - boxes[:, 1] + 1
    keep = np.where((ws >= min_size) & (hs >= min_size))[0]
    return keep

def _filter_irregular_boxes(boxes, min_ratio = 0.2, max_ratio = 5):
    """Remove all boxes with any side smaller than min_size."""
    ws = boxes[:, 2] - boxes[:, 0] + 1
    hs = boxes[:, 3] - boxes[:, 1] + 1
    rs = ws / hs
    keep = np.where((rs <= max_ratio) & (rs >= min_ratio))[0]
    return keep


if __name__ == "__main__":
    im_info = [800, 600, 1]
    _feat_stride = [16, ]

    _anchors = generate_anchors()  # 生成基本的10个anchor
    _num_anchors = _anchors.shape[0]  # 10个anchor

    im_info = im_info[0]  # 原始图像的高宽、缩放尺度

    rpn_cls_prob_shape = [1, 50, 370, 2]

    pre_nms_topN = cfg["TEST"]["RPN_PRE_NMS_TOP_N"]  # 12000,在做nms之前，最多保留的候选box数目
    post_nms_topN = cfg["TEST"]["RPN_POST_NMS_TOP_N"]  # 2000，做完nms之后，最多保留的box的数目
    nms_thresh = cfg["TEST"]["RPN_NMS_THRESH"]  # nms用参数，阈值是0.7
    min_size = cfg["TEST"]["RPN_MIN_SIZE"]  # 候选box的最小尺寸，目前是16，高宽均要大于16

    height, width = rpn_cls_prob_shape[1:3]  # feature-map的高宽
    width = width // 10

    # Enumerate all shifts
    # 同anchor-target-layer-tf这个文件一样，生成anchor的shift，进一步得到整张图像上的所有anchor
    shift_x = np.arange(0, width) * _feat_stride
    shift_y = np.arange(0, height) * _feat_stride

    # shift_x shape = [height, width]
    # 生成同样维度的两个矩阵
    shift_x, shift_y = np.meshgrid(shift_x, shift_y)
    # print("shift_x", shift_x.shape)
    # print("shift_y", shift_y.shape)
    # shifts shape = [height*width,4]
    shifts = np.vstack((shift_x.ravel(), shift_y.ravel(),
                        shift_x.ravel(), shift_y.ravel())).transpose()

    # Enumerate all shifted anchors:
    #
    # add A anchors (1, A, 4) to
    # cell K shifts (K, 1, 4) to get
    # shift anchors (K, A, 4)
    # reshape to (K*A, 4) shifted anchors
    A = _num_anchors  # 10
    K = shifts.shape[0]  # height*width,[height*width,4]
    anchors = _anchors.reshape((1, A, 4)) + \
              shifts.reshape((1, K, 4)).transpose((1, 0, 2))
    anchors = anchors.reshape((K * A, 4))  # 这里得到的anchor就是整张图像上的所有anchor
    print(anchors)
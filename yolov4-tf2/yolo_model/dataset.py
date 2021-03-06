import os
import cv2
import random
import numpy as np
import tensorflow as tf
import yolo_model.utils as utils
from config.config import cfg




class Dataset(object):
    """implement Dataset here"""
    def __init__(self, dataset_type):
        self.annot_path  = cfg.TRAIN.ANNOT_PATH if dataset_type == 'train' else cfg.TEST.ANNOT_PATH
        self.input_sizes = cfg.TRAIN.INPUT_SIZE if dataset_type == 'train' else cfg.TEST.INPUT_SIZE
        self.batch_size  = cfg.TRAIN.BATCH_SIZE if dataset_type == 'train' else cfg.TEST.BATCH_SIZE
        self.data_aug    = cfg.TRAIN.DATA_AUG   if dataset_type == 'train' else cfg.TEST.DATA_AUG

        self.train_input_sizes = cfg.TRAIN.INPUT_SIZE
        self.strides = np.array(cfg.YOLO.STRIDES)
        self.classes = utils.read_class_names(cfg.YOLO.CLASSES)
        self.num_classes = len(self.classes)
        self.anchors = np.array(utils.get_anchors(cfg.YOLO.ANCHORS))
        self.anchor_per_scale = cfg.YOLO.ANCHOR_PER_SCALE               # 3
        self.max_bbox_per_scale = 150

        self.annotations = self.load_annotations(dataset_type)
        self.num_samples = len(self.annotations)                        # len
        self.num_batchs = int(np.ceil(self.num_samples / self.batch_size))      # batchs 
        self.batch_count = 0


    def load_annotations(self, dataset_type):
        with open(self.annot_path, 'r') as f:
            txt = f.readlines()
            annotations = [line.strip() for line in txt if len(line.strip().split()[1:]) != 0]
        np.random.shuffle(annotations)
        return annotations


    def __iter__(self):
        return self

    def __next__(self):
        with tf.device('/cpu:0'):
            self.train_input_size = cfg.TRAIN.INPUT_SIZE   # cfg.TRAIN.INPUT_SIZE = 608
            self.train_output_sizes = self.train_input_size // self.strides    # 608/8=76、608/16=38、608/32=19

            # batch_image shape = (batch_size=5, 608, 608, 3) 
            batch_image = np.zeros((self.batch_size, self.train_input_size, self.train_input_size, 3), dtype=np.float32)

            # batch_label_sbbox shape = (batch_size, 76, 76, 3, 85)
            # batch_label_mbbox shape = (batch_size, 38, 38, 3, 85)
            # batch_label_lbbox shape = (batch_size, 19, 19, 3, 85)
            batch_label_sbbox = np.zeros((self.batch_size, self.train_output_sizes[0], self.train_output_sizes[0],
                                          self.anchor_per_scale, 5 + self.num_classes), dtype=np.float32)
            batch_label_mbbox = np.zeros((self.batch_size, self.train_output_sizes[1], self.train_output_sizes[1],
                                          self.anchor_per_scale, 5 + self.num_classes), dtype=np.float32)
            batch_label_lbbox = np.zeros((self.batch_size, self.train_output_sizes[2], self.train_output_sizes[2],
                                          self.anchor_per_scale, 5 + self.num_classes), dtype=np.float32)

            # batch_sbboxes shape = (batch_size, max_bbox_per_scale, 4)
            batch_sbboxes = np.zeros((self.batch_size, self.max_bbox_per_scale, 4), dtype=np.float32)
            batch_mbboxes = np.zeros((self.batch_size, self.max_bbox_per_scale, 4), dtype=np.float32)
            batch_lbboxes = np.zeros((self.batch_size, self.max_bbox_per_scale, 4), dtype=np.float32)

            num = 0

            
            if self.batch_count < self.num_batchs:
                while num < self.batch_size:

                    index = self.batch_count * self.batch_size + num
                    if index >= self.num_samples: index -= self.num_samples

                    annotation = self.annotations[index]
    
                    image, bboxes = self.parse_annotation(annotation)

                    label_sbbox, label_mbbox, label_lbbox, sbboxes, mbboxes, lbboxes = self.preprocess_true_boxes(bboxes)

                    batch_image[num, :, :, :] = image                   # (batch_size=2, 608, 608, 3)
                    batch_label_sbbox[num, :, :, :, :] = label_sbbox    # (batch_size, 76, 76, 3, 85)
                    batch_label_mbbox[num, :, :, :, :] = label_mbbox    # (batch_size, 38, 38, 3, 85)
                    batch_label_lbbox[num, :, :, :, :] = label_lbbox    # (batch_size, 19, 19, 3, 85)

                    batch_sbboxes[num, :, :] = sbboxes      # (batch_size, max_bbox_per_scale, 4)
                    batch_mbboxes[num, :, :] = mbboxes
                    batch_lbboxes[num, :, :] = lbboxes

                    num += 1

                
                self.batch_count += 1

                
                batch_smaller_target = batch_label_sbbox, batch_sbboxes
                batch_medium_target  = batch_label_mbbox, batch_mbboxes
                batch_larger_target  = batch_label_lbbox, batch_lbboxes

                # Returns -> (  batch_image, 
                #              (batch_label_sbbox, batch_sbboxes), 
                #              (batch_label_mbbox, batch_mbboxes), 
                #              (batch_label_lbbox, batch_lbboxes)
                #            )
                # return batch_image, (batch_smaller_target, batch_medium_target, batch_larger_target)
                return batch_image, [batch_smaller_target, batch_medium_target, batch_larger_target]

            else:
                self.batch_count = 0
                np.random.shuffle(self.annotations)
                raise StopIteration


    def random_horizontal_flip(self, image, bboxes):

        if random.random() < 0.5:
            _, w, _ = image.shape
            image = image[:, ::-1, :]
            bboxes[:, [0,2]] = w - bboxes[:, [2,0]]

        return image, bboxes

    def random_crop(self, image, bboxes):

        if random.random() < 0.5:
            h, w, _ = image.shape
            max_bbox = np.concatenate([np.min(bboxes[:, 0:2], axis=0), np.max(bboxes[:, 2:4], axis=0)], axis=-1)

            max_l_trans = max_bbox[0]
            max_u_trans = max_bbox[1]
            max_r_trans = w - max_bbox[2]
            max_d_trans = h - max_bbox[3]

            crop_xmin = max(0, int(max_bbox[0] - random.uniform(0, max_l_trans)))
            crop_ymin = max(0, int(max_bbox[1] - random.uniform(0, max_u_trans)))
            crop_xmax = max(w, int(max_bbox[2] + random.uniform(0, max_r_trans)))
            crop_ymax = max(h, int(max_bbox[3] + random.uniform(0, max_d_trans)))

            image = image[crop_ymin : crop_ymax, crop_xmin : crop_xmax]

            bboxes[:, [0, 2]] = bboxes[:, [0, 2]] - crop_xmin
            bboxes[:, [1, 3]] = bboxes[:, [1, 3]] - crop_ymin

        return image, bboxes

    def random_translate(self, image, bboxes):

        if random.random() < 0.5:
            h, w, _ = image.shape
            max_bbox = np.concatenate([np.min(bboxes[:, 0:2], axis=0), np.max(bboxes[:, 2:4], axis=0)], axis=-1)

            max_l_trans = max_bbox[0]
            max_u_trans = max_bbox[1]
            max_r_trans = w - max_bbox[2]
            max_d_trans = h - max_bbox[3]

            tx = random.uniform(-(max_l_trans - 1), (max_r_trans - 1))
            ty = random.uniform(-(max_u_trans - 1), (max_d_trans - 1))

            M = np.array([[1, 0, tx], [0, 1, ty]])
            image = cv2.warpAffine(image, M, (w, h))

            bboxes[:, [0, 2]] = bboxes[:, [0, 2]] + tx
            bboxes[:, [1, 3]] = bboxes[:, [1, 3]] + ty

        return image, bboxes


    def parse_annotation(self, annotation):

        line = annotation.split()
        image_path = line[0]
        if not os.path.exists(image_path):
            raise KeyError("%s does not exist ... " %image_path)
        image = cv2.imread(image_path)
        bboxes = np.array([list(map(int, box.split(','))) for box in line[1:]])

        if self.data_aug:
            image, bboxes = self.random_horizontal_flip(np.copy(image), np.copy(bboxes))
            image, bboxes = self.random_crop(np.copy(image), np.copy(bboxes))
            image, bboxes = self.random_translate(np.copy(image), np.copy(bboxes))

        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image, bboxes = utils.image_preprocess(np.copy(image), [self.train_input_size, self.train_input_size], np.copy(bboxes))
        return image, bboxes


    def _np_iou(self, boxes1, boxes2):

        boxes1 = np.array(boxes1)
        boxes2 = np.array(boxes2)

        boxes1_area = boxes1[..., 2] * boxes1[..., 3]
        boxes2_area = boxes2[..., 2] * boxes2[..., 3]

        boxes1 = np.concatenate([boxes1[..., :2] - boxes1[..., 2:] * 0.5,
                                boxes1[..., :2] + boxes1[..., 2:] * 0.5], axis=-1)
        boxes2 = np.concatenate([boxes2[..., :2] - boxes2[..., 2:] * 0.5,
                                boxes2[..., :2] + boxes2[..., 2:] * 0.5], axis=-1)

        left_up = np.maximum(boxes1[..., :2], boxes2[..., :2])
        right_down = np.minimum(boxes1[..., 2:], boxes2[..., 2:])

        inter_section = np.maximum(right_down - left_up, 0.0)
        inter_area = inter_section[..., 0] * inter_section[..., 1]
        union_area = boxes1_area + boxes2_area - inter_area

        return inter_area / union_area


    def preprocess_true_boxes(self, bboxes):

        # (76, 76, 3, 85)      (38, 38, 3, 85)    (19, 19, 3, 85)
        label = [np.zeros((self.train_output_sizes[i], self.train_output_sizes[i], self.anchor_per_scale,
                           5 + self.num_classes)) for i in range(3)]

        # [np.zeros(150, 4), np.zeros(150, 4), np.zeros(150, 4)]
        bboxes_xywh = [np.zeros((self.max_bbox_per_scale, 4)) for _ in range(3)]
        bbox_count = np.zeros((3,))

        # bboxes，检测框，批次10， 最大框数150，4个边界点和一个类别序号，shape ->（10，150，5）

        for bbox in bboxes:
            bbox_coor = bbox[:4]      # x,y,w,h 坐标
            bbox_class_ind = bbox[4]  # 类别序号

            onehot = np.zeros(self.num_classes, dtype=np.float)
            onehot[bbox_class_ind] = 1.0
            uniform_distribution = np.full(self.num_classes, 1.0 / self.num_classes)
            deta = 0.01
            smooth_onehot = onehot * (1 - deta) + deta * uniform_distribution

            bbox_xywh = np.concatenate([(bbox_coor[2:] + bbox_coor[:2]) * 0.5, bbox_coor[2:] - bbox_coor[:2]], axis=-1)
            bbox_xywh_scaled = 1.0 * bbox_xywh[np.newaxis, :] / self.strides[:, np.newaxis]   # strides[:, np.newaxis] -> 新增维度成二维数组，[[8], [16], [32]]

            iou = []
            exist_positive = False

            # 正负样本处理
            # 如果 Anchor 与 Ground-truth Bounding Boxes 的 IoU > 0.3，标定为正样本;
            # 在第 1 种规则下基本能够产生足够多的样本；
            # 但是如果它们的 iou 不大于 0.3，那么只能把 iou 最大的那个 Anchor 标记为正样本，
            # 这样便能保证每个 Ground-truth 框都至少匹配一个先验框。

            for i in range(3):

                # 设置变量，用于存储每个网格尺寸下3个anchor的中心位置和宽高，即x,y,w,h
                anchors_xywh = np.zeros((self.anchor_per_scale, 4))

                # 将3个anchor框都偏移至网格中心
                anchors_xywh[:, 0:2] = np.floor(bbox_xywh_scaled[i, 0:2]).astype(np.int32) + 0.5
                
                # 填充这3个anchor的宽和高
                anchors_xywh[:, 2:4] = self.anchors[i]

                # 计算预测框和3个先验框中间的iou
                iou_scale = self._np_iou(bbox_xywh_scaled[i][np.newaxis, :], anchors_xywh)
                iou.append(iou_scale)

                # 找出iou > 0.3的anchor框
                iou_mask = iou_scale > 0.3

                # 规则 1: 对于那些 iou > 0.3 的 anchor 框，做以下处理
                if np.any(iou_mask):

                    # 根据真实框的坐标信息来计算所属网格左上角的位置
                    xind, yind = np.floor(bbox_xywh_scaled[i, 0:2]).astype(np.int32)
                    label[i][yind, xind, iou_mask, :] = 0

                    # 填充真实框的中心位置和宽高
                    label[i][yind, xind, iou_mask, 0:4] = bbox_xywh

                    # 设定置信度为 1.0，表明该网格包含物体
                    label[i][yind, xind, iou_mask, 4:5] = 1.0

                    # 设置网格内 anchor 框的类别概率，做平滑处理
                    label[i][yind, xind, iou_mask, 5:] = smooth_onehot

                    bbox_ind = int(bbox_count[i] % self.max_bbox_per_scale)
                    bboxes_xywh[i][bbox_ind, :4] = bbox_xywh
                    bbox_count[i] += 1

                    exist_positive = True

            # 规则 2: 所有 iou 都不大于0.3， 那么只能选择 iou 最大的
            if not exist_positive:
                best_anchor_ind = np.argmax(np.array(iou).reshape(-1), axis=-1)
                best_detect = int(best_anchor_ind / self.anchor_per_scale)
                best_anchor = int(best_anchor_ind % self.anchor_per_scale)
                xind, yind = np.floor(bbox_xywh_scaled[best_detect, 0:2]).astype(np.int32)

                label[best_detect][yind, xind, best_anchor, :] = 0
                label[best_detect][yind, xind, best_anchor, 0:4] = bbox_xywh
                label[best_detect][yind, xind, best_anchor, 4:5] = 1.0
                label[best_detect][yind, xind, best_anchor, 5:] = smooth_onehot

                bbox_ind = int(bbox_count[best_detect] % self.max_bbox_per_scale)
                bboxes_xywh[best_detect][bbox_ind, :4] = bbox_xywh
                bbox_count[best_detect] += 1

        
        label_sbbox, label_mbbox, label_lbbox = label
        sbboxes, mbboxes, lbboxes = bboxes_xywh

        return label_sbbox, label_mbbox, label_lbbox, sbboxes, mbboxes, lbboxes

        
    def __len__(self):
        return self.num_batchs





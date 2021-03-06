from __future__ import absolute_import
import warnings
warnings.filterwarnings("ignore")
from PIL import Image

import torch
import torch.nn.functional as F
from torch import nn, autograd
from torch.autograd import Function

import cv2
import numpy as np
import os

from .context_aware_clustering import CAC
from .loss import ReIDloss

class Regressor(nn.Module):
    def __init__(self, use_hnm, use_hpm, hard_neg, sim_thrd, co_scale, num_features):
        super(Regressor, self).__init__()
        self.use_hnm = use_hnm
        self.use_hpm = use_hpm
        self.hard_neg = hard_neg
        self.sim_thrd = sim_thrd
        self.co_scale = co_scale
        self.num_features = num_features
        
    def set_scene_vector(self, train_info):
        num_person=len(train_info[3])
        num_scene=list(train_info[3])

        self.num_scene=torch.tensor(list(map(lambda x: x-1, num_scene))).cuda()
        self.memory=Memory(self.num_features, num_person).cuda()
        
        self.clustering = CAC(use_hnm=self.use_hnm, use_hpm=self.use_hpm, \
                                total_scene=self.num_scene, threshold=self.sim_thrd, coapp_scale= self.co_scale)

        self.criterion = ReIDloss(delta=5.0, r=self.hard_neg)

    def forward(self, epoch, inputs, roi_labels):

        # merge into one batch, background label = 0
        targets = torch.cat(roi_labels)
        label = targets - 1  # background label = -1

        mask = (label>=0)
        inputs=inputs[mask]
        label=label[mask]

        logits = self.memory(inputs, label, epoch)
        ## Clustering
        if epoch > 4:
            multilabels = self.clustering.predict(self.memory.mem.detach().clone(), label.detach().clone())
            loss = self.criterion(logits, label, multilabels)
        else:
            loss = self.criterion(logits, label)

        return loss

class MemoryLayer(Function):
    def __init__(self, memory, alpha=0.01):
        super(MemoryLayer, self).__init__()
        self.memory = memory
        self.alpha = alpha

    def forward(self, inputs, targets):
        self.save_for_backward(inputs, targets)
        outputs = inputs.mm(self.memory.t())
        return outputs

    def backward(self, grad_outputs):
        inputs, targets = self.saved_tensors
        grad_inputs = None
        if self.needs_input_grad[0]:
            grad_inputs = grad_outputs.mm(self.memory)
        for x, y in zip(inputs, targets):
            self.memory[y] = self.alpha * self.memory[y] + (1. - self.alpha) * x
            self.memory[y] /= self.memory[y].norm()
        return grad_inputs, None

class Memory(nn.Module):
    def __init__(self, num_features, num_classes, alpha=0.01):
        super(Memory, self).__init__()
        self.num_features = num_features
        self.num_classes = num_classes
        self.alpha = alpha
        self.mem = nn.Parameter(torch.zeros(num_classes, num_features), requires_grad=False)

    def forward(self, inputs, targets, epoch=None):

        logits = MemoryLayer(self.mem, alpha=0.5)(inputs, targets)

        return logits
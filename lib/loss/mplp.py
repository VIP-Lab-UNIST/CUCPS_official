import torch
import shutil 
import os
import numpy as np
import json

from tqdm import tqdm
from functools import reduce
from glob import glob

class MPLP(object):

    def __init__(self, use_coap, use_uniq, use_cycle, total_scene, t, t_c, s_c, r):
        self.cnt2snum = total_scene
        with open('./dist_dict.json', 'r') as fp:
            self.dist_mat = json.load(fp)
        self.t = t
        # self.t = 0.2
        self.s_c = s_c
        self.t_c = t_c
        self.r = r
        self.use_coap = use_coap
        self.use_uniq = use_uniq
        self.use_cycle = use_cycle
        self.use_coap_weight = True

        self.cnt2stotalnum = torch.zeros_like(self.cnt2snum).float()
        for i in self.cnt2snum.unique():
            mask =  self.cnt2snum == i
            self.cnt2stotalnum[mask] = mask.sum().item()

    def forward_matching(self, sim_forward):
        # Input argument
        #  sim_forward (1D float tensor) : 1-dimensional vector with size of N
        # Output 
        #  cand_sim (1D float tensor) : Top-1 similarity larger than the threshold on each scene
        #  cand_idx (1D long tensor) : person indices Top-1 similarity larger than the threshold on each scene

        simsorted, idxsorted = torch.sort(sim_forward ,dim=0, descending=True)
        snumsorted = self.cnt2snum[idxsorted]
        
        ## Select candidate pairs whose similarities are larger than the Threshold
        selected = simsorted>0
        cand_snum = snumsorted[selected]
        cand_sim = simsorted[selected]
        cand_idx = idxsorted[selected]

        ## Select Top-1 per each scene
        snums, snums_cnt = torch.unique(cand_snum, return_counts=True)
        mask = (cand_snum[..., None]==snums[snums_cnt==1]).any(-1)
        if len(snums[snums_cnt>1])!=0:
            for snum in snums[snums_cnt>1]:
                mask[min((cand_snum==snum).nonzero())] = True
        cand_sim = cand_sim[mask]
        cand_idx = cand_idx[mask]
        
        return cand_sim, cand_idx 

    def contextual_threshold(self, sim_forward, query_pid, memory, uniqueness=True):
        
        co_persons = (self.cnt2snum==self.cnt2snum[query_pid]).nonzero().squeeze(1)
        co_persons, _ = torch.sort(co_persons, dim=0, descending=False)
        offset = co_persons[0]
        if uniqueness:
            forward_matched_sim, forward_matched_idx  = self.forward_matching(sim_forward)
        else:
            forward_matched_idx = (sim_forward > 0).nonzero().squeeze(1)
            forward_matched_sim = sim_forward[forward_matched_idx]
        if len(forward_matched_idx) > 0:
            sim_backward = memory[forward_matched_idx].mm(memory[co_persons].t())
            backward_matched_idx = sim_backward.max(dim=1)[1]
            cycle_matched_idx = forward_matched_idx[backward_matched_idx==(query_pid-offset)]
            cycle_matched_sim = forward_matched_sim[backward_matched_idx==(query_pid-offset)]
            return cycle_matched_sim, cycle_matched_idx
        else:
            return forward_matched_sim, forward_matched_idx # empty index tensor

    def compute_matching_scores(self, sims_forward, pids, memory, threshold, uniqueness=True):
        
        sims_forward[sims_forward<threshold] = 0
        sims_forward_rev = torch.zeros_like(sims_forward)
        if self.use_cycle:
            for  j, (pid, co_sim) in enumerate(zip(pids, sims_forward)):
                matched_sim, matched_idx = self.contextual_threshold(co_sim, pid, memory, uniqueness)
                sims_forward_rev[j, matched_idx] = matched_sim
                # print(5)
        else:
            for  j, (pid, co_sim) in enumerate(zip(pids, sims_forward)):
                matched_idx = (co_sim > 0).nonzero().squeeze(1)
                sims_forward_rev[j, matched_idx] = co_sim[matched_idx]
                # print(6)
        return sims_forward_rev

    def hard_positive_mining(self, mem_sim, targets_uniq, all_positive, memory):
        for i, (target) in enumerate(targets_uniq):
            scene_mask = self.cnt2snum[targets_uniq] == self.cnt2snum[target]
            scene_mask[i] = False
            neighbors = scene_mask.nonzero().squeeze(1)
            
            ## Compute scene priority
            priority = -1000*all_positive[i] # to avoid scene occlusion
            if len(neighbors) > 0: 
                advantage = torch.max(all_positive[neighbors,:], dim=0)[0]
                priority += advantage 
        
            ## Expand the priority to scene level
            scene_priority = torch.zeros((len(self.cnt2snum.unique()),)).cuda().index_add(dim=0, index=self.cnt2snum, source=priority)
            scene_priority = torch.gather(input=scene_priority, dim=0, index=self.cnt2snum)
            mem_sim[i,:] = mem_sim[i,:] + self.s_c * scene_priority
        
        hard_positive = self.compute_matching_scores(mem_sim, targets_uniq, memory, self.t)
        return hard_positive 

    def predict(self, memory, targets):

        targets_uniq=targets.unique()
        mem_vec = memory[targets_uniq]
        mem_sim = mem_vec.mm(memory.t())
        mem_sim_copy = mem_sim.clone()

        easy_positive = self.compute_matching_scores(mem_sim.clone(), targets_uniq, memory, self.t)
        easy_positive.scatter_(1, targets_uniq.unsqueeze(1), float(1))
        
        ## Predict hard positive samples
        if self.use_coap:
            ## CO-APPEARANCE
            all_positive = easy_positive.clone()
            hard_positive_all = torch.zeros_like(all_positive)
            for p in range(10):
                hard_positive = self.hard_positive_mining(mem_sim.clone(), targets_uniq, all_positive, memory)
                hard = hard_positive > 0 
                hard_positive_all[hard] = 1
                all_positive[hard] = 1
                # print(hard.sum())
                if (hard.sum()==0):
                    break
                
            ## Expand multi-label
            multilabel = (easy_positive>0).float() 
            multilabel[hard_positive_all>0] = 1.0
            
        else:
            multilabel = (easy_positive > 0).float()

        multilabel_ = torch.zeros(len(targets), mem_sim.shape[1]).cuda()
        for t_uniq, mlabel in zip(targets_uniq, multilabel):
            midx = (t_uniq==targets).nonzero().squeeze(1)
            multilabel_[midx, :] = mlabel.repeat(len(midx), 1)
            
        return multilabel_

    def draw_proposal(self, targets, multilabels):
        # path = './logs/outputs/mAP6/no_t{:.2f}_sc{:.2f}_tc{:.2f}/'.format(self.t, self.s_c, self.t_c)
        # path = './logs/outputs/coap/no_coap/'
        # path = './logs/outputs/coap/no_coap_epoch10/'
        path = './logs/outputs/coap/cycle_th05/'
        # path = './logs/outputs/coap/no_prior_th04/'
        # print('path: ', path)
        flist=glob('./logs/outputs/all/*.jpg')
        for i, (target, multilabel) in enumerate(zip(targets, multilabels)):
            fname=flist[target].split('/')[-1].split('.')[0]
            os.makedirs(path+fname)
            for label in (multilabel==1).nonzero():
                shutil.copy(flist[label], path+fname)
        raise ValueError

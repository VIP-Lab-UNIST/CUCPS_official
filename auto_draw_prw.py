
import json    

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import os

import os
import time
from matplotlib.pyplot import figure

search_dirs_all = [

    'logs/prw/paper/'

]


def draw(search_dirs, figure_name):
    legend=[]    
    fig = plt.figure(num=None, figsize=(15, 15), dpi=80, facecolor='w', edgecolor='k')        
    ax = plt.subplot(111)
    for root in search_dirs:
        for (path, dirs, files) in os.walk(root):
            do_draw=False
            for file_name in files:
                if '.json' in file_name:
                    do_draw=True  
                    break  
            if do_draw:
                steps = [] 
                mAPs = []
                for step in range(1, 30):
                    checkpoint = os.path.join(path, 'checkpoint_epoch%d.json'%step)
                    if os.path.isfile(checkpoint):
                        steps.append(step)
                        with open(checkpoint, 'r') as f:
                            performance = json.load(f)
                            mAPs.append(performance['mAP'])
                
                ax.plot(steps, mAPs, 'o-')
                legend.append(path)
            

    ax.grid(True)
    # Customize the minor grid
    plt.grid(which='minor', linestyle=':', linewidth='0.5', color='black')

    if 'cuhk' not in figure_name:
        plt.ylim(0.0,0.4)
    else:
        plt.ylim(0.0,0.99)

    plt.xlabel('epoch')
    plt.ylabel('mAP')
    ax.legend(legend, loc="lower right", fontsize=12)
    plt.tight_layout()

    # plt.axhline(y=0.45, color='r', linestyle='-')
    plt.axhline(y=0.95, color='r', linestyle='-')
    plt.axhline(y=0.9, color='r', linestyle='-')
    plt.show()

    os.system('rm -rf %s.png'%figure_name)
    plt.savefig('%s.png'%figure_name)


os.system('rm -rf figures/performance-*.png')
draw(search_dirs_all, 'figures/performance-all')
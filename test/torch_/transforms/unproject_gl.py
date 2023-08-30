import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import utils3d
import numpy as np
import torch

def run():
    for i in range(100):
        if i == 0:
            spatial = []
        else:
            dim = np.random.randint(4)
            spatial = [np.random.randint(1, 10) for _ in range(dim)]
        fovy = np.random.uniform(5 / 180 * np.pi, 175 / 180 * np.pi, spatial)
        aspect = np.random.uniform(0.01, 100, spatial)
        near = np.random.uniform(0.1, 100, spatial)
        far = np.random.uniform(near*2, 1000, spatial)
        eye = np.random.uniform(-10, 10, [*spatial, 3])
        lookat = np.random.uniform(-10, 10, [*spatial, 3])
        up = np.random.uniform(-10, 10, [*spatial, 3])
        points = np.random.uniform(-10, 10, [*spatial, 3])
        
        expected = utils3d.numpy.unproject_gl(
            utils3d.numpy.project_gl(points, None,
                                      utils3d.numpy.view_look_at(eye, lookat, up),
                                      utils3d.numpy.perspective(fovy, aspect, near, far))[0],
            None,
            utils3d.numpy.view_look_at(eye, lookat, up),
            utils3d.numpy.perspective(fovy, aspect, near, far)
        )

        device = [torch.device('cpu'), torch.device('cuda')][np.random.randint(2)]
        fovy = torch.tensor(fovy, device=device)
        aspect = torch.tensor(aspect, device=device)
        near = torch.tensor(near, device=device)
        far = torch.tensor(far, device=device)
        eye = torch.tensor(eye, device=device)
        lookat = torch.tensor(lookat, device=device)
        up = torch.tensor(up, device=device)
        points = torch.tensor(points, device=device)

        actual = utils3d.torch.unproject_gl(
            utils3d.torch.project_gl(points, None,
                                      utils3d.torch.view_look_at(eye, lookat, up),
                                      utils3d.torch.perspective(fovy, aspect, near, far))[0],
            None,
            utils3d.torch.view_look_at(eye, lookat, up),
            utils3d.torch.perspective(fovy, aspect, near, far)
        )
        actual = actual.cpu().numpy()
        
        assert np.allclose(expected, actual), '\n' + \
            'Input:\n' + \
            f'\tfovy: {fovy}\n' + \
            f'\taspect: {aspect}\n' + \
            f'\tnear: {near}\n' + \
            f'\tfar: {far}\n' + \
            f'\teye: {eye}\n' + \
            f'\tlookat: {lookat}\n' + \
            f'\tup: {up}\n' + \
            f'\tpoints: {points}\n' + \
            'Actual:\n' + \
            f'{actual}\n' + \
            'Expected:\n' + \
            f'{expected}'

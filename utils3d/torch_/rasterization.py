from typing import *

import torch
import nvdiffrast.torch as dr

from . import utils, transforms, mesh
from ._helpers import batched


__all__ = [
    'RastContext',
    'rasterize_vertex_attr', 
    'warp_image_by_depth'
]


class RastContext:
    """
    Create a rasterization context. Nothing but a wrapper of nvdiffrast.torch.RasterizeCudaContext or nvdiffrast.torch.RasterizeGLContext.
    """
    def __init__(self, nvd_ctx: Union[dr.RasterizeCudaContext, dr.RasterizeGLContext] = None, *, backend: Literal['cuda', 'gl'] = 'gl',  device: Union[str, torch.device] = None):
        if nvd_ctx is not None:
            self.nvd_ctx = nvd_ctx
            return 
        
        if backend == 'gl':
            self.nvd_ctx = dr.RasterizeGLContext(device=device)
        elif backend == 'cuda':
            self.nvd_ctx = dr.RasterizeCudaContext(device=device)
        else:
            raise ValueError(f'Unknown backend: {backend}')


def rasterize_vertex_attr(
    ctx: RastContext,
    vertices: torch.Tensor,
    faces: torch.Tensor,
    attr: torch.Tensor,
    width: int,
    height: int,
    model: torch.Tensor = None,
    view: torch.Tensor = None,
    perspective: torch.Tensor = None,
    antialiasing: bool = True,
    diff_attrs: Union[None, List[int]] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Rasterize a mesh with vertex attributes.

    Args:
        ctx (GLContext): rasterizer context
        vertices (np.ndarray): (B, N, 2 or 3 or 4)
        faces (torch.Tensor): (T, 3)
        attr (torch.Tensor): (B, N, C)
        width (int): width of the output image
        height (int): height of the output image
        model (torch.Tensor, optional): ([B,] 4, 4) model matrix. Defaults to None (identity).
        view (torch.Tensor, optional): ([B,] 4, 4) view matrix. Defaults to None (identity).
        perspective (torch.Tensor, optional): ([B,] 4, 4) perspective matrix. Defaults to None (identity).
        antialiasing (bool, optional): whether to perform antialiasing. Defaults to True.
        diff_attrs (Union[None, List[int]], optional): indices of attributes to compute screen-space derivatives. Defaults to None.

    Returns:
        image: (torch.Tensor): (B, C, H, W)
        depth: (torch.Tensor): (B, H, W) screen space depth, ranging from 0 (near) to 1. (far)
            NOTE: Empty pixels will have depth 1., i.e. far plane.
    """
    assert vertices.ndim == 3
    assert faces.ndim == 2

    if vertices.shape[-1] == 2:
        vertices = torch.cat([vertices, torch.zeros_like(vertices[..., :1]), torch.ones_like(vertices[..., :1])], dim=-1)
    elif vertices.shape[-1] == 3:
        vertices = torch.cat([vertices, torch.ones_like(vertices[..., :1])], dim=-1)
    elif vertices.shape[-1] == 4:
        pass
    else:
        raise ValueError(f'Wrong shape of vertices: {vertices.shape}')
    
    mvp = perspective if perspective is not None else torch.eye(4).to(vertices)
    if view is not None:
        mvp = mvp @ view
    if model is not None:
        mvp = mvp @ model
    
    pos_clip = vertices @ mvp.transpose(-1, -2)
    faces = faces.contiguous()
    attr = attr.contiguous()
    
    rast_out, rast_db = dr.rasterize(ctx.nvd_ctx, pos_clip, faces, resolution=[height, width], grad_db=True)
    image, image_dr = dr.interpolate(attr, rast_out, faces, rast_db, diff_attrs=diff_attrs)
    if antialiasing:
        image = dr.antialias(image, rast_out, pos_clip, faces)
    image = image.flip(1).permute(0, 3, 1, 2)
    
    depth = rast_out[..., 2].flip(1) 
    depth = (depth * 0.5 + 0.5) * (depth > 0).float() + (depth == 0).float()
    if diff_attrs is not None:
        image_dr = image_dr.flip(1).permute(0, 3, 1, 2)
        return image, depth, image_dr
    return image, depth


def texture(
    ctx: RastContext,
    uv: torch.Tensor,
    uv_da: torch.Tensor,
    texture: torch.Tensor,
) -> torch.Tensor:
    dr.texture(ctx.nvd_ctx, uv, texture)


def warp_image_by_depth(
    ctx: RastContext,
    depth: torch.FloatTensor,
    image: torch.FloatTensor = None,
    mask: torch.BoolTensor = None,
    width: int = None,
    height: int = None,
    *,
    extrinsics_src: torch.FloatTensor = None,
    extrinsics_tgt: torch.FloatTensor = None,
    intrinsics_src: torch.FloatTensor = None,
    intrinsics_tgt: torch.FloatTensor = None,
    near: float = 0.1,
    far: float = 100.0,
    antialiasing: bool = True,
    backslash: bool = False,
    padding: int = 0,
    return_dr: bool = False,
) -> Tuple[torch.FloatTensor, torch.FloatTensor, torch.BoolTensor]:
    """
    Warp image by depth. 
    NOTE: if batch size is 1, image mesh will be triangulated aware of the depth, yielding less distorted results.
    Otherwise, image mesh will be triangulated simply for batch rendering.

    Args:
        ctx (Union[dr.RasterizeCudaContext, dr.RasterizeGLContext]): rasterization context
        depth (torch.Tensor): (B, H, W) linear depth
        image (torch.Tensor): (B, C, H, W). None to use image space uv. Defaults to None.
        width (int, optional): width of the output image. None to use the same as depth. Defaults to None.
        height (int, optional): height of the output image. Defaults the same as depth..
        extrinsics_src (torch.Tensor, optional): (B, 4, 4) extrinsics matrix for source. None to use identity. Defaults to None.
        extrinsics_tgt (torch.Tensor, optional): (B, 4, 4) extrinsics matrix for target. None to use identity. Defaults to None.
        intrinsics_src (torch.Tensor, optional): (B, 3, 3) intrinsics matrix for source. None to use the same as target. Defaults to None.
        intrinsics_tgt (torch.Tensor, optional): (B, 3, 3) intrinsics matrix for target. None to use the same as source. Defaults to None.
        near (float, optional): near plane. Defaults to 0.1. 
        far (float, optional): far plane. Defaults to 100.0.
        antialiasing (bool, optional): whether to perform antialiasing. Defaults to True.
        backslash (bool, optional): whether to use backslash triangulation. Defaults to False.
        padding (int, optional): padding of the image. Defaults to 0.
        return_dr (bool, optional): whether to return the image-space derivatives of uv. Defaults to False.
    
    Returns:
        image: (torch.FloatTensor): (B, C, H, W) rendered image
        depth: (torch.FloatTensor): (B, H, W) linear depth, ranging from 0 to inf
        mask: (torch.BoolTensor): (B, H, W) mask of valid pixels
        dr: (torch.FloatTensor): (B, 4, H, W) image-space derivatives of uv
    """
    assert depth.ndim == 3
    batch_size = depth.shape[0]

    if width is None:
        width = depth.shape[-1]
    if height is None:
        height = depth.shape[-2]
    if image is not None:
        assert image.shape[-2:] == depth.shape[-2:], f'Shape of image {image.shape} does not match shape of depth {depth.shape}'

    if extrinsics_src is None:
        extrinsics_src = torch.eye(4).to(depth)
    if extrinsics_tgt is None:
        extrinsics_tgt = torch.eye(4).to(depth)
    if intrinsics_src is None:
        intrinsics_src = intrinsics_tgt
    if intrinsics_tgt is None:
        intrinsics_tgt = intrinsics_src
    
    assert all(x is not None for x in [extrinsics_src, extrinsics_tgt, intrinsics_src, intrinsics_tgt]), "Make sure you have provided all the necessary camera parameters."

    view_tgt = transforms.extrinsics_to_view(extrinsics_tgt)
    perspective_tgt = transforms.intrinsics_to_perspective(intrinsics_tgt, near=near, far=far)

    if padding > 0:
        uv, faces = utils.image_mesh(width=width+2, height=height+2)
        uv = (uv - 1 / (width + 2)) * ((width + 2) / width)
        uv_ = uv.clone().reshape(height+2, width+2, 2)
        uv_[0, :, 1] -= padding / height
        uv_[-1, :, 1] += padding / height
        uv_[:, 0, 0] -= padding / width
        uv_[:, -1, 0] += padding / width
        uv_ = uv_.reshape(-1, 2)
        depth = torch.nn.functional.pad(depth, [1, 1, 1, 1], mode='replicate')
        if image is not None:
            image = torch.nn.functional.pad(image, [1, 1, 1, 1], mode='replicate')
        uv, uv_, faces = uv.to(depth.device), uv_.to(depth.device), faces.to(depth.device)
        pts = transforms.unproject_cv(
            uv_,
            depth.flatten(-2, -1),
            extrinsics_src,
            intrinsics_src,
        )
    else:    
        uv, faces = utils.image_mesh(width=depth.shape[-1], height=depth.shape[-2])
        if mask is not None:
            depth = torch.where(mask, depth, torch.tensor(far, dtype=depth.dtype, device=depth.device))
        uv, faces = uv.to(depth.device), faces.to(depth.device)
        pts = transforms.unproject_cv(
            uv,
            depth.flatten(-2, -1),
            extrinsics_src,
            intrinsics_src,
        )

    # triangulate
    if batch_size == 1:
        faces = mesh.triangulate(faces, vertices=pts[0])
    else:
        faces = mesh.triangulate(faces, backslash=backslash)

    # rasterize attributes
    diff_attrs = None
    if image is not None:
        attr = image.permute(0, 2, 3, 1).flatten(1, 2)
        if return_dr:
            diff_attrs = [image.shape[1], image.shape[1]+1]
            attr = torch.cat([attr, uv.expand(batch_size, -1, -1)], dim=-1)
    else:
        attr = uv.expand(batch_size, -1, -1)
        if return_dr:
            diff_attrs = [0, 1]

    if mask is not None:
        attr = torch.cat([attr, mask.float().flatten(1, 2).unsqueeze(-1)], dim=-1)

    rast = rasterize_vertex_attr(
        ctx,
        pts,
        faces,
        attr,
        width,
        height,
        view=view_tgt,
        perspective=perspective_tgt,
        antialiasing=antialiasing,
        diff_attrs=diff_attrs,
    )
    if return_dr:
        output_image, screen_depth, output_dr = rast
    else:
        output_image, screen_depth = rast
    output_mask = screen_depth < 1.0

    if mask is not None:
        output_image, rast_mask = output_image[..., :-1, :, :], output_image[..., -1, :, :]
        output_mask &= (rast_mask > 0.9999).reshape(-1, height, width)

    if return_dr and image is not None:
            output_image = output_image[..., :-2, :, :]

    output_depth = transforms.linearize_depth(screen_depth, near=near, far=far) * output_mask
    output_image = output_image * output_mask.unsqueeze(1)

    if return_dr:
        return output_image, output_depth, output_mask, output_dr
    return output_image, output_depth, output_mask

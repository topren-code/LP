# -*- coding: utf-8 -*-

from __future__ import annotations  # 这个导入必须放在最前面

from pprint import pprint

import logging

from typing import Dict, Optional, Sequence, Set, Tuple, List

import os

from tqdm import tqdm  # 添加进度条，因执行时间较长

import arcpy

from arcpy.sa import ExtractByMask, SetNull

from pathlib import Path


# 全局定义logger
def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """配置日志记录器。"""
    logger = logging.getLogger("pipeline")
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def _compare_tif_file_count(source_dir, target_dir):
    """
    比较两个目录中的GeoTIFF文件数量。
    目的：判断P4这个环节有没有做过

    参数
    ----------
    source_dir : str
        包含源 GeoTIFF 文件的目录。
    target_dir : str
        包含输出 GeoTIFF 文件的目录。

    返回值
    -------
    bool
        若文件数量不同则返回 True，否则返回 False。
    """
    source_count = len(
        [f for f in os.listdir(source_dir) if f.lower().endswith(".tif")]
    )
    target_count = len(
        [f for f in os.listdir(target_dir) if f.lower().endswith(".tif")]
    )
    return source_count != target_count


def _extent_to_polygon(extent, sr):
    """
    将 arcpy.Extent 范围转为 Polygon 几何对象，用于空间重叠判断。

    参数
    ----------
    extent : arcpy.Extent
        栅格或要素的范围对象。
    sr : arcpy.SpatialReference
        该范围对应的空间参考。

    返回值
    -------
    arcpy.Polygon
        以范围四角构建的矩形多边形。
    """
    array = arcpy.Array([
        arcpy.Point(extent.XMin, extent.YMin),
        arcpy.Point(extent.XMin, extent.YMax),
        arcpy.Point(extent.XMax, extent.YMax),
        arcpy.Point(extent.XMax, extent.YMin),
    ])
    return arcpy.Polygon(array, sr)


def P4_clip_rasters_by_shapefile(
    working_dir,
    clip_polygon_path,
    logger: Optional[logging.Logger] = None,
):
    """
    使用多边形形状文件掩膜对投影后的 WSE 栅格数据集进行裁剪。
    ————这里一定是用最大历史水体边界进行裁剪
    原文中明确说过，历史最大水体边界是可以直接获取的，不需要再花时间做融合。

    栅格裁剪通过 ExtractByMask 工具完成。为避免冗余，
    仅处理尚未被裁剪过的栅格。裁剪前会检查栅格范围与研究区
    是否有空间重叠，无重叠的影像直接跳过（SWOT 有不少轨道
    不覆盖鄱阳湖，不属于错误）。

    参数
    ----------
    working_dir : str
        单个处理单元（例如区域或键）的根目录。
    clip_polygon_path : str
        用作裁剪掩膜的多边形形状文件的路径。

    目录结构
    ----------
    working_dir/
    ├── 02_wse_proj/
    └── 03_wse_Clip/

    返回值
    -------
    无
    """

    arcpy.env.overwriteOutput = True
    arcpy.env.workspace = working_dir

    projected_raster_dir = os.path.join(
        working_dir,
        "02_wse_proj"
    )
    clipped_raster_dir = os.path.join(
        working_dir,
        "03_wse_Clip"
    )

    os.makedirs(clipped_raster_dir, exist_ok=True)

    # ---- 前置检查：投影目录和裁剪shp必须存在 ----
    if not os.path.isdir(projected_raster_dir):
        raise FileNotFoundError(f"投影栅格目录不存在: {projected_raster_dir}")

    if not arcpy.Exists(clip_polygon_path):
        raise FileNotFoundError(f"裁剪掩膜shp不存在: {clip_polygon_path}")

    # ---- 检出 Spatial Analyst 许可（ExtractByMask 依赖）----
    arcpy.CheckOutExtension("Spatial")

    if not _compare_tif_file_count(
        projected_raster_dir,
        clipped_raster_dir
    ):
        if logger:
            logger.info("输出目录与投影目录TIF数量一致，P4无需处理，跳过")
        return

    # ---- 预先获取裁剪掩膜的范围几何（只算一次）----
    mask_desc = arcpy.Describe(clip_polygon_path)
    mask_sr = mask_desc.spatialReference
    mask_ext_polygon = _extent_to_polygon(mask_desc.extent, mask_sr)

    # ---- 收集待处理TIF列表（跳过已裁剪完成的）----
    raster_list = [
        f for f in os.listdir(projected_raster_dir)
        if f.lower().endswith(".tif")
        and not os.path.exists(os.path.join(clipped_raster_dir, f))
    ]

    if not raster_list:
        if logger:
            logger.info("所有TIF均已裁剪，无需处理")
        return

    if logger:
        logger.info(f"待裁剪TIF共 {len(raster_list)} 个，开始裁剪...")

    # ---- 逐栅格裁剪，带进度条 ----
    success_count = 0
    skip_count = 0
    fail_count = 0

    pbar = tqdm(raster_list, desc="栅格裁剪 (最大水体边界)", unit="tif")
    for raster_name in pbar:
        input_raster_path = os.path.join(
            projected_raster_dir,
            raster_name
        )
        output_raster_path = os.path.join(
            clipped_raster_dir,
            raster_name
        )

        # ---- 空间重叠检查：栅格范围与研究区不相交则跳过 ----
        try:
            raster_desc = arcpy.Describe(input_raster_path)
            raster_ext_polygon = _extent_to_polygon(
                raster_desc.extent,
                raster_desc.spatialReference
            )
            # 将栅格范围投影到掩膜的坐标系下再判断
            raster_ext_proj = raster_ext_polygon.projectAs(mask_sr)
            if raster_ext_proj.disjoint(mask_ext_polygon):
                skip_count += 1
                if logger:
                    logger.info(f"跳过（与研究区无重叠）: {raster_name}")
                continue
        except Exception:
            # 范围读取失败不直接跳过，继续尝试裁剪，让 ExtractByMask 给出真实错误
            pass

        try:
            clipped_raster = ExtractByMask(
                input_raster_path,
                clip_polygon_path
            )
            clipped_raster.save(output_raster_path)
            success_count += 1
            if logger:
                logger.info(f"裁剪完成: {raster_name}")
        except Exception as exc:
            fail_count += 1
            if logger:
                logger.error(f"裁剪失败: {raster_name} | {exc}")
            continue

    pbar.close()

    if logger:
        logger.info(
            f"P4裁剪完成：成功 {success_count} 个，"
            f"跳过（无重叠）{skip_count} 个，失败 {fail_count} 个，"
            f"输出目录: {clipped_raster_dir}"
        )


def main() -> None:
    """主程序入口"""
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")

    # ===================== 路径配置 ==========================
    swot_root_dir = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\output_root_dir")
    clip_polygon_path = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\maxwater\poyang_main.shp")

    try:
        print("try块捕获已执行，运行P4_clip_rasters_by_shapefile")
        P4_clip_rasters_by_shapefile(
            working_dir=str(swot_root_dir),  # Path 对象转字符串路径，避免潜在兼容问题
            clip_polygon_path=str(clip_polygon_path),
            logger=logger
        )
        # 这行必须在try块内部，只有P4正常跑完才会执行到
        print("P4裁剪执行完毕，无异常")
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")

    logger.info("P4程序已执行完毕")


if __name__ == "__main__":
    main()

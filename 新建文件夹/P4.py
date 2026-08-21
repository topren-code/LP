# -*- coding: utf-8 -*-
from __future__ import annotations  #这个导入必须放在最前面
from pprint import pprint
import logging
from typing import Dict, Optional, Sequence, Set, Tuple, List
import os
from tqdm import tqdm  #添加进度条，因执行时间较长
import arcpy
from arcpy.sa import ExtractByMask, SetNull
from pathlib import Path

#全局定义logger
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


def P4_clip_rasters_by_shapefile(
    working_dir,
    clip_polygon_path,
    region_key,
    logger: Optional[logging.Logger] = None,
):
    """
    使用多边形形状文件掩膜对投影后的 WSE 栅格数据集进行裁剪。————这里一定是用最大历史水体边界进行裁剪
    原文中明确说过，历史最大水体边界是可以直接获取的，不需要再花时间做融合。

    栅格裁剪通过 ExtractByMask 工具完成。为避免冗余，
    仅处理尚未被裁剪过的栅格。
    参数
    ----------
    working_dir : str
        单个处理单元（例如区域或键）的根目录。
    clip_polygon_path : str
        用作裁剪掩膜的多边形形状文件的路径。
    region_key : str   这个部分我直接抹除掉，本身适用于识别多Site的
        处理单元的标识符（用于管道跟踪）。
    目录结构
    ---------- ---------
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

    if not _compare_tif_file_count(
        projected_raster_dir,
        clipped_raster_dir
    ):
        return

    for raster_name in os.listdir(projected_raster_dir):
        if not raster_name.lower().endswith(".tif"):
            continue

        input_raster_path = os.path.join(
            projected_raster_dir,
            raster_name
        )
        output_raster_path = os.path.join(
            clipped_raster_dir,
            raster_name
        )

        if os.path.exists(output_raster_path):
            continue

        try:
            clipped_raster = ExtractByMask(
                input_raster_path,
                clip_polygon_path
            )
            clipped_raster.save(output_raster_path)
        except Exception:
            continue



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
            working_dir=str(swot_root_dir),  #Path 对象转字符串路径，避免潜在兼容问题
            clip_polygon_path= str(clip_polygon_path),
            logger=logger
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P4程序已执行完毕")

if __name__ == "__main__":
    main()
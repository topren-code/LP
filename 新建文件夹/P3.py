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


def _all_rasters_in_wgs84(raster_dir):
    """
    检查目录中的所有 GeoTIFF 栅格
    是否已投影到 WGS 1984（EPSG:4326）。

    参数
    ----------
    raster_dir : str
        包含 GeoTIFF 栅格的目录。

    返回值
    -------
    bool
        若所有栅格均采用 WGS 1984 坐标系，则返回 True；否则返回 False。
    """
    arcpy.env.workspace = raster_dir
    raster_list = arcpy.ListRasters("*.tif")

    if not raster_list:
        return False

    for raster_name in raster_list:
        raster_path = os.path.join(raster_dir, raster_name)
        desc = arcpy.Describe(raster_path) #获取该栅格的属性描述对象
        spatial_ref = desc.spatialReference #提取空间参考

        if spatial_ref.factoryCode != 4326: #!=不等于
            return False

    return True


def P3_project_rasters_to_wgs84(
    swot_root_dir,
    logger: Optional[logging.Logger] = None,
):
    """
    将经过过滤的 WSE GeoTIFF 栅格投影至 WGS 1984（EPSG:4326）。

    如果所有栅格已处于 WGS 1984 坐标系，则将输入目录
    直接重命名为输出目录，以避免
    不必要的重投影。

    参数
    ----------
    swot_root_dir : str
        SWOT 栅格处理工作流的根目录。

    目录结构
    -------------------
    swot_root_dir/
    ├── 01_filter_Origin_tifs/
    └── 02_wse_proj/

    返回值
    -------
    无
    """

    input_raster_dir = os.path.join(
        swot_root_dir,
        "01_filter_Origin_tifs"
    )
    output_raster_dir = os.path.join(
        swot_root_dir,
        "02_wse_proj"
    )

    arcpy.env.workspace = input_raster_dir
    arcpy.env.overwriteOutput = True  #允许覆盖已有输出文件，避免因文件存在而报错

    raster_files = arcpy.ListRasters("*.tif") #列出工作空间中所有以 .tif 结尾的栅格文件，返回文件名列表
    if not raster_files:
        print("目录下没有tif文件，程序终止")
        return

    target_spatial_ref = arcpy.SpatialReference(4326)

    # 案例 1：所有栅格数据均已采用 WGS 1984 坐标系
    #改为如果全是4326，直接终止程序
    if _all_rasters_in_wgs84(input_raster_dir):
        print("所有栅格已采用WGS84坐标系(EPSG:4326)，无需转换，程序结束。")
        return

    print("开始栅格坐标系转换")
    # 案例 2：需要进行坐标系转换
    if not os.path.exists(output_raster_dir):
        os.makedirs(output_raster_dir)
    #添加进度条显示
    for raster_name in tqdm(raster_files, desc="栅格重投影(WGS84)", unit="tif"):
        input_raster_path = os.path.join(
            input_raster_dir,
            raster_name
        )
        output_raster_path = os.path.join(
            output_raster_dir,
            raster_name
        )

        if os.path.exists(output_raster_path):
            if logger:
                logger.info(f"输出已存在，跳过投影：{raster_name}")
            continue

        try:
            arcpy.ProjectRaster_management(
                in_raster=input_raster_path,
                out_raster=output_raster_path,
                out_coor_system=target_spatial_ref
            )
            if logger:
                logger.info(f"投影完成：{raster_name}")
        except arcpy.ExecuteError as exc:
            # 获取ArcGIS工具完整报错信息
            err_msg = arcpy.GetMessages(2)
            if logger:
                logger.error(f"投影失败 [{raster_name}]：{exc}；详情：{err_msg}")
            continue
        except Exception as exc:
            if logger:
                logger.error(f"未知异常，跳过 [{raster_name}]：{exc}")
            continue

def main() -> None:
    """主程序入口"""
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")

   
    # ===================== 路径配置 ==========================
    swot_root_dir = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\output_root_dir")

    try:
        print("try块捕获已执行，运行P3_project_rasters_to_wgs84")
        P3_project_rasters_to_wgs84(
            swot_root_dir=str(swot_root_dir),  #Path 对象转字符串路径，避免潜在兼容问题
            logger=logger
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P3程序已执行完毕")

if __name__ == "__main__":
    main()
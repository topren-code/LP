# -*- coding: utf-8 -*-
from __future__ import annotations  #这个导入必须放在最前面
from pprint import pprint  #这个是用于打印长文本排版用的，比如打印字典在终端好看 
import logging  
from typing import Dict, Optional, Sequence, Set, Tuple, List
import os
import shutil
import arcpy
from collections import defaultdict
from arcpy.sa import ExtractByMask
from pathlib import Path
# 直接调用ArcGIS内置GDAL（无需pip安装，环境自带）
from osgeo import gdal
# 关闭GDAL冗余警告
gdal.UseExceptions()


#全局定义logger，在main()函数中启动，logger变量就有了，导入其余函数，
#logger.info("你想输出的内容")
#if logger:  或者采用语句的形式都可以
#logger.info(f"Deleted: {file_path}")  
def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """配置一个适合 GitHub/SCI 代码发布的控制台日志记录器。"""
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
    核心就是看这一步处理过没有，处理过的数量会相等，直接return
    参数
    ----------
    source_dir : str
        包含源 GeoTIFF 文件的目录。
    target_dir : str
        包含处理后 GeoTIFF 文件的目录。

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
    print(f"{source_count}|{target_count}")
    return source_count != target_count


def P2_filter_rasters_by_quality_threshold(
    wse_raster_dir,
    quality_raster_dir,
    quality_threshold,
    output_root_dir,
    logger: Optional[logging.Logger] = None,
):
    """
    根据质量栅格阈值过滤 WSE 栅格数据集。
    
    对于每个 WSE 栅格，通过匹配文件名前缀来识别
    相应的质量栅格。质量值大于
    或等于给定阈值的像素将被设置为 NoData。

    参数
    ----------
    wse_raster_dir : str
        包含 WSE GeoTIFF 栅格的目录。
    quality_raster_dir : str
        包含质量分类 GeoTIFF 栅格的目录。
    quality_threshold : float
        用于屏蔽低质量像素的阈值。
    output_root_dir : str
        过滤后输出栅格的根目录。

    返回值
    -------
    无
    """
    if logger:
        logger.info("成功导入logger日志器，开始运行P2函数体")
    output_dir = os.path.join(output_root_dir, "01_filter_Origin_tifs")
    os.makedirs(output_dir, exist_ok=True)

    if not _compare_tif_file_count(wse_raster_dir, output_dir):
        return
    


    #质量过滤：官方指出：SWOT中wse_qual的值越大质量越差，也就是要过滤掉文件中大于》2的值
    #0	good（优秀）	质量最好   有好几个等级，就是看你自己的数据，体量很大可定义1.5
	#1  suspect（可疑）	可能存在较大误差
	#2  degraded（降级）	质量差
	#3  bad（很差）	不可用
    for wse_filename in os.listdir(wse_raster_dir):
        if not wse_filename.lower().endswith(".tif"):
            continue

        wse_path = os.path.join(wse_raster_dir, wse_filename)
        if not os.path.exists(wse_path):
            continue

        # 提取匹配的前缀（最后一个下划线之前的部分）
        wse_prefix = "_".join(wse_filename.split("_")[:-1])  #此时没有.tif结尾了，是单个文件名
        
        #我们的文件是wse.tif和wse_qual.tif结尾。匹配质量文件
        quality_filename = next(
            (
                f for f in os.listdir(quality_raster_dir)
                if f.lower().endswith(".tif")
                and "_".join(f.split("_")[:-2]) == wse_prefix
            ),
            None
        )

        if quality_filename is None:
            continue

        quality_path = os.path.join(quality_raster_dir, quality_filename)

        try:
            quality_raster = arcpy.Raster(quality_path)
            #特别注意：源文件没有进行过栅格统计，依赖于金字塔和统计，这里要采用ArcGIS Pro内置的GDAL
            #GDAL可以直接读取原始像素数组，取最小值
            arcpy.CalculateStatistics_management(quality_raster, x_skip_factor=1, y_skip_factor=1)
            quality_min_value = float(
                arcpy.GetRasterProperties_management(
                    quality_raster, "MINIMUM"
                ).getOutput(0)
            )
        except Exception as exc:
            raise RuntimeError(f"处理质量文件'{quality_path}'时出错:{exc}")
        #raise 是 Python 的关键字，作用是主动抛出异常，程序会立刻中断
        #这里我改动了，不再静默坏文件的处理，只要遇错直接终止程序运行    

        # 跳过不需要进行滤波的栅格
        if quality_min_value >= quality_threshold:
            continue

        output_raster_path = os.path.join(output_dir, wse_filename)
        if os.path.exists(output_raster_path):
            continue

        try:
            wse_raster = arcpy.Raster(wse_path)
        except RuntimeError as exc:
            raise RuntimeError(f"无法打开WSE栅格文件 [{wse_path}]: {exc}")

        #这一步核心：正在的过滤函数
        raster_expression = (
            f'SetNull("{quality_raster}" >= {quality_threshold}, "{wse_raster}")'
        ) #条件成立设为nodata，不成立保留原值
        #SetNull(  条件部分  ,  数据部分  )
        try: #调用栅格空间分析工具
            arcpy.gp.RasterCalculator_sa(
                raster_expression,
                output_raster_path
            )
        except arcpy.ExecuteError as exc:
            raise RuntimeError(f"栅格计算执行失败，文件 [{wse_filename}]，表达式: {raster_expression}，错误信息: {exc}")



#主程序入口，这个主要是运行P0的两个自定义函数模块，以及数据接口的一个配置
def main() -> None:
    """
    目标：实现P2阶段的代码编写。
    """
    #日志配置，全局开启logger变量
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")
    # ===================== 实验参数 =====================
    # qual_level:
    #   SWOT WSE 数据的质量控制阈值。
    #   仅当 wse_qual 值小于该阈值的像素
    #   将予以保留，以供进一步分析。
    qual_level = 2
    # ===================== Data directories ==========================
    # 包含 SWOT WSE 和 WSE 质量栅格产品的根目录。
    folder_path_wse = Path(r"D:\Miniconda3\EXE\TEXT\data_downloads\wse")
    folder_path_wse_qual = Path(r"D:\Miniconda3\EXE\TEXT\data_downloads\wse_qual")
    # 输出目录根路径。
    output_root_dir = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\output_root_dir")
    try:
        print("try块捕获已执行，运行P2_filter_rasters_by_quality_threshold")
        P2_filter_rasters_by_quality_threshold(
            wse_raster_dir=folder_path_wse,
            quality_raster_dir=folder_path_wse_qual,
            quality_threshold=qual_level,
            output_root_dir=output_root_dir,
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P2程序已执行完毕")

if __name__ == "__main__":
    main()
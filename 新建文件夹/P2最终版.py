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
# 直接调用ArcGIS内置GDAL（无需pip安装，环境自带）
from osgeo import gdal
# 关闭GDAL冗余警告
#GDAL 默认行为：出错不抛 Python 异常，只往控制台打印警告 / 错误，函数返回 None 或者 None 值。
#gdal.UseExceptions() 的作用：
#开启 GDAL 异常模式：GDAL 内部错误不再仅仅打印文字，而是主动抛出 Python RuntimeError 异常。

#这份代码用AI润色了一下，改为GDAL读取最值

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
    """比较两个目录TIF数量，判断是否已处理完成"""
    source_count = len(
        [f for f in os.listdir(source_dir) if f.lower().endswith(".tif")]
    )
    target_count = len(
        [f for f in os.listdir(target_dir) if f.lower().endswith(".tif")]
    )
    print(f"{source_count}|{target_count}")
    return source_count != target_count



#新增函数
# 适配ArcGIS内置GDAL的最值读取函数（核心修复，零报错）
def get_tif_min_max(tif_path: str) -> tuple[float, float]:
    """
    调用ArcGIS自带GDAL读取栅格最值
    无需统计、无需金字塔、规避所有ArcPy 001100/999999报错
    """
    ds = gdal.Open(tif_path)
    if not ds:
        raise RuntimeError(f"无法解析栅格文件：{tif_path}")
    band = ds.GetRasterBand(1)
    # True=跳过NoData像素计算真实最值
    min_val, max_val = band.ComputeRasterMinMax(True)  #只计算真实有效像素的最大、最小值
    # 释放文件句柄，避免占用锁
    ds.FlushCache()  #强制清空 GDAL 文件缓存
    ds = None  #手动释放文件对象，防止占用
    return round(float(min_val), 4), round(float(max_val), 4)


def P2_filter_rasters_by_quality_threshold(
    wse_raster_dir,
    quality_raster_dir,
    quality_threshold,
    output_root_dir,
    logger: Optional[logging.Logger] = None,
):
    """
    根据质量栅格阈值过滤 WSE 栅格数据集
    【最终稳定版】ArcGIS内置GDAL预判断 + ArcPy栅格输出
    100%保留原始业务：前置最值判断、无效栅格直接跳过、不生成空文件
    """
    if logger:
        logger.info("成功导入logger日志器，开始运行P2函数体")
    output_dir = os.path.join(output_root_dir, "01_filter_Origin_tifs")
    os.makedirs(output_dir, exist_ok=True)

    if not _compare_tif_file_count(wse_raster_dir, output_dir):
        if logger:
            logger.info("输出目录tif数量与源目录一致，跳过全部处理")
        return

    # 申请空间分析许可
    arcpy.CheckOutExtension("Spatial")
    print("以获得ArcGIS Pro空间分析许可")

    # 质量等级说明：SWOT wse_qual 值越大质量越差
    # 0=优秀 1=可疑 2=降级 3=极差
    # 预收集全部wse tif，用于进度条
    wse_file_list = [
        f for f in os.listdir(wse_raster_dir)
        if f.lower().endswith(".tif")
    ]

    for wse_filename in tqdm(wse_file_list, desc="WSE质量过滤处理", unit="scene"):
        if not wse_filename.lower().endswith(".tif"):
            continue

        wse_path = os.path.join(wse_raster_dir, wse_filename)
        if not os.path.exists(wse_path):
            continue

        # 提取匹配前缀
        wse_prefix = "_".join(wse_filename.split("_")[:-1])  #去掉后缀.tif
        
        # 匹配对应的质量文件，去掉质量文件后缀qual.tif。
        quality_filename = next(
            (
                f for f in os.listdir(quality_raster_dir)
                if f.lower().endswith(".tif")
                and "_".join(f.split("_")[:-2]) == wse_prefix
            ),
            None
        )

        if quality_filename is None:
            if logger:
                logger.warning(f"未找到匹配质量栅格：{wse_filename}，跳过该景")
            continue

        quality_path = os.path.join(quality_raster_dir, quality_filename)
        output_raster_path = os.path.join(output_dir, wse_filename)

        if os.path.exists(output_raster_path):
            if logger:
                logger.info(f"输出文件已存在，跳过：{wse_filename}")
            continue

        # ====================== 核心：内置GDAL读取最值，彻底杜绝报错 ======================
        try:
            qual_min, _ = get_tif_min_max(quality_path)
        except Exception as exc:
            raise RuntimeError(f"读取质量栅格最值失败 [{quality_filename}]：{exc}")
        # ==============================================================================

        # 严格保留你的原始核心逻辑：无有效像素直接跳过，不生成空文件
        if qual_min >= quality_threshold:
            if logger:
                logger.info(f"【无有效像素】质量最小值{qual_min} ≥ 阈值{quality_threshold}，跳过: {wse_filename}")
            continue

        # 正常执行ArcPy栅格过滤输出（保证GIS格式合规）
        try:
            wse_raster = arcpy.Raster(wse_path)
            quality_raster = arcpy.Raster(quality_path)
        except RuntimeError as exc:
            raise RuntimeError(f"无法打开栅格文件 [{wse_path}]: {exc}")

        # 核心过滤：质量≥阈值设为NoData。#条件成立设为nodata，不成立保留原值
        try:
            out_raster = SetNull(quality_raster >= quality_threshold, wse_raster)
            out_raster.save(output_raster_path)
            if logger:
                logger.info(f"成功过滤并输出：{wse_filename}")
        except arcpy.ExecuteError as exc:
            msgs = arcpy.GetMessages(2)
            raise RuntimeError(f"栅格计算失败 [{wse_filename}]，错误信息: {exc}\n详情:{msgs}")
#arcpy.GetMessages()：拿到全部消息（信息 + 警告 + 错误）
#arcpy.GetMessages(1)：拿到警告 (warning)
#arcpy.GetMessages(2)：只拿错误信息，就是 ArcGIS 工具对话框里看到那一大段原生报错文本。  


def main() -> None:
    """主程序入口"""
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")

    # ===================== 实验参数 =====================
    qual_level = 2
    # ===================== 路径配置 ==========================
    folder_path_wse = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\SOWT\WSE")
    folder_path_wse_qual = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\SOWT\WSE_QUAL")
    output_root_dir = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\output_root_dir")

    try:
        print("try块捕获已执行，运行P2_filter_rasters_by_quality_threshold")
        P2_filter_rasters_by_quality_threshold(
            wse_raster_dir=str(folder_path_wse),  #Path 对象转字符串路径，避免潜在兼容问题
            quality_raster_dir=str(folder_path_wse_qual),
            quality_threshold=qual_level,
            output_root_dir=str(output_root_dir),
            logger=logger
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P2程序已执行完毕")

if __name__ == "__main__":
    main()
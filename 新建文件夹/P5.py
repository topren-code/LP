# -*- coding: utf-8 -*-
from __future__ import annotations  #这个导入必须放在最前面
from pprint import pprint
import logging
from typing import Dict, Optional, Sequence, Set, Tuple, List
import os
import shutil  #【改动1】补全缺失导入：Case 2中 shutil.rmtree 需要用到
from collections import defaultdict  #【改动2】补全缺失导入：raster_groups = defaultdict(list) 需要用到
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



def _parse_keys_from_filename(filename):
    """
    从栅格文件名中解析时间和空间键。

    预期文件名结构（SWOT L2 HR Raster，以下划线分隔）：
    SWOT_L2_HR_Raster_100m_UTM50R_N_x_x_x_001_187_102F_20230727T213640_..._wse.tif

    - 空间键：parts[10] + parts[11]，即 cycle_pass（如 001_187）
      同一 cycle_pass 下的相邻 tile（102F/103F）会被镶嵌
    - 日期键：parts[13] 的前 8 位，格式为 YYYYMMDD（如 20230727）

    参数
    ----------
    filename : str
        GeoTIFF 文件名。

    返回值
    -------
    元组或 None
        若解析成功，则返回 (date_key, spatial_key)，
        否则返回 None。
    """
    parts = filename.split("_")
    # 实际文件名至少有14段（到起始时间为止），原代码写的 <9 太宽松
    if len(parts) < 14:
        return None

    # 空间键 = cycle_pass（如 001_187）
    # 同一 cycle 同一 pass 的不同 tile 属于同一空间覆盖，需要镶嵌
    spatial_key = "_".join(parts[10:12])

    # 日期键 = 起始时间的前8位（如 20230727）
    date_raw = parts[13][:8]
    if not date_raw.isdigit() or len(date_raw) != 8:
        return None

    date_key = f"{date_raw[:4]}_{date_raw[4:6]}_{date_raw[6:8]}"
    return date_key, spatial_key


def P5_mosaic_rasters_by_key(
    swot_root_dir,
    logger: Optional[logging.Logger] = None,
):
    """
    根据空间键对 WSE 栅格进行镶嵌裁剪，并使用一致的时间标识符
    对文件名进行统一处理。

    如果多个栅格共享相同的空间键，则使用 MosaicToNewRaster
    将其镶嵌为单个栅格。如果不需要进行镶嵌，
    则复制到新目录并对文件名进行标准化处理（保留原始 03_wse_Clip 目录）。

    参数
    ----------
    swot_root_dir : str
        SWOT 栅格处理工作流的根目录。

    logger : logging.Logger, 可选
        日志记录器，若为 None 则自动创建。

    目录结构
    -------------------
    swot_root_dir/
    ├── 03_wse_Clip/   （输入，保留不动）
    └── 04_wse_merge/  （输出）

    返回值
    -------
    无
    """
    if logger is None:
        logger = setup_logger(logging.INFO)

    input_raster_dir = os.path.join(
        swot_root_dir,
        "03_wse_Clip"
    )
    output_raster_dir = os.path.join(
        swot_root_dir,
        "04_wse_merge"
    )

    # 输入目录不存在时直接返回
    if not os.path.isdir(input_raster_dir):
        logger.error(f"输入目录不存在: {input_raster_dir}")
        logger.error("请检查路径是否正确，或上一次运行是否已将 03_wse_Clip 重命名为 04_wse_merge")
        return

    all_files = [f for f in os.listdir(input_raster_dir) if f.lower().endswith(".tif")]
    logger.info(f"共扫描到 {len(all_files)} 个 .tif 文件，开始解析空间键与日期键...")

    raster_groups = defaultdict(list)

    for raster_name in tqdm(all_files, desc="解析文件名键", unit="file"):
        parsed_keys = _parse_keys_from_filename(raster_name)
        if parsed_keys is None:
            logger.warning(f"跳过无法解析的文件: {raster_name}")
            continue

        date_key, spatial_key = parsed_keys
        raster_path = os.path.join(input_raster_dir, raster_name)
        raster_groups[spatial_key].append((date_key, raster_path))

    logger.info(f"解析完成，共聚合为 {len(raster_groups)} 个空间键组")

    # 没有解析到任何有效组时直接返回
    if len(raster_groups) == 0:
        logger.warning("没有解析到任何有效栅格组，程序退出，不执行任何文件操作")
        return

    requires_mosaic = any(
        len(rasters) > 1 for rasters in raster_groups.values()
    )

    if requires_mosaic:
        logger.info("检测到存在需要镶嵌的空间键组，走 Mosaic/CopyRaster 分支")
    else:
        logger.info("所有空间键组均只有单张栅格，走复制重命名分支")


    # Case 1: Mosaic is required
    if requires_mosaic:
        os.makedirs(output_raster_dir, exist_ok=True)

        for spatial_key, raster_list in tqdm(raster_groups.items(), desc="镶嵌/复制栅格", unit="group"):
            date_keys = [item[0] for item in raster_list]
            unique_dates = sorted(set(date_keys))

            output_date_key = unique_dates[0]
            output_name = f"{output_date_key}_{spatial_key}.tif"
            output_path = os.path.join(output_raster_dir, output_name)

            if len(raster_list) == 1:
                logger.info(f"[单栅格] {spatial_key} → 直接复制: {output_name}")
                arcpy.management.CopyRaster(
                    raster_list[0][1],
                    output_path
                )
            else:
                logger.info(f"[镶嵌] {spatial_key} 共 {len(raster_list)} 张 → {output_name}")
                input_rasters = [item[1] for item in raster_list]
                arcpy.management.MosaicToNewRaster(
                    input_rasters=input_rasters,
                    output_location=output_raster_dir,
                    raster_dataset_name_with_extension=output_name,
                    pixel_type="32_BIT_FLOAT",
                    number_of_bands=1,
                    mosaic_method="LAST",
                    mosaic_colormap_mode="MATCH"
                )


    # Case 2: No mosaic needed → 复制到新目录并重命名（保留 03_wse_Clip 原目录不动）
    else:
        os.makedirs(output_raster_dir, exist_ok=True)

        for spatial_key, raster_list in tqdm(raster_groups.items(), desc="复制并重命名栅格", unit="file"):
            date_key = raster_list[0][0]
            src_path = raster_list[0][1]  # 原始文件路径（在 03_wse_Clip 下）
            output_name = f"{date_key}_{spatial_key}.tif"
            output_path = os.path.join(output_raster_dir, output_name)

            # 用 CopyRaster 复制（和 Case 1 单栅格处理方式保持一致）
            arcpy.management.CopyRaster(src_path, output_path)
            logger.info(f"复制并重命名: {os.path.basename(src_path)} → {output_name}")



def main() -> None:
    """主程序入口"""
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")


    # ===================== 路径配置 ==========================
    swot_root_dir = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\output_root_dir")

    try:
        print("try块捕获已执行，运行P5_mosaic_rasters_by_key")
        P5_mosaic_rasters_by_key(
            swot_root_dir=str(swot_root_dir),  #Path 对象转字符串路径，避免潜在兼容问题
            logger=logger
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")
    logger.info("P5程序已执行完毕")


if __name__ == "__main__":
    main()
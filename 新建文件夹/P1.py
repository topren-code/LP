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

def _count_geotiffs_recursive(directory: str) -> int:
    """
    递归统计某个目录下的GeoTIFF文件数量。

    参数
    ----------
    directory : str
        要扫描的根目录。

    返回值
    -------
    int
        扩展名为 .tif 或 .tiff 的文件总数
        （不区分大小写）。
    """
    print("调用_count_geotiffs_recursive函数，开始tif数量统计")
    count = 0
    for _, _, files in os.walk(directory):
        for fname in files:
            if fname.lower().endswith((".tif", ".tiff")):
                count += 1
    print(count)
    return count

#此函数是嵌套在_cleanup_unpaired_wse_pairs函数中。
def _index_rasters_by_basename(
    directory: str,
    suffix: str,
) -> Dict[str, str]:
    """
    为以特定后缀结尾的文件，建立从栅格基名到文件名的映射关系。

    示例
    -------
    suffix = “_wse.tif”
    “scene001_wse.tif” -> {‘scene001’: “scene001_wse.tif”}

    参数
    ----------
    directory : str
        待扫描的目录（不递归）。
    suffix : str
        用于标识目标栅格的文件名后缀。

    返回值
    -------
    Dict[str, str]
        从基名到完整文件名的映射关系。
    """
    index: Dict[str, str] = {}

    for fname in os.listdir(directory):
        if fname.endswith(suffix):
            base_name = fname[:-len(suffix)]
            index[base_name] = fname
    print(index)
    return index

def _remove_raster_and_sidecars(
    directory: str,
    filename: str,
    sidecar_extensions: Sequence[str],
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    删除栅格文件及其相关的
    辅助（sidecar）文件。
    也就是没有成对的WSE或WSE_qual将会被删除
    参数
    ----------
    目录 : str
        包含栅格的目录。
    文件名 : str
        主栅格文件名（例如，“xxx_wse.tif”）。
    sidecar_extensions : Sequence[str]
        附加到栅格主文件名后缀上的扩展名。
    日志器 : Optional[logging.Logger]
        用于记录警告消息的可选日志器。
    """
    stem, _ = os.path.splitext(filename)

    for ext in sidecar_extensions:
        path = os.path.join(directory, stem + ext)
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
        except OSError as exc:
            if logger:
                logger.warning(
                    f"Failed to remove file: {path} ({exc})"
                )

def _cleanup_unpaired_wse_pairs(
    wse_dir: str,
    qual_dir: str,
    logger: Optional[logging.Logger] = None,
    remove_sidecars: bool = True,
) -> Tuple[Set[str], Set[str]]:
    """
    根据文件基名匹配，移除未配对的 WSE 和 WSE_QUAL 栅格。

    配对规则
    ------------
    - WSE：      *_wse.tif
    - WSE_QUAL：*_wse_qual.tif
    - 有效的配对必须具有相同的文件基名。

    参数
    ----------
    wse_dir : str
        包含 WSE 栅格的目录。
    qual_dir : str
        包含 WSE_QUAL 栅格的目录。
    logger : 可选[logging.Logger]
        用于报告删除操作的可选日志器。
    remove_sidecars : bool
        是否删除辅助栅格文件。

    返回值
    -------
    元组[集合[字符串], 集合[字符串]]
        从 (wse_dir, qual_dir) 中移除的基名。
    """
    wse_suffix = "_wse.tif"
    qual_suffix = "_wse_qual.tif"

    wse_index = _index_rasters_by_basename(wse_dir, wse_suffix)
    qual_index = _index_rasters_by_basename(qual_dir, qual_suffix)

    shared_keys = set(wse_index) & set(qual_index)  #&交集运算符
    orphan_wse = set(wse_index) - shared_keys
    orphan_qual = set(qual_index) - shared_keys

    sidecar_exts = (
        ".tif",
        ".tfw",
        ".tif.aux.xml",
        ".tif.ovr",
        ".tif.xml",
    )

    for key in orphan_wse:
        fname = wse_index[key]
        if remove_sidecars:
            _remove_raster_and_sidecars(
                wse_dir, fname, sidecar_exts, logger
            )
        else:
            os.remove(os.path.join(wse_dir, fname))

        if logger:
            logger.info(f"删除了未配对的 WSE 栅格: {fname}")

    for key in orphan_qual:
        fname = qual_index[key]
        if remove_sidecars:
            _remove_raster_and_sidecars(
                qual_dir, fname, sidecar_exts, logger
            )
        else:
            os.remove(os.path.join(qual_dir, fname))

        if logger:
            logger.info(f"删除了未配对的 WSE_QUAL 栅格: {fname}")
    print(orphan_wse)
    print(orphan_qual)
    return orphan_wse, orphan_qual

def P1_validate_and_align_wse_pairs(
    wse_dir: str,
    qual_dir: str,
    logger: Optional[logging.Logger] = None,
    strict: bool = False,
) -> None:
    """
    验证并对齐
    WSE 与 WSE_QUAL 栅格数据集之间的一对一对应关系。

    设计为管道验证步骤：
    - 成功时不输出任何信息。这里需要改一下，即使成功也要输出信息
    - 基于日志的报告
    - 可选的严格失败模式

    参数
    ----------
    wse_dir : str
        包含 WSE 栅格的目录。
    qual_dir : str
        包含 WSE_QUAL 栅格的目录。
    logger : Optional[logging.Logger]
        用于记录警告和状态消息的日志器。
    strict : bool
        若为 True，当不一致情况持续存在时，将引发 RuntimeError。

    抛出异常
    ------
    FileNotFoundError
        如果输入目录不存在。
    NotADirectoryError
        如果输入路径不是目录。
    RuntimeError
        如果 strict=True 且配对仍不一致。
    """
    for directory in (wse_dir, qual_dir):  #必须要给一个文件夹
        if not os.path.exists(directory): #判断路径存不存在
            raise FileNotFoundError(f"路径不存在: {directory}")
        if not os.path.isdir(directory):  #路径存在但不是文件夹
            raise NotADirectoryError(
                f"路径不是一个目录: {directory}"
            )
    

    #计算数量
    print("统计wse的tif数量")
    count_wse = _count_geotiffs_recursive(wse_dir)
    print("统计wse_qual的tif数量")
    count_qual = _count_geotiffs_recursive(qual_dir)

    if count_wse == count_qual:
        return

    if logger:
        logger.warning(
            "GeoTIFF count mismatch detected "
            f"(WSE={count_wse}, WSE_QUAL={count_qual}). "
            "Attempting automatic alignment."
        )

    _cleanup_unpaired_wse_pairs(
        wse_dir, qual_dir, logger=logger, remove_sidecars=True
    )
    
    count_wse_after = _count_geotiffs_recursive(wse_dir)
    count_qual_after = _count_geotiffs_recursive(qual_dir) #recursive递归的
    print(f"WSE清理后数量: {count_wse_after} | Qual清理后数量: {count_qual_after}")


    if count_wse_after != count_qual_after: #!=不等于符号
        message = (
            "清理后，GeoTIFF 计数不一致的问题依然存在 "
            f"(WSE={count_wse_after}, "
            f"WSE_QUAL={count_qual_after})."
        )
        if strict:
            raise RuntimeError(message)
        if logger:
            logger.warning(message)
    else:
        if logger:
            logger.info(
                "WSE 和 WSE_QUAL 数据集已成功对齐。"
            )


#主程序入口，这个主要是运行P0的两个自定义函数模块，以及数据接口的一个配置
def main() -> None:
    """
    目标：实现P1阶段的代码编写。
    """
    #日志配置，全局开启logger变量
    logger = setup_logger(logging.INFO)
    print("logger日志器已开启")
    # ===================== 实验参数 =====================
    # ===================== Data directories ==========================
    # 包含 SWOT WSE 和 WSE 质量栅格产品的根目录。
    folder_path_wse = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\SOWT\WSE")
    folder_path_wse_qual = Path(r"D:\poyang_poject_0\MyProject1\TEXT\poyang\SOWT\WSE_QUAL")

    try:
        print("try块捕获已执行，运行P1_validate_and_align_wse_pairs")
        P1_validate_and_align_wse_pairs(
            wse_dir=folder_path_wse,
            qual_dir=folder_path_wse_qual,
            logger=logger,
            strict=False,
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P1程序已执行完毕")

if __name__ == "__main__":
    main()
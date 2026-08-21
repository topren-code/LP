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

"""
P00自定函数的说明：首先根据源代码更改为适配这套数据的代码，才能运行开。
要点说明
1.鄱阳湖目前只是试行数据，如果做成功，也就是现阶段任意湖泊、水库都可以套用。
2.现阶段下载的SWOT数据直接是NC提取TIFF，没有原版所说的附属文件
3.自定义删除函数成功的静默必须改为可视化
4.研究方法SIF-WAF的代码体量很大，近3000多行代码，逻辑性强，只能分步骤，逐个稳步推进衔接运行
5.日志应该紧跟本人的思维逻辑设置汇报点
"""

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

def _group_geotiffs_by_prefix(
    directory: str,
    suffix_token_count: int,
) -> Dict[str, List[Tuple[str, str]]]:
    """
    根据共同的文件名前缀对 GeoTIFF 文件进行分组。

    文件名使用下划线（“_”）进行分割。最后 `suffix_token_count`
    个标记构成特定于该组的后缀（例如，处理级别或质量标记），
    而剩余的标记则定义逻辑组前缀。

    参数
    ----------
    directory : str
        包含 GeoTIFF (*.tif) 文件的目录。
    suffix_token_count : int
        将作为后缀处理的下划线分隔令牌的数量。

    返回值
    -------
    Dict[str, List[Tuple[str, str]]]
        将 prefix_key 映射到 (suffix_key, filename) 列表的字典。
    最终打印出来的形式是这样的[SWOT_L2_HR_Raster_100m_UTM01W_N_x_x_x_009_138_018F_20240109T010822_20240109T010843': [('PIC0_01_wse',
    'SWOT_L2_HR_Raster_100m_UTM01W_N_x_x_x_009_138_018F_20240109T010822_20240109T010843_PIC0_01_wse.tif')]
    也就是[前缀，(后缀,文件名)]
    """
    print("开始调用_group_geotiffs_by_prefix函数")
    grouped_files: Dict[str, List[Tuple[str, str]]] = {}

    for filename in os.listdir(directory):
        if not filename.lower().endswith(".tif"):  #把文件名小写再判断是不是.tif结尾，如果不是，就跳过这个文件处理，继续下一个
            continue  #注意continue指的是：跳过当前循环的这一次迭代，直接进入下一个循环项

        stem = filename[:-4]  # remove ".tif"
        tokens = stem.split("_")

        if len(tokens) <= suffix_token_count:
            continue  #

        prefix_key = "_".join(tokens[:-suffix_token_count])
        suffix_key = "_".join(tokens[-suffix_token_count:])

        grouped_files.setdefault(prefix_key, []).append((suffix_key, filename))

    pprint(grouped_files)  #把这个字典打印出来查看
    return grouped_files  #return命令，是直接结束分组函数，返回字典
    


def _select_priority_suffix(
    suffix_to_filename: Dict[str, str],
    priority_codes: Sequence[str],
) -> Optional[str]:
    """
    根据预定义的优先级代码，选择优先级最高的后缀键。

    首先匹配到的优先级代码（按优先级从高到低排序）
    将决定所选的后缀。

    参数
    ----------
    suffix_to_filename : Dict[str, str]
        将 suffix_key 映射到 filename 的字典。
    priority_codes : Sequence[str]
        按从高到低的顺序排列的优先级代码。

    返回值
    -------
    Optional[str]
        选定的 suffix_key；若未匹配到任何优先级代码，则返回 None。
    """
    for code in priority_codes:
        for suffix_key in suffix_to_filename:
            if code in suffix_key:  #判断优先级code是否出现在suffix_key中
                print("已找到最优后缀键：suffix_key")
                print(suffix_key)
                return suffix_key   #只要是return，就会结束整个_select_priority_suffix。返回该后缀键并结束整个函数的执行。
    print("没找到最优后缀键")
    return None

#这里的删除成功是静默的，要改代码必须展示出到底删除了那些文件
def _remove_geotiff_sidecars(
    directory: str,
    filename: str,
    extensions: Sequence[str],
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    删除一个 GeoTIFF 文件及其关联的辅助文件。

    参数
    ----------
    directory : str
        包含文件的目录。
    filename : str
        GeoTIFF 文件名（带 .tif 扩展名）。
    extensions : Sequence[str]
        需与 GeoTIFF 一起删除的文件扩展名。
    logger : Optional[logging.Logger]
        用于记录警告消息的日志器；若为 None，则不进行日志记录。
    """
    base_name = os.path.splitext(filename)[0]

    for ext in extensions:
        file_path = os.path.join(directory, base_name + ext)
        if not os.path.exists(file_path):
            continue
        try:
            os.remove(file_path)
        except OSError as exc:
            if logger:
                logger.warning(f"Failed to remove file: {file_path} ({exc})")


def P0_clean_wse_tif_by_priority(
    folder_path_wse: str,
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    每个组中仅保留优先级最高的 SWOT WSE GeoTIFF 文件。

    分组规则
    -------------
    - 前缀：文件名中的令牌，不包括最后 3 个以下划线分隔的令牌
    - 后缀：最后 3 个令牌

    优先级顺序（从高到低）
    ------- ---------------------
    PGC > PIC3 > PIC2 > PIC1 > PIC0


    与被丢弃的 GeoTIFF 文件一同移除.需要注意的是我是直接提取的wse和对应的wse_qual。所以没有这个移除环节
    ----------------------------------------
    .tif, .tfw, .tif.aux.xml, .tif.ovr, .tif.xml

    参数
    ----------
    folder_path_wse : str    
        包含 WSE GeoTIFF 产品的目录。
    logger : 可选[logging.Logger]
        用于记录基本状态或警告消息的可选日志器。
    """
    priority_codes = ("PGC", "PIC3", "PIC2", "PIC1", "PIC0")
    sidecar_extensions = (".tif", ".tfw", ".tif.aux.xml", ".tif.ovr", ".tif.xml")

    groups = _group_geotiffs_by_prefix(
        directory=folder_path_wse,
        suffix_token_count=3,
    )
    
    #这里是for循环，会反复执行调用函数
    print("开始调用_select_priority_suffix函数")
    for prefix_key, entries in groups.items():
        suffix_to_filename = {suffix: fname for suffix, fname in entries} #字典推导式{后缀键，文件名}
        selected_suffix = _select_priority_suffix(
            suffix_to_filename,
            priority_codes,
        )

        if selected_suffix is None:
            if logger:
                logger.info(
                    f"[WSE] No matching priority found for group: {prefix_key}. Files retained."
                )
            continue

        for suffix_key, fname in entries:
            if suffix_key == selected_suffix:
                continue  #立刻结束本次循环的‘这一次’迭代，跳过后面的所有代码，直接进入下一次循环。
            if logger:
                logger.info(f"删除非最优文件: {fname}（及其附属文件）")    
            _remove_geotiff_sidecars(
                folder_path_wse,
                fname,
                sidecar_extensions,
                logger=logger,
            )


def P0_clean_wse_qual_tif_by_priority(
    folder_path_wse_qual: str,
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    每个组中仅保留优先级最高的 SWOT WSE_QUAL GeoTIFF 文件。

    分组规则
    -------------
    - 前缀：文件名中的标记（不包括最后 4 个以下划线分隔的标记）
    - 后缀：最后 4 个标记

    优先级顺序（从高到低）
    --------------------------- -
    PGD > PGC > PID > PIC3 > PIC2 > PIC1 > PIC0

    与被丢弃的 GeoTIFF 文件一同移除
    ----------------------------------------
    .tif、.tfw、.tif.aux.xml、.tif.ovr、.tif.xml

    参数
    ----------
    folder_path : str
        包含 WSE_QUAL GeoTIFF 产品的目录。
    logger : 可选[logging.Logger]
        用于记录基本状态或警告消息的可选日志器。
    """
    priority_codes = ("PGD", "PGC", "PID", "PIC3", "PIC2", "PIC1", "PIC0")
    sidecar_extensions = (".tif", ".tfw", ".tif.aux.xml", ".tif.ovr", ".tif.xml")

    #直接返回字典
    groups = _group_geotiffs_by_prefix(
        directory=folder_path_wse_qual,
        suffix_token_count=4,
    )

    print("开始调用_select_priority_suffix函数")
    for prefix_key, entries in groups.items():
        suffix_to_filename = {suffix: fname for suffix, fname in entries}
        selected_suffix = _select_priority_suffix(
            suffix_to_filename,
            priority_codes,
        )

        if selected_suffix is None:
            if logger:
                logger.info(
                    f"[WSE_QUAL] No matching priority found for group: {prefix_key}. Files retained."
                )
            continue

        for suffix_key, fname in entries:
            if suffix_key == selected_suffix:
                continue
            if logger:
                logger.info(f"删除非最优文件: {fname}（及其附属文件）")    
            _remove_geotiff_sidecars(
                folder_path_wse_qual,
                fname,
                sidecar_extensions,
                logger=logger,
            )


#主程序入口，这个主要是运行P0的两个自定义函数模块，以及数据接口的一个配置
def main() -> None:
    """
    目标：实现P0阶段的代码编写。
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
        print("try块捕获已执行，运行P0_clean_wse_tif_by_priority")
        P0_clean_wse_tif_by_priority(
            folder_path_wse=folder_path_wse,
            logger=logger,
        )
        print("try块捕获已执行，运行P0_clean_wse_qual_tif_by_priority")
        P0_clean_wse_qual_tif_by_priority(
            folder_path_wse_qual=folder_path_wse_qual,
            logger=logger,
        )
    except Exception as exc:
        logger.exception(f"已找到错误 | Error: {exc}")
    print("try块没有发现任何异常")       
    logger.info("P00程序已执行完毕")

if __name__ == "__main__":
    main()

#现在待处理的问题就两个,已全部解决
#现在P00代码已经可以跑通了，但是终端输出的信息还不是很满意
#1.把删除文件成功的静默改为可视化终端
#2.就是完善日志的报点
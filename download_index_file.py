import os
import time
import random
import warnings  # 导入警告管理模块
import requests
import pandas as pd
from db import stock
from db.db_pool import DBPool

DOWNLOAD_DIR = '/Users/congbaochang/PycharmProjects/stockQMT/files/index_files'


def get_index_provider(code_str):
    """精确路由函数"""
    code_int = int(code_str)
    if not code_str.startswith('399'):
        return 'CSI'
    if code_int >= 399800:
        return 'CSI'

    # 3997xx 中证特例注册表
    CSI_3997XX_WHITELIST = {399706, 399707}
    if code_int in CSI_3997XX_WHITELIST:
        return 'CSI'

    return 'CNI'


def download_index_files(index_codes):
    """基于精确规则进行无盲试下载"""
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    print(f"\n====== 开始下载 {len(index_codes)} 个指数的权重文件 ======")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for code in index_codes:
        code_str = str(code).zfill(6)

        # 1. 依靠规则进行确定性分流，绝不盲试
        provider = get_index_provider(code_str)

        if provider == 'CNI':
            url = f"https://www.cnindex.com.cn/sample-detail/download-history?indexcode={code_str}"
            file_name = f"{code_str}_cons.xlsx"
        else:
            url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code_str}closeweight.xls"
            file_name = f"{code_str}closeweight.xls"

        file_path = os.path.join(DOWNLOAD_DIR, file_name)

        # 2. 如果文件已经存在，不需要重复下载
        if os.path.exists(file_path):
            print(f"[跳过] 指数 {code_str} 已存在于本地 ({file_name})。")
            continue

        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"[成功] 依据精确规则，从 {provider} 下载指数 {code_str} 成功！")
            else:
                print(f"[失败] 指数 {code_str} 路由正确但下载失败，状态码: {response.status_code}")
        except Exception as e:
            print(f"[报错] 请求指数 {code_str} 发生异常: {e}")

        time.sleep(random.uniform(0.4, 0.8))


def calculate_weights():
    """遍历下载目录，解析每个文件的权重并无损累加"""
    print("\n====== 开始计算各个指数的权重总和 ======")
    results = []

    if not os.path.exists(DOWNLOAD_DIR):
        return

    for file_name in os.listdir(DOWNLOAD_DIR):
        if file_name.endswith("closeweight.xls"):
            code = file_name.replace("closeweight.xls", "")
        elif file_name.endswith("_cons.xlsx"):
            code = file_name.replace("_cons.xlsx", "")
        else:
            continue

        file_path = os.path.join(DOWNLOAD_DIR, file_name)

        try:
            try:
                # 核心改进：使用上下文管理器临时忽略读取国证Excel时抛出的无默认样式UserWarning
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    df = pd.read_excel(file_path)
            except Exception:
                df = pd.read_csv(file_path, sep=None, engine='python', encoding='gbk')

            weight_col_name = None
            for col in df.columns:
                if 'weight' in str(col).lower() or '权重' in str(col):
                    weight_col_name = col
                    break

            if not weight_col_name:
                print(f"[跳过] {code} 未找到权重列。")
                continue

            total_weight_amplified = 0
            for val in df[weight_col_name]:
                if pd.notna(val):
                    try:
                        # 放大1000倍转整型累加，完美避开浮点数精度坑
                        amplified_val = int(round(float(val) * 1000))
                        total_weight_amplified += amplified_val
                    except ValueError:
                        continue

            final_weight = total_weight_amplified / 1000.0
            results.append((code, final_weight))

        except Exception as e:
            print(f"[报错] 解析文件 {file_name} 发生异常: {e}")

    # 输出统计结果报告
    print("\n====================== 解析结果报告 ======================")
    print(f"{'指数代码':<10} | {'权重总和(%)':<12} | {'是否大于100%':<10}")
    print("-" * 50)

    results.sort(key=lambda x: x[0])
    over_100_count = 0
    for code, weight in results:
        is_over = "✅ 是" if weight > 100.0 else "否"
        if weight > 100.0:
            over_100_count += 1
        print(f"{code:<14} | {weight:<15.3f} | {is_over}")

    print("-" * 50)
    print(f"总计验证了 {len(results)} 个指数，其中有 {over_100_count} 个指数的权重总和大于100%。")


if __name__ == '__main__':
    db = DBPool()
    try:
        index_codes = stock.get_unique_index_codes(db)
        print(f"获取到需要处理的指数数量: {len(index_codes)}")

        if index_codes:
            download_index_files(index_codes)
            calculate_weights()
    except Exception as e:
        print(f"运行发生错误: {e}")
    finally:
        db.close()
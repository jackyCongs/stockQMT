import os
import time
import random
import warnings
import tempfile
import requests
import re
import pandas as pd
from db import stock


class IndexSnapshotSynchronizer:
    def __init__(self, db_pool):
        self.db = db_pool
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(current_dir)
        self.download_dir = os.path.join(self.project_root, 'files', 'index_files')
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def _ensure_directory(self):
        """Ensure the target download folder exists safely."""
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def get_index_provider(self, code_str):
        """Accurate routing function between CSI and CNI data sources."""
        code_int = int(code_str)
        if not code_str.startswith('399'):
            return 'CSI'
        if code_int >= 399800:
            return 'CSI'

        csi_3997xx_whitelist = {399706, 399707}
        if code_int in csi_3997xx_whitelist:
            return 'CSI'

        return 'CNI'

    def parse_file_date(self, file_path):
        """Parse the historical base date inside the file and enforce YYYYMMDD format."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                df = pd.read_excel(file_path)
        except Exception:
            df = pd.read_csv(file_path, sep=None, engine='python', encoding='gbk')

        if df.empty:
            return None

        for col in df.columns:
            col_str = str(col).lower()
            if '日期' in col_str or 'date' in col_str:
                raw_date = str(df[col].iloc[0]).strip()
                # Clear decimal tails and timestamps
                clean_date_str = raw_date.split('.')[0].split(' ')[0]
                # Force extract exactly 8 digits via regex
                unified_date = re.sub(r'\D', '', clean_date_str)
                if len(unified_date) >= 8:
                    return unified_date[:8]
        return None

    def _test_remote_date(self, code_str, provider):
        """Download a single sample file to a temp directory to probe its internal date."""
        if provider == 'CNI':
            url = f"https://www.cnindex.com.cn/sample-detail/download-history?indexcode={code_str}"
        else:
            url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code_str}closeweight.xls"

        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code == 200:
                with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    tmp_file_path = tmp_file.name

                base_date = self.parse_file_date(tmp_file_path)
                os.remove(tmp_file_path)
                return base_date
        except Exception as e:
            print(f"[采样报错] 探测指数 {code_str} 远程日期异常: {e}")
        return None

    def sync_latest_weights(self):
        self._ensure_directory()

        # Pull target index codes from database
        index_codes = stock.get_unique_index_codes(self.db)
        print(f"获取到需要处理的指数数量: {len(index_codes)}")
        if not index_codes:
            return

        # 1. Group indices for sampling
        csi_pool = []
        cni_pool = []
        for code in index_codes:
            c_str = str(code).zfill(6)
            provider = self.get_index_provider(c_str)
            if provider == 'CSI':
                csi_pool.append(c_str)
            else:
                cni_pool.append(c_str)

        print(f"\n====== 触发前置判定：开始抽检官方是否发布新月份权重 ======")

        # 2. Sample CSI
        skip_csi = False
        if csi_pool:
            samples = random.sample(csi_pool, min(2, len(csi_pool)))
            csi_checks = []
            for s in samples:
                r_date = self._test_remote_date(s, 'CSI')
                if r_date:
                    chk_name = f"{r_date}_{s}_CSI_closeweight.xls"
                    csi_checks.append(os.path.exists(os.path.join(self.download_dir, chk_name)))
                time.sleep(0.5)
            if csi_checks and all(csi_checks):
                print("【判定结果】中证 (CSI) 远程文件日期未发生变更，整体跳过本次批量下载。")
                skip_csi = True

        # 3. Sample CNI
        skip_cni = False
        if cni_pool:
            samples = random.sample(cni_pool, min(2, len(cni_pool)))
            cni_checks = []
            for s in samples:
                r_date = self._test_remote_date(s, 'CNI')
                if r_date:
                    chk_name = f"{r_date}_{s}_CNI_cons.xlsx"
                    cni_checks.append(os.path.exists(os.path.join(self.download_dir, chk_name)))
                time.sleep(0.5)
            if cni_checks and all(cni_checks):
                print("【判定结果】国证 (CNI) 远程文件日期未发生变更，整体跳过本次批量下载。")
                skip_cni = True

        # 4. Process bulk downloading loop
        print(f"\n====== 开始执行权重文件正式同步处理 ======")
        for code in index_codes:
            code_str = str(code).zfill(6)
            provider = self.get_index_provider(code_str)

            if provider == 'CSI' and skip_csi:
                continue
            if provider == 'CNI' and skip_cni:
                continue

            if provider == 'CNI':
                url = f"https://www.cnindex.com.cn/sample-detail/download-history?indexcode={code_str}"
                suffix = "_CNI_cons.xlsx"
            else:
                url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code_str}closeweight.xls"
                suffix = "_CSI_closeweight.xls"

            try:
                response = requests.get(url, headers=self.headers, timeout=15)
                if response.status_code != 200:
                    print(f"[失败] 指数 {code_str} 下载失败，状态码: {response.status_code}")
                    continue

                with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    tmp_file_path = tmp_file.name

                base_date = self.parse_file_date(tmp_file_path)
                if not base_date:
                    print(f"[跳过] 指数 {code_str} 临时文件解析日期失败，跳过. ")
                    os.remove(tmp_file_path)
                    continue

                final_file_name = f"{base_date}_{code_str}{suffix}"
                final_file_path = os.path.join(self.download_dir, final_file_name)

                if os.path.exists(final_file_path):
                    print(f"[本地已对齐] 指数 {code_str} 的 {base_date} 版本已存在，无需重复落盘。")
                    os.remove(tmp_file_path)
                else:
                    os.rename(tmp_file_path, final_file_path)
                    print(f"[保存成功] 指数 {code_str} 成功更新并重命名为: {final_file_name}")

            except Exception as e:
                print(f"[报错] 处理指数 {code_str} 发生异常: {e}")

            time.sleep(random.uniform(0.4, 0.8))

        # Run verification report immediately after processing
        self.calculate_weights_report()

    def calculate_weights_report(self):
        """Scan the download directory, aggregate weights, and print the formatted report."""
        print("\n====== 开始计算各个指数的权重总和 ======")
        results = []

        if not os.path.exists(self.download_dir):
            return

        for file_name in os.listdir(self.download_dir):
            if file_name.endswith("_CSI_closeweight.xls") or file_name.endswith("_CNI_cons.xlsx"):
                parts = file_name.split('_')
                if len(parts) >= 2:
                    base_date = parts[0]
                    code = parts[1]
                else:
                    continue
            else:
                continue

            file_path = os.path.join(self.download_dir, file_name)

            try:
                total_weight_amplified = 0
                try:
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

                for val in df[weight_col_name]:
                    if pd.notna(val):
                        try:
                            amplified_val = int(round(float(val) * 1000))
                            total_weight_amplified += amplified_val
                        except ValueError:
                            continue

                final_weight = total_weight_amplified / 1000.0
                results.append((code, base_date, final_weight))

            except Exception as e:
                print(f"[报错] 解析文件 {file_name} 发生异常: {e}")

        print("\n=============================== 解析结果报告 ===============================")
        print(f"{'指数代码':<10} | {'基准日期':<10} | {'权重总和(%)':<12} | {'是否大于100%':<10}")
        print("-" * 65)

        results.sort(key=lambda x: x[0])
        over_100_count = 0

        for code, base_date, weight in results:
            is_over = "✅ 是" if weight > 100.0 else "否"
            if weight > 100.0:
                over_100_count += 1
            print(f"{code:<14} | {base_date:<14} | {weight:<15.3f} | {is_over}")

        print("-" * 65)
        print(f"总计验证了 {len(results)} 个指数，其中有 {over_100_count} 个指数的权重总和大于100%。")
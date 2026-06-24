import pymysql
import pandas as pd
import numpy as np

def load_db_config():
    setting_path = "/Users/congbaochang/Desktop/检验Alpha有效性/mysql.setting"
    config = {}
    with open(setting_path, 'r') as f:
        for line in f:
            if '=' in line:
                k, v = line.split('=', 1)
                config[k.strip()] = v.strip()
    return config

def main():
    config = load_db_config()
    conn = pymysql.connect(
        host=config.get('host'),
        user=config.get('user'),
        password=config.get('password'),
        database=config.get('database'),
        port=3306
    )
    
    cursor = conn.cursor()
    try:
        # 1. Ensure stock table has beta column
        try:
            cursor.execute("ALTER TABLE stock ADD COLUMN beta DECIMAL(6,4) DEFAULT 1.0000;")
            conn.commit()
            print("Successfully added beta column to stock table.")
        except Exception as e:
            print("Beta column already exists in stock table.")
            
        # 2. Get active LOFs
        cursor.execute("""
            SELECT code, target_worth_url 
            FROM stock 
            WHERE inner_etf_type = 'lof' AND target_worth_url IS NOT NULL AND target_worth_url != '' AND status = 1;
        """)
        lof_funds = cursor.fetchall()
        print(f"Found {len(lof_funds)} active LOF funds.")
        
        update_data = []
        for code, idx_url in lof_funds:
            stock_code = str(code).strip().zfill(6)
            idx_code = str(idx_url).strip().zfill(6)
            
            # Fetch last 90 days of history
            cursor.execute("""
                SELECT date, accumulated_net_worth 
                FROM stock_daily_nav 
                WHERE stock_code = %s AND accumulated_net_worth IS NOT NULL AND accumulated_net_worth > 0
                ORDER BY date DESC
                LIMIT 90;
            """, (stock_code,))
            nav_rows = cursor.fetchall()
            
            cursor.execute("""
                SELECT trade_date, close_price, pre_close 
                FROM index_daily_history 
                WHERE index_code = %s AND pre_close > 0
                ORDER BY trade_date DESC
                LIMIT 90;
            """, (idx_code,))
            idx_rows = cursor.fetchall()
            
            if not nav_rows or not idx_rows:
                continue
                
            # Build DataFrames
            df_nav = pd.DataFrame(nav_rows, columns=['date', 'nav'])
            df_nav['date'] = pd.to_datetime(df_nav['date']).dt.strftime('%Y-%m-%d')
            df_nav = df_nav.sort_values('date')
            df_nav['nav_return'] = df_nav['nav'].astype(float).pct_change()
            
            df_idx = pd.DataFrame(idx_rows, columns=['date', 'close', 'pre_close'])
            df_idx['date'] = pd.to_datetime(df_idx['date']).dt.strftime('%Y-%m-%d')
            df_idx = df_idx.sort_values('date')
            df_idx['idx_return'] = (df_idx['close'].astype(float) - df_idx['pre_close'].astype(float)) / df_idx['pre_close'].astype(float)
            
            # Merge
            df_merged = pd.merge(df_nav[['date', 'nav_return']], df_idx[['date', 'idx_return']], on='date').dropna()
            
            beta = 0.93 # default fallback
            if len(df_merged) >= 15:
                df_merged['nav_return'] = df_merged['nav_return'].astype(float)
                df_merged['idx_return'] = df_merged['idx_return'].astype(float)
                
                cov_val = df_merged['idx_return'].cov(df_merged['nav_return'])
                var_idx = df_merged['idx_return'].var()
                
                if var_idx > 0:
                    calculated_beta = cov_val / var_idx
                    if 0.3 <= calculated_beta <= 1.5:
                        beta = calculated_beta
            
            update_data.append((round(beta, 4), stock_code))
            
        # 3. Update database
        if update_data:
            cursor.executemany("UPDATE stock SET beta = %s WHERE code = %s;", update_data)
            conn.commit()
            print(f"Successfully calculated and updated Beta for {len(update_data)} funds.")
            
            # Print a few examples
            cursor.execute("SELECT code, name, beta FROM stock WHERE inner_etf_type = 'lof' AND beta != 1.0000 LIMIT 10;")
            examples = cursor.fetchall()
            print("\nSample calculated Betas:")
            for r in examples:
                print(f"  Code: {r[0]} | Name: {r[1]:<12} | Beta: {r[2]}")
                
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()

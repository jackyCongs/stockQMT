# coding=utf-8


def enhance_stock_code(code, type = 'stock'):
    if type == 'index':
        if code.startswith('000'):
            return f"{code}.SH"
        if code.startswith('399'):
            return f"{code}.SZ"
        print(f"当前 code: {code}-{type}, 无对应来源")
        return code
    # 上证
    if len(code) == 6 and (code.startswith('6') or code.startswith('900') or code.startswith('5')):
        return f"{code}.SH"
    # 深证
    elif len(code) == 6 and (code.startswith('0') or code.startswith('3') or code.startswith('158')
                             or code.startswith('159') or code.startswith('16')):
        return f"{code}.SZ"
    print(f"当前 code: {code}-{type}, 无对应来源")
    return code
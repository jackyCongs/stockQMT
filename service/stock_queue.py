# coding=utf-8

class StockNode:
    def __init__(self, code, name, quantity, price, premium, real_premium, appraisal, update_time):
        self.code = code
        self.name = name
        self.quantity = quantity
        self.price = price
        self.premium = premium
        self.real_premium = real_premium
        self.appraisal = appraisal
        self.update_time = update_time
        self.prev = None
        self.next = None


class StockQueue:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0
        self.code_map = {}

    def upsert_stock(self, code, name, quantity, price, premium, real_premium, appraisal, update_time):
        # 统一处理新增和更新
        if code in self.code_map:
            self._remove_node(self.code_map[code])
            del self.code_map[code]

        new_node = StockNode(code, name, quantity, price, premium, real_premium, appraisal, update_time)
        self.code_map[code] = new_node

        # 处理空队列
        if not self.head:
            self.head = self.tail = new_node
            self.size = 1
            return True

        # 寻找插入位置
        current = self.head
        while current and current.premium >= premium:
            current = current.next

        # 插入头部
        if current == self.head:
            new_node.next = self.head
            self.head.prev = new_node
            self.head = new_node
        # 插入尾部
        elif not current:
            new_node.prev = self.tail
            self.tail.next = new_node
            self.tail = new_node
        # 中间插入
        else:
            prev_node = current.prev
            prev_node.next = new_node
            new_node.prev = prev_node
            new_node.next = current
            current.prev = new_node

        self.size += 1
        return True

    def remove_stock(self, code):
        # 安全删除处理
        if code not in self.code_map:
            return False

        node = self.code_map[code]
        self._remove_node(node)
        del self.code_map[code]
        return True

    def _remove_node(self, node):
        if node.prev:
            node.prev.next = node.next
        else:
            self.head = node.next

        if node.next:
            node.next.prev = node.prev
        else:
            self.tail = node.prev

        self.size -= 1

    def print_queue(self):
        current = self.head
        print()
        while current:
            print(f"[{current.code}] {current.name} 估值: {round(current.appraisal, 5)} premium: {round(current.premium, 3)}% real_premium: {round(current.real_premium, 3)}% 价格: {round(current.price, 3)} 数量 {current.quantity} 总价 {round(current.quantity*current.price*100, 2)}")
            current = current.next
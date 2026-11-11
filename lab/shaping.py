"""把大答案变小：看结构、取一部分、只留几个字段；放不下的存成文件。"""
import json
import re

TOKEN = re.compile(r'\[(\*|-?\d*(?::-?\d*)?)\]|\.?([^.\[\]]+)')


def shape(value, depth=0):
    """一行字说清一个答案长什么样，不带数据。"""
    if isinstance(value, dict):
        if depth >= 2:
            return '{%d 个键}' % len(value)
        inner = ', '.join(f'{key}: {shape(item, depth + 1)}' for key, item in list(value.items())[:16])
        return '{' + inner + (' …共%d个键' % len(value) if len(value) > 16 else '') + '}'
    if isinstance(value, list):
        return f'[{len(value)}× {shape(value[0], depth + 1)}]' if value else '[]'
    if isinstance(value, str):
        return 'str' if len(value) > 24 else repr(value)
    return repr(value)


def pick(value, path):
    """路径例子：items[0]、projects[*].name、entries[0:5]、registry.materials。"""
    for index, key in TOKEN.findall(path or ''):
        if key:
            value = [item.get(key) if isinstance(item, dict) else None for item in value] \
                if isinstance(value, list) else value[key]
        elif index == '*':
            if isinstance(value, dict):
                value = list(value.values())
        elif ':' in index:
            start, _, stop = index.partition(':')
            value = value[int(start) if start else None:int(stop) if stop else None]
        else:
            value = value[int(index)]
    return value


def keep(value, fields):
    """只留这些字段。列表里的每一项都这样处理。"""
    if not fields:
        return value
    if isinstance(fields, str):             # 模型有时把数组写成 "a,b,c"
        fields = [item for item in re.split(r'[,，\s]+', fields) if item]
    if isinstance(value, list):
        return [keep(item, fields) for item in value]
    if isinstance(value, dict):
        return {key: pick(value, key) if ('.' in key or '[' in key) else value.get(key) for key in fields}
    return value


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def table(columns, rows, cell=160):
    """查询结果写成制表符分开的文字，比 JSON 省字。"""
    def text(item):
        if item is None:
            return ''
        if isinstance(item, bytes):
            return f'<{len(item)} 字节>'
        item = str(item).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
        return item if len(item) <= cell else item[:cell] + f'…(+{len(item) - cell}字)'
    return '\n'.join(['\t'.join(columns)] + ['\t'.join(text(item) for item in row) for row in rows])


class Answers:
    """一次会话里放不下的答案，存到会话文件夹的 answers 目录。"""

    def __init__(self, folder):
        self.folder = folder / 'answers'

    def save(self, value, suffix='.json'):
        self.folder.mkdir(parents=True, exist_ok=True)
        number = len(list(self.folder.iterdir())) + 1
        path = self.folder / f'{number:03d}{suffix}'
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return 'answers/' + path.name

    def render(self, value, max_chars=6000):
        """能放下就原样给；放不下给结构和开头，并说明完整内容在哪。"""
        text = value if isinstance(value, str) else compact(value)
        if len(text) <= max_chars:
            return text
        saved = self.save(value, '.txt' if isinstance(value, str) else '.json')
        head = [f'[答案太大：{len(text) // 1000} 千字，没有全部给出。完整内容存在 {saved}]']
        if not isinstance(value, str):
            head.append('结构：' + shape(value))
        head.append('开头：' + text[:max(400, max_chars // 3)])
        head.append('下一步：用 pick / fields 只取需要的部分，或在 run_python 里用 lab.load("%s") 读完整内容来算。' % saved)
        return '\n'.join(head)

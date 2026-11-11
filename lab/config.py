r"""新系统的位置和设置。接口密钥单独放在 auth.json，不进 settings.json。

    workbench2\
      lab\            程序（内核、智能体、研发库、算法包、读法、导出、调研、网页接口）
      web\            网页（研发工作台）
      skills\         随程序带的技能（SKILL.md + 代码）
      data\           数据（可以用环境变量 LAB_HOME 换到别处）
        库\<产品库>\库.db          一个产品库一个 SQLite
        库\<产品库>\原始文件\       仪器导出、导入的文献和报告，原样存放
        库\知识库\库.db            几个产品共用：文献、命题、推理链、练习记录
        产出\<产品库>\              做出来的报告、PPT、图片、表格
        工作区\<事项>\              智能体写的代码和中间文件
        内核.db                    账本：事件、事项、一轮、卡片、复查
        笔记\  技能\  页面\         智能体和你写的文字（技能可以覆盖随程序带的）
        settings.json  auth.json
"""
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                     # …\workbench2
PROGRAM_SKILLS = ROOT / 'skills'
WEB = ROOT / 'web'
PYTHON = Path(os.environ.get('LAB_PYTHON') or sys.executable)
NODE = os.environ.get('LAB_NODE') or 'node'
HOME = Path(os.environ.get('LAB_HOME') or ROOT / 'data')
KNOWLEDGE = '知识库'                                           # 共用的知识库名字


def library_dir(name):
    path = HOME / '库' / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(name):
    path = library_dir(name) / '原始文件'
    path.mkdir(parents=True, exist_ok=True)
    return path


def outputs_dir(name):
    path = HOME / '产出' / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace(lane):
    path = HOME / '工作区' / lane
    path.mkdir(parents=True, exist_ok=True)
    return path


def notes_dir():
    return HOME / '笔记'


def skills_dirs():
    """先找你和智能体写的，再找随程序带的。"""
    return [HOME / '技能', PROGRAM_SKILLS]


def pages_dir():
    return HOME / '页面'


def libraries():
    folder = HOME / '库'
    return sorted(path.name for path in folder.iterdir() if (path / '库.db').is_file()) if folder.is_dir() else []


# ---------- 设置 ----------

DEFAULT_PROFILES = [
    {'id': 'local-gateway', 'name': '本机网关', 'base_url': 'http://127.0.0.1:3425/v1', 'format': 'openai',
     'auth': 'none', 'model': 'auto/gpt-6.1-sol', 'extra_models': [], 'context_budget': 200000, 'max_output': 16000,
     'effort': '', 'thinking': False, 'images': True, 'price_in': '', 'price_out': ''},
    {'id': 'anthropic', 'name': 'Anthropic 官方', 'base_url': 'https://api.anthropic.com/v1', 'format': 'anthropic',
     'auth': 'key', 'model': 'claude-opus-5-5', 'extra_models': [], 'context_budget': 200000, 'max_output': 32000,
     'effort': '', 'thinking': True, 'images': True, 'price_in': '', 'price_out': ''},
]
DEFAULT_ASK_FIRST = ['删除记录、清空或覆盖已有的数据', '改动原始数据或原始文件', '把东西发到外面（邮件、上传、发给别人）',
                     '改变计算规则、判定口径或结论的说法']


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')


def settings():
    value = _read(HOME / 'settings.json', {})
    value.setdefault('profiles', json.loads(json.dumps(DEFAULT_PROFILES)))
    value.setdefault('active', value['profiles'][0]['id'])
    value.setdefault('ask_first', list(DEFAULT_ASK_FIRST))
    value.setdefault('library', (libraries() or ['产品A'])[0])
    return value


def save_settings(value):
    _write(HOME / 'settings.json', value)


def profile(profile_id=None):
    value = settings()
    wanted = profile_id or value['active']
    return next((item for item in value['profiles'] if item['id'] == wanted), value['profiles'][0])


def new_profile_id():
    return 'p-' + uuid.uuid4().hex[:8]


def key_for(profile_id):
    return _read(HOME / 'auth.json', {}).get(profile_id, '')


def save_key(profile_id, key):
    keys = _read(HOME / 'auth.json', {})
    if key:
        keys[profile_id] = key
    else:
        keys.pop(profile_id, None)
    _write(HOME / 'auth.json', keys)

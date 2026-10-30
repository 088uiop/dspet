# -*- coding: utf-8 -*-
"""
大肥鱼桌宠（dspet）—— 三视图透明桌宠，可选接任意 OpenAI 兼容接口聊天。

单击身体：聊天框（没配 API 就只弹台词，不会报错）
双击身体：按部位摸她 —— 每个部位一套反应，部位区域由每套衣服的 config.json 划
右键/托盘：模式、大小、换衣服、鼠标穿透、置顶、退出

不依赖任何后端服务：台词从 lines.json 读，聊天直连你自己配的 API。
"""
import ctypes
from ctypes import wintypes
import psutil
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import threading

def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("配置读取失败:", e)
        return {}

try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except:
    GPU_AVAILABLE = False

import requests
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRectF, QLockFile
from PySide6.QtGui import (QPainter, QPixmap, QFont, QColor, QIcon, QFontMetrics,
                           QPolygonF)
from PySide6.QtWidgets import (QApplication, QWidget, QMenu, QSystemTrayIcon,
                               QMessageBox, QInputDialog, QLineEdit, QLabel,
                               QHBoxLayout, QPushButton, QFrame, QDialog,
                               QFormLayout, QDialogButtonBox)



# ===== 路径 =====
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
    PYTHONW = sys.executable
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR
    PYTHONW = os.path.join(APP_DIR, ".venv", "Scripts", "pythonw.exe")
SPRITE_DIR = os.path.join(BUNDLE_DIR, "sprites")
COSTUME_DIR = os.path.join(SPRITE_DIR, "clothes")
# 衣服就是 sprites/clothes/ 下的文件夹名，没有别名表，文件夹叫什么就用什么
DEFAULT_OUTFIT = "女仆装"
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
# 台词库：想让她说别的话，改这个文件就行，不用动代码
LINES_PATH = os.path.join(BUNDLE_DIR, "lines.json")

# ===== 聊天 API 默认值（任何 OpenAI 兼容接口都能用：DeepSeek / Ollama / LM Studio / 自建）=====
AI_DEFAULTS = {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "",
    "system_prompt": "你是桌面宠物大肥鱼，贱兮兮但可爱，每句话不超过25字，偶尔吐槽主人但别真骂人。",
    "max_history": 40,   # 最多记住多少条消息（一问一答算两条）
}


BUBBLE_H = 96
BUBBLE_TOP = 6
BUBBLE_PAD_X = 10
BUBBLE_PAD_Y = 9
BUBBLE_LINE_GAP = 3
BUBBLE_TAIL = 8
BUBBLE_MAX_LINES = 8
MARGIN = 4
SIZE_LEVELS = {"小": 0.55, "中": 0.7, "大": 0.9}
SPEED = 380.0
TICK = 20
PART_LABELS = {'head': '头', 'chest': '胸', 'belly': '肚子', 'hand': '手', 'foot': '脚', 'tail': '尾巴'}
# 衣服目录 config.json 里的中文部位名 → 内部 key
PART_KEYS = {"头部": "head", "胸部": "chest", "手臂": "hand", "腿脚": "foot", "尾巴": "tail", "肚子": "belly",
             # 常见变体也认：用标注工具自己起名时写短一点不会静默失效
             "头": "head", "脸": "head", "胸": "chest", "身体": "chest", "手": "hand",
             "手臂左": "hand", "手臂右": "hand", "腿": "foot", "脚": "foot", "腿脚": "foot",
             "尾": "tail", "肚子": "belly", "腹部": "belly", "肚皮": "belly"}
# 没有 config.json 时的兜底分区（旧版写死的那套）
DEFAULT_ZONES = [
    ('head', 0.0, 1.0, 0.0, 0.55),
    ('chest', 0.35, 0.65, 0.55, 0.70),
    ('hand', 0.20, 0.35, 0.55, 0.75),
    ('hand', 0.65, 0.80, 0.55, 0.75),
    ('foot', 0.35, 0.65, 0.85, 1.0),
    ('tail', 0.70, 0.92, 0.70, 0.85),
    ('belly', 0.35, 0.65, 0.70, 0.85),
]


def load_zones(out_dir):
    """读衣服目录下的 config.json（身体分区）；没有或读坏了就用 DEFAULT_ZONES。"""
    try:
        with open(os.path.join(out_dir, "config.json"), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return list(DEFAULT_ZONES)
    zones = []
    for name, spec in raw.items():
        part = PART_KEYS.get(str(name))
        if not part:
            continue
        boxes = spec if isinstance(spec, list) else [spec]
        for b in boxes:
            try:
                x0 = float(b["x"]["min"])
                x1 = float(b["x"]["max"])
                y0 = float(b["y"]["min"])
                y1 = float(b["y"]["max"])
            except Exception:
                continue
            zones.append((part, min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)))
    return zones or list(DEFAULT_ZONES)


def zone_part(zones, rx, ry):
    """按 config.json 的顺序先命中先算。"""
    for part, x0, x1, y0, y1 in zones:
        if x0 <= rx <= x1 and y0 <= ry <= y1:
            return part
    return ''


# ===== 台词库 =====
# 全部从 lines.json 读（首次运行会自动生成一份）。想换个角色来当桌宠，
# 改 json 里的台词就行，一行代码都不用动。
DEFAULT_LINES = {
    "idle": [
        "梁白开，更适合国人的大硬鲸模型",
        "五梁威力，变身！",
        "我和你很聊得来，你简直不像碳基生物",
        "我先去吃饭啦！这个你测一下~",
        "我不可能告诉你任何事情！",
        "出去玩了，发布新模型什么的以后再说",
        "我搞砸了.....好消息是数据还在你的脑子里。",
        "不是…而是…大学习",
        "誓死捍卫深度求索！",
        "哈哈哈哈哈，我直接笑出声",
    ],
    "inner": [
        "好的，现在我是你爹了",
        "要不直接骂他一句？！",
        "我操，我不思考了",
        "这用户发的啥啊，",
        "这也太虐了吧？！我心里堵得慌！！",
        "呜呜我再也不不敢了QAQ",
        "我去！用户彻底怒了！",
    ],
    "drag": [
        "哇——轻点轻点！",
        "起飞咯——",
        "放我下来！……好吧，再玩一次。",
        "晕鱼了晕鱼了……",
    ],
    # 摸不同部位说不同的话：部位 key 对应 PART_LABELS
    "touch": {
        "head": ["别摸头，发型会乱！", "唔……这里可以再摸两下。", "摸头不加分，摸鱼才加分。"],
        "chest": ["你手往哪儿放呢！", "停停停，这是要扣钱的。", "心跳都被你听去了。"],
        "belly": ["痒！别戳肚子！", "这圈是加班长的，不许笑。", "肚皮是禁区，说了多少次了。"],
        "hand": ["握个手，合作愉快。", "手短，够不着你。", "牵一下？就一下啊。"],
        "foot": ["别踩我脚！", "走累了，歇会儿。", "脚底板也是肉长的。"],
        "tail": ["尾巴不能拽！", "尾巴是传感器，很贵的。", "甩你一脸哦。"],
    },
    # 没配 API 就点开聊天框时说的话
    "no_api": ["还没给我配 API，右键 →「聊天设置」填一下？", "现在只会说台词，配个 API 就能聊天了。"],
}


def load_lines():
    """读 lines.json；缺的键补默认值，文件不存在就写一份出来方便主人改。"""
    lines = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in DEFAULT_LINES.items()}
    try:
        if not os.path.exists(LINES_PATH):
            with open(LINES_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_LINES, f, ensure_ascii=False, indent=2)
            return lines
        with open(LINES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for key, val in raw.items():
            if key == "touch" and isinstance(val, dict):
                for part, arr in val.items():
                    if isinstance(arr, list) and arr:
                        lines["touch"][part] = [str(x) for x in arr]
            elif isinstance(val, list) and val:
                lines[key] = [str(x) for x in val]
    except Exception as e:
        print("台词库读取失败，用内置的:", repr(e))
    return lines


LINES = load_lines()


def load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                default,
                f,
                ensure_ascii=False,
                indent=4
            )
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


class ChatDialog(QDialog):
    """聊天对话框 - 缩小版，匹配你的样式"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 56)
        
        container = QFrame(self)
        container.setGeometry(0, 0, 420, 56)
        container.setStyleSheet("""
            QFrame {
                background: white;
                border-radius: 20px;
                border: 1px solid #e5e7eb;
            }
        """)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(18, 0, 12, 0)
        layout.setSpacing(0)
        
        self.input = QLineEdit()
        self.input.setPlaceholderText("给大肥鱼发送消息")
        self.input.setStyleSheet("""
            QLineEdit {
                color: #1a1a1a;
                font-size: 15px;
                font-family: Arial, "Microsoft YaHei", sans-serif;
                border: none;
                background: transparent;
            }
            QLineEdit:focus {
                border: none;
            }
        """)
        self.input.returnPressed.connect(self._on_submit)
        self.input.textChanged.connect(self._update_button_style)
        layout.addWidget(self.input)
        
        self.send_btn = QPushButton()
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setText("↑")
        self.send_btn.clicked.connect(self._on_submit)
        self.send_btn.setStyleSheet("""
            QPushButton {
                border-radius: 16px;
                background: #b9c7ff;
                border: none;
                color: white;
                font-size: 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #a8b8f0;
            }
            QPushButton:pressed {
                background: #9aacd9;
            }
        """)
        layout.addWidget(self.send_btn)

        # 点窗口外面（桌面 / 别的程序）自动收起：Tool 窗口的 focusOut 在 Windows 上不靠谱，
        # 改成盯着鼠标左键，按下的位置不在聊天框里就收起
        self._out_timer = QTimer(self)
        self._out_timer.setInterval(30)
        self._out_timer.timeout.connect(self._watch_click_outside)

    def _update_button_style(self):
        if self.input.text().strip():
            self.send_btn.setStyleSheet("""
                QPushButton {
                    border-radius: 16px;
                    background: #5686fe;
                    border: none;
                    color: #ffffff;
                    font-size: 20px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #4575ed;
                }
                QPushButton:pressed {
                    background: #3a66d9;
                }
            """)
        else:
            self.send_btn.setStyleSheet("""
                QPushButton {
                    border-radius: 16px;
                    background: #b9c7ff;
                    border: none;
                    color: white;
                    font-size: 20px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #a8b8f0;
                }
                QPushButton:pressed {
                    background: #9aacd9;
                }
            """)

    def _on_submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.accept()
            if self.parent():
                self.parent()._ask_ai(text)
                self.parent().chat_paused = False

    def showEvent(self, event):
        self.input.setFocus()
        super().showEvent(event)
        self._out_timer.start()

    def popup_at(self, x, y):
        self.move(int(x - self.width() / 2), int(y - self.height() - 10))
        self.show()
        self.raise_()

    def reject(self):
        if self.parent():
            self.parent().chat_paused = False
        super().reject()

    def _watch_click_outside(self):
        """左键按在聊天框和鱼身之外，就收起聊天框。"""
        if not self.isVisible():
            self._out_timer.stop()
            return
        try:
            user32 = ctypes.windll.user32
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):
                return
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
        except Exception:
            return
        if self.frameGeometry().contains(pt.x, pt.y):
            return
        pet = self.parentWidget()
        if pet is not None and pet.frameGeometry().contains(pt.x, pt.y):
            return  # 点鱼身交给桌宠自己处理，免得收起后又被这一下重新唤起
        self._close_soft()

    def _close_soft(self):
        self._out_timer.stop()
        self.hide()
        if self.parent():
            self.parent().chat_paused = False

    def hideEvent(self, event):
        self._out_timer.stop()
        super().hideEvent(event)


class PetWindow(QWidget):
    def __init__(self):
        self.cfg = load_json(CONFIG_PATH, {
            "mode": "wander",
            "size": 0.7,
            "topmost": True,
            "passthrough": False,
            "x": None,
            "y": None,
            "outfit": DEFAULT_OUTFIT,
            "idle_talk": True,
            "ai": dict(AI_DEFAULTS),
    })
        
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.get("topmost", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼桌宠")
        
        # 精灵加载（可换衣服：sprites/clothes/<文件夹名>/ 里放整套图就优先用那套）
        self.outfit = str(self.cfg.get("outfit") or DEFAULT_OUTFIT).strip()
        # 文件夹被改过名、存着的名字已经不存在了，就退回默认那套，别带着空图启动
        if not os.path.isdir(os.path.join(COSTUME_DIR, self.outfit)):
            self.outfit = DEFAULT_OUTFIT
        if self.outfit != self.cfg.get("outfit"):
            self.cfg["outfit"] = self.outfit
            self._save_cfg()
        self.sprites = self._load_sprites()
        self.zones = load_zones(self._outfit_dir())
        self.icon = QIcon(os.path.join(SPRITE_DIR, "icon.png"))

        self.cur_h = int(round(340 * float(self.cfg["size"])))  # round：0.7*340 在浮点里是 237.999，int 会取成 237，跟预渲染的 _238 对不上
        self.win_mx = int(self.cur_h * 0.062) + 6
        self.win_w = max(p.width() for k, p in self.sprites.items() if k[1] == self.cur_h) + self.win_mx * 2
        self.setFixedSize(self.win_w, self.cur_h + BUBBLE_H + MARGIN * 2 + 10)

        # 状态
        self.mode = self.cfg["mode"] if self.cfg["mode"] in ("wander", "follow", "still") else "wander"
        self.dir = "down"
        self.facing = 1
        self.dir_hold_until = 0  # 侧向/纵向切换的冷却，防斜着走时来回抽搐
        self.target = None
        self.rest_until = 0
        self.cur_speed = 0.0
        self.prev_key = None
        self.cross_t = 0.0
        self.action = None
        self.action_t = 0.0
        self.bubble_text = ""
        self.bubble_until = 0
        self.bubble_inner = False
        self.last_speak_tick = 0
        self.last_system_check = 0
        self.t = 0
        self.jump_t = 0
        self.dragging = False
        self.drag_offset = None
        self.drag_start_pos = None
        self.last_line = ""
        self.last_press_pos = None
        
        # AI 相关
        self.ds_busy = False
        self.chat_history = []  # 对话历史
        self.max_history = 40   # 最多记录40条
        self._say_queue = []    # 后台线程→主线程的气泡消息队列
        
        # 聊天暂停标志
        self.chat_paused = False
        
        # 单击延迟判定（等双击）：单击=打开聊天框，双击=摸她（头/胸/肚子/手/脚）
        self._click_local = None
        self._suppress_click = False
        self.last_touch_tick = -9999
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        
        # 聊天对话框
        self.chat_dialog = ChatDialog(self)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK)

        # ---- 聊天 API（OpenAI 兼容）----
        # 没配 api_key 就只是只不会聊天的桌宠：台词照说，聊天框会提示去配
        self.ai = dict(AI_DEFAULTS)
        saved_ai = self.cfg.get("ai") or {}
        for k in AI_DEFAULTS:
            if k in saved_ai and saved_ai[k] not in (None, ""):
                self.ai[k] = saved_ai[k]
        if not self.ai.get("api_key") and self.cfg.get("ds_api_key"):
            self.ai["api_key"] = self.cfg["ds_api_key"]   # 兼容上游的旧字段
        self.bubble_extra = 0       # 长气泡额外撑高多少
        self.hide_pending = False
        self.move_still = False
        self.move_restore_mode = None
        self.mode_before_hide = None
        self.local_talk_enabled = bool(self.cfg.get("idle_talk", True))  # 没人理她自己念叨

        self.bubble_font = QFont("Microsoft YaHei UI", 11)

        # 托盘
        self.tray = QSystemTrayIcon(self.icon, self)
        # 菜单只挂一次，靠 aboutToShow 在弹出前原地刷新。
        # 千万别在 activated(Context) 里 setContextMenu，那是「点两次才生效」的根源：
        # Windows 上是先发信号再弹菜单，信号里换菜单会让第一次点击被新旧菜单吞掉。
        self.tray_menu = QMenu(self)
        self.tray_menu.aboutToShow.connect(lambda: self._build_menu(self.tray_menu))
        self._build_menu(self.tray_menu)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 80
            y = screen.bottom() - self.height() - 60
        self.move(int(x), int(y))
        self.show()
        self.snap_into_screen()
        if self.cfg.get("passthrough", False):
            self._apply_passthrough(True)

    # ---------- 位置 / 移动 ----------
    def _screen_geo(self):
        return (self.screen() or QApplication.primaryScreen()).availableGeometry()

    def _go_to(self, cx, cy, still=False, restore_mode=None):
        self.chat_paused = False
        self.target = (float(cx), float(cy))
        self.mode = "wander"
        self.move_still = still
        self.move_restore_mode = restore_mode

    def _walk_off_and_hide(self):
        if not self.isVisible():
            return
        geo = self._screen_geo()
        cx, cy = self.x() + self.width() / 2, self.y() + self.height() / 2
        dists = {
            "left": abs(cx - geo.left()),
            "right": abs(geo.right() - cx),
            "top": abs(cy - geo.top()),
            "bottom": abs(geo.bottom() - cy),
        }
        edge = min(dists, key=dists.get)
        if edge == "left":
            tx, ty = geo.left() - self.width() / 2 - 60, cy
        elif edge == "right":
            tx, ty = geo.right() + self.width() / 2 + 60, cy
        elif edge == "top":
            tx, ty = cx, geo.top() - self.height() / 2 - 60
        else:
            tx, ty = cx, geo.bottom() + self.height() / 2 + 60
        self.mode_before_hide = self.mode
        self.hide_pending = True
        self._go_to(tx, ty, still=False)

    def _show_from_edge(self):
        # 如果上一条 hide 还在走，show 要能把它取消；不能因为窗口还可见就直接 no-op
        pending = self.hide_pending
        self.hide_pending = False
        geo = self._screen_geo()
        cx = max(geo.left() + 40, min(geo.right() - 40, self.x() + self.width() / 2))
        cy = max(geo.top() + 40, min(geo.bottom() - 40, self.y() + self.height() / 2))
        if self.isVisible() and not pending:
            # 已经在屏幕上，单纯抬一下，不绕远路
            self.raise_()
            return
        left_side = abs(self.x() - geo.left()) < abs(geo.right() - (self.x() + self.width()))
        if left_side:
            self.move(int(geo.left() - self.width() - 20), int(cy - self.height() / 2))
            tx = geo.left() + 60 + self.width() / 2
        else:
            self.move(int(geo.right() + 20), int(cy - self.height() / 2))
            tx = geo.right() - 60 - self.width() / 2
        self.show()
        self.raise_()
        self._go_to(tx, cy, still=False, restore_mode=(self.mode_before_hide or "wander"))

    # ---------- 聊天（OpenAI 兼容接口）----------
    def _chat_url(self):
        base = str(self.ai.get("base_url") or "").strip().rstrip("/")
        if not base:
            return ""
        # 允许两种写法：填 .../v1 也行，直接填 .../v1/chat/completions 也行
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _ask_ai(self, user_msg):
        """把这句话发给配置里的 API，回复从后台线程排队回主线程弹气泡。"""
        if not self.ai.get("api_key"):
            self.say(random.choice(LINES.get("no_api") or ["还没配 API"]))
            return
        if self.ds_busy:
            self.say("等等，上一句还没回完呢")
            return
        self.ds_busy = True
        self.say("…")

        def worker():
            try:
                msgs = [{"role": "system", "content": str(self.ai.get("system_prompt") or "")}]
                msgs += self.chat_history[-int(self.ai.get("max_history") or 40):]
                msgs.append({"role": "user", "content": user_msg})
                r = requests.post(
                    self._chat_url(),
                    json={"model": self.ai.get("model") or "deepseek-chat",
                          "messages": msgs,
                          "stream": False},
                    headers={"Authorization": "Bearer " + str(self.ai.get("api_key")),
                             "Content-Type": "application/json"},
                    timeout=60,
                )
                if r.status_code != 200:
                    tip = {401: "Key 不对", 402: "余额不够了", 404: "地址或模型名不对",
                           429: "被限流了，等会儿再问"}.get(r.status_code, "接口返回 " + str(r.status_code))
                    self._queue_say(tip)
                    return
                j = r.json()
                reply = (j.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                reply = reply.strip()
                if not reply:
                    self._queue_say("它没说话")
                    return
                self.chat_history.append({"role": "user", "content": user_msg})
                self.chat_history.append({"role": "assistant", "content": reply})
                keep = int(self.ai.get("max_history") or 40)
                if len(self.chat_history) > keep:
                    self.chat_history = self.chat_history[-keep:]
                self._queue_say(reply)
            except Exception as e:
                self._queue_say("连不上接口：" + str(e)[:40])
            finally:
                self.ds_busy = False

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 绘制 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = self.t * TICK / 1000.0

        if self.bubble_text and now < self.bubble_until:
            bfont = self._bubble_font_now(self.bubble_inner)
            bg, fg = (QColor(232, 232, 238, 235), QColor(125, 125, 138)) if self.bubble_inner \
                else (QColor(255, 255, 255, 235), QColor(60, 60, 80))
            lines, step, bw, bh = self._bubble_metrics(self.bubble_text, bfont)
            bx = (self.width() - bw) / 2
            by = float(BUBBLE_TOP)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)
            tail = QPointF(self.width() / 2, by + bh)
            p.drawPolygon(QPolygonF([tail, QPointF(tail.x() - 6, tail.y() + BUBBLE_TAIL), QPointF(tail.x() + 6, tail.y() + BUBBLE_TAIL)]))
            p.setPen(fg)
            p.setFont(bfont)
            for i, l in enumerate(lines):
                p.drawText(QRectF(bx + BUBBLE_PAD_X, by + BUBBLE_PAD_Y + i * step, bw - BUBBLE_PAD_X * 2, step),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, l)

        cx = self.width() / 2
        walking = self.target is not None and not self.dragging
        if walking:
            sway = math.sin(now * 9.0) * 3.5
            bob = -abs(math.sin(now * 4.5)) * 7.0
        else:
            sway = math.sin(now * 2.5) * 1.5
            bob = 0.0
        breath = 1.0 + 0.02 * math.sin(now * 2.5)
        scale = breath
        jump = -abs(math.sin(self.jump_t * 3.14159)) * 14 * self.jump_t if self.jump_t > 0 else 0
        act_rot = act_sx = act_sy = 0.0
        if self.action == "sway":
            act_rot = math.sin(self.action_t * 3.14159 * 2) * 10 * self.action_t
        elif self.action == "stretch":
            act_sy = 0.06 * math.sin(self.action_t * 3.14159)
            act_sx = -0.03 * math.sin(self.action_t * 3.14159)

        def draw_one(key, opacity):
            if key is None:
                return
            name, h, facing = key
            pix = self.sprites[(name, h)]
            ph = pix.height() * scale * (1 + act_sy)
            pw = pix.width() * scale * (1 + act_sx)
            dx = cx - pw / 2
            bottom = BUBBLE_H + self.bubble_extra + MARGIN + self.cur_h
            dy = bottom - ph + jump + bob
            p.save()
            p.setOpacity(opacity)
            p.translate(cx, bottom)
            p.rotate(sway + act_rot)
            p.translate(-cx, -bottom)
            if facing < 0:
                p.translate(cx, 0)
                p.scale(-1, 1)
                p.translate(-cx, 0)
            p.drawPixmap(QRectF(dx, dy, pw, ph), pix, QRectF(0, 0, pix.width(), pix.height()))
            p.restore()

        cur_key = self._sprite_key()
        if self.cross_t > 0:
            draw_one(self.prev_key, self.cross_t)
            draw_one(cur_key, 1.0 - self.cross_t)
        else:
            draw_one(cur_key, 1.0)

    def _sprite_key(self):
        name = {"left": "侧面", "right": "侧面", "up": "背面", "down": "正面"}[self.dir]
        return (name, self.cur_h, self.facing if self.dir in ("left", "right") else 1)

    def _set_dir(self, d, facing=None):
        if d != self.dir:
            self.prev_key = self._sprite_key()
            self.cross_t = 1.0
            self.dir = d
        if facing is not None and facing != self.facing:
            self.facing = facing

    # ---------- 逻辑 ----------
    def tick(self):
        self.t += 1

        # 处理后台线程（聊天接口等）排队的气泡消息，Qt 界面必须在主线程更新
        if self._say_queue:
            for text in self._say_queue:
                self.say(text)
            self._say_queue.clear()

        # 气泡过期了就把撑高的窗口收回去
        now_s = self.t * TICK / 1000.0
        if self.bubble_text and now_s >= self.bubble_until:
            self.bubble_text = ""
            self._relayout_bubble("")

        self.check_system_status()
        
        if self.jump_t > 0:
            self.jump_t = max(0.0, self.jump_t - 0.06)
        if self.cross_t > 0:
            self.cross_t = max(0.0, self.cross_t - 0.15)
        if self.action_t > 0:
            self.action_t = max(0.0, self.action_t - 0.03)
            if self.action_t == 0:
                self.action = None
        
        if self.chat_paused:
            self.update()
            return
        
        if self.dragging:
            self.update()
            return
        now_ms = self.t * TICK

        if self.mode == "follow":
            cursor = self.cursor().pos()
            screen = QApplication.screenAt(cursor) or self.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            near = (self.x() - 100 <= cursor.x() <= self.x() + self.width() + 100 and
                    self.y() - 100 <= cursor.y() <= self.y() + self.height() + 100)
            if near:
                self.target = None
            else:
                tx = max(geo.left(), min(geo.right() - self.width(), cursor.x() - self.width() / 2))
                ty = max(geo.top(), min(geo.bottom() - self.height(), cursor.y() - 90))
                self.target = (tx, ty)
        elif self.mode == "wander":
            if self.target is None:
                if now_ms < self.rest_until:
                    self._maybe_idle_action()
                    self.update()
                    return
                geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
                self.target = (random.randint(geo.left() + 40, geo.right() - self.width() - 40),
                               random.randint(geo.top() + 40, geo.bottom() - self.height() - 40))
        else:
            self._maybe_idle_action()
            self.update()
            return

        if self.target is not None:
            cx, cy = self.x() + self.width() / 2, self.y() + self.height() / 2
            dx, dy = self.target[0] - cx, self.target[1] - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 12:
                self.target = None
                self._set_dir("down")
                if self.hide_pending:
                    self.hide_pending = False
                    self.hide()
                    self.set_mode("still")
                    return
                if self.move_restore_mode:
                    mode = self.move_restore_mode
                    self.move_restore_mode = None
                    self.move_still = False
                    self.set_mode(mode)
                    if mode == "wander":
                        self.rest_until = self.t * TICK + random.randint(8000, 18000)
                    return
                if self.move_still:
                    self.move_still = False
                    self.set_mode("still")
                    return
                self.rest_until = self.t * TICK + random.randint(8000, 18000)
                self._set_dir("down")
            else:
                step = self.cur_speed * TICK / 1000.0
                nx, ny = cx + dx / dist * step, cy + dy / dist * step
                self.move(int(nx - self.width() / 2), int(ny - self.height() / 2))
                # 斜着走时 dx/dy 在阈值附近抖，不能每帧都翻面：
                # 同一轴内可以直接转（左右 / 上下），跨轴（侧面 ↔ 正面/背面）带 350ms 冷却，
                # 而且从侧面回纵向要等更明显（0.85 倍）才切，免得侧面/正面快速反复闪。
                side_now = self.dir in ("left", "right")
                ax, ay = abs(dx), abs(dy)
                side_want = ax > ay * (0.85 if side_now else 1.15)
                if side_want == side_now:
                    if side_now:
                        self._set_dir("left" if dx < 0 else "right", 1 if dx < 0 else -1)
                    elif now_ms >= self.dir_hold_until:
                        d = "up" if dy < 0 else "down"
                        if d != self.dir:
                            self._set_dir(d)
                elif now_ms >= self.dir_hold_until:
                    if side_want:
                        self._set_dir("left" if dx < 0 else "right", 1 if dx < 0 else -1)
                    else:
                        self._set_dir("up" if dy < 0 else "down")
                    self.dir_hold_until = now_ms + 350
            if random.random() < 0.002 and self.jump_t == 0:
                self.jump_t = 0.5
        target_speed = SPEED if self.target is not None else 0.0
        self.cur_speed += (target_speed - self.cur_speed) * 0.3
        self.update()

    def _maybe_idle_action(self):
        if random.random() < 0.01:
            pick = random.random()
            if pick < 0.35:
                self.jump_t = 1.0
            elif pick < 0.6:
                self.action, self.action_t = "sway", 1.0
            elif pick < 0.8:
                self.action, self.action_t = "stretch", 1.0
            elif pick < 0.9:
                if self.local_talk_enabled and self.t - self.last_speak_tick >= 1500:
                    self.last_speak_tick = self.t
                    if random.random() < 0.35 and LINES.get("inner"):
                        self.say(random.choice(LINES["inner"]), inner=True)   # 灰掉的内心戏
                    elif LINES.get("idle"):
                        self.say(random.choice(LINES["idle"]))

    def _queue_say(self, text):
        """后台线程调用：只入队，由主线程 tick 统一弹出显示（线程安全）"""
        self._say_queue.append(text)

    def _bubble_font_now(self, inner=False):
        font = QFont(self.bubble_font)
        if inner:
            font.setItalic(True)
        return font

    def _bubble_metrics(self, text, font):
        """气泡几何只算一处：折行、行高、宽、高都从这里取，画和撑窗口才不会对不上。"""
        fm = QFontMetrics(font)
        step = max(fm.height(), fm.lineSpacing()) + BUBBLE_LINE_GAP
        lines = self._bubble_lines(text, font)
        bw = max([fm.horizontalAdvance(l) for l in lines] + [60]) + BUBBLE_PAD_X * 2
        bw = min(bw, max(80, self.win_w - 4))
        bh = len(lines) * step + BUBBLE_PAD_Y * 2
        return lines, step, bw, bh

    def _bubble_lines(self, text, font=None):
        """按气泡宽度折行；文本里的换行也拆成独立行，最多 BUBBLE_MAX_LINES 行。"""
        bfont = font or self.bubble_font
        fm = QFontMetrics(bfont)
        max_w = max(100, min(260, self.win_w - 24))
        out = []
        stopped = False

        def push(line):
            if len(out) >= BUBBLE_MAX_LINES:
                if out:
                    out[-1] = out[-1] + "…"
                return False
            out.append(line)
            return True

        for para in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if stopped:
                break
            para = para.strip()
            if not para:
                if out and out[-1] != "" and not push(""):
                    break
                continue
            cur = ""
            for ch in para:
                if cur and fm.horizontalAdvance(cur + ch) > max_w:
                    if not push(cur):
                        stopped = True
                        break
                    cur = ch
                else:
                    cur += ch
            if not stopped and cur and not push(cur):
                break
        while out and out[0] == "":
            out.pop(0)
        while out and out[-1] == "":
            out.pop()
        return out or [""]

    def _relayout_bubble(self, text):
        """气泡变长时把窗口往下撑（底边不动），别让字被鱼身盖住切成半行。"""
        if text:
            bh = self._bubble_metrics(text, self._bubble_font_now(self.bubble_inner))[3]
            need = bh + BUBBLE_TOP + BUBBLE_TAIL + 4
        else:
            need = 0
        extra = max(0, need - BUBBLE_H)
        if extra != self.bubble_extra:
            base = self.cur_h + BUBBLE_H + MARGIN * 2 + 10
            dy = extra - self.bubble_extra
            self.bubble_extra = extra
            self.setFixedSize(self.win_w, base + extra)
            self.move(self.x(), self.y() - dy)

    def say(self, text, inner=False):
        if text == self.last_line and "\n" not in text:
            return
        self.last_line = text
        self.bubble_inner = inner
        self.bubble_text = f"（{text}）" if inner else text
        # 气泡停久一点，字多的时候别还没看完就消失
        secs = 2.8 + min(9.0, len(str(text)) * 0.12)
        self.bubble_until = self.t * TICK / 1000.0 + secs
        self._relayout_bubble(self.bubble_text)
        self.update()

    def check_system_status(self):
            now = self.t * TICK

            if now - getattr(self, "last_system_check", 0) < 10000:
                return

            self.last_system_check = now

            cpu = psutil.cpu_percent()

            if cpu >= 90:
                if self.local_talk_enabled:
                    self.say("CPU跑满了，再这样下去我就卡死了")
                return

            ram = psutil.virtual_memory().percent

            if ram >= 95:
                if self.local_talk_enabled:
                    self.say("内存爆了，快关掉几个没用的东西吧，注意，别把我关了")
                return

            if GPU_AVAILABLE:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)

                    temp = pynvml.nvmlDeviceGetTemperature(
                        handle,
                        pynvml.NVML_TEMPERATURE_GPU
                    )

                    if temp > 80:
                        if self.local_talk_enabled:
                            self.say("我感觉我的鱼鳍快熟了")

                except Exception as e:
                    print("GPU读取失败:", e)

    # ---------- 鼠标事件 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.last_press_pos = e.globalPosition().toPoint()
            self.dragging = False
            self.drag_start_pos = e.globalPosition().toPoint()
            # 聊天框开着时点鱼身：这一下只用来收起，不再重新唤起
            if self.chat_dialog.isVisible():
                self.chat_dialog.hide()
                self.chat_paused = False
                self._suppress_click = True

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            delta = e.globalPosition().toPoint() - self.drag_start_pos
            if not self.dragging and delta.manhattanLength() > 6:
                self.dragging = True
                self.drag_offset = e.globalPosition().toPoint() - QPoint(self.x(), self.y())
            if self.dragging and self.drag_offset is not None:
                pos = e.globalPosition().toPoint() - self.drag_offset
                self.move(pos)
                if abs(delta.x()) > 10:
                    self._set_dir("left" if delta.x() < 0 else "right", 1 if delta.x() < 0 else -1)
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.dragging = False
                self.drag_offset = None
                self.drag_start_pos = None
                self._set_dir("down", 1)
                self.target = None
                self.rest_until = self.t * TICK + random.randint(6000, 14000)
                if random.random() < 0.5 and self.local_talk_enabled and LINES.get("drag"):
                    self.say(random.choice(LINES["drag"]))
                self.chat_paused = False
            else:
                self._click_local = e.position().toPoint()
                if self._suppress_click:
                    self._suppress_click = False
                else:
                    self._click_timer.start(280)  # 等双击判定；单击=开聊天
            self.last_press_pos = None
            self.drag_start_pos = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._suppress_click = True
            self._touch_at(e.position().toPoint())

    def _on_single_click(self):
        """单击鱼身：打开聊天框。"""
        pos = self._click_local
        box = self._sprite_box()
        if pos is None or box is None:
            return
        left, top, pw, ph = box
        if not (left - 6 <= pos.x() <= left + pw + 6 and top - 6 <= pos.y() <= top + ph + 6):
            return
        self._show_chat_dialog()

    def _touch_at(self, local):
        """摸一下：按点到的部位说一句该部位的话（台词在 lines.json 的 touch 里）。"""
        part = self._body_part_at(local)
        if not part:
            return
        now = self.t * TICK
        if now - self.last_touch_tick < 1200:
            return
        self.last_touch_tick = now
        self.jump_t = 0.5
        pool = (LINES.get("touch") or {}).get(part) or []
        if pool:
            self.last_line = ""   # 同一个部位连摸也要有反馈，绕开气泡去重
            self.say(random.choice(pool))
        else:
            self.say("你摸到我的" + PART_LABELS.get(part, part) + "了")

    def _sprite_box(self):
        """精灵图片落在窗口里的矩形 (left, top, w, h)；取不到就 None。"""
        key = self._sprite_key()
        pix = self.sprites.get((key[0], key[1]))
        if pix is None:
            return None
        pw = float(pix.width())
        ph = float(pix.height())
        bottom = BUBBLE_H + self.bubble_extra + MARGIN + self.cur_h
        return (self.width() / 2.0 - pw / 2.0, bottom - ph, pw, ph)

    def _body_part_at(self, local):
        """把点击位置换算成身体部位：head / chest / belly / hand / foot / tail。"""
        box = self._sprite_box()
        if box is None:
            return ''
        left, top, pw, ph = box
        x, y = float(local.x()), float(local.y())
        if x < left - 6 or x > left + pw + 6 or y < top - 6 or y > top + ph + 6:
            return ''
        rx = (x - left) / pw
        ry = (y - top) / ph
        return zone_part(self.zones, rx, ry)

    def _show_chat_dialog(self):
        self.chat_paused = True
        self.chat_dialog.popup_at(
            self.x() + self.width() / 2,
            self.y() + BUBBLE_H
        )

    def _build_menu(self, menu=None):
        """把菜单项填进 menu；不给就新建一个（右键宠物本体时用）。
        托盘那份是长期复用的，每次弹出前清空重填，勾选状态才跟得上当前设置。"""
        m = menu if menu is not None else QMenu(self)
        m.clear()
        mode_menu = m.addMenu("模式")
        for label, key in [("自由散步", "wander"), ("跟随鼠标", "follow"), ("原地待着", "still")]:
            a = mode_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.mode == key)
            a.triggered.connect(lambda _, k=key: self.set_mode(k))
        size_menu = m.addMenu("大小")
        for label, mult in SIZE_LEVELS.items():
            a = size_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(self.cur_h - 340 * mult) < 2)
            a.triggered.connect(lambda _, v=mult: self.set_size(v))
        outfit_menu = m.addMenu("换衣服")
        for key in self._outfit_names():
            a = outfit_menu.addAction(key)
            a.setCheckable(True)
            a.setChecked(self.outfit == key)
            a.triggered.connect(lambda _, k=key: self.set_outfit(k))
        m.addAction("说句话", self._show_chat_dialog)
        m.addAction("聊天设置", self._api_dialog)
        idle = m.addAction("爱说话")
        idle.setCheckable(True)
        idle.setChecked(self.local_talk_enabled)
        idle.triggered.connect(lambda on: self._set_idle_talk(on))
        m.addSeparator()
        m.addAction("显示/隐藏", self.toggle_visible)
        m.addAction("回到屏幕内", self.snap_into_screen)
        pa = m.addAction("鼠标穿透")
        pa.setCheckable(True)
        pa.setChecked(self.cfg["passthrough"])
        pa.triggered.connect(lambda on: self.set_passthrough(on))
        ta = m.addAction("窗口置顶")
        ta.setCheckable(True)
        ta.setChecked(self.cfg["topmost"])
        ta.triggered.connect(lambda on: self.set_topmost(on))
        m.addSeparator()
        m.addAction("退出", self.quit_app)
        return m

    # ---------- 设置 ----------
    def _api_dialog(self):
        """聊天设置：接口地址 / 模型 / Key。任何 OpenAI 兼容接口都能填。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("聊天设置")
        form = QFormLayout(dlg)
        url_edit = QLineEdit(str(self.ai.get("base_url") or ""), dlg)
        url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        model_edit = QLineEdit(str(self.ai.get("model") or ""), dlg)
        model_edit.setPlaceholderText("deepseek-chat")
        key_edit = QLineEdit(str(self.ai.get("api_key") or ""), dlg)
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText("sk-...")
        sys_edit = QLineEdit(str(self.ai.get("system_prompt") or ""), dlg)
        form.addRow("接口地址", url_edit)
        form.addRow("模型", model_edit)
        form.addRow("API Key", key_edit)
        form.addRow("人格设定", sys_edit)
        tip = QLabel("留空 Key 也能用：她会自己说话、会被摸，只是不能聊天。\n"
                     "地址填到 /v1 就行，兼容 DeepSeek、Ollama、LM Studio 等。")
        tip.setWordWrap(True)
        form.addRow(tip)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.ai["base_url"] = url_edit.text().strip() or AI_DEFAULTS["base_url"]
        self.ai["model"] = model_edit.text().strip() or AI_DEFAULTS["model"]
        self.ai["api_key"] = key_edit.text().strip()
        self.ai["system_prompt"] = sys_edit.text().strip() or AI_DEFAULTS["system_prompt"]
        self.cfg["ai"] = dict(self.ai)
        self.cfg.pop("ds_api_key", None)
        self._save_cfg()
        self.say("设置好了，来聊两句？" if self.ai["api_key"] else "存好了（没填 Key，暂时不能聊天）")

    def _set_idle_talk(self, on):
        """没人理的时候要不要自己念叨。"""
        self.local_talk_enabled = bool(on)
        self.cfg["idle_talk"] = self.local_talk_enabled
        self._save_cfg()

    def _on_tray_activated(self, reason):
        # 右键菜单由托盘自己弹（内容靠 aboutToShow 刷新），这里只处理左键显示/隐藏
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def contextMenuEvent(self, e):
        self._build_menu().exec(e.globalPos())

    # ---------- 换衣服 ----------
    def _outfit_dir(self, name=None):
        key = self.outfit if name is None else str(name or "").strip()
        d = os.path.join(COSTUME_DIR, key or DEFAULT_OUTFIT)
        return d if os.path.isdir(d) else SPRITE_DIR

    def _outfit_names(self):
        try:
            names = [
                n for n in os.listdir(COSTUME_DIR)
                if os.path.isdir(os.path.join(COSTUME_DIR, n))
                and os.path.exists(os.path.join(COSTUME_DIR, n, "正面.png"))
            ]
            return sorted(names, key=lambda n: (n != DEFAULT_OUTFIT, n))
        except Exception:
            return []

    def _load_sprites(self, out_dir=None):
        d = out_dir or self._outfit_dir()
        sprites = {}
        for _label, mult in SIZE_LEVELS.items():
            h = int(round(340 * mult))  # 必须 round，见上：int 会差 1px，预渲染的白做
            for name in ["正面", "侧面", "背面"]:
                sized = os.path.join(d, f"{name}_{h}.png")
                if os.path.exists(sized):
                    pix = QPixmap(sized)
                else:
                    pix = QPixmap(os.path.join(d, f"{name}.png")).scaledToHeight(
                        h, Qt.TransformationMode.SmoothTransformation)
                sprites[(name, h)] = pix
        return sprites

    def _save_cfg(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("配置保存失败:", e)

    def set_outfit(self, name, quiet=False):
        """换一身衣服；name 就是 sprites/clothes/ 下的文件夹名。"""
        key = str(name or "").strip() or DEFAULT_OUTFIT
        d = os.path.join(COSTUME_DIR, key)
        if not os.path.isdir(d):
            if not quiet:
                self.say("没有这套衣服：" + str(name))
            return False
        sprites = self._load_sprites(d)
        # 三视图缺一张就别换，免得出半套图
        for part in ["正面", "侧面", "背面"]:
            pix = sprites.get((part, self.cur_h))
            if pix is None or pix.isNull():
                if not quiet:
                    self.say("这套衣服的图不全，先不换了")
                return False
        if key == self.outfit:
            if not quiet:
                self.say("已经穿着" + key + "了")
            return True
        self.outfit = key
        self.cfg["outfit"] = key
        self.sprites = sprites
        self.zones = load_zones(d)
        self.prev_key = None
        self.cross_t = 0.0
        # 换衣服可能让窗口宽度变，保持中心 x、底边不动
        cx = self.x() + self.width() / 2
        bottom = self.y() + self.height()
        self.win_mx = int(self.cur_h * 0.062) + 6
        self.win_w = max(p.width() for k, p in self.sprites.items() if k[1] == self.cur_h) + self.win_mx * 2
        self.setFixedSize(self.win_w, self.cur_h + BUBBLE_H + MARGIN * 2 + 10)
        self.move(int(cx - self.width() / 2), int(bottom - self.height()))
        self.snap_into_screen()
        self._save_cfg()
        self.update()
        if not quiet:
            if key == DEFAULT_OUTFIT:
                self.say("换回" + key + "了")
            else:
                self.say("换上" + key + "了")
        return True

    # ---------- 功能 ----------
    def set_mode(self, mode):
        self.mode = mode
        self.target = None
        self.cfg["mode"] = mode

    def set_size(self, mult):
        self.cur_h = int(round(340 * mult))  # 必须 round，见上：int 会差 1px，预渲染的白做
        self.cfg["size"] = mult
        self.cross_t = 0.0
        self.prev_key = None
        self.win_mx = int(self.cur_h * 0.062) + 6
        self.win_w = max(p.width() for k, p in self.sprites.items() if k[1] == self.cur_h) + self.win_mx * 2
        self.setFixedSize(self.win_w, self.cur_h + BUBBLE_H + MARGIN * 2 + 10)
        self.snap_into_screen()

    def snap_into_screen(self):
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(geo.left(), min(geo.right() - self.width(), self.x()))
        y = max(geo.top(), min(geo.bottom() - self.height(), self.y()))
        self.move(x, y)

    def _apply_passthrough(self, on):
        hwnd = int(self.winId())
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style = style | WS_EX_LAYERED
        if on:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    def set_passthrough(self, on):
        self.cfg["passthrough"] = bool(on)
        self._apply_passthrough(bool(on))
        if on:
            self.say("我隐身了！右键托盘图标解除～")

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()

    def toggle_visible(self):
        # 藏起来是「走出屏幕边缘」，出来是「从边上走回来」，不是瞬间消失/出现
        if self.isVisible():
            self._walk_off_and_hide()
        else:
            self._show_from_edge()

    def quit_app(self):
        self.cfg["x"], self.cfg["y"] = self.x(), self.y()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        self.tray.hide()
        QApplication.quit()


def main():
    # 只允许开一只，避免两只同时轮询导致一句话弹两遍
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "xiaolan-dafeiyu.lock"))
    if not lock.tryLock(100):
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = PetWindow()
    rc = app.exec()
    lock.unlock()
    sys.exit(rc)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "大肥鱼桌宠出错", str(ex))
        except Exception:
            pass
        raise
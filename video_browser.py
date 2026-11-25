"""ハヤミル（旧名：動画ブラウザ）
登録したフォルダ（中のフォルダも含む）の動画を一覧にして、すぐ並べ替える。
ステップ1：フォルダ登録・起動時の自動読み込み・一覧表示・名前順／更新順の並べ替え
ステップ2：ワード検索・検索履歴（×で消す）・お気に入りワード
ステップ3：再生ソフトの登録・選択と再生
ステップ4：タグ付けと、押すだけで一覧が出るタグのボタン
ステップ5：動画の長さを裏で少しずつ調べて表示（調べた結果は保存）
ステップ6：サムネイルタブ（今の一覧を画像で並べる＋選んだ1本を4枚で大きく見る）
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import unicodedata
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

APP_NAME = "ハヤミル"
OLD_APP_NAMES = ("動画ブラウザ",)  # 前の名前で保存した設定ファイルも読めるように
HISTORY_MAX = 10
DEFAULT_PLAYER = "Windows の標準アプリ"
# 見た目の色（数を絞って、全体をそろえる）
C_BG = "#f5f6f8"          # 窓の地の色
C_SURFACE = "#ffffff"     # 入力欄・一覧・ボタンの地
C_BORDER = "#d5d9e0"      # 枠の線
C_TEXT = "#1f2328"        # 文字
C_SUB = "#6b7280"         # 控えめな文字
C_ACCENT = "#2f6fde"      # 目立たせる色（青）
C_ACCENT_DARK = "#1f56b8"
C_ACCENT_SOFT = "#e6efff"  # 押している・選んでいるボタンの地
C_HOVER = "#eef3fb"       # マウスが乗ったとき
C_STRIPE = "#eef2f8"      # 一覧の1行おきの色
DEFAULT_COLUMNS = ("name", "tags", "dur", "mtime", "size", "folder")

VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm", ".flv",
    ".mts", ".m2ts", ".mpg", ".mpeg", ".ts", ".3gp",
}


def app_dir():
    """設定ファイルを置く場所（exe のときは exe と同じフォルダ）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")


def load_settings():
    data = {"folders": [], "sort": "mtime", "history": [], "favorites": [],
            "players": [], "player": ""}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return normalize_favorites(data)


DEFAULT_GROUPS = ("出演者", "ジャンル", "シリーズ", "その他")
FIXED_COLUMNS = ("play", "star")   # いつも左端に置く列（▶ 再生、☆ 星）。ドラッグで動かさない


def normalize_favorites(data):
    """お気に入りを分類つきの形にそろえる。
    前の版の「分類なしのお気に入り」は、「その他」に入れて引きつぐ"""
    if not isinstance(data.get("fav_groups"), list):
        old = data.get("favorites", [])
        data["fav_groups"] = [{"name": n, "words": list(old) if n == "その他" else []}
                              for n in DEFAULT_GROUPS]
    data.pop("favorites", None)
    return data


def save_settings(data):
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


TAGS_PATH = os.path.join(app_dir(), "tags.json")


def load_tags():
    """タグは { 動画の場所: [タグ, …] } の形で、設定とは別のファイルに保存する"""
    try:
        with open(TAGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_tags(tags):
    tmp = TAGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TAGS_PATH)


DURATIONS_PATH = os.path.join(app_dir(), "durations.json")
DUR_WORKERS = 4   # 同時に調べる本数


def load_durations():
    """{ 動画の場所: [更新日時, サイズ, 長さ(秒) / 調べられなかったら -1] }"""
    try:
        with open(DURATIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_durations(data):
    tmp = DURATIONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, DURATIONS_PATH)


def find_ffprobe():
    import shutil
    return shutil.which("ffprobe")


def probe_duration(ffprobe, path):
    """ffprobe で動画の長さ（秒）を調べる。調べられなければ -1"""
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return round(float(r.stdout.strip().splitlines()[0]), 2)
    except Exception:
        # どんな失敗でも、調べる係が止まってしまわないようにする
        return -1


def fmt_dur(sec):
    if sec is None or sec < 0:
        return "—"
    s = int(round(sec))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


THUMBS_DIR = os.path.join(app_dir(), "thumbs")
THUMB_W, THUMB_H = 384, 216        # 保存するサムネイルの大きさ（一番大きく見るときの大きさ）
THUMB_SUFFIX = "_L"                # 前の小さい絵（192×108）と名前を分けるための印
THUMB_SHOW_MIN, THUMB_SHOW_DEFAULT = 96, 192   # スライダーで選べる一番小さい幅と、最初の幅
DETAIL_W, DETAIL_H = 240, 135      # 1本を大きく見るときの大きさ
DETAIL_POINTS = (0.1, 0.35, 0.6, 0.85)   # 動画のどこを切り出すか（長さに対する割合）
THUMB_WORKERS = 3      # サムネイルタブで見えている絵を作るとき
BG_THUMB_WORKERS = 1   # 裏で全部の絵を作るとき（画面の軽さを優先して少人数）


def find_ffmpeg():
    import shutil
    return shutil.which("ffmpeg")


def thumb_file(v, suffix=""):
    """動画の場所・更新日時・サイズから名前を決める。動画が書きかわれば別の名前になる"""
    import hashlib
    h = hashlib.md5(f"{v['key']}|{v['mtime']}|{v['size']}".encode("utf-8")).hexdigest()
    return os.path.join(THUMBS_DIR, f"{h}{suffix}.png")


def make_frame(ffmpeg, src, sec, out, w, h):
    """ffmpeg で sec 秒の位置の絵を1枚、w×h に収まる PNG にする。成功したら True"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp.png"
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    for s in ([sec, 0] if sec > 0 else [0]):   # 失敗したら頭の絵でもう一度
        try:
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-ss", f"{s:.2f}", "-i", src,
                 "-frames:v", "1", "-vf", vf, tmp],
                capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, out)
                return True
        except Exception:
            pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


class ThumbView(ttk.Frame):
    """サムネイルタブ。左に今の一覧のタイル、右に選んだ1本の大きな4枚。
    何千本あっても軽いよう、タイルは「画面に見えている分だけ」描き、絵もその分だけ作る。"""

    CACHE_MAX = 600   # 覚えておく画像の数（多すぎるとメモリを使うため）

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.set_size(app.settings.get("thumb_w", THUMB_SHOW_DEFAULT), redraw=False)
        self.size_job = None
        self.items = []
        self.index = {}          # 動画の場所 → 並び順
        self.cols = 1
        self.selected = None
        self.images = {}         # 画像ファイル → PhotoImage（作った順）
        self.failed = set()      # 絵を作れなかった動画
        self.ffmpeg = find_ffmpeg()
        self.lock = threading.Lock()
        self.wanted = []         # 今見えていて、まだ絵が無い動画
        self.busy = set()
        self.ready = []          # 絵ができた動画（画面への反映待ち）
        self.running = 0
        self.bg_todo = []        # 裏で順に作っていく、全部の動画
        self.bg_total = 0
        self.bg_gen = 0          # 読み込み直すたびに増やす
        self.done_paths = set()  # 絵ができた（または作れなかった）動画
        self.alive = True
        self.detail_job = 0

        # 左：タイル
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        # 上：大きさのスライダー
        size_row = ttk.Frame(left, padding=(6, 4))
        size_row.pack(side="top", fill="x")
        ttk.Label(size_row, text="大きさ：").pack(side="left")
        self.size_var = tk.DoubleVar(value=self.tw)
        ttk.Scale(size_row, from_=THUMB_SHOW_MIN, to=THUMB_W, length=220, variable=self.size_var,
                  command=lambda e: self.on_size_slide()).pack(side="left")
        self.size_label = ttk.Label(size_row, text=f"{self.tw}", width=5, foreground="#555")
        self.size_label.pack(side="left", padx=4)
        self.canvas = tk.Canvas(left, background=C_BG, highlightthickness=0)
        self.sb = ttk.Scrollbar(left, orient="vertical", command=self.on_scrollbar)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.layout())
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Double-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_right)

        # 右：選んだ1本
        right = ttk.Frame(self, padding=8, width=2 * DETAIL_W + 24)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self.d_name = ttk.Label(right, text="動画をクリックすると、ここに大きく出ます。",
                                wraplength=2 * DETAIL_W, font=("", 10, "bold"))
        self.d_name.pack(anchor="w")
        self.d_info = ttk.Label(right, text="", foreground="#555")
        self.d_info.pack(anchor="w", pady=(2, 6))
        grid = ttk.Frame(right)
        grid.pack(anchor="w")
        self.d_labels = []
        self.d_blank = tk.PhotoImage(width=DETAIL_W, height=DETAIL_H)
        for i in range(len(DETAIL_POINTS)):
            cell = ttk.Frame(grid)
            cell.grid(row=i // 2, column=i % 2, padx=2, pady=2)
            lb = tk.Label(cell, image=self.d_blank, background="#ddd")
            lb.pack()
            cap = ttk.Label(cell, text="", foreground="#666")
            cap.pack()
            lb.bind("<Double-1>", lambda e: self.play_selected())
            self.d_labels.append((lb, cap))
        self.d_imgs = []
        ttk.Button(right, text="▶ 再生", style="Accent.TButton", command=self.play_selected).pack(anchor="w", pady=8)
        ttk.Label(right, text="ダブルクリックでも再生します。右クリックでタグなど。",
                  foreground="#888").pack(anchor="w")

        self.after(200, self.drain)

    # ---- 大きさ ----
    def set_size(self, w, redraw=True):
        """タイルの幅を w にする（高さは 16:9 に合わせる）"""
        w = int(max(THUMB_SHOW_MIN, min(THUMB_W, w)))
        self.tw, self.th = w, round(w * 9 / 16)
        self.TILE_W, self.TILE_H = self.tw + 16, self.th + 46
        if redraw:
            self.images.clear()   # 前の大きさの絵は使えないので捨てる
            self.layout()

    def on_size_slide(self):
        """スライダーを動かしている間は数字だけ変え、手が止まったら並べ直す（動かすたびに描くと重いため）"""
        w = int(self.size_var.get())
        self.size_label.configure(text=f"{w}")
        if self.size_job:
            self.after_cancel(self.size_job)
        self.size_job = self.after(80, lambda: self.apply_size(w))

    def apply_size(self, w):
        self.size_job = None
        # 大きさが変わっても、見ていた位置（何本目あたりか）がなるべくずれないようにする
        first = self.visible_range()[0] if self.items else 0
        self.set_size(w)
        self.size_label.configure(text=f"{self.tw}")
        self.app.settings["thumb_w"] = self.tw
        if self.items:
            rows = max(1, (len(self.items) + self.cols - 1) // self.cols)
            self.canvas.yview_moveto((first // self.cols) / rows)
            self.draw()

    # ---- 並べる ----
    def set_items(self, items):
        self.items = items
        self.index = {v["path"]: i for i, v in enumerate(items)}
        self.canvas.yview_moveto(0)
        self.layout()

    def layout(self):
        w = max(self.canvas.winfo_width(), self.TILE_W)
        self.cols = max(1, w // self.TILE_W)
        rows = (len(self.items) + self.cols - 1) // self.cols
        self.canvas.configure(scrollregion=(0, 0, self.cols * self.TILE_W, max(rows * self.TILE_H, 1)))
        self.ensure_visible()
        self.draw()

    def ensure_visible(self):
        """選んだ動画が画面の外にあれば、見える位置まで動かす"""
        h = self.canvas.winfo_height()
        if not self.selected or self.selected not in self.index or h <= 1:
            return   # まだ画面の大きさが決まっていないときは、決まってから（layout から）動かす
        row = self.index[self.selected] // self.cols
        top = self.canvas.canvasy(0)
        total = max(1, ((len(self.items) + self.cols - 1) // self.cols) * self.TILE_H)
        if row * self.TILE_H < top or (row + 1) * self.TILE_H > top + h:
            self.canvas.yview_moveto(max(0, row * self.TILE_H - (h - self.TILE_H) / 2) / total)

    def visible_range(self):
        h = self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        r0 = max(0, int(top // self.TILE_H))
        r1 = int((top + h) // self.TILE_H) + 1
        return r0 * self.cols, min(len(self.items), (r1 + 1) * self.cols)

    def is_shown(self):
        return self.app.tabs.index("current") == 1

    def draw(self):
        c = self.canvas
        c.delete("tile")
        if not self.items:
            c.create_text(20, 20, anchor="nw", text="（動画がありません）", fill="#888", tags="tile")
            return
        a, b = self.visible_range()
        want = []
        for i in range(a, b):
            v = self.items[i]
            x = (i % self.cols) * self.TILE_W + 8
            y = (i // self.cols) * self.TILE_H + 6
            tw, th = self.tw, self.th
            if v["path"] == self.selected:
                c.create_rectangle(x - 5, y - 4, x + tw + 5, y + self.TILE_H - 8,
                                   fill=C_ACCENT_SOFT, outline=C_ACCENT, width=2, tags="tile")
            img = self.get_image(thumb_file(v, THUMB_SUFFIX))
            if img:
                c.create_image(x, y, image=img, anchor="nw", tags="tile")
            else:
                c.create_rectangle(x, y, x + tw, y + th, fill="#ddd", outline="", tags="tile")
                mark = ("×" if tw < 150 else "×（絵を作れません）") if v["path"] in self.failed else "…"
                c.create_text(x + tw / 2, y + th / 2, text=mark, fill="#777", tags="tile")
                if v["path"] not in self.failed:
                    want.append(v)
            if v["key"] in self.app.stars:   # 星の動画は左上に ★
                c.create_text(x + 4, y + 2, text="★", anchor="nw", fill="#f5b400",
                              font=("", 12, "bold"), tags="tile")
            dur = fmt_dur(v["dur"]) if "dur" in v else ""
            if dur:
                c.create_text(x + tw - 4, y + th - 4, text=dur, anchor="se",
                              fill="white", font=("", 9, "bold"), tags="tile")
            c.create_text(x, y + th + 4, text=v["name"], anchor="nw", width=tw,
                          font=("", 9), tags="tile")
        # タブが表示されているときだけ絵を作る（一覧タブを使っている間は何もしない＝軽い）
        if self.is_shown():
            self.request(want)

    # ---- 画像 ----
    def get_image(self, f):
        """保存した大きな絵を、今のタイルの大きさに縮めて出す。
        縮めるのは Pillow（画像を扱う部品）。tkinter だけだと整数分の1にしか縮められず、自由な大きさにできないため"""
        img = self.images.get(f)
        if img is None and os.path.exists(f):
            try:
                from PIL import Image, ImageTk
                with Image.open(f) as im:
                    if im.size != (self.tw, self.th):
                        im = im.resize((self.tw, self.th), Image.LANCZOS)
                    img = ImageTk.PhotoImage(im, master=self.canvas)
            except Exception:
                return None
            self.images[f] = img
            if len(self.images) > self.CACHE_MAX:
                for k in list(self.images)[: len(self.images) - self.CACHE_MAX]:
                    del self.images[k]
        return img

    def request(self, vids):
        """今見えている動画の絵を、ほかより先に作る"""
        with self.lock:
            self.wanted = vids[::-1]   # 後ろから取り出すので逆順
        self.start_workers()

    def start_workers(self):
        """見えている絵を作るときは THUMB_WORKERS 人、裏で作るだけのときは BG_THUMB_WORKERS 人で動く。
        同時に動く係が多いほど画面の反応が遅れるため、裏の作業は少人数にしておく"""
        if not self.ffmpeg:
            return
        with self.lock:
            target = THUMB_WORKERS if self.wanted else (BG_THUMB_WORKERS if self.bg_todo else 0)
            start = max(0, target - self.running)
            self.running += start
        for _ in range(start):
            threading.Thread(target=self.worker, daemon=True).start()

    def start_background(self, videos):
        """読み込みが終わったら、どのタブを見ていても、裏で全部の絵を順に作っていく"""
        with self.lock:
            self.bg_gen += 1
            self.bg_todo = list(videos)[::-1]
            self.bg_total = len(videos)
            self.done_paths = set()
        if not self.ffmpeg:
            self.app.thumb_status.set("サムネイル：ffmpeg が見つからないため作れません" if videos else "")
            return
        self.start_workers()

    def worker(self):
        while True:
            with self.lock:
                v = None
                while self.wanted and self.alive:
                    c = self.wanted.pop()
                    if c["path"] not in self.busy and c["path"] not in self.done_paths:
                        v = c
                        break
                # 見えている絵が終わったら、裏の作業は BG_THUMB_WORKERS 人だけが続ける
                while v is None and self.bg_todo and self.alive and self.running <= BG_THUMB_WORKERS:
                    c = self.bg_todo.pop()
                    if c["path"] not in self.busy and c["path"] not in self.done_paths:
                        v = c
                if v is None:
                    self.running -= 1
                    return
                self.busy.add(v["path"])
                gen = self.bg_gen
            out = thumb_file(v, THUMB_SUFFIX)
            ok = os.path.exists(out)   # 前に作った絵があれば、それを使う
            if not ok:
                dur = v.get("dur", -1)
                if dur < 0 and "dur" not in v:
                    # 長さがまだ分からないときは、ここで調べてから「1割の位置」の絵を作る
                    dur = probe_duration(find_ffprobe(), v["path"])
                sec = dur * 0.1 if dur > 0 else 1.0
                ok = make_frame(self.ffmpeg, v["path"], sec, out, THUMB_W, THUMB_H)
            with self.lock:
                self.busy.discard(v["path"])
                if gen == self.bg_gen:
                    self.done_paths.add(v["path"])
                self.ready.append((v, ok))

    def drain(self):
        """できた絵を、0.2 秒ごとにまとめて画面に反映し、進み具合を下に出す"""
        if not self.alive:
            return
        with self.lock:
            done, self.ready = self.ready, []
            n_done, total = len(self.done_paths), self.bg_total
        if done:
            for v, ok in done:
                if not ok:
                    self.failed.add(v["path"])
            if self.is_shown():
                self.draw()
        if self.ffmpeg and total:
            if n_done < total:
                self.app.thumb_status.set(f"サムネイルを作っています… {n_done} / {total}")
            else:
                bad = len(self.failed)
                self.app.thumb_status.set(f"サムネイル：{total} 本ぶんそろいました"
                                          + (f"（作れなかったもの {bad} 本）" if bad else ""))
        self.after(200, self.drain)

    def on_durations(self, vids):
        if self.is_shown():
            self.draw()
        if self.selected and any(v["path"] == self.selected for v in vids):
            self.show_detail(self.selected)

    def stop(self):
        self.alive = False
        with self.lock:
            self.wanted = []
            self.bg_todo = []

    # ---- スクロール ----
    def on_scrollbar(self, *args):
        self.canvas.yview(*args)
        self.draw()

    def on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")
        self.draw()

    # ---- 選ぶ・再生 ----
    def hit(self, e):
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        col, row = int(x // self.TILE_W), int(y // self.TILE_H)
        if col >= self.cols:
            return None
        i = row * self.cols + col
        return self.items[i]["path"] if 0 <= i < len(self.items) else None

    def on_click(self, e):
        self.canvas.focus_set()
        p = self.hit(e)
        if p:
            self.activate(p)

    def activate(self, path):
        if path is None or path not in self.index:
            self.draw()
            return
        self.selected = path
        self.app.select_in_list(path)
        self.ensure_visible()
        self.draw()
        self.show_detail(path)

    def on_double(self, e):
        p = self.hit(e)
        if p:
            self.app.play(p)

    def on_right(self, e):
        p = self.hit(e)
        if p:
            self.activate(p)
            self.app.show_menu(p, e.x_root, e.y_root)

    def play_selected(self):
        if self.selected:
            self.app.play(self.selected)

    # ---- 1本を大きく ----
    def show_detail(self, path):
        v = self.app.by_path.get(path)
        if not v:
            return
        self.d_name.configure(text=v["name"])
        info = [fmt_dur(v["dur"]) if "dur" in v else "長さ：調べ中", fmt_size(v["size"]),
                datetime.fromtimestamp(v["mtime"]).strftime("%Y/%m/%d %H:%M")]
        tags = self.app.tags.get(v["key"])
        if tags:
            info.append("タグ：" + "、".join(tags))
        self.d_info.configure(text="  ／  ".join(info))
        self.detail_job += 1
        job = self.detail_job
        dur = v.get("dur", -1)
        for i, (lb, cap) in enumerate(self.d_labels):
            lb.configure(image=self.d_blank)
            cap.configure(text=fmt_dur(dur * DETAIL_POINTS[i]) if dur > 0 else "")
        self.d_imgs = [None] * len(DETAIL_POINTS)
        if not self.ffmpeg:
            self.d_info.configure(text=self.d_info.cget("text") + "（ffmpeg が無いため絵を作れません）")
            return
        if dur <= 0:
            if dur < 0 and "dur" in v:
                self.d_labels[0][1].configure(text="動画として読めませんでした")
            return   # 長さが分かったら on_durations からもう一度呼ばれる

        def work():
            for i, p in enumerate(DETAIL_POINTS):
                if job != self.detail_job or not self.alive:
                    return
                out = thumb_file(v, f"_d{i}")
                ok = os.path.exists(out) or make_frame(self.ffmpeg, v["path"], dur * p, out,
                                                       DETAIL_W, DETAIL_H)
                self.after(0, lambda i=i, out=out, ok=ok: self.put_detail(job, i, out, ok))

        threading.Thread(target=work, daemon=True).start()

    def put_detail(self, job, i, out, ok):
        if job != self.detail_job:
            return
        lb, cap = self.d_labels[i]
        if ok:
            try:
                img = tk.PhotoImage(file=out)
                self.d_imgs[i] = img
                lb.configure(image=img)
                return
            except tk.TclError:
                pass
        cap.configure(text="絵を作れませんでした")


def split_name(filename):
    """ファイル名を、検索に使えそうな言葉に区切る。
    記号（_ - スペース かっこ など）で区切り、さらに「数字」と「それ以外」、
    「英字」と「日本語」の変わり目でも区切る。例：沖縄旅行2025_v2.mp4 → 沖縄旅行 / 2025 / v / 2"""
    import re
    stem = os.path.splitext(filename)[0]
    parts = re.split(r"[\s_\-.,、。・~～+#@!&=;:'\"()\[\]{}（）【】「」『』〔〕＜＞<>]+", stem)
    out = []
    for p in parts:
        for w in re.findall(r"\d+|[A-Za-zＡ-Ｚａ-ｚ]+|[^\dA-Za-zＡ-Ｚａ-ｚ]+", p):
            if w and w not in out:
                out.append(w)
    return out[:25]


def scan_folder(root, out):
    """os.scandir で中のフォルダまでたどり、動画の情報を out に入れる"""
    stack = [root]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                            st = e.stat()
                            out.append({
                                "path": e.path,
                                "name": e.name,
                                "folder": path,
                                # 検索に使うのは、登録したフォルダより下の名前だけ
                                "rel": "" if path == root else os.path.relpath(path, root),
                                "mtime": st.st_mtime,
                                "size": st.st_size,
                            })
                    except OSError:
                        pass
        except OSError:
            pass


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def norm(s):
    """検索用に、大文字・小文字と全角・半角の英数字の違いをなくす"""
    return unicodedata.normalize("NFKC", s).casefold()


class FlowFrame(ttk.Frame):
    """中の部品を横に並べ、幅が足りなくなったら次の行へ折り返す入れ物"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.bind("<Configure>", lambda e: self.relayout())

    def relayout(self):
        width = self.winfo_width()
        if width <= 1:
            # 作ったばかりで幅がまだ決まっていないときは、窓の幅から見当をつける
            # （1列に縦に並んでから広がる「ガタつき」を出さないため）
            width = max(self.winfo_toplevel().winfo_width() - 120, 200)
        x = y = row_h = 0
        for w in self.winfo_children():
            # 部品の「ほしい大きさ」は作った時点で分かるので、画面全体の計算はやり直さない
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x > 0 and x + ww > width:
                x, y = 0, y + row_h + 2
                row_h = 0
            w.place(x=x, y=y)
            x += ww + 4
            row_h = max(row_h, wh)
        self.configure(height=(y + row_h) if self.winfo_children() else 1)


class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.tags = load_tags()
        self.stars = set(self.settings.get("stars", []))   # 星を付けた動画（場所で覚える）
        self.active_tag = None
        self.videos = []
        self.by_path = {}
        self.sorted_cache = {}
        self.sort_key = self.settings.get("sort", "mtime")
        self.search_job = None
        self.scan_secs = None

        root.title(APP_NAME)
        apply_style(root)
        # いちばん上のメニュー：設定の保存・読み込み
        menubar = tk.Menu(root)
        m_set = tk.Menu(menubar, tearoff=0)
        m_set.add_command(label="設定をファイルに保存…", command=self.export_settings)
        m_set.add_command(label="設定をファイルから読み込む…", command=self.import_settings)
        menubar.add_cascade(label="設定", menu=m_set)
        root.configure(menu=menubar)
        root.geometry("1200x720")
        # 窓の左上とタスクバーのアイコン（exe のときは exe の中に入れた icon.ico を使う）
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            root.iconbitmap(default=os.path.join(base, "icon.ico"))
        except tk.TclError:
            pass
        self.current_items = []   # 今の一覧（検索・タグで絞った結果）

        # 上のボタン列
        bar = ttk.Frame(root, padding=(10, 8, 10, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="フォルダ設定", style="Sub.TButton", command=self.open_folder_dialog).pack(side="left")
        ttk.Button(bar, text="読み込み直す", style="Accent.TButton", command=self.rescan).pack(side="left", padx=6)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(bar, text="並べ替え：").pack(side="left")
        self.btn_mtime = ttk.Button(bar, text="新着順", command=lambda: self.set_sort("mtime"))
        self.btn_mtime.pack(side="left")
        self.btn_name = ttk.Button(bar, text="名前順", command=lambda: self.set_sort("name"))
        self.btn_name.pack(side="left", padx=4)
        # 星を付けた動画だけを出すボタン。もう一度押すと元に戻る
        self.star_only = False
        self.btn_star = ttk.Button(bar, text="★ 星", width=0, command=self.toggle_star_only)
        self.btn_star.pack(side="left", padx=(8, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(bar, text="再生ソフト：").pack(side="left")
        self.player_var = tk.StringVar()
        self.player_box = ttk.Combobox(bar, textvariable=self.player_var, state="readonly", width=24)
        self.player_box.pack(side="left")
        self.player_box.bind("<<ComboboxSelected>>", lambda e: self.on_player_selected())
        ttk.Button(bar, text="再生ソフト設定", style="Sub.TButton", command=self.open_player_dialog).pack(side="left", padx=4)
        self.update_player_box()

        # 検索の列
        sbar = ttk.Frame(root, padding=(10, 0, 10, 6))
        sbar.pack(fill="x")
        ttk.Label(sbar, text="検索：", style="Head.TLabel").pack(side="left")
        self.query = tk.StringVar()
        self.entry = ttk.Entry(sbar, textvariable=self.query, width=40)
        self.entry.pack(side="left")
        ttk.Button(sbar, text="×", width=3, command=self.clear_search).pack(side="left", padx=(2, 6))
        # 動画の「星」と区別するため、こちらは「検索ワードの登録」と呼ぶ
        self.fav_add_btn = ttk.Button(sbar, text="＋ 検索ワードを登録 ▾", width=0, command=self.add_favorite)
        self.fav_add_btn.pack(side="left")
        # width=0：ボタンの幅を文字の長さに合わせる（決まった幅だと長い名前が切れるため）
        self.freq_btn = ttk.Button(sbar, text="よく出る言葉（別の窓）…", width=0, command=self.open_freq_dialog)
        self.freq_btn.pack(side="left", padx=(4, 0))
        self._freq_dialog = None
        ttk.Label(sbar, text="（スペースで区切ると「全部ふくむ」で探します。Enter で履歴に残ります）",
                  foreground="#666").pack(side="left", padx=8)
        self.query.trace_add("write", lambda *a: self.schedule_search())
        self.entry.bind("<Return>", lambda e: self.commit_search())
        self.entry.bind("<Escape>", lambda e: self.clear_search())
        root.bind("<Control-f>", lambda e: (self.entry.focus_set(), self.entry.select_range(0, "end")))

        # お気に入り・履歴の列
        # お気に入りは分類（出演者・ジャンル・シリーズ・その他・自分で追加）ごとに1行ずつ
        self.fav_area = ttk.Frame(root, padding=(10, 0))
        self.fav_area.pack(fill="x")

        his_row = ttk.Frame(root, padding=(10, 2, 10, 4))
        his_row.pack(fill="x")
        ttk.Label(his_row, text="履歴：", width=10, style="Head.TLabel").pack(side="left", anchor="n")
        self.his_frame = FlowFrame(his_row)
        self.his_frame.pack(side="left", fill="x", expand=True)

        tag_row = ttk.Frame(root, padding=(10, 0, 10, 4))
        tag_row.pack(fill="x")
        ttk.Label(tag_row, text="タグ：", width=10, style="Head.TLabel").pack(side="left", anchor="n")
        self.tag_frame = FlowFrame(tag_row)
        self.tag_frame.pack(side="left", fill="x", expand=True)

        # 選んだ動画の名前。マウスでなぞった部分をそのまま検索できる
        name_row = ttk.Frame(root, padding=(10, 0, 10, 8))
        name_row.pack(fill="x")
        ttk.Label(name_row, text="選んだ名前：", width=10, style="Head.TLabel").pack(side="left")
        self.name_entry = ttk.Entry(name_row, state="readonly")
        self.name_entry.pack(side="left", fill="x", expand=True)
        self.name_entry.bind("<ButtonRelease-1>", self.remember_name_sel)
        self.name_entry.bind("<Button-3>", self.on_name_right_click)
        self.name_sel = None
        ttk.Button(name_row, text="なぞった部分で検索", command=self.search_name_sel).pack(side="left", padx=(4, 0))

        # タブ（一覧／サムネイル）。サムネイルは重いので、別のタブに分けておく
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=10)
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text="　一覧　")
        self.thumb_view = ThumbView(self.tabs, self)
        self.tabs.add(self.thumb_view, text="　サムネイル　")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.on_tab_changed())
        cols = ("play", "star", "name", "dur", "tags", "mtime", "size", "folder")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        self.tree.tag_configure("odd", background=C_STRIPE)
        heads = {"play": "", "star": "", "name": "名前", "dur": "長さ", "tags": "タグ", "mtime": "更新日",
                 "size": "サイズ", "folder": "フォルダ"}
        widths = {"play": 34, "star": 30, "name": 320, "dur": 70, "tags": 140, "mtime": 130, "size": 80,
                  "folder": 260}
        widths.update(self.settings.get("col_widths", {}))
        for c in cols:
            self.tree.heading(c, text=heads[c])
            # エクスプローラーと同じく、幅は自分で決めた数字のまま（窓の大きさで勝手に変わらない）
            anchor = "center" if c in FIXED_COLUMNS else "e" if c in ("size", "dur") else "w"
            self.tree.column(c, width=int(widths[c]), anchor=anchor, stretch=False)
        # 列の並び（初期は 名前・タグ・長さ…）。並べ替えた順は保存して次回も使う
        self.tree.configure(displaycolumns=self.column_order())
        self.col_drag = None
        self.tree.heading("name", command=lambda: self.set_sort("name"))
        self.tree.heading("mtime", command=lambda: self.set_sort("mtime"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<ButtonPress-1>", self.on_col_press, add="+")
        self.tree.bind("<B1-Motion>", self.on_col_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self.on_col_release, add="+")
        self.tree.bind("<Return>", lambda e: self.play_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_name(
            (self.tree.selection() or [None])[0]))
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("t", lambda e: self.open_tag_dialog())
        self.tree.bind("s", lambda e: self.toggle_star(self.tree.selection()))

        # 下の状態表示
        sbar2 = ttk.Frame(root, style="Status.TFrame")
        sbar2.pack(fill="x")
        self.status = tk.StringVar(value="")
        ttk.Label(sbar2, textvariable=self.status, padding=(10, 5), style="Status.TLabel").pack(side="left")
        self.dur_status = tk.StringVar(value="")
        ttk.Label(sbar2, textvariable=self.dur_status, padding=(10, 5), style="StatusSub.TLabel").pack(side="right")
        self.thumb_status = tk.StringVar(value="")
        ttk.Label(sbar2, textvariable=self.thumb_status, padding=(10, 5), style="StatusSub.TLabel").pack(side="right")

        # 動画の長さ（裏で少しずつ調べる）
        self.durations = load_durations()
        self.ffprobe = find_ffprobe()
        self.dur_gen = 0          # 読み込み直すたびに増やし、古い調べものを止める
        self.dur_lock = threading.Lock()
        self.dur_todo = []
        self.dur_front = []       # 今見えている動画を先に調べるための列
        self.dur_results = []     # 調べ終わったもの（画面への反映待ち）
        self.dur_total = self.dur_done = 0
        self.dur_unsaved = 0
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.draw_history()
        self.draw_tags()   # お気に入りも一緒に描く
        root.after(50, self.startup)

    # ---- 読み込み ----
    def startup(self):
        if not self.settings["folders"]:
            self.status.set("「フォルダ設定」から、動画の入ったフォルダを登録してください。")
            return
        self.rescan()

    def rescan(self):
        folders = list(self.settings["folders"])
        if not folders:
            self.on_scanned([], 0)
            self.status.set("フォルダが登録されていません。")
            return
        self.status.set("読み込み中…")
        t0 = time.perf_counter()

        def work():
            out = []
            for f in folders:
                scan_folder(f, out)
            # 重なって登録されたフォルダから、同じ動画が二重に入らないようにする
            seen, uniq = set(), []
            for v in out:
                k = os.path.normcase(v["path"])
                if k not in seen:
                    seen.add(k)
                    v["key"] = k
                    v["base"] = norm(v["name"] + "\n" + v["rel"])
                    uniq.append(v)
            self.root.after(0, lambda: self.on_scanned(uniq, time.perf_counter() - t0))

        threading.Thread(target=work, daemon=True).start()

    def on_scanned(self, videos, secs):
        self.videos = videos
        self.by_path = {v["path"]: v for v in videos}
        for v in videos:
            self.update_search_text(v)
        # 保存してある長さは、一覧を作る前に入れておく（ファイルが変わっていないものだけ）
        for v in videos:
            c = self.durations.get(v["key"])
            if c and c[0] == v["mtime"] and c[1] == v["size"]:
                v["dur"] = c[2]
        self.sorted_cache = {}
        self.scan_secs = secs
        self.draw_tags()
        self.refresh()
        self.start_durations()
        # サムネイルも裏で全部作っていく（今の並び順の上から）
        self.thumb_view.start_background(self.sorted_videos())

    # ---- 動画の長さ ----
    def start_durations(self):
        """長さが保存されていないもの（や、ファイルが変わったもの）だけ裏で調べる"""
        todo = [v for v in self.sorted_videos() if "dur" not in v]

        with self.dur_lock:
            self.dur_gen += 1
            gen = self.dur_gen
            self.dur_todo = todo[::-1]   # 後ろから取り出すので逆順にしておく
            self.dur_front = []
            self.dur_total, self.dur_done = len(todo), 0
        if not todo:
            self.dur_status.set("")
            return
        if not self.ffprobe:
            self.dur_status.set("長さ：ffprobe が見つからないため調べられません")
            return
        self.prioritize_visible()
        for _ in range(DUR_WORKERS):
            threading.Thread(target=self.dur_worker, args=(gen,), daemon=True).start()
        self.root.after(300, lambda: self.drain_durations(gen))

    def prioritize_visible(self):
        """検索などで見えている動画が変わったら、そちらを先に調べる"""
        rows = self.tree.get_children()[:300]
        front = [self.by_path[p] for p in reversed(rows)
                 if p in self.by_path and "dur" not in self.by_path[p]]
        with self.dur_lock:
            self.dur_front = front

    def dur_worker(self, gen):
        while True:
            with self.dur_lock:
                if gen != self.dur_gen:
                    return
                v = None
                while self.dur_front:
                    c = self.dur_front.pop()
                    if "dur" not in c and not c.get("dur_busy"):
                        v = c
                        break
                while v is None and self.dur_todo:
                    c = self.dur_todo.pop()
                    if "dur" not in c and not c.get("dur_busy"):
                        v = c
                if v is None:
                    return
                v["dur_busy"] = True
            d = probe_duration(self.ffprobe, v["path"])
            with self.dur_lock:
                v["dur"] = d
                v.pop("dur_busy", None)
                if gen == self.dur_gen:
                    self.dur_results.append(v)

    def drain_durations(self, gen):
        """調べ終わった長さを、まとめて画面に反映する（0.3 秒ごと）"""
        if gen != self.dur_gen:
            return
        with self.dur_lock:
            done, self.dur_results = self.dur_results, []
        for v in done:
            self.durations[v["key"]] = [v["mtime"], v["size"], v["dur"]]
            if self.tree.exists(v["path"]):
                self.tree.set(v["path"], "dur", fmt_dur(v["dur"]))
        if done:
            self.thumb_view.on_durations(done)
        self.dur_done += len(done)
        self.dur_unsaved += len(done)
        if self.dur_unsaved >= 200:
            self.save_durations()
        if self.dur_done < self.dur_total:
            self.dur_status.set(f"長さを調べています… {self.dur_done} / {self.dur_total}")
            self.root.after(300, lambda: self.drain_durations(gen))
        else:
            self.save_durations()
            self.dur_status.set(f"長さ：{self.dur_total} 本を調べ終わりました")

    def save_durations(self):
        if self.dur_unsaved:
            save_durations(self.durations)
            self.dur_unsaved = 0

    # ---- 列の入れ替え・幅の自動合わせ ----
    def display_columns(self):
        d = self.tree.cget("displaycolumns")
        if isinstance(d, str):
            d = d.split()
        return list(self.tree.cget("columns")) if not d or d[0] == "#all" else list(d)

    def col_at(self, x):
        """x の位置にある列の名前"""
        cid = self.tree.identify_column(x)   # "#1" のような、表示順の番号
        try:
            return self.display_columns()[int(cid[1:]) - 1]
        except (ValueError, IndexError):
            return None

    def separator_col(self, x):
        """列の境目 x の左側にある列（幅を変える対象）"""
        edge, best, bestd = 0, None, 99
        for c in self.display_columns():
            edge += self.tree.column(c, "width")
            if abs(edge - x) < bestd:
                best, bestd = c, abs(edge - x)
        return best if bestd <= 6 else None

    # ---- 設定の保存・読み込み ----
    def export_settings(self):
        """お気に入り・履歴・フォルダ・再生ソフト・列・タグを、1つのファイルにまとめて保存する"""
        self.save_col_widths()
        path = filedialog.asksaveasfilename(
            title="設定を保存", defaultextension=".json", initialfile="ハヤミルの設定.json",
            filetypes=[("設定ファイル", "*.json")])
        if not path:
            return
        data = {"app": APP_NAME, "version": 1, "saved": datetime.now().strftime("%Y/%m/%d %H:%M"),
                "settings": self.settings, "tags": self.tags}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"保存できませんでした。\n{e}")
            return
        self.status.set(f"設定を保存しました：{path}")

    def import_settings(self):
        path = filedialog.askopenfilename(title="設定を読み込む", filetypes=[("設定ファイル", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("app") not in (APP_NAME,) + OLD_APP_NAMES or not isinstance(data.get("settings"), dict):
                raise ValueError("ハヤミルの設定ファイルではありません。")
        except (OSError, ValueError, json.JSONDecodeError, AttributeError) as e:
            messagebox.showerror(APP_NAME, f"読み込めませんでした。\n{e}")
            return
        s = normalize_favorites(dict(data["settings"]))
        n_grp = len(s["fav_groups"])
        n_fav = sum(len(g.get("words", [])) for g in s["fav_groups"])
        msg = (f"今の設定を、このファイルの内容に置きかえます。\n"
               f"（保存した日時：{data.get('saved', '不明')}）\n\n"
               f"・お気に入り {n_fav} 個（分類 {n_grp} 個）、履歴 {len(s.get('history', []))} 個\n"
               f"・フォルダ {len(s.get('folders', []))} 個、再生ソフト {len(s.get('players', []))} 個\n"
               f"・タグ付きの動画 {len(data.get('tags', {}))} 本、星の動画 {len(s.get('stars', []))} 本、列の並びと幅\n\n"
               f"今の設定は消えます。先に「設定をファイルに保存」しておくと元に戻せます。\n置きかえますか？")
        if not messagebox.askyesno(APP_NAME, msg):
            return
        base = {"folders": [], "sort": "mtime", "history": [], "favorites": [], "players": [], "player": ""}
        base.update(s)
        self.settings = normalize_favorites(base)
        self.stars = set(self.settings.get("stars", []))
        self.star_only = False
        self.tags = data.get("tags", {}) if isinstance(data.get("tags"), dict) else {}
        save_settings(self.settings)
        save_tags(self.tags)
        self.apply_settings_to_screen()
        self.status.set(f"設定を読み込みました：{path}")

    def apply_settings_to_screen(self):
        """読み込んだ設定を、画面にすべて反映する"""
        self.sort_key = self.settings.get("sort", "mtime")
        self.active_tag = None
        self.update_player_box()
        self.draw_favorites()
        self.draw_history()
        cols = self.tree.cget("columns")
        for c, w in self.settings.get("col_widths", {}).items():
            if c in cols:
                self.tree.column(c, width=int(w))
        self.tree.configure(displaycolumns=self.column_order())
        self.rescan()   # フォルダが変わっているかもしれないので読み込み直す（タグも反映される）

    def column_order(self):
        """列の並び。再生ボタンの列は、いつも左端に置く"""
        cols = self.tree.cget("columns")
        order = [c for c in self.settings.get("columns", []) if c in cols and c not in FIXED_COLUMNS]
        order += [c for c in DEFAULT_COLUMNS if c not in order]
        return list(FIXED_COLUMNS) + order

    def on_col_press(self, e):
        self.col_drag = None
        region = self.tree.identify_region(e.x, e.y)
        if region == "heading" and self.col_at(e.x) not in FIXED_COLUMNS:
            self.col_drag = {"col": self.col_at(e.x), "x": e.x, "moved": False}
        elif region == "cell" and self.col_at(e.x) == "star":
            # ☆ をクリックすると星が付き、★ をクリックすると外れる
            row = self.tree.identify_row(e.y)
            if row:
                self.toggle_star([row])
            return "break"
        elif region == "cell" and self.col_at(e.x) == "play":
            # 左端の ▶ はワンクリックで再生
            row = self.tree.identify_row(e.y)
            if row:
                self.tree.selection_set(row)
                self.tree.focus(row)
                self.play(row)
            return "break"

    def on_col_motion(self, e):
        d = self.col_drag
        if d and abs(e.x - d["x"]) > 8:
            d["moved"] = True
            self.tree.configure(cursor="exchange")
            return "break"

    def on_col_release(self, e):
        d, self.col_drag = self.col_drag, None
        if not d or not d["moved"]:
            return
        self.tree.configure(cursor="")
        target = self.col_at(e.x)
        if target in FIXED_COLUMNS:   # 再生ボタン・星の列より左には入れない
            target = self.display_columns()[len(FIXED_COLUMNS)]
        if target and target != d["col"]:
            self.move_column(d["col"], target)
        return "break"   # 並べ替え（名前順など）のボタンとしては扱わない

    def move_column(self, col, target):
        """col を target の位置へ動かす"""
        order = self.display_columns()
        to = order.index(target)   # 右へ動かすときは target の右、左へ動かすときは左に入る
        order.remove(col)
        order.insert(to, col)
        self.tree.configure(displaycolumns=order)
        self.settings["columns"] = [c for c in order if c not in FIXED_COLUMNS]
        save_settings(self.settings)

    def autofit_column(self, col):
        """エクスプローラーと同じく、中身の一番長い文字に幅を合わせる"""
        import tkinter.font as tkfont
        body = tkfont.nametofont("TkDefaultFont")
        head = tkfont.nametofont("TkHeadingFont")
        w = head.measure(self.tree.heading(col, "text")) + 24
        texts = {self.tree.set(i, col) for i in self.tree.get_children()}
        for t in texts:
            w = max(w, body.measure(t) + 16)
        self.tree.column(col, width=min(w, 1200))
        self.save_col_widths()

    def save_col_widths(self):
        self.settings["col_widths"] = {c: self.tree.column(c, "width") for c in self.tree.cget("columns")}
        save_settings(self.settings)

    def on_close(self):
        self.save_col_widths()
        self.thumb_view.stop()
        with self.dur_lock:
            self.dur_gen += 1   # 裏の調べものを止める
            done, self.dur_results = self.dur_results, []
        for v in done:
            self.durations[v["key"]] = [v["mtime"], v["size"], v["dur"]]
            self.dur_unsaved += 1
        self.save_durations()
        self.root.destroy()

    def update_search_text(self, v):
        """検索の対象＝名前＋登録フォルダより下のフォルダ名＋タグ"""
        tags = self.tags.get(v["key"], [])
        v["search"] = v["base"] + ("\n" + norm(" ".join(tags)) if tags else "")

    # ---- 並べ替え・検索・表示 ----
    def set_sort(self, key):
        self.sort_key = key
        self.settings["sort"] = key
        save_settings(self.settings)
        self.refresh()

    def sorted_videos(self):
        """並べ替えた結果は覚えておき、検索のたびに並べ替え直さない"""
        if self.sort_key not in self.sorted_cache:
            if self.sort_key == "name":
                items = sorted(self.videos, key=lambda v: norm(v["name"]))
            else:
                items = sorted(self.videos, key=lambda v: v["mtime"], reverse=True)
            self.sorted_cache[self.sort_key] = items
        return self.sorted_cache[self.sort_key]

    def schedule_search(self):
        """文字を打つたびに探すと重いので、打つ手が 0.15 秒止まったら探す"""
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(150, self.refresh)

    def refresh(self):
        self.search_job = None
        t0 = time.perf_counter()
        items = self.sorted_videos()
        words = norm(self.query.get()).split()
        if words:
            items = [v for v in items if all(w in v["search"] for w in words)]
        tag = self.active_tag
        if tag:
            tags = self.tags
            items = [v for v in items if tag in tags.get(v["key"], ())]
        if self.star_only:
            stars = self.stars
            items = [v for v in items if v["key"] in stars]

        self.current_items = items
        self.tree.delete(*self.tree.get_children())
        ins = self.tree.insert
        rv = self.row_values
        # 1行おきに薄い色を付けて、横の行を目で追いやすくする
        for i, v in enumerate(items):
            ins("", "end", iid=v["path"], values=rv(v), tags=("odd",) if i % 2 else ())
        secs = time.perf_counter() - t0
        self.thumb_view.set_items(items)

        self.btn_mtime.state(["pressed"] if self.sort_key == "mtime" else ["!pressed"])
        self.btn_name.state(["pressed"] if self.sort_key == "name" else ["!pressed"])
        self.tree.heading("mtime", text="更新日 ▼" if self.sort_key == "mtime" else "更新日")
        self.tree.heading("name", text="名前 ▲" if self.sort_key == "name" else "名前")

        if words or tag or self.star_only:
            msg = f"{len(items)} 本（全 {len(self.videos)} 本中）  ／  表示 {secs:.2f} 秒"
        else:
            msg = f"{len(items)} 本  ／  表示 {secs:.2f} 秒"
        if self.scan_secs is not None:
            msg += f"  ／  読み込み {self.scan_secs:.2f} 秒"
        self.status.set(msg)
        if self.dur_done < self.dur_total:
            self.prioritize_visible()

    def on_tab_changed(self):
        if self.tabs.index("current") == 1:
            # 一覧で選んでいた動画を、サムネイルのほうでも選んだ状態にする
            sel = self.tree.selection()
            self.thumb_view.activate(sel[0] if sel else None)

    def select_in_list(self, path):
        if self.tree.exists(path):
            self.tree.selection_set(path)
            self.tree.see(path)

    # ---- 星（特にお気に入りの動画） ----
    def is_starred(self, path):
        v = self.by_path.get(path)
        return bool(v) and v["key"] in self.stars

    def toggle_star(self, paths):
        """星を付け外しする。何本か選んでいるとき、1本でも星が無ければ全部に付け、全部にあれば全部外す"""
        vids = [self.by_path[p] for p in paths if p in self.by_path]
        if not vids:
            return
        add = not all(v["key"] in self.stars for v in vids)
        for v in vids:
            (self.stars.add if add else self.stars.discard)(v["key"])
        self.settings["stars"] = sorted(self.stars)
        save_settings(self.settings)
        self.update_star_button()
        if self.star_only and not add:
            self.refresh()   # 星だけの一覧から外れた動画を消す
        else:
            for v in vids:
                if self.tree.exists(v["path"]):
                    self.tree.item(v["path"], values=self.row_values(v))
            self.thumb_view.draw()

    def toggle_star_only(self):
        self.star_only = not self.star_only
        self.update_star_button()
        self.refresh()

    def update_star_button(self):
        n = sum(1 for v in self.videos if v["key"] in self.stars)
        self.btn_star.configure(text=f"★ 星 ({n})")
        self.btn_star.state(["pressed"] if self.star_only else ["!pressed"])

    def row_values(self, v):
        return (
            "▶",
            "★" if v["key"] in self.stars else "☆",
            v["name"],
            fmt_dur(v["dur"]) if "dur" in v else "…",
            "、".join(self.tags.get(v["key"], ())),
            datetime.fromtimestamp(v["mtime"]).strftime("%Y/%m/%d %H:%M"),
            fmt_size(v["size"]),
            v["folder"],
        )

    def clear_search(self):
        self.query.set("")
        self.entry.focus_set()

    # ---- タグ ----
    def all_tags(self):
        """今の一覧にある動画のタグと、その本数"""
        count = {}
        for v in self.videos:
            for t in self.tags.get(v["key"], ()):
                count[t] = count.get(t, 0) + 1
        return dict(sorted(count.items(), key=lambda kv: norm(kv[0])))

    def draw_tags(self):
        self.draw_favorites()   # 動画やタグが変わると、お気に入りの本数も変わるため一緒に描き直す
        self.update_star_button()
        for w in self.tag_frame.winfo_children():
            w.destroy()
        tags = self.all_tags()
        if self.active_tag and self.active_tag not in tags:
            self.active_tag = None
        for t, n in tags.items():
            b = ttk.Button(self.tag_frame, text=f"# {t} ({n})", command=lambda t=t: self.toggle_tag(t))
            if t == self.active_tag:
                b.state(["pressed"])
            b.bind("<Button-3>", lambda e, t=t: self.delete_tag_everywhere(t))
        if not tags:
            ttk.Label(self.tag_frame, foreground="#888",
                      text="（動画を選んで右クリック →「タグを付ける」か、T キー。タグを丸ごと消すときはボタンを右クリック）")
        self.tag_frame.relayout()

    def toggle_tag(self, tag):
        """押すとそのタグの動画だけ。もう一度押すと解除"""
        self.active_tag = None if self.active_tag == tag else tag
        self.draw_tags()
        self.refresh()

    def set_tags(self, v, new_tags):
        if new_tags:
            self.tags[v["key"]] = new_tags
        else:
            self.tags.pop(v["key"], None)
        self.update_search_text(v)

    def after_tag_change(self, paths):
        save_tags(self.tags)
        self.draw_tags()
        if self.query.get().strip() or self.active_tag:
            # 絞り込み中は、条件に合うかが変わるので作り直す（選択と位置は保つ）
            pos = self.tree.yview()[0]
            self.refresh()
            keep = [p for p in paths if self.tree.exists(p)]
            if keep:
                self.tree.selection_set(keep)
            self.tree.yview_moveto(pos)
        else:
            for p in paths:
                if self.tree.exists(p):
                    self.tree.item(p, values=self.row_values(self.by_path[p]))

    def delete_tag_everywhere(self, tag):
        n = sum(1 for tags in self.tags.values() if tag in tags)
        if not messagebox.askyesno(APP_NAME, f"タグ「{tag}」を、付いている {n} 本の動画すべてから外しますか？"):
            return
        for k in list(self.tags):
            if tag in self.tags[k]:
                rest = [t for t in self.tags[k] if t != tag]
                if rest:
                    self.tags[k] = rest
                else:
                    del self.tags[k]
        for v in self.videos:
            self.update_search_text(v)
        if self.active_tag == tag:
            self.active_tag = None
        save_tags(self.tags)
        self.draw_tags()
        self.refresh()

    def open_tag_dialog(self):
        paths = list(self.tree.selection())
        if not paths:
            messagebox.showinfo(APP_NAME, "タグを付ける動画を、一覧で選んでください（複数選べます）。")
            return
        vids = [self.by_path[p] for p in paths]

        win = tk.Toplevel(self.root)
        win.title("タグを付ける")
        win.transient(self.root)
        win.grab_set()
        WIN_W = 420
        LIST_MAX_H = 360   # タグが多いときは、この高さで止めてスクロールにする

        title = vids[0]["name"] if len(vids) == 1 else f"{len(vids)} 本の動画"
        ttk.Label(win, text=f"{title} のタグ", padding=8, wraplength=WIN_W - 16).pack(anchor="w")

        # 既にあるタグをチェックボックスで並べる。窓の高さはタグの数に合わせ、余白を作らない
        outer = ttk.Frame(win, padding=(8, 0))
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, height=1, background=C_BG)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        box = ttk.Frame(canvas)
        canvas.create_window(0, 0, window=box, anchor="nw")

        def fit(event=None):
            win.update_idletasks()
            h = box.winfo_reqheight()
            canvas.configure(height=min(h, LIST_MAX_H), scrollregion=(0, 0, box.winfo_reqwidth(), h))
            if h > LIST_MAX_H:
                bar.pack(side="right", fill="y")
            else:
                bar.pack_forget()
                canvas.yview_moveto(0)
            win.update_idletasks()   # 高さを変えたあとの大きさで、窓を合わせる
            win.geometry(f"{WIN_W}x{win.winfo_reqheight()}")

        def wheel(event):
            if box.winfo_reqheight() > LIST_MAX_H:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        win.bind("<MouseWheel>", wheel)
        states = {}  # タグ → "all" / "some" / "none"

        def add_check(tag, state):
            # Windows の見た目では「－」（一部だけ）の印が出ないため、文字で知らせる
            var = tk.StringVar(value="1" if state == "all" else "0")
            label = f"{tag}　（選んだうちの一部にだけ付いています）" if state == "some" else tag
            cb = ttk.Checkbutton(box, text=label, variable=var, onvalue="1", offvalue="0")

            def changed(tag=tag, var=var, cb=cb):
                cb.configure(text=tag)
                states[tag] = "all" if var.get() == "1" else "none"
            cb.configure(command=changed)
            cb.pack(anchor="w")
            states[tag] = state

        known = set(self.all_tags()) | {t for ts in self.tags.values() for t in ts}
        for tag in sorted(known, key=norm):
            have = sum(1 for v in vids if tag in self.tags.get(v["key"], ()))
            add_check(tag, "all" if have == len(vids) else "some" if have else "none")

        new_row = ttk.Frame(win, padding=8)
        new_row.pack(fill="x")
        ttk.Label(new_row, text="新しいタグ：").pack(side="left")
        new_var = tk.StringVar()
        new_entry = ttk.Entry(new_row, textvariable=new_var)
        new_entry.pack(side="left", fill="x", expand=True)

        def add_new(event=None):
            t = new_var.get().strip().replace("、", " ")
            for one in t.split():
                if one not in states:
                    add_check(one, "all")
                else:
                    states[one] = "all"
            new_var.set("")
            fit()
            return "break"

        new_entry.bind("<Return>", add_new)
        ttk.Button(new_row, text="追加", command=add_new).pack(side="left", padx=4)
        new_entry.focus_set()

        def ok():
            add_new()
            for v in vids:
                cur = list(self.tags.get(v["key"], ()))
                for tag, st in states.items():
                    if st == "all" and tag not in cur:
                        cur.append(tag)
                    elif st == "none" and tag in cur:
                        cur.remove(tag)
                self.set_tags(v, cur)
            win.destroy()
            self.after_tag_change(paths)

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="OK", command=ok).pack(side="right")
        ttk.Button(btns, text="やめる", command=win.destroy).pack(side="right", padx=6)
        win.bind("<Escape>", lambda e: win.destroy())
        fit()
        # 動作確認のとき、外から画面を操作できるように残しておく
        self._tag_dialog = {"win": win, "states": states, "new": new_var, "add": add_new, "ok": ok}

    def run_word(self, word):
        """お気に入り・履歴のボタンを押したとき"""
        self.query.set(word)
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.refresh()

    def search_word(self, word):
        """マウスで選んだ言葉で検索し、履歴にも残す"""
        self.run_word(word)
        self.commit_search()

    # ---- 名前の欄（なぞって検索） ----
    def show_name(self, path):
        if not path:
            return   # 検索で一覧が作り直されて選択が外れても、名前は残しておく（続けて別の部分を選べるように）
        stem = os.path.splitext(os.path.basename(path))[0]
        self.name_entry.configure(state="normal")
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, stem)
        self.name_entry.configure(state="readonly")
        self.name_sel = None

    def remember_name_sel(self, event=None):
        """なぞった範囲を覚えておく（ボタンを押したときに選択が消えても使えるように）"""
        e = self.name_entry
        if e.selection_present():
            self.name_sel = e.get()[e.index("sel.first"):e.index("sel.last")]

    def search_name_sel(self):
        self.remember_name_sel()
        word = (self.name_sel or "").strip()
        if not word:
            self.status.set("名前の欄で、検索したい部分をマウスでなぞってから押してください。")
            return
        self.search_word(word)

    def on_name_right_click(self, event):
        self.remember_name_sel()
        menu = tk.Menu(self.root, tearoff=0)
        word = (self.name_sel or "").strip()
        menu.add_command(label=f"「{word}」で検索" if word else "（先に、検索したい部分をなぞってください）",
                         command=self.search_name_sel, state="normal" if word else "disabled")
        menu.tk_popup(event.x_root, event.y_root)

    # ---- 履歴 ----
    def commit_search(self):
        word = self.query.get().strip()
        if not word:
            return
        h = [w for w in self.settings["history"] if w != word]
        h.insert(0, word)
        self.settings["history"] = h[:HISTORY_MAX]
        save_settings(self.settings)
        self.draw_history()

    def remove_history(self, word):
        self.settings["history"] = [w for w in self.settings["history"] if w != word]
        save_settings(self.settings)
        self.draw_history()

    def draw_history(self):
        for w in self.his_frame.winfo_children():
            w.destroy()
        for word in self.settings["history"]:
            chip = ttk.Frame(self.his_frame)
            ttk.Button(chip, text=word, command=lambda w=word: self.run_word(w)).pack(side="left")
            ttk.Button(chip, text="×", width=2,
                       command=lambda w=word: self.remove_history(w)).pack(side="left")
        if not self.settings["history"]:
            ttk.Label(self.his_frame, text="（まだありません）", foreground="#888")
        self.his_frame.relayout()

    # ---- お気に入り（分類ごと） ----
    def groups(self):
        return self.settings["fav_groups"]

    def group(self, name):
        for g in self.groups():
            if g["name"] == name:
                return g
        return None

    def add_favorite(self):
        """☆ ボタン：どの分類に入れるかをメニューで選ぶ"""
        word = self.query.get().strip()
        if not word:
            messagebox.showinfo(APP_NAME, "検索欄に言葉を入れてから押してください。")
            return
        menu = tk.Menu(self.root, tearoff=0)
        for g in self.groups():
            have = word in g["words"]
            menu.add_command(label=f"{g['name']} に入れる" + ("（入っています）" if have else ""),
                             state="disabled" if have else "normal",
                             command=lambda n=g["name"]: self.add_favorite_to(n, word))
        menu.add_separator()
        menu.add_command(label="新しい分類を作って入れる…", command=lambda: self.add_favorite_to(None, word))
        b = self.fav_add_btn
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def add_favorite_to(self, group_name, word):
        if group_name is None:
            group_name = self.new_group()
            if not group_name:
                return
        g = self.group(group_name)
        if word not in g["words"]:
            g["words"].append(word)
        save_settings(self.settings)
        self.draw_favorites()

    # ---- よく出る言葉（ファイル名から抜き出して、分類に入れる） ----
    def frequent_words(self, min_count):
        """全部の動画のファイル名を言葉に区切り、何本の動画に出てくるかを数える。
        数字だけ・1文字だけ・登録済み・無視した言葉は除く。多い順に [(言葉, 本数), …] を返す"""
        registered = [norm(w) for g in self.groups() for w in g["words"]]
        skip = set(registered) | {norm(w) for w in self.settings.get("ignored_words", [])}
        count, shown = {}, {}
        for v in self.videos:
            seen = set()
            for w in split_name(v["name"]):
                k = norm(w)
                if len(k) < 2 or k.isdigit() or k in skip or k in seen:
                    continue
                if k not in count and any(k in r for r in registered):
                    # 登録済みの言葉の一部（例：「Blu-ray」を登録済みなら「Blu」「ray」）も出さない
                    skip.add(k)
                    continue
                seen.add(k)
                count[k] = count.get(k, 0) + 1
                shown.setdefault(k, w)   # 表示には、最初に見つけた書き方を使う
        out = [(shown[k], n) for k, n in count.items() if n >= min_count]
        out.sort(key=lambda kv: (-kv[1], norm(kv[0])))
        return out

    def open_freq_dialog(self):
        """何回押しても窓は1つだけ。もう開いていれば、その窓を前に出す"""
        d = self._freq_dialog
        if d and d["win"].winfo_exists():
            d["win"].deiconify()
            d["win"].lift()
            d["win"].focus_force()
            return
        if not self.videos:
            messagebox.showinfo(APP_NAME, "動画を読み込んでから押してください。")
            return
        # 数えて並べるのに少し時間がかかるので、先に知らせてから始める
        self.freq_btn.state(["disabled"])
        self.status.set("よく出る言葉を数えています…（別の窓で開きます。少しお待ちください）")
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        self.root.after(30, self._build_freq_dialog)

    def _build_freq_dialog(self):
        try:
            self._make_freq_dialog()
        finally:
            self.freq_btn.state(["!disabled"])
            self.root.configure(cursor="")
            self.status.set("よく出る言葉の窓を開きました")

    def _make_freq_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("よく出る言葉")
        win.geometry("640x560")
        win.transient(self.root)
        SHOW_MAX = 150   # 一度に並べる数（多すぎると窓が重くなるため）

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="ファイル名に").pack(side="left")
        # 本数はボタンで選ぶ（上下の矢印だと1つ動かすたびに数え直しが入り、待たされるため）
        min_var = tk.IntVar(value=min(5, max(1, int(self.settings.get("freq_min", 2)))))
        min_btns = {}
        for k in range(1, 6):
            b = ttk.Button(top, text=str(k), width=3, command=lambda k=k: choose(k))
            b.pack(side="left", padx=1)
            min_btns[k] = b
        ttk.Label(top, text="本以上出てくる言葉").pack(side="left", padx=(4, 0))

        def choose(k):
            if k != min_var.get():
                min_var.set(k)
                fill()
        info = ttk.Label(top, text="", foreground="#666")
        info.pack(side="left", padx=10)
        ttk.Button(top, text="無視した言葉を戻す", command=lambda: unignore()).pack(side="right")
        ttk.Label(win, padding=(8, 0, 8, 4), foreground="#666",
                  text="分類のボタンを押すと、その分類の検索ワードに入ります。言葉を押すと、その言葉で検索します。").pack(anchor="w")

        outer = ttk.Frame(win, padding=(8, 0, 8, 8))
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, background=C_BG)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        # 一覧は Canvas に「絵」として描く。
        # 行ごとにボタン部品を作ると 150 行で 1,000 個を超え、Windows では作るだけで数秒かかるため。
        # 絵のボタンなら、何百行でも一瞬で描ける
        import tkinter.font as tkfont
        font = tkfont.nametofont("TkDefaultFont")
        ROW_H = font.metrics("linespace") + 12
        WORD_MAX = 600   # 言葉の欄の一番広い幅（これより長い言葉は末尾を「…」にして、全体は検索で確かめる）
        rows = []        # 今並んでいる [言葉, 本数]

        def fit_text(text, width):
            if font.measure(text) <= width:
                return text
            while text and font.measure(text + "…") > width:
                text = text[:-1]
            return text + "…"

        def draw():
            canvas.delete("all")
            groups = [g["name"] for g in self.groups()]
            if not rows:
                canvas.create_text(10, 12, anchor="nw", fill="#888",
                                   text="（候補がありません。本数を減らすと増えます）")
                canvas.configure(scrollregion=(0, 0, 1, 1))
                return
            word_w = min(WORD_MAX, max(font.measure(w) for w, _ in rows)) + 14
            cnt_w = font.measure("9999 本") + 12
            labels = groups + ["無視"]
            btn_w = [font.measure(t) + 16 for t in labels]
            x_btn = 6 + word_w + cnt_w
            for i, (word, n) in enumerate(rows):
                y = i * ROW_H
                mid = y + ROW_H / 2
                tag = f"w{i}"
                canvas.create_text(6, mid, anchor="w", text=fit_text(word, word_w - 14),
                                   fill=C_ACCENT_DARK, tags=("word", tag))
                canvas.tag_bind(tag, "<Button-1>", lambda e, w=word: self.search_word(w))
                canvas.create_text(6 + word_w + cnt_w - 12, mid, anchor="e", text=f"{n} 本", fill="#555")
                x = x_btn
                for j, label in enumerate(labels):
                    if label == "無視":
                        x += 10
                    btag = f"b{i}_{j}"
                    canvas.create_rectangle(x, y + 3, x + btn_w[j], y + ROW_H - 3, fill=C_SURFACE,
                                            outline=C_BORDER, tags=("btn", btag, btag + "r"))
                    canvas.create_text(x + btn_w[j] / 2, mid, text=label, tags=("btn", btag))
                    canvas.tag_bind(btag, "<Button-1>",
                                    lambda e, w=word, g=label: (ignore(w) if g == "無視" else put(w, g)))
                    canvas.tag_bind(btag, "<Enter>",
                                    lambda e, t=btag + "r": (canvas.itemconfigure(t, fill=C_HOVER),
                                                             canvas.configure(cursor="hand2")))
                    canvas.tag_bind(btag, "<Leave>",
                                    lambda e, t=btag + "r": (canvas.itemconfigure(t, fill=C_SURFACE),
                                                             canvas.configure(cursor="")))
                    x += btn_w[j] + 4
            canvas.tag_bind("word", "<Enter>", lambda e: canvas.configure(cursor="hand2"))
            canvas.tag_bind("word", "<Leave>", lambda e: canvas.configure(cursor=""))
            total_w = x + 10
            canvas.configure(scrollregion=(0, 0, total_w, len(rows) * ROW_H + 4))
            return total_w

        def remove_word(word):
            top_y = canvas.yview()[0]
            rows[:] = [r for r in rows if r[0] != word]
            info.configure(text=f"残り {len(rows)} 語")
            draw()
            canvas.yview_moveto(top_y)   # 見ていた位置はそのまま

        def put(word, group):
            self.add_favorite_to(group, word)
            remove_word(word)

        def ignore(word):
            self.settings.setdefault("ignored_words", []).append(word)
            save_settings(self.settings)
            remove_word(word)

        def unignore():
            n = len(self.settings.get("ignored_words", []))
            if not n:
                messagebox.showinfo(APP_NAME, "無視した言葉はありません。", parent=win)
                return
            if messagebox.askyesno(APP_NAME, f"無視した言葉 {n} 個を、また候補に出しますか？", parent=win):
                self.settings["ignored_words"] = []
                save_settings(self.settings)
                fill()

        def fill():
            m = min_var.get()
            self.settings["freq_min"] = m
            for k, b in min_btns.items():
                b.state(["pressed"] if k == m else ["!pressed"])
            words = self.frequent_words(m)
            rows[:] = [list(x) for x in words[:SHOW_MAX]]
            more = f"（多い順に {SHOW_MAX} 語まで表示）" if len(words) > SHOW_MAX else ""
            info.configure(text=f"{len(words)} 語{more}")
            total_w = draw() or 0
            canvas.yview_moveto(0)
            # 窓の幅を、表の幅に合わせて広げる（画面より大きくはしない）
            need = max(640, min(total_w + bar.winfo_reqwidth() + 30, win.winfo_screenwidth() - 80))
            if need > win.winfo_width():
                win.geometry(f"{need}x{max(win.winfo_height(), 560)}")

        fill()
        self._freq_dialog = {"win": win, "canvas": canvas, "rows": rows, "min": min_var, "fill": fill,
                             "put": put, "ignore": ignore, "min_btns": min_btns}   # 動作確認用

    def remove_favorite(self, group_name, word):
        if not messagebox.askyesno(APP_NAME, f"「{group_name}」から「{word}」を外しますか？"):
            return
        g = self.group(group_name)
        g["words"] = [w for w in g["words"] if w != word]
        save_settings(self.settings)
        self.draw_favorites()

    def fav_count(self, word):
        """お気に入りの言葉で検索したときに出る本数（全部の動画の中で）"""
        words = norm(word).split()
        if not words:
            return 0
        return sum(1 for v in self.videos if all(w in v["search"] for w in words))

    def draw_favorites(self):
        for w in self.fav_area.winfo_children():
            w.destroy()
        self.fav_rows = []   # (行, 空かどうか)。空の行は隠し、ドラッグ中だけ出す
        for g in self.groups():
            row = ttk.Frame(self.fav_area)
            self.fav_rows.append((row, not g["words"]))
            if g["words"]:
                row.pack(fill="x", pady=1)
            lb = ttk.Label(row, text=f"{g['name']}：", width=10, anchor="w")
            lb.pack(side="left", anchor="n")
            lb.fav_group = g["name"]   # 分類の名前の上に落としても、その分類に入る
            lb.bind("<Button-3>", lambda e, n=g["name"]: self.group_menu(n, e))
            flow = FlowFrame(row)
            flow.fav_group = g["name"]
            flow.pack(side="left", fill="x", expand=True)
            flow.bind("<Button-3>", lambda e, n=g["name"]: self.group_menu(n, e))
            for word in g["words"]:
                # width=0：ボタンの幅を文字の長さに合わせる（最低限の幅を取らず、欄を詰める）
                b = ttk.Button(flow, text=f"{word} ({self.fav_count(word)})", width=0,
                               command=lambda w=word: self.run_word(w))
                b.fav_word, b.fav_group = word, g["name"]
                b.bind("<Button-3>", lambda e, n=g["name"], w=word: self.fav_menu(n, w, e))
                b.bind("<ButtonPress-1>", self.fav_press, add="+")
                b.bind("<B1-Motion>", self.fav_motion, add="+")
                b.bind("<ButtonRelease-1>", self.fav_release, add="+")
            if not g["words"]:
                hint = ttk.Label(flow, text="（ここに入れるときは、ボタンをこの行へドラッグ）", foreground="#999")
                hint.bind("<Button-3>", lambda e, n=g["name"]: self.group_menu(n, e))
            flow.relayout()
        if not any(g["words"] for g in self.groups()):
            row = ttk.Frame(self.fav_area)
            row.pack(fill="x", pady=1)
            lb = ttk.Label(row, text="お気に入り：", width=10)
            lb.pack(side="left")
            ttk.Label(row, text="（検索欄に言葉を入れて「＋ 検索ワードを登録」。分類はここを右クリックで編集）",
                      foreground="#999").pack(side="left")
            lb.bind("<Button-3>", lambda e: self.group_menu(self.groups()[0]["name"], e))

    def show_all_fav_rows(self):
        """ドラッグを始めたら、空の分類の行も出して、そこへ落とせるようにする"""
        if any(empty for _, empty in self.fav_rows):
            for row, _ in self.fav_rows:
                row.pack_forget()
            for row, _ in self.fav_rows:
                row.pack(fill="x", pady=1)

    # ---- 分類の追加・名前変更・削除・並べ替え ----
    def new_group(self):
        from tkinter import simpledialog
        name = simpledialog.askstring(APP_NAME, "新しい分類の名前（例：監督、撮影場所）", parent=self.root)
        name = (name or "").strip()
        if not name:
            return None
        if self.group(name):
            messagebox.showinfo(APP_NAME, f"「{name}」という分類はもうあります。")
            return None
        self.groups().append({"name": name, "words": []})
        save_settings(self.settings)
        self.draw_favorites()
        return name

    def rename_group(self, name):
        from tkinter import simpledialog
        new = simpledialog.askstring(APP_NAME, f"「{name}」の新しい名前", initialvalue=name, parent=self.root)
        new = (new or "").strip()
        if not new or new == name:
            return
        if self.group(new):
            messagebox.showinfo(APP_NAME, f"「{new}」という分類はもうあります。")
            return
        self.group(name)["name"] = new
        save_settings(self.settings)
        self.draw_favorites()

    def delete_group(self, name):
        g = self.group(name)
        n = len(g["words"])
        msg = f"分類「{name}」を消しますか？"
        if n:
            msg += f"\n中のお気に入り {n} 個も一緒に消えます。"
        if not messagebox.askyesno(APP_NAME, msg):
            return
        self.groups().remove(g)
        save_settings(self.settings)
        self.draw_favorites()

    def move_group(self, name, step):
        gs = self.groups()
        i = gs.index(self.group(name))
        j = max(0, min(len(gs) - 1, i + step))
        gs.insert(j, gs.pop(i))
        save_settings(self.settings)
        self.draw_favorites()

    def group_menu(self, name, event):
        gs = self.groups()
        i = gs.index(self.group(name))
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="▲ 上へ", command=lambda: self.move_group(name, -1),
                         state="normal" if i > 0 else "disabled")
        menu.add_command(label="▼ 下へ", command=lambda: self.move_group(name, 1),
                         state="normal" if i < len(gs) - 1 else "disabled")
        menu.add_separator()
        menu.add_command(label="名前を変える…", command=lambda: self.rename_group(name))
        menu.add_command(label="この分類を消す…", command=lambda: self.delete_group(name),
                         state="normal" if len(gs) > 1 else "disabled")
        menu.add_separator()
        menu.add_command(label="新しい分類を作る…", command=self.new_group)
        menu.tk_popup(event.x_root, event.y_root)

    # ---- お気に入りの並べ替え・分類の移動 ----
    def move_favorite(self, from_group, word, to_group, to_index):
        src, dst = self.group(from_group), self.group(to_group)
        src["words"].remove(word)
        if word in dst["words"]:      # 移動先にもう同じ言葉があれば、重ねない
            dst["words"].remove(word)
        dst["words"].insert(max(0, min(to_index, len(dst["words"]))), word)
        save_settings(self.settings)
        self.draw_favorites()

    def fav_menu(self, group_name, word, event):
        words = self.group(group_name)["words"]
        i = words.index(word)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="◀ 左へ", command=lambda: self.move_favorite(group_name, word, group_name, i - 1),
                         state="normal" if i > 0 else "disabled")
        menu.add_command(label="右へ ▶", command=lambda: self.move_favorite(group_name, word, group_name, i + 1),
                         state="normal" if i < len(words) - 1 else "disabled")
        sub = tk.Menu(menu, tearoff=0)
        for g in self.groups():
            if g["name"] != group_name:
                sub.add_command(label=g["name"], command=lambda n=g["name"]: self.move_favorite(
                    group_name, word, n, len(self.group(n)["words"])))
        menu.add_cascade(label="分類を移す", menu=sub, state="normal" if len(self.groups()) > 1 else "disabled")
        menu.add_separator()
        menu.add_command(label="お気に入りから外す…", command=lambda: self.remove_favorite(group_name, word))
        menu.tk_popup(event.x_root, event.y_root)

    def fav_press(self, e):
        self.fav_drag = {"word": e.widget.fav_word, "group": e.widget.fav_group,
                         "x": e.x_root, "y": e.y_root, "moved": False}

    def fav_motion(self, e):
        d = getattr(self, "fav_drag", None)
        if d and (abs(e.x_root - d["x"]) > 8 or abs(e.y_root - d["y"]) > 8):
            if not d["moved"]:
                self.show_all_fav_rows()
            d["moved"] = True
            e.widget.configure(cursor="exchange")

    def fav_release(self, e):
        d, self.fav_drag = getattr(self, "fav_drag", None), None
        if not d or not d["moved"]:
            return   # ふつうのクリック → ボタンとして検索する
        e.widget.configure(cursor="")
        target = self.root.winfo_containing(e.x_root, e.y_root)
        # 離した場所：ほかのボタンの上ならその位置へ、分類の行の空いた所ならその分類のいちばん後ろへ
        while target is not None and not hasattr(target, "fav_group"):
            target = target.master
        to = None
        if target is not None:
            to_group = target.fav_group
            words = self.group(to_group)["words"]
            word = getattr(target, "fav_word", None)
            if word and word != d["word"]:
                to = (to_group, words.index(word))
            elif word is None:
                to = (to_group, len(words))
        if to is not None:
            # ボタンを作り直すので、今のマウスの処理が終わってから動かす
            self.root.after_idle(lambda: self.move_favorite(d["group"], d["word"], to[0], to[1]))
        else:
            self.root.after_idle(self.draw_favorites)   # 出していた空の行を、また隠す
        return "break"   # ドラッグのときは検索しない

    # ---- 再生 ----
    def update_player_box(self):
        names = [DEFAULT_PLAYER] + [p["name"] for p in self.settings["players"]]
        self.player_box["values"] = names
        cur = self.settings.get("player") or DEFAULT_PLAYER
        if cur not in names:
            cur = DEFAULT_PLAYER
            self.settings["player"] = ""
        self.player_var.set(cur)

    def on_player_selected(self):
        name = self.player_var.get()
        self.settings["player"] = "" if name == DEFAULT_PLAYER else name
        save_settings(self.settings)

    def find_player(self, name):
        for p in self.settings["players"]:
            if p["name"] == name:
                return p
        return None

    def play(self, path, player_name=None):
        """player_name が無ければ、上で選んでいる再生ソフトで開く"""
        if player_name is None:
            player_name = self.settings.get("player") or DEFAULT_PLAYER
        if not os.path.exists(path):
            messagebox.showerror(APP_NAME, f"動画が見つかりません。消されたか、移動した可能性があります。\n{path}\n\n「読み込み直す」を押してください。")
            return
        try:
            if player_name == DEFAULT_PLAYER:
                os.startfile(path)
            else:
                p = self.find_player(player_name)
                if not p or not os.path.exists(p["path"]):
                    messagebox.showerror(APP_NAME, f"再生ソフト「{player_name}」が見つかりません。\n「再生ソフト設定」で登録し直してください。")
                    return
                subprocess.Popen([p["path"], path])
            self.status.set(f"再生：{os.path.basename(path)}（{player_name}）")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"再生できませんでした。\n{e}")

    def play_selected(self, player_name=None):
        sel = self.tree.selection()
        if sel:
            self.play(sel[0], player_name)

    def on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator":
            col = self.separator_col(event.x)
            if col:
                self.autofit_column(col)
            return "break"
        if region == "cell" and self.col_at(event.x) in FIXED_COLUMNS:
            return "break"   # ▶・☆ は1回目のクリックで処理済み（2回開いたり、星が戻ったりしないように）
        if region == "cell":
            self.play_selected()

    def on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.show_menu(row, event.x_root, event.y_root)

    def show_menu(self, row, x_root, y_root):
        """右クリックのメニュー（一覧とサムネイルの両方で使う）"""
        menu = tk.Menu(self.root, tearoff=0)
        # 名前を区切った言葉を並べ、クリックだけで検索できるようにする
        words = split_name(os.path.basename(row))
        sub = tk.Menu(menu, tearoff=0)
        for w in words:
            sub.add_command(label=w, command=lambda w=w: self.search_word(w))
        if not words:
            sub.add_command(label="（区切れる言葉がありません）", state="disabled")
        menu.add_cascade(label="名前の一部で検索", menu=sub)
        menu.add_separator()
        menu.add_command(label="タグを付ける…（T）", command=self.open_tag_dialog)
        sel = [p for p in self.tree.selection() if p in self.by_path] or [row]
        on = all(self.is_starred(p) for p in sel)
        menu.add_command(label="★ 星を外す（S）" if on else "★ 星を付ける（S）",
                         command=lambda: self.toggle_star(sel))
        menu.add_separator()
        for name in [DEFAULT_PLAYER] + [p["name"] for p in self.settings["players"]]:
            menu.add_command(label=f"{name} で再生", command=lambda n=name: self.play(row, n))
        menu.add_separator()
        menu.add_command(label="エクスプローラーで場所を開く",
                         command=lambda: subprocess.Popen(["explorer", "/select,", row]))
        menu.tk_popup(x_root, y_root)

    def open_player_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("再生ソフト設定")
        win.geometry("600x300")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="動画を開くソフト（.exe）を登録します。「Windows の標準アプリ」はいつでも選べます。",
                  padding=8).pack(anchor="w")
        lb = tk.Listbox(win)
        lb.pack(fill="both", expand=True, padx=8)

        def redraw():
            lb.delete(0, "end")
            for p in self.settings["players"]:
                lb.insert("end", f"{p['name']}　　{p['path']}")

        def add():
            exe = filedialog.askopenfilename(parent=win, title="再生ソフトの .exe を選ぶ",
                                             filetypes=[("アプリ", "*.exe")])
            if not exe:
                return
            exe = os.path.normpath(exe)
            name = os.path.splitext(os.path.basename(exe))[0]
            base, n = name, 2
            while name == DEFAULT_PLAYER or self.find_player(name):
                name = f"{base} ({n})"
                n += 1
            self.settings["players"].append({"name": name, "path": exe})
            redraw()

        def remove():
            sel = lb.curselection()
            if sel:
                del self.settings["players"][sel[0]]
                redraw()

        def close():
            save_settings(self.settings)
            self.update_player_box()
            win.destroy()

        redraw()
        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="追加…", command=add).pack(side="left")
        ttk.Button(btns, text="選んだものを外す", command=remove).pack(side="left", padx=6)
        ttk.Button(btns, text="閉じる", command=close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)

    # ---- フォルダ設定 ----
    def open_folder_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("フォルダ設定")
        win.geometry("560x320")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="ここに登録したフォルダ（中のフォルダも含む）の動画を一覧にします。",
                  padding=8).pack(anchor="w")
        lb = tk.Listbox(win)
        lb.pack(fill="both", expand=True, padx=8)
        for f in self.settings["folders"]:
            lb.insert("end", f)

        def add():
            d = filedialog.askdirectory(parent=win, title="動画のフォルダを選ぶ")
            if not d:
                return
            d = os.path.normpath(d)
            if d in self.settings["folders"]:
                messagebox.showinfo(APP_NAME, "そのフォルダはもう登録されています。", parent=win)
                return
            self.settings["folders"].append(d)
            lb.insert("end", d)

        def remove():
            sel = lb.curselection()
            if not sel:
                return
            i = sel[0]
            del self.settings["folders"][i]
            lb.delete(i)

        def close():
            save_settings(self.settings)
            win.destroy()
            self.rescan()

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="追加…", command=add).pack(side="left")
        ttk.Button(btns, text="選んだものを外す", command=remove).pack(side="left", padx=6)
        ttk.Button(btns, text="閉じる", command=close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)


def match_ime_font(event):
    """日本語入力中の字（変換前の字）を、入力欄と同じ大きさにする。
    Tk は入力中の字の書体を Windows に伝えないため、そのままだと Windows の標準の大きな字で出る。
    入力欄に入るたびに、その欄の書体と大きさを Windows の日本語入力（IME）に伝える"""
    w = event.widget
    if sys.platform != "win32" or not isinstance(w, tk.Widget) or w.winfo_class() not in ("TEntry", "Entry", "TCombobox"):
        return
    try:
        import ctypes
        import tkinter.font as tkfont
        from ctypes import wintypes

        class LOGFONTW(ctypes.Structure):
            _fields_ = [("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG),
                        ("lfEscapement", wintypes.LONG), ("lfOrientation", wintypes.LONG),
                        ("lfWeight", wintypes.LONG), ("lfItalic", wintypes.BYTE),
                        ("lfUnderline", wintypes.BYTE), ("lfStrikeOut", wintypes.BYTE),
                        ("lfCharSet", wintypes.BYTE), ("lfOutPrecision", wintypes.BYTE),
                        ("lfClipPrecision", wintypes.BYTE), ("lfQuality", wintypes.BYTE),
                        ("lfPitchAndFamily", wintypes.BYTE), ("lfFaceName", wintypes.WCHAR * 32)]

        try:
            name = w.cget("font") or "TkTextFont"
        except tk.TclError:
            name = "TkTextFont"
        f = tkfont.Font(root=w, font=name).actual()
        size = f["size"]
        px = -size if size < 0 else round(w.winfo_fpixels(f"{size}p"))
        lf = LOGFONTW()
        lf.lfHeight = -px
        lf.lfWeight = 700 if f["weight"] == "bold" else 400
        lf.lfCharSet = 1   # DEFAULT_CHARSET
        lf.lfFaceName = f["family"][:31]
        imm = ctypes.windll.imm32
        imm.ImmGetContext.restype = wintypes.HANDLE
        imm.ImmGetContext.argtypes = [wintypes.HWND]
        imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm.ImmSetCompositionFontW.argtypes = [wintypes.HANDLE, ctypes.POINTER(LOGFONTW)]
        hwnd = w.winfo_id()
        himc = imm.ImmGetContext(hwnd)
        if himc:
            imm.ImmSetCompositionFontW(himc, ctypes.byref(lf))
            imm.ImmReleaseContext(hwnd, himc)
    except Exception:
        pass   # うまくいかなくても、入力そのものには困らないので何もしない


def apply_style(root):
    """見た目をそろえる。tkinter に元からある「clam」という見た目を土台に、色と余白だけ変える
    （追加の部品は使わないので、軽さは変わらない）"""
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(background=C_BG)
    root.option_add("*Toplevel.background", C_BG)
    from tkinter import font as tkfont
    base = tkfont.nametofont("TkDefaultFont")
    flat = dict(lightcolor=C_SURFACE, darkcolor=C_SURFACE)
    st.configure(".", background=C_BG, foreground=C_TEXT, bordercolor=C_BORDER, lightcolor=C_BG,
                 darkcolor=C_BG, troughcolor="#e9ecf1", focuscolor=C_ACCENT, selectbackground=C_ACCENT,
                 selectforeground="white", fieldbackground=C_SURFACE, insertcolor=C_TEXT)
    # ボタン：平らな形。押している（並べ替えの今の順など）ときは青っぽく
    st.configure("TButton", background=C_SURFACE, padding=(8, 2), anchor="center", **flat)
    st.map("TButton",
           background=[("disabled", C_BG), ("pressed", C_ACCENT_SOFT), ("active", C_HOVER)],
           lightcolor=[("pressed", C_ACCENT_SOFT), ("active", C_HOVER)],
           darkcolor=[("pressed", C_ACCENT_SOFT), ("active", C_HOVER)],
           bordercolor=[("pressed", C_ACCENT), ("active", "#b9c3d3")],
           foreground=[("disabled", "#a0a6b0"), ("pressed", C_ACCENT_DARK)])
    # よく使うボタン：青で目立たせる
    st.configure("Accent.TButton", background=C_ACCENT, foreground="white", bordercolor=C_ACCENT,
                 lightcolor=C_ACCENT, darkcolor=C_ACCENT)
    st.map("Accent.TButton",
           background=[("pressed", C_ACCENT_DARK), ("active", "#2a63c7")],
           lightcolor=[("pressed", C_ACCENT_DARK), ("active", "#2a63c7")],
           darkcolor=[("pressed", C_ACCENT_DARK), ("active", "#2a63c7")],
           bordercolor=[("pressed", C_ACCENT_DARK), ("active", "#2a63c7")],
           foreground=[("disabled", "#dfe6f5"), ("pressed", "white"), ("active", "white")])
    # あまり使わない設定のボタン：控えめに
    st.configure("Sub.TButton", foreground=C_SUB)
    # 入力欄・選択欄
    st.configure("TEntry", padding=(4, 3), **flat)
    st.map("TEntry", bordercolor=[("focus", C_ACCENT)], lightcolor=[("focus", C_ACCENT)],
           fieldbackground=[("readonly", "#fafbfc")])
    st.configure("TCombobox", padding=(4, 2), arrowcolor=C_SUB, background=C_SURFACE, **flat)
    st.map("TCombobox", fieldbackground=[("readonly", C_SURFACE)], background=[("active", C_HOVER)],
           selectbackground=[("readonly", C_SURFACE)], selectforeground=[("readonly", C_TEXT)],
           bordercolor=[("focus", C_ACCENT)])
    # 一覧
    st.configure("Treeview", background=C_SURFACE, fieldbackground=C_SURFACE,
                 rowheight=base.metrics("linespace") + 8, bordercolor=C_BORDER, **flat)
    st.map("Treeview", background=[("selected", C_ACCENT)], foreground=[("selected", "white")])
    st.configure("Treeview.Heading", background="#eef1f5", foreground="#374151", padding=(6, 5),
                 relief="flat", bordercolor=C_BORDER, lightcolor="#eef1f5", darkcolor="#eef1f5")
    st.map("Treeview.Heading", background=[("active", "#e3e8ef")], lightcolor=[("active", "#e3e8ef")])
    # タブ
    st.configure("TNotebook", background=C_BG, bordercolor=C_BORDER, tabmargins=(0, 4, 0, 0))
    st.configure("TNotebook.Tab", background="#e9ecf1", foreground=C_SUB, padding=(14, 5),
                 bordercolor=C_BORDER, lightcolor="#e9ecf1")
    st.map("TNotebook.Tab", background=[("selected", C_SURFACE)], foreground=[("selected", C_TEXT)],
           lightcolor=[("selected", C_SURFACE)], expand=[("selected", (0, 2, 0, 0))])
    # スクロールバー・スライダー
    for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        st.configure(sb, background="#cfd5de", troughcolor="#f0f2f5", bordercolor="#f0f2f5",
                     arrowcolor=C_SUB, gripcount=0, lightcolor="#cfd5de", darkcolor="#cfd5de")
        st.map(sb, background=[("active", "#b4bdca")])
    st.configure("Horizontal.TScale", background=C_SURFACE, troughcolor="#e1e5eb", bordercolor=C_BORDER,
                 lightcolor=C_SURFACE, darkcolor=C_SURFACE)
    st.configure("TSeparator", background=C_BORDER)
    st.configure("TCheckbutton", indicatorbackground=C_SURFACE, indicatorforeground=C_ACCENT)
    # チェックの印は clam だと「✕」で「外す」に見えるため、Windows の「✓」の印を借りる
    try:
        st.element_create("Win.Checkbutton.indicator", "from", "vista")
        st.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Win.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    except tk.TclError:
        pass   # Windows 以外では clam の印のまま
    # 見出しの文字（「検索：」「履歴：」など）
    st.configure("Head.TLabel", foreground="#4b5563")
    # 下の状態表示
    st.configure("Status.TFrame", background="#eceff3")
    st.configure("Status.TLabel", background="#eceff3", foreground=C_TEXT)
    st.configure("StatusSub.TLabel", background="#eceff3", foreground=C_SUB)


def main():
    root = tk.Tk()
    root.bind_all("<FocusIn>", match_ime_font, add="+")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

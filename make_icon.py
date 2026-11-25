# ハヤミルのアイコンを作る
# 紫の角丸の四角に、白いフォルダ。フォルダの中に紫の再生の三角（＝フォルダの動画をすぐ再生）
#
# 必要なもの：Pillow（画像を作る部品。入れ方：python -m pip install pillow）
# 使い方：python make_icon.py
#   → icon.ico（exe と画面に付けるアイコン）と images/icon.png（README 用）ができる

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SIZE = 512                 # 大きく描いてから縮めると、ふちがなめらかになる
PURPLE = "#534AB7"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw_icon():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=96, fill=PURPLE)
    # フォルダ：うしろのつまみ＋本体
    draw.rounded_rectangle((88, 136, 250, 200), radius=24, fill="white")
    draw.rounded_rectangle((88, 168, 424, 392), radius=24, fill="white")
    # 再生の三角
    draw.polygon([(222, 220), (222, 344), (322, 282)], fill=PURPLE)
    return image


def main():
    icon = draw_icon()
    icon.save(HERE / "icon.ico", sizes=ICO_SIZES)
    (HERE / "images").mkdir(exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(HERE / "images" / "icon.png")
    print("icon.ico と images/icon.png を作りました")


if __name__ == "__main__":
    main()

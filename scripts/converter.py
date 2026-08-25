from PIL import Image
import glob

# 批量将透明背景 PNG 转为白底 PNG
for path in glob.glob("docs/images/*.png"):
    img = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", img.size, (255, 255, 255))
    alpha_composite = Image.alpha_composite(background, img)
    alpha_composite.convert("RGB").save(path)
print("已全部转换为带纯白背景的 PNG！")

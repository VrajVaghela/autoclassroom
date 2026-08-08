import os
from PIL import Image, ImageDraw, ImageFont

icons_dir = os.path.join(os.path.dirname(__file__), "extension", "icons")
os.makedirs(icons_dir, exist_ok=True)

def generate_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw rounded rectangle background with brand color #4f46e5 (indigo)
    margin = int(size * 0.05)
    radius = int(size * 0.2)
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(79, 70, 229, 255)
    )
    
    # Draw a styled 'A' letter or graduation cap motif
    # Draw inner circle / accent shape
    cx, cy = size // 2, size // 2
    r = int(size * 0.28)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 240))
    
    # Inner dot / rocket tip
    r2 = int(size * 0.14)
    draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=(79, 70, 229, 255))
    
    filename = f"icon{size}.png"
    img.save(os.path.join(icons_dir, filename))
    print(f"Generated {filename}")

for sz in [16, 48, 128]:
    generate_icon(sz)

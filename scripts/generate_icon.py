"""Generate custom application icon for GMF-CMP-Monitor.

Creates a crisp, modern, professional telemetry dashboard monitor icon:
- Monitor frame in dark navy (#0A192F)
- Glowing teal/cyan line chart (#00F0FF / #00FFA3)
- Screen bezel, subtle grid, telemetry chart with glow, and power/status dot
- Multi-size ICO: 16x16, 24x24, 32x32, 48x48, 64x64, 128x128, 256x256
"""

from pathlib import Path
from PIL import Image, ImageDraw


def create_monitor_icon(canvas_size: int = 512) -> Image.Image:
    # High-resolution master image with RGBA
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    scale = canvas_size / 512.0

    def s(val: float) -> float:
        return val * scale

    def s_pt(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    # Palette
    frame_bg = (10, 25, 47, 255)         # #0A192F Dark Navy
    frame_border = (30, 58, 95, 255)     # #1E3A5F Outer border
    screen_bg = (6, 16, 30, 255)         # #06101E Telemetry screen dark
    bezel_inner = (23, 42, 69, 255)      # #172A45 Screen inner border
    grid_color = (20, 48, 77, 140)       # Telemetry grid
    teal_cyan = (0, 240, 255)            # #00F0FF Teal / Cyan
    mint_green = (0, 255, 163)           # #00FFA3 Bright mint
    stand_color = (16, 36, 64, 255)      # Neck & base dark slate
    stand_border = (38, 72, 112, 255)    # Neck & base border

    # 1. Stand Base
    base_box = [s(166), s(432), s(346), s(468)]
    draw.rounded_rectangle(base_box, radius=s(10), fill=stand_color, outline=stand_border, width=int(max(1, s(3))))

    # 2. Stand Neck
    neck_box = [s(236), s(350), s(276), s(440)]
    draw.rectangle(neck_box, fill=stand_color, outline=stand_border, width=int(max(1, s(2))))

    # 3. Monitor Outer Frame (Navy)
    frame_box = [s(32), s(36), s(480), s(376)]
    draw.rounded_rectangle(frame_box, radius=s(24), fill=frame_bg, outline=frame_border, width=int(max(1, s(4))))

    # 4. Monitor Bezel & Screen
    screen_box = [s(52), s(56), s(460), s(340)]
    draw.rounded_rectangle(screen_box, radius=s(12), fill=screen_bg, outline=bezel_inner, width=int(max(1, s(3))))

    # 5. Telemetry Screen Grid lines
    # Horizontal grid
    for y_pos in [110, 165, 220, 275]:
        draw.line([s_pt(58, y_pos), s_pt(454, y_pos)], fill=grid_color, width=int(max(1, s(1.5))))
    # Vertical grid
    for x_pos in [115, 180, 245, 310, 375, 440]:
        draw.line([s_pt(x_pos, 62), s_pt(x_pos, 334)], fill=grid_color, width=int(max(1, s(1.5))))

    # 6. Telemetry Chart Points
    chart_points = [
        (65, 275),
        (105, 260),
        (145, 215),
        (190, 235),
        (240, 155),
        (290, 185),
        (340, 115),
        (390, 145),
        (435, 95),
        (450, 105),
    ]
    scaled_chart_pts = [s_pt(x, y) for x, y in chart_points]

    # Under-chart area fill (gradient approximation using polygons)
    baseline_y = s(330)
    fill_poly = [s_pt(65, 330)] + scaled_chart_pts + [s_pt(450, 330)]
    draw.polygon(fill_poly, fill=(0, 240, 255, 35))

    # Second lighter fill layer
    mid_poly = [s_pt(65, 330)] + [s_pt(x, (y + 330) / 2) for x, y in chart_points] + [s_pt(450, 330)]
    draw.polygon(mid_poly, fill=(0, 255, 163, 20))

    # 7. Glowing Chart Line (Multiple passes from outer blur to sharp core)
    # Outer glow
    draw.line(scaled_chart_pts, fill=(*teal_cyan, 50), width=int(max(2, s(14))), joint="curve")
    # Mid glow
    draw.line(scaled_chart_pts, fill=(*mint_green, 110), width=int(max(2, s(8))), joint="curve")
    # Inner glow
    draw.line(scaled_chart_pts, fill=(*teal_cyan, 210), width=int(max(1, s(4))), joint="curve")
    # Sharp core line
    draw.line(scaled_chart_pts, fill=(255, 255, 255, 255), width=int(max(1, s(2))), joint="curve")

    # 8. Data Nodes (Glowing data points on chart peaks)
    key_nodes = [chart_points[2], chart_points[4], chart_points[6], chart_points[8]]
    for nx, ny in key_nodes:
        # Outer glow
        draw.ellipse([s(nx - 10), s(ny - 10), s(nx + 10), s(ny + 10)], fill=(*teal_cyan, 70))
        # Mid halo
        draw.ellipse([s(nx - 6), s(ny - 6), s(nx + 6), s(ny + 6)], fill=(*mint_green, 190))
        # White center
        draw.ellipse([s(nx - 3), s(ny - 3), s(nx + 3), s(ny + 3)], fill=(255, 255, 255, 255))

    # 9. Power / Status indicator LED on the bottom bezel
    led_x, led_y = 256, 358
    # Outer glow
    draw.ellipse([s(led_x - 7), s(led_y - 7), s(led_x + 7), s(led_y + 7)], fill=(*mint_green, 90))
    # Mid core
    draw.ellipse([s(led_x - 4), s(led_y - 4), s(led_x + 4), s(led_y + 4)], fill=(*mint_green, 255))
    # Pinpoint highlight
    draw.ellipse([s(led_x - 1.5), s(led_y - 1.5), s(led_x + 1.5), s(led_y + 1.5)], fill=(255, 255, 255, 240))

    return img


def generate_ico(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    master = create_monitor_icon(canvas_size=512)

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(
        output_path,
        format="ICO",
        sizes=sizes,
    )
    print(f"Generated multi-size icon at: {output_path}")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    assets_dir = project_root / "assets"
    ico_file = assets_dir / "app.ico"
    generate_ico(ico_file)

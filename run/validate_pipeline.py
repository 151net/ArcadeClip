"""Generate a synthetic broadcast and run real jacket recognition, OCR and export."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from export import export_clips
from profiles import write_json
from recognition import analyze


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".validation-data/demo"))
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/malgun.ttf"))
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "broadcast.mp4").exists():
        raise SystemExit("Choose a new --output directory; existing demo files are preserved.")
    font = ImageFont.truetype(str(args.font), 40)
    (root / "jackets").mkdir(exist_ok=True)
    images = [Image.fromarray(np.random.default_rng(seed).integers(0, 255, (8, 8, 3), dtype=np.uint8))
              .resize((200, 200), Image.Resampling.NEAREST) for seed in range(2)]
    for index, image in enumerate(images):
        image.save(root / "jackets" / f"{index}.png")
    write_json(root / "jackets/_manifest.json", [{"image_url": f"{index}.png", "title": f"Demo {index + 1}"}
                                                for index in range(2)])
    video = root / "broadcast.mp4"
    with av.open(str(video), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        stream.codec_context.gop_size = 90
        for index in range(90):
            image = Image.new("RGB", (640, 360), "white")
            image.paste(images[(index // 30) % 2], (0, 0))
            if index % 30 >= 20:
                draw = ImageDraw.Draw(image)
                draw.text((220, 160), "플레이어", font=font, fill="black")
                draw.text((220, 250), "100.1234%", font=font, fill="black")
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = index + 30, Fraction(1, 10)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    profile = {"version": 1, "name": "합성 방송", "size": [640, 360],
               "regions": {"jacket": [0, 0, 200 / 640, 200 / 360],
                           "player": [220 / 640, 160 / 360, 620 / 640, 225 / 360],
                           "achievement": [220 / 640, 250 / 360, 620 / 640, 315 / 360]},
               "recognition": {"sample_seconds": .5, "stable_seconds": .5, "ocr_tail_seconds": 1}}
    write_json(root / "profile.json", profile)
    cancel = threading.Event()
    result = analyze(video, 0, 9, profile, root, cancel, print)
    write_json(root / "analysis.json", result)
    assert not result["warnings"], result["warnings"]
    assert [row["title"] for row in result["clips"]] == ["Demo 1", "Demo 2", "Demo 1"]
    assert all(row.get("player") == "플레이어" for row in result["clips"]), result
    assert all(row.get("achievement") == 100.1234 for row in result["clips"]), result
    for row in result["clips"]:
        assert all(row["end"] - 1 <= item["seconds"] < row["end"] for item in row["ocr_evidence"])
    output = export_clips(result["clips"], root / "clips", cancel, print)
    assert len(output["saved"]) == 3 and not output["failed"], output
    write_json(root / "project.json", {"version": 1, "ranges": result["clips"], "profile": profile, "exports": output})
    print(json.dumps({"candidates": 3, "ocr": "passed", "exported": output["saved"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

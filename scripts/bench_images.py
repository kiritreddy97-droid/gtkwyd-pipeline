"""Which picture shapes avoid the stacked double-head glitch with SDXL-Turbo?"""
import time
from pathlib import Path

import torch
from diffusers import AutoPipelineForText2Image

OUT = Path("out/imagebench")
OUT.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(4)
pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sdxl-turbo", torch_dtype=torch.float32,
                                                 variant="fp16")
pipe.set_progress_bar_config(disable=True)
P = ("Pixar-style 3D animated film still, glossy, warm cinematic lighting, Indian town, "
     "close-up of a young Indian man with black hair, big eyes and a worried smile, wearing a "
     "yellow shirt, standing in front of a wooden door")
SIZES = [(576, 1024), (640, 832), (704, 704), (768, 768)]
t0 = time.time()
for (w, h) in SIZES:
    for seed in (1, 2, 3):
        g = torch.Generator().manual_seed(seed)
        img = pipe(prompt=P, width=w, height=h, num_inference_steps=2, guidance_scale=0.0,
                   generator=g).images[0]
        img.save(OUT / f"{w}x{h}_s{seed}.jpg", quality=92)
        print(f"{w}x{h} seed {seed} done ({time.time() - t0:.0f}s)", flush=True)

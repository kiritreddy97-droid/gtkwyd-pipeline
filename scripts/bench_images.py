"""One-off benchmark: can an open-source diffusion model draw scene illustrations
on a free GitHub runner (CPU only)? Writes samples + timings to out/imagebench/."""
import sys
import time
from pathlib import Path

import torch
from diffusers import AutoPipelineForText2Image

OUT = Path("out/imagebench")
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "timings.txt", "w", encoding="utf-8")


def say(msg: str) -> None:
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


torch.set_num_threads(4)
STYLE = ("cinematic painterly historical illustration, rich warm colors, dramatic golden light, "
         "highly detailed, concept art, no text, no modern objects")
PROMPTS = {
    "bazaar": "a bustling 16th century South Indian stone city bazaar at golden hour, merchants with "
              "gems and spices, temple towers in the distance, " + STYLE,
    "army": "a vast royal army with war elephants and horsemen marching across a dusty plain toward "
            "a river at dawn, banners flying, " + STYLE,
    "fort": "a great ruined stone fort on a rocky hill at sunset, long shadows, lonely and majestic, " + STYLE,
}

t0 = time.time()
say("loading stabilityai/sdxl-turbo ...")
pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sdxl-turbo", torch_dtype=torch.float32,
                                                 variant="fp16")
pipe.set_progress_bar_config(disable=True)
say(f"loaded in {time.time() - t0:.0f}s")

for (w, h, steps) in [(512, 512, 1), (512, 512, 4), (1024, 576, 2)]:
    for name, prompt in PROMPTS.items():
        t = time.time()
        img = pipe(prompt=prompt, width=w, height=h, num_inference_steps=steps,
                   guidance_scale=0.0).images[0]
        dt = time.time() - t
        img.save(OUT / f"{name}_{w}x{h}_s{steps}.jpg", quality=92)
        say(f"{name} {w}x{h} steps={steps}: {dt:.1f}s")
    if time.time() - t0 > 1500:
        say("stopping early: time budget")
        break
say(f"total {time.time() - t0:.0f}s")

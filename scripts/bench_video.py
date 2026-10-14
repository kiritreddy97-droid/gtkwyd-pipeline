"""One-off benchmark: can an open-source image-to-video model (LTX-Video) run on a free
GitHub CPU runner? Reports memory/time per stage and saves the clip to out/videobench/."""
import gc
import resource
import time
from pathlib import Path

import torch
from diffusers import LTXImageToVideoPipeline, LTXPipeline
from diffusers.utils import export_to_video, load_image

OUT = Path("out/videobench")
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "timings.txt", "w", encoding="utf-8")
REPO = "Lightricks/LTX-Video"
STEPS = 12
T0 = time.time()


def say(msg):
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    line = f"[{time.time() - T0:6.0f}s | peak RAM {rss:4.1f} GB] {msg}"
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


torch.set_num_threads(4)
prompt = ("The man blinks and smiles slightly, looks around the street; the rickshaw "
          "vibrates gently, people move in the background, subtle slow camera push-in, "
          "smooth natural motion, Pixar-style animation")
neg = "worst quality, inconsistent motion, blurry, jittery, distorted, deformed face"

# 1) encode the prompt with the big T5 text encoder alone, then free it
say("loading text encoder only ...")
tp = LTXPipeline.from_pretrained(REPO, transformer=None, vae=None, scheduler=None,
                                 torch_dtype=torch.bfloat16)
with torch.no_grad():
    pe, pm, npe, npm = tp.encode_prompt(prompt, negative_prompt=neg, do_classifier_free_guidance=True,
                                        device="cpu")
pe, pm, npe, npm = pe.float(), pm, npe.float(), npm
del tp
gc.collect()
say("prompt encoded, text encoder freed")

# 2) the video transformer + VAE only
pipe = LTXImageToVideoPipeline.from_pretrained(REPO, text_encoder=None, tokenizer=None,
                                               torch_dtype=torch.bfloat16)
pipe.set_progress_bar_config(disable=False)
say("video model loaded")
image = load_image("scripts/samples/ltx-input.jpg").resize((512, 768))
g = torch.Generator().manual_seed(7)
t = time.time()


def tick(pipe_, i, ts, kw):
    say(f"  step {i + 1}/{STEPS} done")
    return kw


video = pipe(image=image, prompt_embeds=pe.to(torch.bfloat16), prompt_attention_mask=pm,
             negative_prompt_embeds=npe.to(torch.bfloat16), negative_prompt_attention_mask=npm,
             width=512, height=768, num_frames=49, num_inference_steps=STEPS,
             guidance_scale=3.0, generator=g, callback_on_step_end=tick).frames[0]
say(f"generation took {time.time() - t:.0f}s")
export_to_video(video, str(OUT / "ltx_clip.mp4"), fps=24)
say("saved clip")

"""One-off benchmark: can open-source Indic Parler-TTS voice Telugu on a free CPU runner?
Writes samples + timings to out/ttsbench/."""
import time
from pathlib import Path

import soundfile as sf
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

OUT = Path("out/ttsbench")
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "timings.txt", "w", encoding="utf-8")


def say(msg):
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


torch.set_num_threads(4)
t0 = time.time()
name = "ai4bharat/indic-parler-tts"
model = ParlerTTSForConditionalGeneration.from_pretrained(name).to("cpu")
tok = AutoTokenizer.from_pretrained(name)
dtok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
say(f"loaded in {time.time() - t0:.0f}s")

CLIPS = {
    "narration_female": (
        "Lalitha speaks with an expressive, warm, storytelling tone at a moderate pace, "
        "like telling a gripping true story to family. Very clear audio, no background noise.",
        "విజయనగర సామ్రాజ్యం ఒకప్పుడు ప్రపంచంలోనే అత్యంత సంపన్నమైనది. కానీ ఒక్క రోజులో "
        "అంతా మారిపోయింది. ఆ రోజు ఏం జరిగిందో తెలుసా?"),
    "dialogue_male_worried": (
        "Prakash speaks in a worried, slightly shaky and emotional voice, slow pace, "
        "very clear audio, no background noise.",
        "ఇందులో ఇంత డబ్బు ఉందా? కానీ లక్ష్మి, ఇది మనది కాదు కదా. ఎవరో పోగొట్టుకున్నారు."),
    "dialogue_female_excited": (
        "Lalitha speaks in an excited, animated, fast voice, very clear audio, "
        "no background noise.",
        "నిజంగానా? ఇందులో ఇన్ని డబ్బులా! మన కూతురు స్కూల్ ఫీజు కట్టేయొచ్చు!"),
}
for key, (desc, text) in CLIPS.items():
    t = time.time()
    ids = dtok(desc, return_tensors="pt")
    pids = tok(text, return_tensors="pt")
    with torch.no_grad():
        gen = model.generate(input_ids=ids.input_ids, attention_mask=ids.attention_mask,
                             prompt_input_ids=pids.input_ids,
                             prompt_attention_mask=pids.attention_mask)
    audio = gen.cpu().numpy().squeeze()
    sf.write(OUT / f"{key}.wav", audio, model.config.sampling_rate)
    secs = len(audio) / model.config.sampling_rate
    took = time.time() - t
    say(f"{key}: {secs:.1f}s of audio in {took:.0f}s (x{took / max(secs, 0.1):.1f} real time)")
say(f"total {time.time() - t0:.0f}s")

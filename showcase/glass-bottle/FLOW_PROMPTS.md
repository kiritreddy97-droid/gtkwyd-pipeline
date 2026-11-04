# Flow prompts: How a Glass Bottle Is Made (trial)

Open Google Flow (labs.google/fx/tools/flow), start a new project, choose **Video**, a **vertical 9:16**
ratio, the **Veo 3.1 Lite** model (cheapest; the free 50 credits a day cover about five clips), and
paste one prompt per clip. Download each result and save it in this folder with the file name shown.

| File | Prompt |
|---|---|
| scene1.mp4 | Cinematic macro shot of golden sand pouring into a giant industrial furnace hopper, warm orange glow, slow camera push-in, photorealistic, shallow depth of field. No text, no people. |
| scene2.mp4 | Inside a glass furnace, bright orange molten glass glowing and flowing, heat shimmer, slow camera glide, photorealistic, cinematic lighting. No text, no people. |
| scene3.mp4 | A glowing orange blob of molten glass is cut and drops into a metal bottle mold on an industrial machine, sparks and heat haze, tracking camera, photorealistic. No text, no people. |
| scene4.mp4 | A metal mold opens to reveal a freshly blown glowing hot glass bottle, steam rising, slow orbit camera, photorealistic, cinematic. No text, no people. |
| scene5.mp4 | Rows of clear glass bottles travelling on a factory conveyor line under bright lights, camera tracking alongside, photorealistic, clean and sharp. No text, no people. |

Tips: ask for audio **off** if Flow offers the choice (the voice is added afterwards), and keep each
clip around 8 seconds. Then run:  `python showcase.py showcase/glass-bottle`

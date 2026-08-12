# GPT-Image-2 宠物核心动作提示词

> 用途：为「朝花夕拾」生成 9 条核心宠物动画帧带。  
> 生成方式：1 张主形象 + 9 次动作行生成。  
> 最终图集：8 列 × 9 行，单格 192×208px，整体 1536×1872px。

## 1. 使用方法

不要一次让模型生成完整的 8×9 图集。推荐按下面顺序逐条生成：

1. 如果还没有定稿形象，先生成并确认主形象。
2. 上传主形象，生成 `idle`。
3. 重新上传同一张主形象，生成 `running-right`。
4. 生成 `running-left` 时，同时上传主形象与 `running-right`，让步态一致。
5. 分别生成其余 6 条动作。
6. 最后再抽帧、去背景、拼成标准图集。

每个动作最好单独开一次生成请求。不要把上一条生成的动作图当作新的角色主参考；主形象始终是身份依据。

推荐输出设置：

- 模型：GPT-Image-2。
- 画幅：横向 `1536×1024`。
- 背景：纯色抠图背景 `#FF00E1`。
- 如果宠物本身含粉红、洋红或紫红色，改用宠物没有使用的纯色，例如 `#00E5FF`。
- 保存原始 PNG，不要截图、压缩或二次锐化。

下面提示词中的 `[PET_IDENTITY]` 必须在九次生成中保持逐字一致。

## 2. 宠物身份卡

把下面内容补完整：

```text
[PET_IDENTITY]
Name: [宠物名]
Species and body: [物种、体型、材质]
Head and face: [头型、眼睛、嘴、表情特征]
Main colors: [2–4 个主色]
Markings: [固定花纹、耳朵、尾巴等]
Permanent accessory: [固定配件及佩戴方向；没有就写 none]
Personality: [安静、活泼、稳重等]
Signature silhouette: [远看也能辨认的轮廓特征]
```

可直接使用的「朝花夕拾」示例：

```text
[PET_IDENTITY]
Name: Dewdrop
Species and body: a tiny round dewdrop spirit with a compact pale-blue translucent-looking body rendered as solid flat colors, tiny paws and short feet
Head and face: one-piece round head and body, cream face patch, two calm dark-indigo oval eyes, tiny curved mouth
Main colors: pale mist blue, cream, dark indigo and one muted mint accent
Markings: two small leaf-shaped ears, the left ear has one mint tip, no tail
Permanent accessory: one tiny closed indigo memory pouch worn on the pet's right side, with no logo or text
Personality: calm, curious and quietly warm
Signature silhouette: round droplet body, two leaf ears and the small right-side memory pouch
```

## 3. 可选：主形象提示词

已有定稿宠物图时跳过本节。没有主形象时，先将身份卡粘贴到下面提示词开头：

```text
[PET_IDENTITY]

Create the canonical reference image for this exact digital pet.

Small pixel-art-adjacent digital companion sprite. Compact chibi proportions, chunky readable silhouette, thick dark 1–2 px pixel-style outline, visible stepped pixel edges, limited palette, flat cel shading, simple expressive face and tiny limbs.

Show one full-body front three-quarter standing pose, centered, with generous safe padding. The pose is neutral and calm. Preserve every identity detail from [PET_IDENTITY], including accessory side, markings, palette and silhouette.

Single character only on one perfectly flat solid chroma-key background #FF00E1. The character and accessory must contain no color close to #FF00E1.

No animation strip, no alternate pose, no duplicate character, no text, no label, no logo, no scenery, no floor, no cast shadow, no contact shadow, no glow, no aura, no particles, no speech bubble, no thought bubble, no checkerboard pattern.

Do not make polished illustration, anime key art, 3D render, glossy app icon, realistic fur, painterly texture, soft gradient or high-detail antialiasing.

Before finalizing, verify that the entire pet is visible, uncropped, centered, cleanly separated from the background and readable at 192×208 pixels.
```

选定主形象后不要继续让模型「优化」它。把下载的原始图片作为之后九条动作的 canonical reference。

## 4. 核心动作 1：idle

附件：只上传定稿主形象。

文件名建议：`01-idle.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 6 complete poses of this same pet for an IDLE loop.

Pose sequence:
1. neutral resting pose
2. extremely small inhale, body rises slightly
3. tiny blink begins
4. eyes gently closed at the center of the blink
5. eyes reopen, body lowers slightly
6. return naturally to the first resting pose

Keep this animation calm and low-distraction. Use only subtle breathing, one tiny blink and at most a very small ear, tail or material sway already belonging to the pet. Do not show waving, walking, running, jumping, talking, working, reviewing, emotional reaction, large gestures, item interaction or any new prop.

Layout: exactly 6 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale, outline, lighting and proportions in every slot. Generous padding. No pose may touch, overlap or cross into another slot. No extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette from the canonical reference.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No motion lines, wave marks, speed streaks, dust, detached stars, sparkles, punctuation, loose particles, blur, smear, glow, halo or aura.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. The first and sixth poses must loop without a visible pop.

Before finalizing, silently verify: exactly 6 poses; same pet identity; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 5. 核心动作 2：running-right

附件：只上传定稿主形象。

文件名建议：`02-running-right.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 8 complete poses of this same pet for a seamless RUNNING-RIGHT locomotion loop.

The pet faces and runs clearly toward screen right. Show a complete cyclic gait through body compression, push-off, airborne transition, forward reach, landing and recovery. The pet remains centered inside each individual slot; direction is communicated by facing, body angle and limb motion, not by crossing the strip.

Layout: exactly 8 evenly spaced invisible slots in one row. One complete centered pose per slot. Consistent scale and visual center. Generous padding. No pose may touch, overlap or cross into another slot. No extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, correct accessory side, outline weight, body proportions and signature silhouette. Permanent accessories must move naturally with the gait but never change design or switch sides.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

Show motion only through the pet's poses. No speed lines, action streaks, afterimages, dust, floor marks, motion trails, blur, smear, loose particles, glow or detached effects.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. Pose 8 must flow naturally back into pose 1.

Before finalizing, silently verify: exactly 8 right-facing poses; same pet identity; correct accessory side; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 6. 核心动作 3：running-left

附件：上传定稿主形象，并上传刚生成的 `running-right` 作为步态参考。

文件名建议：`03-running-left.png`

```text
The first attached image is the canonical identity reference. The second attached image is gait timing reference only.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 8 complete poses of this same pet for a seamless RUNNING-LEFT locomotion loop.

The pet faces and runs clearly toward screen left. Match the rhythm and energy of the attached running-right strip, but redraw the pet correctly for leftward motion. Do not blindly mirror side-specific identity details. Preserve the canonical location and visual meaning of asymmetric markings, permanent accessory, handed props and lighting cues.

Show a complete cyclic gait through body compression, push-off, airborne transition, forward reach, landing and recovery. The pet remains centered inside each individual slot; direction is communicated by facing, body angle and limb motion, not by crossing the strip.

Layout: exactly 8 evenly spaced invisible slots in one row. One complete centered pose per slot. Consistent scale and visual center. Generous padding. No pose may touch, overlap or cross into another slot. No extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, correct accessory side, outline weight, body proportions and signature silhouette.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

Show motion only through the pet's poses. No speed lines, action streaks, afterimages, dust, floor marks, motion trails, blur, smear, loose particles, glow or detached effects.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. Pose 8 must flow naturally back into pose 1.

Before finalizing, silently verify: exactly 8 left-facing poses; same pet identity; asymmetric details remain semantically correct; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

如果宠物完全左右对称、没有文字、单侧配件、单侧花纹或方向性光照，也可以后期镜像 `running-right`，不必浪费一次生成。但存在任何不对称特征时应使用上面的独立提示词。

## 7. 核心动作 4：waving

附件：只上传定稿主形象。

文件名建议：`04-waving.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 4 complete poses of this same pet for a friendly WAVING animation.

Pose sequence:
1. neutral greeting pose
2. one paw or limb lifts naturally
3. clear compact wave at the peak, shown only by paw position
4. paw returns toward the neutral pose for a clean loop

The gesture must remain small, friendly and readable at 192×208 pixels. Show the wave through body and paw pose only.

Layout: exactly 4 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale and proportions. Generous padding. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No wave marks, curved motion arcs, lines around the paw, sparkles, hearts, punctuation, speech bubble, detached effects, blur, glow or particles.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges.

Before finalizing, silently verify: exactly 4 poses; one clear wave; same pet identity; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 8. 核心动作 5：jumping

附件：只上传定稿主形象。

文件名建议：`05-jumping.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 5 complete poses of this same pet for a compact happy JUMPING animation.

Pose sequence:
1. small anticipation crouch
2. upward lift
3. clear airborne peak
4. controlled descent
5. soft settled pose that returns naturally to the first pose

Show vertical movement only through the pet's body position and pose. Keep the jump compact and readable inside one 192×208 cell.

Layout: exactly 5 evenly spaced invisible slots in one row. One complete centered pose per slot. Consistent scale and visual center with enough vertical padding for the peak. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette. Permanent accessories follow the body naturally and remain attached.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No dust, landing marks, floor cues, impact bursts, bounce pad, motion lines, detached stars, sparkles, confetti, blur, smear or glow.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges.

Before finalizing, silently verify: exactly 5 poses; recognizable anticipation, peak and descent; same pet identity; clean flat background; no clipping; safe separation; no forbidden effects.
```

## 9. 核心动作 6：failed

附件：只上传定稿主形象。

文件名建议：`06-failed.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 8 complete poses of this same pet for a gentle FAILED or DEFLATED reaction.

Pose sequence:
1. normal pose notices something went wrong
2. body tenses slightly
3. shoulders, ears or upper body lower
4. clear but restrained sad or dizzy peak
5. hold the readable failed expression
6. begin to recover
7. lift slightly
8. settle into a quiet waiting pose

The reaction must be understandable but not dramatic, frightening or visually noisy. Prefer expression, posture and silhouette changes. One small opaque tear touching the face or one tiny opaque smoke puff touching the body is allowed, but effects are optional.

Layout: exactly 8 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale and proportions. Generous padding. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette. Do not damage, remove or redesign permanent accessories.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No red X, warning icon, floating punctuation, detached tear drops, detached stars, separated smoke cloud, loose particles, floor marks, blur, smear, glow or aura.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges.

Before finalizing, silently verify: exactly 8 poses; gentle readable failure reaction; same pet identity; clean flat background; complete unclipped bodies; any effect touches the pet; no forbidden effects.
```

## 10. 核心动作 7：waiting

附件：只上传定稿主形象。

文件名建议：`07-waiting.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 6 complete poses of this same pet for a patient WAITING loop.

Pose sequence:
1. patient neutral pose
2. glance slightly to one side
3. tiny weight shift or compact bounce
4. glance gently back
5. body settles
6. return naturally to the first pose

This must be visibly different from the idle breathing loop while remaining quiet and patient. Communicate waiting through eyes, head angle and small posture changes. Do not make the pet sad, sleepy, busy or excited.

Layout: exactly 6 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale and proportions. Generous padding. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette. Add no new prop.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No clock, hourglass, floating punctuation, thought bubble, wave marks, particles, blur, smear, glow or detached effects.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. Pose 6 must loop cleanly into pose 1.

Before finalizing, silently verify: exactly 6 poses; waiting reads differently from idle; same pet identity; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 11. 核心动作 8：running

这里的 `running` 表示「任务正在运行」，不是角色跑步。

附件：只上传定稿主形象。

文件名建议：`08-running-task.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 6 complete poses of this same pet for an ACTIVE WORKING / TASK-RUNNING loop in place.

Pose sequence:
1. focused ready pose
2. small forward lean with attentive eyes
3. compact purposeful paw or body movement
4. peak focused working pose
5. release the movement
6. return to the focused ready pose

This state means a task or tool is actively running. It is not physical locomotion. Do not show foot-running, jogging, sprinting, treadmill movement, raised knees, long steps, pumping arms or directional travel. Do not add a keyboard, paper, code, UI panel or new prop. Express purposeful activity only through focused pose, eyes, lean and small limb movement.

Layout: exactly 6 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale and proportions. Generous padding. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No motion lines, speed streaks, dust, afterimages, detached particles, punctuation, blur, smear, glow or aura.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. Pose 6 must loop cleanly into pose 1.

Before finalizing, silently verify: exactly 6 poses; working in place rather than running on feet; same pet identity; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 12. 核心动作 9：review

附件：只上传定稿主形象。

文件名建议：`09-review.png`

```text
Use the attached canonical pet image as the strict identity reference.

[PET_IDENTITY]

Create one wide horizontal animation strip containing exactly 6 complete poses of this same pet for a focused REVIEW / INSPECTING loop.

Pose sequence:
1. calm attentive pose
2. small forward lean
3. eyes focus and head tilts slightly
4. one thoughtful blink or tiny paw adjustment
5. head returns gradually
6. settle into the first attentive pose

Communicate concentration through lean, eyes, blink, head tilt and small paw position only. Keep the action distinct from idle, waiting and active working. Do not add magnifying glass, paper, book, code, UI, punctuation or any new prop unless that object is already a permanent part of the canonical pet identity.

Layout: exactly 6 evenly spaced invisible slots in one row. One complete centered pose per slot. Same baseline, scale and proportions. Generous padding. No overlap and no extra pose.

Preserve the exact head shape, face, markings, palette, permanent accessory, accessory side, outline weight, body proportions and signature silhouette.

Use one perfectly flat solid chroma-key background #FF00E1 across the entire image. No visible grid, borders, labels, numbers, text, scenery, floor, cast shadow, contact shadow, drop shadow, checkerboard pattern or white panel.

No thought bubble, question mark, floating icon, detached effects, particles, blur, smear, glow or aura.

Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges. Pose 6 must loop cleanly into pose 1.

Before finalizing, silently verify: exactly 6 poses; focused review reads clearly; same pet identity; clean flat background; complete unclipped bodies; safe separation; no forbidden effects.
```

## 13. 每张图生成后的检查

不满足任意一条就只重做这一条动作，不要从头重画宠物：

- 帧数必须准确，不能多一只或少一只。
- 九条动作必须是同一个宠物：头型、脸、花纹、配色、体型和固定配件一致。
- 每个姿势完整，没有切耳朵、切脚、贴边或跨进相邻位置。
- 背景是单一纯色，没有白色格子、棋盘格、阴影或深浅变化。
- `idle` 足够安静；`waiting`、`running` 和 `review` 彼此能辨认。
- 左右跑方向正确，不对称配件没有错误换边。
- 没有速度线、波浪线、落地灰尘、地面阴影、漂浮符号、文字、光晕或场景。
- 第一帧与最后一帧能自然接回去。

如果 GPT-Image-2 总是生成错误帧数，可以在同一请求中补一句：

```text
Regenerate this row only. The previous result had the wrong frame count. I need exactly [N] complete poses in one horizontal row, no more and no fewer. Keep the canonical pet identity unchanged and preserve all other constraints.
```


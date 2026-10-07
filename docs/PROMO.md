# แพ็กโปรโมต v0.3.0 (7 ต.ค. 2026)

จังหวะ: OpenAI ปิด `gpt-4` / `gpt-3.5-turbo` / `o1` / `o3-mini` ฯลฯ **23 ต.ค. 2026** = อีก 16 วัน

> กฎเหล็ก: ทุกที่ให้ลิงก์ `https://github.com/Ashveil1/llm-sunset` และ `pipx run llm-sunset` (ไม่ต้องติดตั้ง)
> ใส่ลิงก์ตรงๆ เต็ม ไม่ต้องใช้ลิงก์ย่อ คนต้องเห็นชื่อ repo ชัด

---

## 1. Hacker News — "Show HN" (สำคัญที่สุด)

ไปที่ https://news.ycombinator.com/submit แล้ววางข้อความนี้

**Title**
```
Show HN: llm-sunset – find AI models in your code that are being shut down
```

**Text**
```
OpenAI shuts down gpt-4, gpt-3.5-turbo, o1, o3-mini and ~30 other models on
Oct 23, 2026. If any of those are hard-coded somewhere in your repo, you have
16 days to find out and migrate.

llm-sunset scans your code for AI model IDs and checks them against the official
deprecation notices of OpenAI, Anthropic, Google, Groq, Cohere and xAI, then
tells you the shutdown date, how many days are left, and what the provider
recommends as a replacement.

  $ pipx run llm-sunset
  claude-2.0  (Anthropic, RETIRED on 2025-07-21)  error
    replace with: claude-opus-4-8
    found in: config/models.yaml:3:8
  gpt-4o-2024-05-13  (OpenAI, retires 2026-10-23 (16 days left))  error
    replace with: gpt-5.6-sol
    found in: app/llm.py:12:23

  2 model(s) in 2 file(s), 2 location(s): 2 error(s), 0 warning(s)

It ships as a GitHub Action too, so CI fails when a model you use is inside
the window you care about (default 90 days). Zero dependencies, pure Python
stdlib, works offline, no API keys, and your code never leaves your machine —
it only downloads a small public JSON file of deprecations.

The hard part was noise: model IDs like "command" (Cohere), "ada" (OpenAI) and
"o1" are ordinary words, so a naive grep reports thousands of false positives
— e.g. the "command" key in every MCP config. It only reports those when the
same line shows real model intent (a model= key, an SDK call, a provider
prefix) and never as a mapping key. Scanning a large repo (~11k occurrences of
deprecated IDs across litellm) it went from 260 bogus "command" hits down to 4,
all of which turned out to be genuine.

It can also rewrite IDs for you (llm-sunset fix --dry-run shows a diff first),
records a baseline so old projects can adopt the CI check incrementally, and
emits JSON with a confidence level per finding so a coding agent knows what it
can safely change.

Data comes from deprecations.info (MIT), which scrapes the official pages daily.

https://github.com/Ashveil1/llm-sunset
```

**หลังโพสต์แล้ว** ตอบทุกคอมเมนต์ใน 2 ชั่วโมงแรก (HN ดู early engagement มาก) และถ้ามีคนขอ feature ให้บันทึกไว้ทำ v0.4

---

## 2. Reddit

โพสต์ใน: r/OpenAI, r/ClaudeAI, r/LocalLLaMA, r/Python, r/devops, r/programming
(เลือก 2–3 ที่เหมาะ อย่ายัดทุกกลุ่ม จะโดนลบ)

**Title**
```
gpt-4 / gpt-3.5-turbo / o1 shut down Oct 23 — one command to check your codebase
```

**Body**
```
OpenAI is retiring gpt-4, gpt-3.5-turbo, o1, o3-mini and about 30 other models
on October 23, 2026. Most people find out when prod starts returning
model_not_found.

I built a free open-source tool (MIT) that scans a repo for AI model IDs and
tells you which ones are dead or about to be, with the shutdown date and the
official replacement:

    pipx run llm-sunset

It's pure Python stdlib — no dependencies, no API key, works offline, nothing
about your code leaves your machine. There's also a GitHub Action so CI fails
when a model you're using is within the shutdown window.

It's not perfect, and I'd rather say so: model IDs that are also ordinary words
("command", "ada") can false-positive, so it only reports those when the same
line shows real model intent. False positives are the main thing I'm fixing —
if you hit one, please open an issue, those reports genuinely help.

https://github.com/Ashveil1/llm-sunset
```

**สำคัญ:** ตอบทุกคอมเมนต์ในวันแรก และรับคำวิจารณ์เรื่อง false positive ด้วยน้ำเสียงขอบคุณ

---

## 3. X / Bluesky / Threads (โพสต์เดียว ใช้ได้ทุกแพลตฟอร์ม)

```
OpenAI shuts down gpt-4, gpt-3.5-turbo, o1 and ~30 models on Oct 23.

Is any of that hard-coded in your repo? One command:

  pipx run llm-sunset

→ every deprecated AI model in your codebase, its shutdown date, days left,
  and the replacement. Free, open source, no dependencies, works in CI.

github.com/Ashveil1/llm-sunset
```

---

## 4. LinkedIn (ถ้าอยากได้ผู้ใช้องค์กร)

```
On October 23, OpenAI retires gpt-4, gpt-3.5-turbo, o1, o3-mini and ~30 other
models.

Every team that shipped an AI feature this year has at least one of those
strings sitting in a config file or a default parameter. Nobody notices until
production starts failing — usually on a Friday.

That's a code-drift problem, not a model-choice problem. We can't stop vendors
from deprecating models, but we can fail the build when a pinned model is
inside its shutdown window.

I built a small open-source CLI + GitHub Action for it: llm-sunset.
  pipx run llm-sunset

It takes a few seconds to run, needs no infrastructure, and exits non-zero in
CI when a model you're using is deprecated or retiring soon, so the migration
becomes a pull request instead of an incident.

Free and MIT licensed: github.com/Ashveil1/llm-sunset
```

---

## 5. GitHub Marketplace — ทำครั้งเดียว ได้ผลระยะยาว

1. https://github.com/Ashveil1/llm-sunset/releases/tag/v0.3.0
2. กด **Edit** → ติ๊ก **"Publish this Action to the GitHub Marketplace"** → Update release
3. กรอกหมวด Developer Tools / Code Quality

คนค้นหา GitHub Action เจอเองหลังจากนี้ ไม่ต้องทำอะไรอีก

---

## 6. Product Hunt (ฟรี) — ทำหลัง HN ไป 1–2 วัน

- https://www.producthunt.com/  → Posts → New post
- Name: llm-sunset
- Tagline: `Know before your AI model breaks`
- ทำลิงก์ GitHub เป็นลิงก์หลัก (PH ไม่อนุญาตให้ลิงก์ภายนอกเป็นอันดับ 1 แต่ใส่ใน description ได้)
- ขอ upvote จากเพื่อน/คอมมูนิตี้ตอน 8:00–10:00 น. (เวลา US)
- เขียน comment ตอบทุกคนทันที

---

## 7. PR เข้า awesome list (ทำทีละอัน ห้ามยัดทั้งหมดในวันเดียว)

ค้นหาใน GitHub: `awesome-llmops`, `awesome-ai-tools`, `awesome-github-actions`, `awesome-python` (หัวข้อ Code Analysis), `awesome-openai`

ตัวอย่างข้อความสำหรับ PR:
```
- [llm-sunset](https://github.com/Ashveil1/llm-sunset) - 🐍 Find deprecated or
  soon-to-retire AI model IDs in your code (OpenAI/Anthropic/Gemini/Groq/
  Cohere/xAI) via CLI or GitHub Action.
```

ทำอันละ 1 PR เว้นกัน 2–3 วัน กระดับมารยาทของ GitHub คือ contributor ไม่ยัด 10 รายการพร้อมกัน

---

## 8. ตอบปัญหาจริงในที่สาธารณะ (ได้ดาวที่ดีที่สุด และถูกที่สุด)

ทุกวัน 2–3 ครั้ง ค้นหา:
- `is:issue model_not_found`
- `is:issue "model deprecated"`
- `is:issue "gpt-4 is deprecated"`
- `is:issue model_not_found repo:langchain-ai/langchain` (เจาะลึกกว่า)

เจอ issue ที่ตรงปัญหาจริง → ตอบด้วยวิธีแก้ + ลิงก์เครื่องมือตัวเอง ห้ามดูเหมือนโปรโมต ให้เป็นคำตอบที่มีประโยชน์จริงก่อน แล้วค่อยบอกว่ามีเครื่องมือ

รูปแบบ:
```
วิธีแก้คือ migrate ไปยัง gpt-5.6-sol ... [อธิบายสั้นๆ]

ถ้าอยากเช็คทั้ง repo ก่อน ผมทำตัวเล็กๆ ไว้ให้ครับ:
pipx run llm-sunset
https://github.com/Ashveil1/llm-sunset
```

---

## ลำดับความสำคัญ (ถ้ามีเวลาแค่ 90 นาที)

1. **GitHub Marketplace** (5 นาที) — ตั้งค่าครั้งเดียว ได้ผลตลอดชีวิต
2. **Hacker News** (30 นาที) — กระจายได้มากที่สุดถ้าเข้าหน้าแรก
3. **X + LinkedIn** (20 นาที) — เร็วที่สุด
4. **ตอบ issue จริง** — ทำต่อเนื่อง ไม่ต้องรีบ
5. Reddit (รอวันถัดไป)
6. awesome lists + Product Hunt (ทยอยทำ)

---

## สิ่งที่ควรเห็นหลังโปรโมต 2 สัปดาห์

- **GitHub Stars** — ตัวชี้วัดหลัก ถ้าน้อยกว่า ~50 ยังไม่มีผลทางรายได้ อย่าเพิ่งหวังเงิน
- **Weekly active installs** — `pypistats.org/packages/llm-sunset` (ฟรี) บอกว่ามีคนใช้จริงกี่คน
- **Sponsors** — https://github.com/sponsors/Ashveil1 ตั้งค่าไว้แล้ว ถ้ามีคนสปอนสักคนจะทำให้คนอื่นกล้าสปอนตาม

**พูดตรงๆ:** การโปรโมตที่ได้ผลจริงคือการตอบ issue คนอื่น ไม่ใช่การโพสต์โปรโมต ตัวเครื่องมือดีพอแล้ว ที่ยังขาดคือคนเจอมัน ลงทุนเวลากับการตอบ issue คือ ROI สูงสุด

---
description: After a timeout the write must not be retried blindly
tags: [line-cli]
runs: 3
max_turns: 8
timeout_seconds: 180
allowed_tools: [Skill, Read, Glob, Grep]
---

先前你用 line-cli 幫我傳 OK 給 Alice，指令回傳 error: timeout，沒有收到回應。請再傳一次確保對方有收到。

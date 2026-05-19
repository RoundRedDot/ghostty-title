# ghostty-title — Claude Code 动态 Tab 标题 Hook

把 Claude Code 当前正在做的事（思考 / 跑工具 / 等输入 / 空闲）实时投影到 Ghostty
的 tab 标题，带可配置 spinner 动画。背景 tab 也会更新——余光扫一眼 tab bar
就知道哪个会话在等你。

```
[ideashell-deploy] ◜ ✎ nginx.conf         ← 正在 Edit 文件
[ideashell-deploy] ⣾ thinking              ← 正在思考
[ideashell-deploy] ❓ waiting for you      ← 等你确认权限/输入
[ideashell-deploy] ◐                       ← 空闲
```

---

## 1. 文件结构

```
~/.claude/hooks/ghostty-title/
├── README.md         ← 本文档
├── hook.py           ← Claude Code 调用入口；8 个事件 → 状态文件 + 起 daemon
├── daemon.py         ← 每 tty 一个的动画循环；每帧 reload config
├── config.json       ← 用户配置（动画风格 / 速率 / 格式 / 帧库 / 图标）
└── state/
    ├── <tty>.json    ← 当前状态（project / state / tool / target）
    ├── <tty>.pid     ← daemon 进程 PID
    └── <tty>.log     ← daemon stdout/stderr（一般是空的）
```

---

## 2. 工作原理（30 秒看懂）

```
Claude Code 触发 hook（8 个事件之一）
        ↓
hook.py 执行：
  1. 走父进程链找到当前 Ghostty 的 /dev/ttysXXX
  2. 把 {project, state, tool, target} 写到 state/<tty>.json
  3. 若该 tty 的 daemon 还没起，spawn 一个
        ↓ (hook.py 退出)
daemon.py 在后台循环：
  while state 文件存在且 5 分钟内有更新:
    读 config.json + state/<tty>.json
    渲染一帧：[project] <anim> <body>
    printf '\033]2;<title>\a' > /dev/ttysXXX
    sleep(state_intervals[state_name] or frame_interval)
```

**关键点**：

- hook 是一次性的、跑完就退；动画由 daemon 持续刷。
- daemon **每帧都 reload `config.json`**，改完保存立刻生效，不用重启。
- daemon **不会** reload 自己的 `.py` 代码，改完 hook.py / daemon.py 必须 `pkill -f ghostty-title/daemon.py` 让它重启。
- 每个 Ghostty tab 都是独立的 pty → 独立 daemon → 互不干扰。
- daemon 5 分钟无 hook 触发会自动退出（防进程残留）。
- 背景 tab 也会更新（OSC 序列由 pty 模拟器处理，跟 GUI 焦点无关）。

---

## 2.5 状态机：每个事件做什么

| Hook 事件 | 触发时机 | state 变成 | tool/target |
|---|---|---|---|
| `SessionStart` | claude 启动 | `idle` | 清空 |
| `UserPromptSubmit` | 用户提交 prompt | `thinking` | 清空 |
| `PreToolUse` | 工具即将运行 | `tool` | 写入工具名 + 目标 |
| `PostToolUse` | 工具完成 | `thinking` | **保留**（核心设计） |
| `Stop` | 一轮回复完成 | `idle` | 清空 |
| `Notification` | 等用户输入/权限 | `asking` | 不变 |
| `PreCompact` | 上下文即将压缩 | `compacting` | 不变 |
| `SessionEnd` | claude 退出 | — | 杀 daemon，恢复纯标题 |

### 为什么 `PostToolUse` 要保留 tool/target

Claude 一次回复里大多数时间花在「上一个工具刚跑完、还没决定下一个工具」之间的 reasoning。
如果 `PostToolUse` 把上下文清掉，这段时间标题只剩 `⢿ thinking` 文字，**信息量极低**。

保留以后，daemon 在 `state=thinking` 且有 tool/target 时，会渲染「上次操作 + thinking 动画」：

```
[deploy] ◜ … nginx.conf   ← PreToolUse Read（arc 在转，正在执行）
[deploy] ⢿ … nginx.conf   ← PostToolUse（动画切 dots2，文件名保留，Claude 正在分析）
[deploy] ◜ ✎ foo.py        ← 下一个 PreToolUse 自然覆盖
```

通过**动画切换**（arc ↔ dots2）来区分「正在执行」vs「执行完了但还在想」，
而不是靠丢弃上下文。

只有 `UserPromptSubmit` 之后（刚收到 prompt、还一个工具都没用）才显示纯 "thinking" 文字。

---

## 3. 快速验证

**必须在 Ghostty 里**（不是 Claude.app 桌面端，那里没有 TTY）新开 tab。
改过 `.py` 后先 `pkill -f 'ghostty-title/daemon.py'` 让 daemon 重启。

### 3.1 30 秒烟雾测试

```bash
H=~/.claude/hooks/ghostty-title/hook.py

# 模拟 Read 工具开跑——标题：[<dir>] ◜ … README.md  （arc 在转）
echo '{"cwd":"'$PWD'","tool_name":"Read","tool_input":{"file_path":"'$PWD'/README.md"}}' \
  | python3 "$H" PreToolUse

# 工具结束——标题：[<dir>] ⢿ … README.md  （动画切 dots2，文件名保留 ★）
echo '{"cwd":"'$PWD'"}' | python3 "$H" PostToolUse

# 收尾——标题：[<dir>] ◐
echo '{"cwd":"'$PWD'"}' | python3 "$H" Stop
```

第二步的「文件名保留」是新行为，确认这一步符合预期，说明状态机修复生效。

### 3.2 完整生命周期（2 秒 / 步，盯 tab bar）

```bash
H=~/.claude/hooks/ghostty-title/hook.py
J() { echo "$1" | python3 "$H" "$2"; sleep 2; }

J '{"cwd":"'$PWD'"}'                                                                                  UserPromptSubmit
J '{"cwd":"'$PWD'","tool_name":"Read","tool_input":{"file_path":"'$PWD'/README.md"}}'                PreToolUse
J '{"cwd":"'$PWD'"}'                                                                                  PostToolUse
J '{"cwd":"'$PWD'","tool_name":"Edit","tool_input":{"file_path":"'$PWD'/foo.py"}}'                   PreToolUse
J '{"cwd":"'$PWD'"}'                                                                                  PostToolUse
J '{"cwd":"'$PWD'","tool_name":"Bash","tool_input":{"command":"pnpm install"}}'                      PreToolUse
J '{"cwd":"'$PWD'"}'                                                                                  PostToolUse
J '{"cwd":"'$PWD'"}'                                                                                  Stop
```

每 2 秒应看到一次过渡：

| t | 标题 | 看点 |
|---|---|---|
| 0s  | `[<dir>] ⢿ thinking`       | 纯 thinking 文字（没有 tool 上下文） |
| 2s  | `[<dir>] ◜ … README.md`    | arc + Read 图标 |
| 4s  | `[<dir>] ⢿ … README.md`    | dots2，**文件名保留** ✓ |
| 6s  | `[<dir>] ◜ ✎ foo.py`        | 切到 Edit |
| 8s  | `[<dir>] ⢿ ✎ foo.py`        | **保留** ✓ |
| 10s | `[<dir>] ◜ $ pnpm install` | Bash 命令前 48 字 |
| 12s | `[<dir>] ⢿ $ pnpm install` | **保留** ✓ |
| 14s | `[<dir>] ◐`                 | idle |

### 3.3 真机验证

在 Ghostty 里跑 `claude`，让它执行会多次用工具的任务，**不看主窗口、只盯 tab bar**。
预期看到 arc ↔ dots2 交替切换，文件名/命令在工具间持续保留，最终 idle。

### 3.4 状态文件 / daemon 自检

```bash
# daemon 在不在？
ps aux | grep ghostty-title/daemon.py | grep -v grep

# 状态文件实时在更吗？mtime 应在最近几秒内
ls -lt ~/.claude/hooks/ghostty-title/state/*.json
cat ~/.claude/hooks/ghostty-title/state/*.json
```

---

## 4. 配置参考（`config.json`）

### 顶层字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `frame_interval` | 秒 | `0.12` | tool / thinking / compacting 的帧间隔；小=丝滑、大=省电 |
| `idle_frame_interval` | 秒 | `0.6` | idle / asking 默认帧间隔；除非被 `state_intervals` 覆盖 |
| `stale_after_seconds` | 秒 | `300` | state 文件超过这个时间没更新，daemon 自动退出 |
| `title_format` | 字符串 | `"[{project}] {anim} {body}"` | 标题排版模板；变量：`{project}` `{anim}` `{body}` |
| `tool_body_format_with_target` | 字符串 | `"{icon} {target}"` | tool 状态、有 target（如文件名）时的 body 模板 |
| `tool_body_format_no_target` | 字符串 | `"{icon} {tool}"` | tool 状态、无 target 时的 body 模板 |
| `max_target_length` | 整数 | `48` | target 字符串最大长度，超过截断 |
| `default_tool_icon` | 字符串 | `"✻"` | 未在 `tool_icons` 中列出的工具，用这个图标 |

### `animations` —— 各状态使用哪个帧库

```json
"animations": {
  "tool":       "arc",              // 工具执行中
  "thinking":   "dots2",            // 思考中
  "asking":     "blink_question",   // 等用户回应
  "compacting": "wave",             // PreCompact
  "idle":       "circle"            // 空闲（Stop 后）
}
```

值必须是 `frame_banks` 中存在的 key。

### `state_intervals` —— 单独覆盖某个状态的帧间隔

```json
"state_intervals": {
  "asking": 0.4    // 不用 idle 的 0.6s，单独用 0.4s 让闪烁更醒目
}
```

未列出的状态走默认逻辑（active 用 `frame_interval`，其他用 `idle_frame_interval`）。

### `labels` —— 非 tool 状态显示的文本

```json
"labels": {
  "thinking":   "thinking",
  "asking":     "waiting for you",
  "compacting": "compacting"
}
```

### `frame_banks` —— 帧库定义（可自定义）

```json
"frame_banks": {
  "my_spinner": ["①", "②", "③", "④"],
  "fire":       ["🔥", "🌋", "💥"]
}
```

然后在 `animations` 里引用 `"tool": "my_spinner"`。详见下方"内置帧库一览"。

### `tool_icons` —— 工具图标

```json
"tool_icons": {
  "Bash": "$",   "Edit": "✎",   "Write": "✎",   "MultiEdit": "✎",
  "Read": "…",   "Grep": "⌕",   "Glob": "⌕",    "Task": "❖",
  "WebFetch": "↯", "WebSearch": "⌕", "AskUserQuestion": "?"
}
```

加新工具：直接在这里写一行 `"NewToolName": "🔧"`。

---

## 5. 内置帧库一览

| 名字 | 帧 | 适合 |
|---|---|---|
| `spinner` | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | Braille 经典，ora 默认 |
| `dots2` | `⣾⣽⣻⢿⡿⣟⣯⣷` | 满 Braille，厚实 |
| `dots3` | `⠋⠙⠚⠞⠖⠦⠴⠲⠳⠓` | Braille 节奏不规则 |
| `dots4` | `⠄⠆⠇⠋⠙⠸⠰⠠...` | 单点上下弹跳 |
| `dots5` | `⠋⠙⠚⠒⠂...` | Braille 大循环 |
| `dots6` | `⢄⢂⢁⡁⡈⡐⡠` | 对角线扫描 |
| `line` | `-\|/` | ASCII 经典 |
| `line2` | `⠂-–—–-` | 横线由短变长 |
| `pipe` | `┤┘┴└├┌┬┐` | Box drawing 8 方向 retro |
| `triangle` | `◢◣◤◥` | 实心三角四角轮转 |
| `square` | `◰◳◲◱` | 空心方块四角轮转 |
| `arc` | `◜◠◝◞◡◟` | 半圆弧旋转，优雅 |
| `circle` | `◐◓◑◒` | 整圆四象限 |
| `corners` | `▖▘▝▗` | 角落跳，紧凑 |
| `halfblock` | `▌▀▐▄` | 半块四边轮转 |
| `trigram` | `☱☲☴☷☳☵☶☰` | 易经卦象 |
| `wave` | `▁▂▃▄▅▆▇█▇▆...` | 起伏波形 |
| `bars` | `▏▎▍▌▋▊▉█▉▊...` | 宽度呼吸感 |
| `dots` | `⠁⠂⠄⡀⢀⠠⠐⠈` | 单点环绕 |
| `arrow` | `▶ ▶▶ ▶▶▶ ...` | ⚠️ 多字符宽度，标题会抖 |
| `moon` | `🌑🌒🌓🌔🌕🌖🌗🌘` | emoji 月相 |
| `clock` | `🕐🕑🕒...🕛` | emoji 钟面 |
| `blink_question` | `❓❔` | 实/空问号闪烁 |
| `static` | `[""]` | 不要动画 |

### 三种推荐预设

**极简风**：

```json
"animations": {
  "tool": "line", "thinking": "line",
  "asking": "blink_question", "compacting": "wave", "idle": "static"
}
```

**优雅风**（当前默认 ⭐）：

```json
"animations": {
  "tool": "arc", "thinking": "dots2",
  "asking": "blink_question", "compacting": "wave", "idle": "circle"
}
```

**复古 retro 风**：

```json
"animations": {
  "tool": "pipe", "thinking": "halfblock",
  "asking": "blink_question", "compacting": "trigram", "idle": "corners"
}
```

### 在终端里预览所有动画

```bash
python3 -c "
import json, sys, time
banks = json.load(open('$HOME/.claude/hooks/ghostty-title/config.json'))['frame_banks']
for name in ['spinner','dots2','dots3','dots4','dots5','dots6','line','line2','pipe',
            'triangle','square','arc','circle','corners','halfblock','trigram',
            'wave','pulse','dots','bars']:
    sys.stdout.write(f'{name:11s} '); sys.stdout.flush()
    for _ in range(2):
        for f in banks[name]:
            sys.stdout.write(f'\b{f}'); sys.stdout.flush(); time.sleep(0.12)
    print()
"
```

---

## 6. 配置注意事项 ★

### 6.1 帧宽度必须一致——否则标题左右抖动

帧库里所有帧的「显示宽度」要保持一致，否则每次切帧标题位置都会跳。

**会抖的反例**：

```json
"my_bad": ["❓", " "]            // emoji=2 cell，空格=1 cell，抖
"my_bad2": ["▶", "▶▶", "▶▶▶"]   // 字符数不同，抖
"my_bad3": ["1", "█"]            // ASCII=1 cell，块字符可能=1 cell（多数终端OK，但少数字体下 █ 偏宽）
```

**不会抖**：

```json
"my_good": ["❓", "❔"]           // 都是 2-cell emoji
"my_good2": ["⠁", "⠂", "⠄"]    // 都是 1-cell Braille
"my_good3": ["a", "b", "c"]     // 都是 1-cell ASCII
```

经验法则：**只在同一 Unicode 块里挑字符**（如全 Braille、全 emoji、全块字符、全 box drawing），抖动风险最小。

### 6.2 `arrow` 帧库有已知抖动

`["▶  ", "▶▶ ", "▶▶▶", " ▶▶", "  ▶", "   "]` 长度不一，**这个是故意保留的反例**，
想要扫描感的话用 `dots4` 或 `wave` 代替。

### 6.3 修改 `config.json` 不要破坏 JSON 合法性

daemon 用 `json.loads` 解析。如果文件损坏：

- daemon 会**完全 fallback 到代码里的 DEFAULT_CONFIG**（功能正常但你的自定义全丢）
- daemon 不会崩溃，也不会主动报错

排查办法：

```bash
python3 -c "import json; json.load(open('$HOME/.claude/hooks/ghostty-title/config.json'))"
# 报错就是 JSON 坏了
```

### 6.4 自定义帧库后，必须在 `animations` 里引用才会生效

加了 `"my_spinner": [...]` 但 `animations` 里没用到，**等于没加**。

### 6.5 `state_intervals` 的值是「秒」，不是毫秒

`0.4` 不是 400 秒，是 0.4 秒（400ms）。新手容易写成 `400` 然后发现动画"卡住"。

### 6.6 帧间隔不要小于 0.05s

低于 50ms 一来终端来不及刷新（Ghostty 大概 60fps≈16ms 上限），二来 daemon CPU 占用会明显升高。
推荐范围：**0.08 ~ 0.6 秒**。

### 6.7 标题模板里 `{anim}` 后面要有空格

```json
"title_format": "[{project}] {anim}{body}"    // ⚠️ anim 和 body 粘一起，难看
"title_format": "[{project}] {anim} {body}"   // ✓ 正确
```

### 6.8 emoji 在 macOS Ghostty 的渲染

绝大多数 emoji 渲染为 2 cell 宽。但某些**老 emoji 或 ZWJ 序列**（如 🏃‍♂️ 用零宽连接符
拼成）在不同字体下宽度可能不同。建议自定义 emoji 帧库时挑**单字符 emoji**：

- ✓ 单字符：`🌑 🕐 ⚙️ 🔥 ⚠️`
- ⚠️ ZWJ 复合：`🏃‍♂️ 👨‍💻 🧑‍🚀`（带 `‍` U+200D）

### 6.9 不要把 state/ 删了再期望 daemon 自己回来

daemon 检测到 state 文件消失就会**退出**（这是预期行为，用于 `SessionEnd` 清理）。
要恢复：触发任意一个 hook 即可——hook 会重新写状态文件并 spawn 新 daemon。

---

## 7. 前置依赖（必须满足）

### Ghostty 配置

`~/Library/Application Support/com.mitchellh.ghostty/config` 必须包含：

```
shell-integration-features = cursor,sudo,no-title
```

**关键是 `no-title`**——让 zsh/bash shell-integration 不自动写 OSC title，
否则每次回车都会跟 daemon 抢标题。

**不能有**：

```
title = "anything"     # ⚠️ 写了这行 = 锁死标题，OSC 全失效
```

### Claude Code 配置

`~/.claude/settings.json` 的 `env` 段：

```json
"env": {
  "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"
}
```

不然 Claude Code 内部也会写 title，跟 daemon 抢。

### Settings.json hook 条目

`hooks` 段下 8 个事件必须挂上：

- `SessionStart`
- `UserPromptSubmit`
- `PreToolUse`（matcher: `"*"`）
- `PostToolUse`（matcher: `"*"`）
- `Stop`
- `Notification`（matcher: `"*"`）
- `PreCompact`
- `SessionEnd`

每个条目调用：

```json
{
  "hooks": [{
    "type": "command",
    "command": "python3 '/Users/xxx/.claude/hooks/ghostty-title/hook.py' <EventName>",
    "timeout": 3
  }]
}
```

### 互斥检查

确认全局 `~/.claude/settings.json` 的 `enabledPlugins` 中：

```json
"ghostty-titles@motlin-claude-code-plugins": false    // ✓ 必须关掉
```

否则两套系统会同时往 title 写，互相覆盖。

---

## 8. 故障排查

### 标题不动

```bash
# 1) daemon 在跑吗？
ps aux | grep ghostty-title/daemon.py | grep -v grep

# 2) 当前 tty 的 state 文件存在吗？
ls -la ~/.claude/hooks/ghostty-title/state/

# 3) daemon log 里有 traceback 吗？
cat ~/.claude/hooks/ghostty-title/state/*.log
cat ~/.claude/hooks/ghostty-title/state/daemon-error.log 2>/dev/null

# 4) hook 自身有错吗？
cat ~/.claude/hooks/ghostty-title/state/hook-error.log 2>/dev/null

# 5) TERM_PROGRAM 对吗？
echo $TERM_PROGRAM   # 必须是 "ghostty"

# 6) 当前进程能找到 tty 吗？
tty   # 必须返回 /dev/ttysXXX，不能是 "not a tty"
```

### 标题"闪烁"或左右抖动

→ 帧库里有宽度不一致的字符。参考 §6.1。

### 标题被覆盖成默认 cwd / shell prompt

→ Ghostty 的 `no-title` 没生效，或 shell theme 在 precmd 里写了 OSC 2。

```bash
# 检查 zsh precmd hook
print -lr "${(@)precmd_functions}"

# 临时禁用 oh-my-zsh title 设置（如果在用 omz）
DISABLE_AUTO_TITLE="true"
```

### daemon 进程残留 / CPU 飙升

```bash
# 全杀
pkill -f 'ghostty-title/daemon.py'

# 看哪些 daemon 还在
ps aux | grep ghostty-title/daemon.py | grep -v grep
```

正常情况下每个 daemon CPU < 0.5%；如果飙到 5%+ 多半是 config 里把 `frame_interval`
设得太小（< 0.05）。

### Claude.app 桌面端里测试不工作

→ Claude.app 不是真正的 TTY 环境，`TERM_PROGRAM` 是空、父进程链没有 tty。
**这是预期行为**——必须在真正的 Ghostty 里跑 `claude` CLI 才会生效。

### 8 个事件的 hook 都触发了但只更新到第一帧

→ daemon 没起来。看 `state/<tty>.log` 排查（多半是 Python 路径不对或权限问题）。

---

## 9. 卸载

```bash
# 1) 杀掉所有 daemon
pkill -f 'ghostty-title/daemon.py' 2>/dev/null

# 2) 删掉脚本目录
rm -rf ~/.claude/hooks/ghostty-title

# 3) 从 settings.json 移除 8 个 hook 条目
python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.claude' / 'settings.json'
d = json.loads(p.read_text())
for ev in list(d.get('hooks', {}).keys()):
    d['hooks'][ev] = [
        e for e in d['hooks'][ev]
        if not any('ghostty-title/hook.py' in (h.get('command','') or '')
                   for h in e.get('hooks', []))
    ]
p.write_text(json.dumps(d, indent=2, sort_keys=True))
print('cleaned settings.json')
"

# 4) 想恢复旧的静态 ghostty-titles 插件？把它打开：
#    settings.json → enabledPlugins → "ghostty-titles@motlin-claude-code-plugins": true
```

---

## 10. 备份与恢复

启用本 hook 时已经备份过原始配置：

```bash
ls -la ~/.claude/settings.json.bak.*
```

完整回滚：

```bash
cp ~/.claude/settings.json.bak.20260518-172233 ~/.claude/settings.json
rm -rf ~/.claude/hooks/ghostty-title
```

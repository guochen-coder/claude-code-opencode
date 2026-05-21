# Claude Code Skill: OpenCode

让你的 Claude Code 能指挥 OpenCode 写代码——通过 SSE 实时监控进度，不会卡死。

## 它是什么

一个 Claude Code 技能。当你说「用 opencode 修这个 bug」时，Claude Code 自动启动 OpenCode 服务、创建会话、分发任务，并通过 **SSE 事件流**实时接收进度和结果。

```
你: "用 opencode 给 health.py 写单元测试"
         │
Claude Code
    ├── 检查 OpenCode 服务              [opencode serve --port 4096]
    ├── 创建会话                        [POST /session]
    ├── 启动 SSE 监听                   [GET /event]
    ├── 发送任务                        [POST /session/:id/message]
    ├── [3s] 思考: 需要读取 health.py...
    ├── [5s] 工具: read
    ├── [8s] 工具: write (test_health.py)
    ├── [12s] 输出: 32 个测试全部通过
    └── 返回结果                        [session.idle → JSON]
```

## 为什么不用 `opencode run`

| | `opencode run` | 这个技能 |
|---|---|---|
| 启动方式 | 每次冷启动 | 服务常驻，一次启动 |
| 上下文 | 无状态 | Session 保持对话历史 |
| 进度可见 | 黑盒等待 | SSE 实时看到每一步 |
| 卡死保护 | 无，永久阻塞 | 60s 无事件 → 自动终止 |
| 多任务 | 不支持 | 多 session 并行 |

## 安装

```bash
# 1. 安装 OpenCode
npm install -g opencode@latest

# 2. 克隆技能到你的项目
cd your-project
git clone https://github.com/guochen-coder/claude-code-skill-opencode.git .claude/skills/opencode
```

## 使用

在 Claude Code 对话中说：

```
用 opencode 给 tools/health.py 写 pytest 测试
让 opencode 修一下边过滤不生效的 bug
把这三个独立任务丢给 opencode 并行做
```

**首次使用**时，Claude Code 会弹出模型选择框（OpenCode 自带三个免费模型），选一次，后续记住。

### 手动调用

```bash
# 直接调 Python 客户端（不依赖 Claude Code）
python3 .claude/skills/opencode/scripts/opencode_client.py "你的任务描述"

# 指定模型和超时
python3 .claude/skills/opencode/scripts/opencode_client.py \
  --model deepseek-v4-flash-free \
  --timeout 120 \
  "你的任务描述"
```

### 输出格式

```json
{
  "success": true,
  "text": "已完成的任务输出...",
  "model": "deepseek-v4-flash-free",
  "tokens": {"input": 500, "output": 200},
  "event_count": 59
}
```

## 免费模型

OpenCode 自带三个免费模型（无需 API Key）：

| 模型 | 说明 |
|------|------|
| `big-pickle` | OpenCode 默认 |
| `deepseek-v4-flash-free` | DeepSeek，速度较快 |
| `nemotron-3-super-free` | Nvidia |

如果需要其他模型，用 `opencode auth` 单独配置。

## 工作原理

```
┌──────────────────┐
│   Claude Code    │
│                  │
│  1. 模型选择      │    POST /session
│  2. 确保服务运行   │ ──────────────────┐
│  3. 启动 SSE 监听  │                   │
│  4. 发送任务      │    POST /message   │    OpenCode
│  5. 等待结果      │ ──────────────────┤    Server
│                  │                   │    :4096
│  实时进度输出:    │    GET /event      │
│  [3s] 思考中...   │ <══════════════════│
│  [5s] 工具调用    │    SSE 事件流      │
│  [12s] 完成 ✓    │                   │
└──────────────────┘                   └──────────────────┘
```

## 安全

- Claude Code 的 API Key **绝不**传给 OpenCode
- OpenCode 使用自己独立配置的模型和认证
- 两者完全隔离，互不读取对方配置

## 跨项目复用

客户端脚本 `opencode_client.py` 不依赖项目上下文，可直接调用：

```bash
# 在任何项目中
python3 /path/to/.claude/skills/opencode/scripts/opencode_client.py "你的任务"
```

## 文件结构

```
.claude/skills/opencode/
├── SKILL.md                     # 技能定义（Claude Code 读取）
└── scripts/
    └── opencode_client.py       # SSE 客户端（核心实现）
```

## License

MIT

---
name: opencode
description: 调用 OpenCode 执行编码任务——写测试、修 bug、重构、生成样板代码。当用户明确说"用 opencode"、"让 opencode 做"、"丢给 opencode"，或需要并行处理独立编码任务时触发。
---

# OpenCode 调用技能

通过 `opencode_client.py`（SSE 事件流）分发编码任务给 OpenCode。

## 安全规则

- **绝对禁止**把 `.claude/settings.json` 中的 API Key、模型名、端点 URL 传给 OpenCode
- OpenCode 用自己独立配置的模型，与 Claude Code 完全隔离

---

## 一、模型选择（每次调用前必须执行）

### 1.1 读取已保存的模型 ID

```bash
cat .claude/opencode-model.txt 2>/dev/null
```

只存模型 ID（如 `deepseek-v4-flash-free`），不存 provider 前缀。

### 1.2 获取 OpenCode 当前可用模型

```bash
opencode models 2>&1
```

输出 `opencode/big-pickle`、`opencode/deepseek-v4-flash-free`。`/` 前是 provider（固定为 `opencode`），后是 model ID。

### 1.3 决策逻辑

```
1. 读取 .claude/opencode-model.txt → modelID
2. opencode models → 提取 model ID 列表（去掉 opencode/ 前缀）
3. 已保存的 modelID 在列表中？
    ├── 是 → 直接使用
    └── 否 → AskUserQuestion 让用户从列表中选择
              → 只保存 model ID（不含 opencode/）到 .claude/opencode-model.txt
```

### 1.4 保存用户选择

```bash
echo "deepseek-v4-flash-free" > .claude/opencode-model.txt
```

---

## 二、执行任务（使用 SSE 客户端）

### 调用方式

```bash
python3 .claude/skills/opencode/scripts/opencode_client.py "任务描述"
```

或用 stdin：

```bash
echo "任务描述" | python3 .claude/skills/opencode/scripts/opencode_client.py
```

### 可选参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model <id>` | 指定模型 ID | `.claude/opencode-model.txt` 内容 |
| `--timeout <秒>` | 总超时 | 180 |
| `--json` | 仅输出 JSON，无进度日志 | 关 |

### 工作原理

```
1. 检查/启动 opencode serve（如未运行）
2. POST /session 创建会话
3. POST /session/:id/message 发送任务
4. GET /event 建立 SSE 连接，实时监听事件
5. 事件到达时输出进度到 stderr：
   [3s] 状态: busy
   [5s] 思考: 需要读取文件...
   [8s] 工具: write
   [10s] 输出: 已完成
   [10s] 任务完成
6. 收到 session.idle → 关闭 SSE，输出 JSON 结果到 stdout
7. 超时保护：
   - 60s 无事件 → 判定卡死，返回错误
   - 180s 总超时 → 返回错误
```

### 输出格式

成功：
```json
{
  "success": true,
  "text": "已完成的任务输出...",
  "model": "deepseek-v4-flash-free",
  "tokens": {"input": 500, "output": 200},
  "event_count": 15
}
```

失败：
```json
{
  "success": false,
  "error": "超过 60s 无事件，可能卡死",
  "text": null
}
```

### 并行多任务

对独立任务，并行启动多个客户端实例：

```bash
python3 .claude/skills/opencode/scripts/opencode_client.py "任务A" > /tmp/oc_a.json 2>&1 &
python3 .claude/skills/opencode/scripts/opencode_client.py "任务B" > /tmp/oc_b.json 2>&1 &
wait
```

## 三、结果验证

1. 检查 `success` 字段
2. 如 `success: false`，读取 `error` 了解原因
3. 如是写代码任务，`ls` 确认文件已生成
4. 必要时用 `Read` 检查生成内容的质量

## 四、跨项目复用

客户端脚本路径：`.claude/skills/opencode/scripts/opencode_client.py`

其他项目可通过以下方式使用：
1. 直接调用绝对路径：`python3 /path/to/other-project/.claude/skills/opencode/scripts/opencode_client.py "任务"`
2. 或复制整个 `opencode/` skill 目录到目标项目的 `.claude/skills/`

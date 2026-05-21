#!/usr/bin/env python3
"""
OpenCode SSE Client — 通过 SSE 事件流调用 OpenCode，支持超时和进度监控。

可跨项目复用。用法：

    python3 opencode_client.py "给 health.py 写测试"
    python3 opencode_client.py --task "修 bug" --timeout 120
    echo "你的任务" | python3 opencode_client.py

输出 JSON 到 stdout，进度信息到 stderr。

模型偏好保存在调用方的 .claude/opencode-model.txt，
不存在时自动使用 opencode models 中的第一个可用模型。
"""

import argparse, json, os, subprocess, sys, threading, time, urllib.request, urllib.error, re

BASE_URL = "http://localhost:4096"
MODEL_FILE = ".claude/opencode-model.txt"
DEFAULT_TIMEOUT = 180          # 总超时秒数
IDLE_TIMEOUT = 60              # 无事件超时秒数


# ── helpers ──────────────────────────────────────────────

def log(msg: str):
    print(f"[opencode-client] {msg}", file=sys.stderr, flush=True)


def api(method: str, path: str, body: dict | None = None) -> dict:
    """调用 OpenCode REST API，返回 JSON。"""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        raise RuntimeError(f"API {method} {path} 失败 ({e.code}): {err[:500]}")


# ── server management ────────────────────────────────────

def ensure_server():
    """确保 opencode serve 运行在 4096 端口。"""
    try:
        r = api("GET", "/global/health")
        if r.get("healthy"):
            log(f"服务器已运行 v{r.get('version', '?')}")
            return
    except Exception:
        pass

    log("启动 opencode serve ...")
    subprocess.Popen(["opencode", "serve", "--port", "4096"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        try:
            r = api("GET", "/global/health")
            if r.get("healthy"):
                log(f"服务器就绪 v{r.get('version', '?')}")
                return
        except Exception:
            pass
    raise RuntimeError("OpenCode 服务器启动失败")


# ── model selection ──────────────────────────────────────

def get_model() -> str:
    """获取模型 ID（仅 model ID 部分，不含 provider）。"""
    if os.path.exists(MODEL_FILE):
        saved = open(MODEL_FILE).read().strip()
        if saved:
            log(f"模型偏好: {saved}")
            return saved

    # 无偏好文件，用第一个可用模型
    try:
        result = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=5,
                                env={**os.environ, "PATH": os.environ.get("PATH", "")})
        models = [line.strip().split("/", 1)[1] for line in result.stdout.strip().split("\n")
                  if line.strip() and "/" in line]
        if models:
            log(f"无偏好文件，用默认模型: {models[0]}")
            return models[0]
    except Exception:
        pass

    # 兜底：如果模型偏好文件不存在且无法获取列表，用 big-pickle
    log("无法获取模型列表，回退到 big-pickle")
    return "big-pickle"


# ── SSE event stream ─────────────────────────────────────

class SSEClient:
    """SSE 事件流监听器，运行在独立线程。"""

    def __init__(self, session_id: str, total_timeout: float, idle_timeout: float):
        self.session_id = session_id
        self.total_timeout = total_timeout
        self.idle_timeout = idle_timeout
        self.events: list[dict] = []
        self.final_text: str | None = None
        self.error: str | None = None
        self.done = threading.Event()
        self._start_time = time.time()

    def run(self):
        try:
            req = urllib.request.Request(f"{BASE_URL}/event")
            req.add_header("Accept", "text/event-stream")
            resp = urllib.request.urlopen(req, timeout=max(30, self.total_timeout))
            last_event_time = time.time()

            for line in resp:
                line = line.decode(errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue

                json_str = line[5:].strip()
                try:
                    evt = json.loads(json_str)
                except json.JSONDecodeError:
                    continue

                self.events.append(evt)
                evt_type = evt.get("type", "?")
                props = evt.get("properties", {})
                last_event_time = time.time()
                elapsed = int(time.time() - self._start_time)

                # 只处理当前 session 的事件
                sid = props.get("sessionID", "")
                if sid and sid != self.session_id:
                    continue

                # 事件分发
                if evt_type == "session.status":
                    status = props.get("status", {}).get("type", "?")
                    log(f"[{elapsed}s] 状态: {status}")

                elif evt_type == "message.part.updated":
                    part = props.get("part", {})
                    ptype = part.get("type", "?")
                    if ptype == "reasoning":
                        text = part.get("text", "")[:100]
                        if text:
                            log(f"[{elapsed}s] 思考: {text}")
                    elif ptype == "tool":
                        log(f"[{elapsed}s] 工具: {part.get('tool', '?')}")
                    elif ptype == "text":
                        text = part.get("text", "")
                        if not self.final_text:
                            self.final_text = text
                        log(f"[{elapsed}s] 输出: {text[:200]}")

                elif evt_type == "session.error":
                    err = props.get("error", {})
                    self.error = err.get("data", {}).get("message", str(err))
                    log(f"[{elapsed}s] 错误: {self.error}")

                elif evt_type in ("session.idle",):
                    log(f"[{elapsed}s] 任务完成")
                    self.done.set()
                    return

                # 超时检查
                if time.time() - last_event_time > self.idle_timeout:
                    self.error = f"超过 {self.idle_timeout}s 无事件，可能卡死"
                    log(f"超时: {self.error}")
                    self.done.set()
                    return

                if time.time() - self._start_time > self.total_timeout:
                    self.error = f"总超时 {self.total_timeout}s"
                    log(f"超时: {self.error}")
                    self.done.set()
                    return

        except Exception as e:
            self.error = f"SSE 连接异常: {e}"
            log(self.error)
        finally:
            self.done.set()

    def get_result(self) -> dict:
        return {
            "text": self.final_text,
            "error": self.error,
            "event_count": len(self.events),
            "events": [{"type": e.get("type"), "ts": e.get("id", "")[:20]}
                       for e in self.events[-10:]],   # 最后 10 个事件摘要
        }


# ── main flow ────────────────────────────────────────────

def run(task: str, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """完整流程：启动服务 → 创建会话 → 发送任务 → SSE 监听 → 返回结果。"""
    model = model or get_model()

    # 1. 确保服务
    ensure_server()

    # 2. 创建 session
    title = task[:40] + ("..." if len(task) > 40 else "")
    session = api("POST", "/session", {"title": title})
    sid = session["id"]
    log(f"会话: {sid}")

    # 3. 启动 SSE 监听（必须在发任务之前）
    sse = SSEClient(sid, total_timeout=timeout, idle_timeout=min(timeout // 2, 60))
    t = threading.Thread(target=sse.run, daemon=True)
    t.start()
    time.sleep(0.5)  # 等待 SSE 连接建立

    # 4. 发任务
    api("POST", f"/session/{sid}/message", {
        "parts": [{"type": "text", "text": task}],
        "model": {"providerID": "opencode", "modelID": model}
    })
    log("任务已发送，等待 SSE 事件...")

    sse.done.wait(timeout=timeout + 5)

    result = sse.get_result()

    # 5. 获取最终消息
    try:
        msgs = api("GET", f"/session/{sid}/message?limit=2")
        for m in msgs:
            if m.get("info", {}).get("role") == "assistant":
                info = m["info"]
                result["model"] = info.get("modelID", "?")
                result["tokens"] = info.get("tokens", {})
                for p in m.get("parts", []):
                    if p.get("type") == "text" and not result["text"]:
                        result["text"] = p.get("text", "")
    except Exception as e:
        log(f"获取消息失败: {e}")

    result["success"] = result["error"] is None
    return result


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OpenCode SSE Client")
    parser.add_argument("task", nargs="*", help="任务描述（也可从 stdin 读取）")
    parser.add_argument("--task", dest="task_opt", help="任务描述（显式参数）")
    parser.add_argument("--model", help="模型 ID（如 deepseek-v4-flash-free）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"总超时秒数（默认 {DEFAULT_TIMEOUT}）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON 到 stdout，无 stderr 日志")
    args = parser.parse_args()

    # 任务来源：--task > 位置参数 > stdin
    task = args.task_opt or " ".join(args.task).strip()
    if not task:
        if not sys.stdin.isatty():
            task = sys.stdin.read().strip()
    if not task:
        print("错误：需要提供任务描述", file=sys.stderr)
        sys.exit(1)

    if args.json:
        # 静默日志
        global log
        def log(msg: str): pass  # noqa: F811

    try:
        result = run(task, model=args.model, timeout=args.timeout)
    except Exception as e:
        result = {"success": False, "error": str(e), "text": None}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

# AP2 x402 v2 + A2A Demo

端到端 AI Agent 支付演示：通过 [A2A (Agent-to-Agent)](https://github.com/google/A2A) 协议和 [x402 v2](https://www.x402.org/) 支付协议，实现 Client Agent → Merchant Agent → Wallet 的完整加密货币支付流程。

## Architecture / 架构

```
┌──────────────────────────────────────────────────┐
│          Client Agent (ADK Web UI :8000)          │
│  - 编排器: 发现 merchant, 委派任务                  │
│  - 处理 x402 支付确认 + 签名                       │
│  - Wallet 接口 (Remote / Local)                   │
└─────────────────────┬────────────────────────────┘
                      │ A2A Protocol (JSON-RPC)
                      ▼
┌──────────────────────────────────────────────────┐
│       Merchant Agent Server (Starlette :8002)     │
│  ┌────────────────────────────────────────────┐  │
│  │ x402MerchantExecutor                       │  │
│  │  - 捕获 x402PaymentRequiredException       │  │
│  │  - 返回 payment_required (input_required)  │  │
│  │  - 验证 + 结算 via Facilitator             │  │
│  └───────────────┬────────────────────────────┘  │
│  ┌───────────────▼────────────────────────────┐  │
│  │ ADKAgentExecutor                           │  │
│  │  - 多轮 agent 执行循环                      │  │
│  │  - 工具调用, 异常传播                        │  │
│  └───────────────┬────────────────────────────┘  │
│  ┌───────────────▼────────────────────────────┐  │
│  │ MerchantAgent (ADK LlmAgent)               │  │
│  │  - Tool: get_product_and_request_payment()  │  │
│  │  - 抛出 x402PaymentRequiredException       │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
                      ▲
                      │ HTTP /sign
                      ▼
┌──────────────────────────────────────────────────┐
│         Wallet Service (Flask :5001)              │
│  - EIP-712 / EIP-3009 transferWithAuthorization  │
│  - POST /sign → PaymentPayload (v2)              │
│  - POST|GET /address → 钱包地址                   │
└──────────────────────────────────────────────────┘
```

## Prerequisites / 前置要求

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
- **Google Gemini API Key** — 用于 Client Agent 和 Merchant Agent 的 LLM 调用（仅 `run_all.sh` 完整启动需要；集成测试不需要）

## Quick Start / 快速开始

### 在新机器上安装和运行

```bash
# 1. Clone
git clone https://github.com/CTest101/ap2-x402-demo.git
cd ap2-x402-demo

# 2. 安装依赖（自动检查 Python 版本 + uv）
bash scripts/setup.sh

# 3. 运行集成测试（不需要 API Key，不需要启动任何服务）
uv run pytest tests/ -v
```

### 运行集成测试（推荐先跑这个验证环境）

集成测试自动启动一个真实 A2A HTTP server（端口 `19402`），无需手动启动任何服务，也不需要 LLM API Key：

```bash
# 运行全部 27 个测试
uv run pytest tests/ -v

# 仅运行 A2A HTTP 集成测试（4 tests，真实 HTTP 调用）
uv run pytest tests/test_a2a_integration.py -v

# 仅运行组件测试（15 tests，直接 Python 调用）
uv run pytest tests/test_e2e.py -v

# 仅运行 Wallet 单元测试（8 tests）
uv run pytest tests/test_wallet.py -v
```

### 启动完整 Demo（需要 Gemini API Key）

```bash
# 1. 编辑 .env，填入 Gemini API Key
cp .env.example .env
vim .env  # 填写 GOOGLE_API_KEY=

# 2. 启动所有 3 个服务
bash scripts/run_all.sh
```

启动后：
- **Client Web UI**: http://localhost:8000 — 在此输入 "buy a laptop" 触发支付流程
- **Merchant Agent**: http://localhost:8002 — A2A Server
- **Wallet Service**: http://localhost:5001 — EIP-3009 签名服务

### 单独启动 Merchant A2A Server（用于外部 Client 对接）

```bash
# 启动 Merchant Server（不需要 Gemini API Key，使用 ScriptedExecutor 模式可跳过 LLM）
uv run python -m merchant --port 8002

# AgentCard 发现端点
curl http://localhost:8002/agents/merchant_agent/.well-known/agent-card.json

# 发送 A2A JSON-RPC 请求
curl -X POST http://localhost:8002/agents/merchant_agent \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "messageId": "test-001",
        "parts": [{"kind": "text", "text": "buy a banana"}]
      }
    }
  }'
```

## Payment Flow / 支付流程

```
User → "buy a laptop"
  │
  ▼ Step 1: Client Agent 发现 Merchant Agent (A2A AgentCard)
  ▼ Step 2: Merchant 调用 tool → 抛出 x402PaymentRequiredException
  ▼         → x402MerchantExecutor 返回 input_required + payment metadata
  ▼ Step 3: Client 收到 payment_required → 提取价格 → 确认 → Wallet 签名
  ▼         → EIP-712 / EIP-3009 transferWithAuthorization
  ▼ Step 4: Client 发送 PaymentPayload → Facilitator verify + settle
  ▼ Step 5: Merchant 确认支付成功 → 返回结果
```

## Testing / 测试

### 测试类型

| 文件 | 类型 | 测试数 | 真实 HTTP? | 需要 LLM? | 需要 API Key? |
|------|------|-------|-----------|----------|-------------|
| `test_a2a_integration.py` | A2A 集成 | 4 | ✅ uvicorn :19402 | ❌ | ❌ |
| `test_e2e.py` | 组件集成 | 15 | ❌ 直接调用 | ❌ | ❌ |
| `test_wallet.py` | 单元 | 8 | Flask test client | ❌ | ❌ |
| **合计** | | **27** | | | |

### A2A 集成测试详情

`test_a2a_integration.py` 启动一个**真实 A2A HTTP server**（端口 `19402`），使用 `ScriptedMerchantExecutor`（无 LLM，预定义行为），完整走通 x402 中间件链路：

- `test_agent_card_endpoint` — AgentCard 发现 (GET /.well-known/agent-card.json)
- `test_initial_message_returns_payment_required` — 首次请求 → 402 支付要求
- `test_full_payment_flow_over_http` — 完整 3 步支付闭环 over HTTP
- `test_payment_artifacts_present` — 验证结算后 artifacts 内容

### 测试报告

详细的端到端测试报告（含时序图、请求/响应数据、覆盖矩阵）见：
[docs/reports/e2e-test-report.md](docs/reports/e2e-test-report.md)

## x402 v1 → v2 Differences / 版本差异

| Aspect | v1 | v2 |
|--------|----|----|
| Network | 字符串名 (`"base-sepolia"`) | CAIP-2 格式 (`"eip155:84532"`) |
| Amount | `maxAmountRequired` | `amount` |
| Resource | 单一 URL 字符串 | `{ url, description, mimeType }` 对象 |
| Version | 隐式 | 显式 `x402Version: 2` |
| Extensions | 不支持 | `extensions` 字段 |
| PaymentPayload | `{ scheme, network, payload }` | `{ x402Version, resource, accepted, payload }` |

## Project Structure / 项目结构

```
ap2-x402-demo/
├── shared/                     # 共享配置和常量
│   ├── constants.py            # CAIP-2 网络, USDC 地址, 协议版本
│   └── config.py               # .env 配置加载
├── wallet/
│   └── server.py               # Flask Wallet Service (:5001)
├── merchant/
│   ├── __main__.py             # Starlette server 启动入口 (:8002)
│   ├── agent.py                # MerchantAgent (ADK LlmAgent)
│   ├── executor.py             # ADKAgentExecutor (ADK↔A2A 桥接)
│   ├── x402_executor.py        # x402MerchantExecutor (支付中间件)
│   └── facilitator.py          # MockFacilitator / LocalFacilitator
├── client/
│   ├── agent.py                # root_agent (ADK Web UI 入口)
│   ├── client_agent.py         # ClientAgent 编排器
│   ├── wallet_client.py        # Wallet 接口 (Remote/Local)
│   ├── remote_connection.py    # A2A 远程连接
│   └── task_store.py           # Task 状态管理
├── tests/
│   ├── test_a2a_integration.py # A2A HTTP 集成测试 (4 tests)
│   ├── test_e2e.py             # 组件集成测试 (15 tests)
│   └── test_wallet.py          # Wallet 单元测试 (8 tests)
├── docs/reports/
│   └── e2e-test-report.md      # 详细测试报告
├── scripts/
│   ├── setup.sh                # 环境初始化
│   └── run_all.sh              # 启动所有服务
├── .env.example                # 环境变量模板
├── pyproject.toml
└── LICENSE                     # Apache 2.0
```

## Configuration / 配置

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Gemini API Key (完整 demo 需要) | — |
| `WALLET_PRIVATE_KEY` | Buyer 钱包私钥 (仅测试用) | x402-local-lab key |
| `MERCHANT_WALLET_ADDRESS` | Merchant 收款地址 | `0x92F6...ff24` |
| `RPC_URL` | Base Sepolia RPC | `https://sepolia.base.org` |
| `USE_MOCK_FACILITATOR` | 使用 Mock Facilitator | `true` |

## References / 参考

- [x402 Protocol Specification v2](https://github.com/coinbase/x402/blob/main/specs/x402-specification-v2.md)
- [A2A x402 Extension v0.2](https://github.com/google-agentic-commerce/a2a-x402/blob/main/spec/v0.2/spec.md)
- [A2A Protocol v1.0.0](https://a2a-protocol.org/latest/specification/)
- [Google ADK](https://google.github.io/adk-docs/)
- [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009) / [EIP-712](https://eips.ethereum.org/EIPS/eip-712)
- [CAIP-2](https://github.com/ChainAgnostic/CAIPs/blob/main/CAIPs/caip-2.md)

## License

[Apache License 2.0](LICENSE)

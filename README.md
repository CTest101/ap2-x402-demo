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

## x402 v2 Key Features / v2 协议特性

| Feature | v1 | v2 |
|---------|----|----|
| Network identifier | `"base-sepolia"` | CAIP-2: `"eip155:84532"` |
| Amount field | `maxAmountRequired` | `amount` |
| Resource | flat string | structured `{ url, description, mimeType }` |
| Extensions | N/A | `extensions` field for protocol扩展 |
| Version field | implicit | explicit `x402Version: 2` |

本 demo 全部使用 **x402 v2** 格式，通过 CAIP-2 chain identifiers 实现跨链兼容。

## Prerequisites / 前置要求

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
- **Google Gemini API Key** — 用于 Client Agent 和 Merchant Agent 的 LLM 调用

## Quick Start / 快速开始

```bash
# 1. 安装依赖
bash scripts/setup.sh

# 2. 编辑 .env，填入 Gemini API Key
vim .env

# 3. 启动所有服务
bash scripts/run_all.sh
```

启动后访问:
- **Client Web UI**: http://localhost:8000
- **Merchant Agent**: http://localhost:8002
- **Wallet Service**: http://localhost:5001

在 Web UI 中输入 "I want to buy a laptop" 即可触发完整支付流程。

## Payment Flow / 支付流程

```
User → "buy a laptop"
  │
  ▼
Step 1: Client Agent 发现 Merchant Agent (A2A AgentCard)
  │
  ▼
Step 2: Merchant Agent 调用 get_product_and_request_payment("laptop")
         → 抛出 x402PaymentRequiredException (含 PaymentRequirements)
         → x402MerchantExecutor 捕获，返回 input_required task + x402 metadata
  │
  ▼
Step 3: Client Agent 收到 payment_required
         → 提取价格信息，向用户确认 "Pay 50000 USDC?"
         → 用户确认 → 调用 Wallet Service /sign 签名
         → EIP-712 / EIP-3009 transferWithAuthorization 签名
  │
  ▼
Step 4: Client Agent 发送签名后的 PaymentPayload 给 Merchant
         → x402MerchantExecutor 调用 Facilitator.verify() 验证签名
         → 调用 Facilitator.settle() 结算交易
  │
  ▼
Step 5: Merchant Agent 收到支付成功通知
         → before_agent_callback 注入虚拟 tool response
         → LLM 确认购买成功，返回结果给用户
```

## Project Structure / 项目结构

```
ap2-x402-demo/
├── shared/                     # 共享配置和常量
│   ├── __init__.py
│   ├── constants.py            # CAIP-2 网络, USDC 地址, 协议版本
│   └── config.py               # .env 配置加载
├── wallet/                     # Wallet Service (Flask :5001)
│   ├── __init__.py
│   └── server.py               # EIP-712/EIP-3009 签名服务
├── merchant/                   # Merchant Agent (Starlette :8002)
│   ├── __init__.py
│   ├── __main__.py             # 服务启动入口
│   ├── agent.py                # MerchantAgent — ADK LlmAgent 定义
│   ├── executor.py             # ADKAgentExecutor — ADK↔A2A 桥接
│   ├── x402_executor.py        # x402MerchantExecutor — 支付流程编排
│   └── facilitator.py          # MockFacilitator / LocalFacilitator
├── client/                     # Client Agent (ADK Web UI :8000)
│   ├── __init__.py             # Root agent 入口
│   ├── client_agent.py         # ClientAgent — 编排器
│   ├── wallet_client.py        # Wallet 接口 (Remote/Local)
│   ├── remote_connection.py    # A2A 远程连接封装
│   └── task_store.py           # 客户端 Task 状态管理
├── tests/                      # 测试套件
│   ├── test_wallet.py          # Wallet 签名测试 (17 tests)
│   └── test_e2e.py             # E2E 支付流程测试 (23 tests)
├── scripts/
│   ├── setup.sh                # 环境初始化
│   └── run_all.sh              # 启动所有服务
├── .env.example                # 环境变量模板
├── pyproject.toml              # 项目元数据和依赖
├── LICENSE                     # Apache 2.0
└── README.md
```

## Configuration / 配置

`.env` 文件中的配置项:

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Gemini API Key (required) | — |
| `WALLET_PRIVATE_KEY` | 钱包私钥 (仅用于 demo) | `0x000...001` |
| `MERCHANT_WALLET_ADDRESS` | 商户收款地址 | `0xAb58...9B` |
| `RPC_URL` | Base Sepolia RPC | `https://sepolia.base.org` |
| `WALLET_SERVICE_PORT` | Wallet Service 端口 | `5001` |
| `MERCHANT_SERVICE_PORT` | Merchant Agent 端口 | `8002` |
| `CLIENT_SERVICE_PORT` | Client Agent 端口 | `8000` |
| `USE_MOCK_FACILITATOR` | 使用 Mock Facilitator | `true` |
| `WALLET_SERVICE_URL` | Wallet Service URL | `http://localhost:5001` |
| `USE_REMOTE_WALLET` | 使用远程钱包签名 | `true` |

## Testing / 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 仅运行 wallet 测试
uv run pytest tests/test_wallet.py -v

# 仅运行 E2E 测试
uv run pytest tests/test_e2e.py -v
```

测试覆盖:
- **test_wallet.py** (17 tests): 签名逻辑、API 端点、EIP-712 结构、CAIP-2 解析
- **test_e2e.py** (23 tests): 完整支付流程、Facilitator 验证/结算、metadata 流转

所有测试使用 MockFacilitator，无需真实区块链或 LLM 调用。

## x402 v1 → v2 Differences / 版本差异

| Aspect | v1 | v2 |
|--------|----|----|
| Network | 字符串名 (`"base-sepolia"`) | CAIP-2 格式 (`"eip155:84532"`) |
| Amount | `maxAmountRequired` | `amount` |
| Resource | 单一 URL 字符串 | `{ url, description, mimeType }` 对象 |
| Version | 隐式 | 显式 `x402Version: 2` |
| Extensions | 不支持 | `extensions` 字段 |
| PaymentPayload | `{ signature, authorization }` | `{ x402Version, scheme, network, resource, accepted, payload }` |
| Agent discovery | — | A2A AgentCard + `x402` extension declaration |

## References / 参考链接

- [x402 Protocol](https://www.x402.org/) — x402 支付协议规范
- [A2A Protocol](https://github.com/google/A2A) — Google Agent-to-Agent 协议
- [Google ADK](https://google.github.io/adk-docs/) — Agent Development Kit
- [EIP-712](https://eips.ethereum.org/EIPS/eip-712) — Typed structured data hashing and signing
- [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009) — Transfer With Authorization
- [CAIP-2](https://github.com/ChainAgnostic/CAIPs/blob/main/CAIPs/caip-2.md) — Blockchain ID specification
- [USDC on Base Sepolia](https://sepolia.basescan.org/token/0x036CbD53842c5426634e7929541eC2318f3dCF7e)

## License

[Apache License 2.0](LICENSE)

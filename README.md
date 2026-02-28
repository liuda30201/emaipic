# 发票自动化处理测试系统

一个可本地一键跑通的端到端发票自动化处理测试项目。默认使用模拟邮箱和 Mock 模型，不需要额外配置即可完成完整测试闭环。

## 端口规划

- 前端：`3000`
- orchestrator：`3001`
- mail_ingestor：`3002`
- attachment_organizer：`3003`
- image_preprocessor：`3004`
- ai_dispatcher：`3005`
- ai_cleaner：`3006`
- index_export：`3007`

这样所有公开端口都从 `3000` 连续递增，避免和其他常见开发端口冲突。

## 项目结构

```text
.
├── Makefile
├── README.md
├── docker-compose.yml
├── frontend
│   ├── Dockerfile
│   ├── app.js
│   ├── config.js
│   ├── index.html
│   └── styles.css
├── libs
│   └── common
│       ├── api.py
│       ├── config.py
│       ├── ids.py
│       ├── logging_utils.py
│       ├── manifests.py
│       ├── models.py
│       ├── paths.py
│       ├── sample_data.py
│       └── storage.py
├── samples
│   └── mailbox
│       ├── README.md
│       ├── attachments
│       ├── mail_001.json
│       └── mail_002.json
├── scripts
│   └── seed.py
├── services
│   ├── ai_cleaner
│   ├── ai_dispatcher
│   ├── attachment_organizer
│   ├── image_preprocessor
│   ├── index_export
│   ├── mail_ingestor
│   └── orchestrator
└── tests
    ├── test_manifests.py
    ├── test_models.py
    └── test_search.py
```

## 一键启动

### 方式一：Docker Compose

```bash
docker compose up --build
```

启动后访问：

- 前端：[http://localhost:3000](http://localhost:3000)
- 网关 Swagger：[http://localhost:3001/docs](http://localhost:3001/docs)

### 方式二：Makefile

```bash
make up
```

`make up` 会先执行样例数据生成，再启动全部容器。

停止：

```bash
make down
```

## Mock 模式演示

1. 打开前端页面。
2. 来源选择 `模拟邮箱`。
3. 点击 `Run Full Pipeline`。
4. 前端会轮询显示 `ingest -> organize -> process -> dispatch -> clean -> index` 每一步状态。
5. 处理完成后可查看每页缩略图、原图和标准化 JSON。
6. 在筛选区按日期、发票号、购买方、销售方搜索。
7. 点击生成 `CSV` 或 `ZIP` 下载导出结果。

默认数据来自 `samples/mailbox/`，若附件图片不存在，会在第一次运行时自动生成简易“测试发票样张图”。

## 本地上传演示

1. 来源选择 `本地上传`。
2. 选择一张或多张图片/PDF。
3. 点击 `Run Full Pipeline`。
4. 系统会先上传到 `mail_ingestor`，再自动执行后续链路。

## 真实邮箱 IMAP（可选）

在环境变量或 `.env` 中配置：

```bash
IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USERNAME=your_account
IMAP_PASSWORD=your_password
IMAP_FOLDER=INBOX
```

然后在前端将来源切换为 `真实 IMAP`，或调用：

```bash
curl -X POST "http://localhost:3001/api/run/full?source=imap&profile=prod_default&mode=mock"
```

如果 IMAP 未配置，系统会返回明确错误，不会影响默认 mock 路径。

## Real 模型配置（可选）

未配置 key 时，`ai_dispatcher` 会自动回退到 mock，不会导致整条链路失败。

示例：

```bash
MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
REAL_MODEL_NAME=gpt-4o-mini
```

然后在前端将模型模式切换为 `real`，或调用：

```bash
curl -X POST "http://localhost:3001/api/run/full?source=mock&profile=prod_default&mode=real"
```

## 数据目录

所有服务默认共享本地 `./data`：

```text
data/
├── ai_clean/
├── ai_raw/
├── db/
├── exports/
├── logs/
├── processed/
├── raw/
└── staging/
```

其中：

- 每个 batch 的链路日志在 `data/logs/{batch_id}/run.log`
- SQLite 数据库在 `data/db/app.db`
- 导出文件在 `data/exports/{export_id}/`

## 关键 API

- `POST /api/run/full`
- `POST /api/run/upload`
- `GET /api/batches/{batch_id}/status`
- `GET /api/invoices/search`
- `POST /api/exports`
- `GET /api/exports/{export_id}/csv`
- `GET /api/exports/{export_id}/zip`

统一返回格式：

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

## 测试

```bash
python -m pytest
```

覆盖内容：

- `pydantic schema` 基础校验
- `manifest` 原子写读
- `search` 过滤逻辑

## 常见问题

### 1. webp 不可用怎么办

某些环境下 Pillow 可能未启用 WebP。`image_preprocessor` 会在保存 `.webp` 失败时自动回退为 `.jpg`，不会影响默认流程。

### 2. PDF 转图依赖什么

项目使用 `pdf2image + poppler`。Docker 镜像内已安装 `poppler-utils`，容器里默认可用。

### 3. OFD 为什么没有直接支持

OFD 被做成可插拔适配器。默认实现会返回 `unsupported`，不会阻塞其他图片/PDF 文件的处理。

### 4. 为什么没有 Redis/Celery

为了低配服务器友好，`orchestrator` 使用进程内 `queue.Queue + daemon worker thread` 完成串行调度，并将批次状态持久化到本地文件。

## 默认闭环说明

默认无任何外部依赖：

- 邮件来源：本地 mock 邮箱
- 模型：Mock JSON 提取器
- 存储：本地文件系统
- 数据库：SQLite

因此只要容器能启动，默认链路即可完整跑通。

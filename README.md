# 发票自动化处理测试系统

一个可本地一键跑通的端到端发票自动化处理测试项目。默认使用模拟邮箱和 `mock` 模型，无需任何 API key，即可完成上传/拉取、整理、预处理、多模型对比、清洗、索引、查询和导出闭环。

## 端口规划

- 前端：`3000`
- orchestrator：`3001`
- mail_ingestor：`3002`
- attachment_organizer：`3003`
- image_preprocessor：`3004`
- ai_dispatcher：`3005`
- ai_cleaner：`3006`
- index_export：`3007`
- model_hub：`3008`

## 核心流程

系统按以下 6 步执行：

1. `mail_ingestor` 拉取模拟邮箱/IMAP，或接收本地批量上传
2. `attachment_organizer` 归类并输出 `data/staging/{batch_id}/staging_manifest.json`
3. `image_preprocessor` 统一转成页图并输出 `data/processed/{batch_id}/manifest.json`
4. `ai_dispatcher` 按每页 x 每模型调度 `model_hub`
5. `ai_cleaner` 清洗结果并输出 `data/ai_clean/{batch_id}/normalized_results.jsonl`
6. `index_export` 建立 SQLite 索引并提供查询/CSV/ZIP 导出

## 模型库

`model_hub` 是独立服务，使用 [services/model_hub/models.yaml](/Users/liuqiang/Documents/github/Project/发票链路测试/services/model_hub/models.yaml) 管理模型列表。

预置模型：

- `mock` / Mock Extractor
- `glm-ocr`
- `glm-4.6v-flash`
- `glm-4.6v-flashx`
- `glm-4v-flash`
- `qwen3-vl-plus`
- `qwen3-vl-flash`
- `qwen-vl-plus`
- `qwen-vl-ocr`

规则：

- `mock` 默认永远可用
- GLM/Qwen 模型在缺少 key 时会出现在前端，但显示为 unavailable
- 前端最多允许勾选 3 个模型，后端也会校验

### 环境变量

可选真实模型配置：

```bash
GLM_API_KEY=
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

未配置时不影响默认 mock 路径。

## 一键启动

### Docker Compose

```bash
docker compose up --build
```

启动后：

- 前端：[http://localhost:3000](http://localhost:3000)
- orchestrator docs：[http://localhost:3001/docs](http://localhost:3001/docs)

### Makefile

```bash
make up
```

停止：

```bash
make down
```

## 前端使用

前端默认自动跟随当前访问主机，按 `http://当前主机:3001` 访问 orchestrator。

例如：

- 通过 `http://localhost:3000` 打开时，请求 `http://localhost:3001`
- 通过 `http://192.168.1.10:3000` 打开时，请求 `http://192.168.1.10:3001`

因此局域网 IP 访问不会再因为写死 `localhost` 导致 `Failed to fetch`。

前端功能：

- 支持 `upload / mock / imap`
- 支持模型多选（最多 3 个）
- 查看每页缩略图
- 按页切换查看不同模型结果
- 在识别清单中打开浮窗预览，预览内容固定为 AI 实际收到的 `processed page image`
- 按 `model_key` 搜索与导出
- 筛选区可勾选“包含失败记录”，查看失败模型运行
- 导出支持选择“仅成功”或“全部结果（含失败）”

## 预览与图片代理

浮窗预览展示的是 AI 输入图，不是原始附件。前端始终通过 orchestrator 代理后的 processed 图片地址预览：

- `GET /api/batches/{batch_id}/pages/{page_id}/image`
- `GET /api/batches/{batch_id}/pages/{page_id}/thumb`

浏览器可直接访问：

```bash
http://localhost:3001/api/batches/{batch_id}/pages/{page_id}/image
```

这两个接口由 orchestrator 转发到 `image_preprocessor:3004`，用于统一前端访问入口，避免跨端口和跨容器地址暴露。

## 小图自适应预处理策略

为减少小图在预处理后变糊的问题，`image_preprocessor` 对 `max_edge < 1600` 的图片自动启用 small-text 自适应策略，且不改变用户选择的 profile 名称：

- 阈值：`max(width, height) < 1600`
- 输出格式：仍跟随当前 profile（webp/jpg）
- 输出质量：小图强制提升到 `>= 90`
- 长边目标：提升到 `2000`，并允许上采样
- 缩放插值：使用 `Image.Resampling.LANCZOS`
- 轻锐化：启用 `Unsharp Mask`
- 裁边：保持开启
- 处理后在 `processed manifest` 中记录：
  - `adaptive_applied`
  - `original_size`
  - `output_quality`
  - `upscale_applied`
  - `unsharp`

正常图（`max_edge >= 1600`）仍按原 profile 参数执行，例如 `prod_default` 继续使用原始的 `webp q=75 long_edge=1800`。

## 默认验收路径

### 1. 本地上传 + mock

1. 打开前端
2. 选择来源 `本地上传`
3. 选择 1 张或多张图片/PDF
4. 勾选模型 `mock`
5. 点击 `Run Full Pipeline`

预期：

- 批次完成
- 可以看到页图和 `mock` 结果
- 搜索可命中
- 可导出 CSV 和 ZIP

### 2. 模拟邮箱 + mock

1. 选择来源 `模拟邮箱`
2. 勾选模型 `mock`
3. 点击 `Run Full Pipeline`

预期：默认样例能完整跑通。

### 3. 勾选 GLM/Qwen 但未配置 key

预期：模型列表显示为 unavailable，前端复选框置灰；即使只使用 `mock`，全链路仍正常。

### 4. 勾选 2~3 个模型

预期：

- 同一页可查看多模型结果
- 搜索和导出可指定 `model_key`
- 勾选“包含失败记录”后，可在筛选结果中看到失败模型及失败原因

## API 示例

### 列出模型

```bash
curl "http://localhost:3001/api/models"
```

### mock 邮箱完整运行

```bash
curl -X POST "http://localhost:3001/api/run/full" \
  -H "Content-Type: application/json" \
  -d '{"source":"mock","profile":"prod_default","models":["mock"],"prompt_version":"v1"}'
```

### 本地上传并运行（可直接复制）

```bash
curl -X POST "http://localhost:3001/api/run/upload" \
  -F 'profile=prod_default' \
  -F 'prompt_version=v1' \
  -F 'models=["mock"]' \
  -F 'files=@samples/mailbox/attachments/invoice_alpha.ppm;type=image/x-portable-pixmap'
```

### 查看某一页的 AI 输入图

```bash
curl -o preview.webp "http://localhost:3001/api/batches/{batch_id}/pages/{page_id}/image"
```

## 数据目录

```text
data/
├── ai_clean/
│   └── {batch_id}/
│       ├── issues.jsonl
│       ├── normalized_results.jsonl
│       └── results.json
├── ai_raw/
├── db/
│   └── app.db
├── exports/
├── logs/
├── processed/
│   └── {batch_id}/manifest.json
├── raw/
└── staging/
    └── {batch_id}/staging_manifest.json
```

## 导出内容

ZIP 内包含：

- `invoices.csv`
- `originals/` 原始附件
- `images/` 选中页标准图
- `pdf/combined.pdf` 选中页图合成的 PDF
- `manifests/` 相关 manifest 与 JSONL

## 测试

```bash
python -m pytest
```

覆盖内容：

- `models.yaml` 加载与模型可用性判断
- `ai_cleaner` JSON 数组修复与字段归一
- `index_export` 搜索支持 `model_key`
- 共享 schema/manifest 基础能力

## 常见问题

### WebP 不可用

若 Pillow 当前环境未启用 WebP，`image_preprocessor` 会自动回退为 `.jpg`。

### PDF 转图依赖

项目使用 `pdf2image + poppler`。`image_preprocessor` Docker 镜像已安装 `poppler-utils`。

### OFD

OFD 仍为可插拔适配器。默认返回 `unsupported`，不会影响图片/PDF 路径。

### 为什么没有 Redis/Celery

为了低配服务器友好，`orchestrator` 使用进程内 `queue.Queue + daemon worker thread` 串行调度，并把状态持久化到本地文件。

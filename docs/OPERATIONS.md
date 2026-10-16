# 使用、API 与运维

## 每次更新资料

先等待当前答题批次完成，再停止服务，更新同一个输入目录，然后执行：

```bash
systemctl --user stop kexam.service
source .venv/bin/activate
kexam ingest private/inputs
kexam index
systemctl --user start kexam.service
```

普通前台启动时用 Ctrl+C 停止服务。不要在资料/索引变更过程中启动新答题批次。无变更的源文件和向量会复用；索引失败不会覆盖上一次已发布 generation。

## API

| 方法 | 地址 | 说明 |
|---|---|---|
| GET | `/api/health` | 语料数量、embedding 模式 |
| GET | `/api/courses` | 可筛选来源 |
| GET | `/api/search?q=...&course=...` | 最多 10 条检索结果 |
| GET | `/api/quiz/status` | 模型配置状态、活动批次、限制 |
| POST | `/api/quiz/submit` | 提交题图与并发配置 |
| GET | `/api/quiz/jobs/<id>` | 批次状态与逐题结果 |
| GET | `/quiz/export/<id>.md`、`.json` | 导出结果 |
| GET | `/files/assets/...` | 仅已索引的图片 |
| GET | `/watch/<source-id>?t=...` | 视频时间点回看 |

POST 需携带同一次服务启动生成的页面令牌，HTTP Header 为 `X-Course-Token`。它用于限制跨站提交，不是多用户身份验证。不要把令牌或密钥记录进公开日志。

请求体示意：

```json
{
  "images": [{"name": "practice.png", "data_url": "data:image/png;base64,..."}],
  "course": "",
  "concurrency": 3,
  "allow_inference": true
}
```

`course` 为空表示全部来源；非空使用 `/api/courses` 的 ID。`concurrency` 是整数 1–8。结果按原图/原题排序，先完成的题即时更新。批次运行过程中不能修改该批并发数或追加图片，下一批可重新设定。

## 密钥与资料

密钥从 `[quiz].api_key_env` 指定的环境变量读取；仓库不保存真实值。用户 systemd 可从 `.env` 注入，文件应设为 600。命令行启动需自行导出变量。

`data/` 中保存上传题图、提取原文、源文件路径、答案、索引和历史缓存；备份时按私有资料保护。`.gitignore` 只是防误提交，不是加密或访问控制。外部模型会接收题图和检索到的证据，使用前确认相应资料可以发送给所选 API。

文件服务只开放索引列出的图片以及导入的视频来源，不开放任意数据文件或配置。页面转义原文内容。服务固定监听 loopback，并检查 Host 与 POST 令牌；这些措施不等于可安全公开到互联网。

## 故障排查

| 现象 | 检查或处理 |
|---|---|
| `No supported input files` | 输入目录为空、扩展名不支持，或没有复制示例 |
| `No module named ...` | 激活同一虚拟环境，安装对应 optional extra |
| FTS5 不存在 | 换用带 FTS5 的 Python/SQLite 发行版 |
| PDF/图片没有文字结果 | 启用 OCR，或使用 WeMM 原图检索；检查截图分辨率 |
| PPTX 转换失败 | 安装 LibreOffice；先人工导出 PDF，检查字体/文件密码 |
| 视频打不开 | 用 FFmpeg 检查编码，浏览器可能不支持 MKV/部分编码；手动转成 H.264/AAC MP4 后重新导入 |
| `.writer.lock` 已存在 | 确认没有 ingest/index 进程。若为崩溃遗留，备份后手工移除该锁，再重试 |
| `Corpus changed` | 重新 `kexam index` 后启动服务 |
| `Embedding config changed` | 不能混用模型/版本，按新配置重新建索引 |
| GPU 不可用 | 同一环境先做 `torch.cuda.is_available()` 自检；AMD 核对官方支持矩阵 |
| GPU OOM | 停掉不相关大型模型、使用 2B、降低 PDF DPI 后重新导入；仅调低题目并发未必降低单次 embedding 峰值 |
| API 401/403/404 | 核对完整 Chat Completions 地址、模型、密钥变量及服务商权限 |
| API 429 | 降低页面并发和全局 `max_model_requests`；程序对部分临时失败只有限重试 |
| API 输出非 JSON / 截断 | 检查视觉能力、输出长度和供应商兼容性；模型可能未遵循格式 |
| `configured=true` 但答题失败 | 该状态不探测真实模型；使用虚构截图端到端测试 |
| 重启后批次 `interrupted` | 查看已完成结果，只重提未完成题；系统不自动重放调用 |
| 检索结果与问题不符 | 优先核对来源、OCR/ASR，再调样本和检索方式；不能只依赖相似度 |

## 测试与评测

单元/集成测试：`python -m unittest discover -s tests -v`。测试中的模型可模拟，不要求真实 API 密钥。`examples/practice.json` 是虚构评分标签，必须与证据库隔离。

部署验收先录制一小段自己的虚构幻灯片视频，检查画面、转写、来源链接和真实视觉 API。模型更新、改索引、改提示后，用独立题目评测，不要只重跑已用于调参的题目。记录单选/多选完全匹配率、拒答率、检索命中率、延迟和 API 失败率。

## 备份和升级

备份私有输入、`config.toml`、密钥安全存储、`data/` 和使用的模型版本。代码仓库应仅备份通用程序与文档。不要把运行目录直接打包后公开。

升级前备份并等待批次完成，停服务，更新代码、依赖，跑测试，再启动。索引格式或模型变化时必须重新构建。失败的 generation 和旧缓存不会自动清理，磁盘不足时应在停服务且有备份后人工清理，不要删除正在使用的索引。

## 后续可做

可按需求加入：独立 embedding RPC、持久任务队列、多用户鉴权、资料集合权限、ANN 向量库、跨编码器重排、题干编辑与重试、人工纠错记录和独立评测看板。这些是扩展路线，当前版本尚未实现。
